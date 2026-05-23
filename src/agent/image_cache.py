"""Self-host event poster bytes (Task 14) with source-URL deduplication (Task 2).

Posters are stored once per upstream URL under ``data/<topic>/images/<hash>.<ext>``.
Many events can share the same file when they point at the same remote image.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import httpx

from agent.models import Resource

logger = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; AIAgentResearch/0.1; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(10.0, connect=4.0)
MAX_BYTES = 4 * 1024 * 1024

IMAGES_SUBDIR = "images"
INDEX_FILENAME = "_index.json"
INDEX_VERSION = 2
_JSON_PATH_PREFIX = f"data/{IMAGES_SUBDIR}"

_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}
_KNOWN_EXTS: tuple[str, ...] = tuple(sorted(set(_CONTENT_TYPE_TO_EXT.values())))


@dataclass
class EventImageRef:
    """One event's mapping to a shared on-disk poster file."""

    source: str
    file: str


@dataclass
class ImageCacheIndex:
    """Sidecar index: many events may reference one cached file."""

    events: dict[str, EventImageRef] = field(default_factory=dict)

    def source_to_file(self) -> dict[str, str]:
        """Build ``source_url → filename`` for fast cache hits before download."""
        out: dict[str, str] = {}
        for ref in self.events.values():
            if ref.source and ref.file:
                out.setdefault(ref.source, ref.file)
        return out


@dataclass
class DedupeStats:
    """Result of a one-time on-disk duplicate merge."""

    files_removed: int = 0
    events_relinked: int = 0


def _json_images_prefix(output_dir: Path, *, data_base: Path | None = None) -> str:
    if data_base is None:
        from agent import config as _cfg

        data_base = _cfg.DATA_BASE_DIR
    try:
        rel = output_dir.resolve().relative_to(data_base.resolve())
    except ValueError:
        return _JSON_PATH_PREFIX
    if rel.parts:
        return f"data/{rel.as_posix()}/{IMAGES_SUBDIR}"
    return _JSON_PATH_PREFIX


def _json_path_for(filename: str, *, images_prefix: str) -> str:
    return f"/{images_prefix.strip('/')}/{filename}"


def file_name_for_source(url: str, ext: str) -> str:
    """Stable on-disk name from the upstream URL (filenames need not match event ids)."""
    digest = hashlib.sha256(url.strip().encode()).hexdigest()[:16]
    return f"{digest}{ext}"


def _file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _is_image_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in _KNOWN_EXTS


def _legacy_file_for_event(event_id: str, images_dir: Path) -> Path | None:
    """Pre-task-2 layout: ``<event-id>.<ext>``."""
    for ext in _KNOWN_EXTS:
        candidate = images_dir / f"{event_id}{ext}"
        if candidate.is_file():
            return candidate
    return None


def _load_index(images_dir: Path) -> ImageCacheIndex:
    p = images_dir / INDEX_FILENAME
    if not p.exists():
        return ImageCacheIndex()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Could not read %s (%s); rebuilding image cache index.", p, exc)
        return ImageCacheIndex()

    if isinstance(raw, dict) and raw.get("version") == INDEX_VERSION:
        events: dict[str, EventImageRef] = {}
        for eid, rec in (raw.get("events") or {}).items():
            if not isinstance(rec, dict):
                continue
            source = str(rec.get("source") or "").strip()
            fname = str(rec.get("file") or "").strip()
            if source and fname:
                events[str(eid)] = EventImageRef(source=source, file=fname)
        return ImageCacheIndex(events=events)

    # Legacy v1: ``{ "event-id": "https://source..." }``
    if isinstance(raw, dict):
        events = {}
        for eid, source in raw.items():
            if isinstance(source, str) and source.strip().startswith("http"):
                events[str(eid)] = EventImageRef(source=source.strip(), file="")
        return ImageCacheIndex(events=events)

    return ImageCacheIndex()


def _save_index(images_dir: Path, index: ImageCacheIndex) -> None:
    payload = {
        "version": INDEX_VERSION,
        "events": {
            eid: {"source": ref.source, "file": ref.file}
            for eid, ref in sorted(index.events.items())
        },
    }
    p = images_dir / INDEX_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


def _write_image_file(dest: Path, data: bytes) -> bool:
    try:
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        tmp.write_bytes(data)
        tmp.replace(dest)
        return True
    except OSError as exc:
        logger.warning("image cache: could not write %s: %s", dest.name, exc)
        return False


def _normalize_local_poster_url(
    url: str,
    event_id: str,
    *,
    images_dir: Path,
    images_prefix: str,
    index: ImageCacheIndex,
) -> str:
    raw = url.strip().lstrip("/")
    ref = index.events.get(event_id)
    if ref and ref.file and (images_dir / ref.file).is_file():
        return _json_path_for(ref.file, images_prefix=images_prefix)

    legacy = _legacy_file_for_event(event_id, images_dir)
    if legacy:
        return _json_path_for(legacy.name, images_prefix=images_prefix)

    legacy_prefix = f"{_JSON_PATH_PREFIX}/"
    if raw.startswith(legacy_prefix) and images_prefix != _JSON_PATH_PREFIX:
        fname = raw[len(legacy_prefix) :]
        if (images_dir / fname).is_file():
            return f"/{images_prefix}/{fname}"

    if raw and not raw.startswith("http"):
        return f"/{raw}"
    return url


def _download(url: str) -> tuple[bytes, str] | None:
    if not url or not url.lower().startswith("http"):
        return None
    try:
        with httpx.Client(
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        ) as client:
            resp = client.get(url)
            resp.raise_for_status()
            ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
            ext = _CONTENT_TYPE_TO_EXT.get(ctype)
            if not ext:
                logger.debug(
                    "image cache: rejecting %s — content-type %r is not a known image",
                    url,
                    ctype,
                )
                return None
            data = resp.content
            if len(data) > MAX_BYTES:
                logger.debug(
                    "image cache: rejecting %s — %d bytes exceeds %d cap",
                    url,
                    len(data),
                    MAX_BYTES,
                )
                return None
            return data, ext
    except Exception as exc:
        logger.debug("image cache: fetch failed for %s: %s", url, exc)
        return None


def _refresh_events_json(output_dir: Path) -> None:
    """Rewrite ``events.json`` thumbnail paths after dedupe relinks."""
    from agent.json_output import JSON_FILENAME, render_events_json
    from agent.local_output import RESEARCH_FILENAME, load_spreadsheet_resources

    xlsx = output_dir / RESEARCH_FILENAME
    if not xlsx.is_file():
        return
    resources = load_spreadsheet_resources(xlsx)
    synced = cache_thumbnails(resources, output_dir=output_dir)
    (output_dir / JSON_FILENAME).write_text(render_events_json(synced), encoding="utf-8")


def dedupe_existing_images(*, output_dir: Path) -> DedupeStats:
    """Merge byte-identical poster files and relink events to one shared file."""
    images_dir = output_dir / IMAGES_SUBDIR
    if not images_dir.is_dir():
        return DedupeStats()

    index = _load_index(images_dir)
    stats = DedupeStats()

    for eid, ref in index.events.items():
        if ref.file and (images_dir / ref.file).is_file():
            continue
        legacy = _legacy_file_for_event(eid, images_dir)
        if legacy:
            ref.file = legacy.name

    all_files = [p for p in images_dir.iterdir() if _is_image_file(p)]
    by_hash: dict[str, list[Path]] = {}
    for path in all_files:
        by_hash.setdefault(_file_hash(path), []).append(path)

    name_map: dict[str, str] = {}
    for paths in by_hash.values():
        keeper = min(paths, key=lambda p: (len(p.name), p.name))
        for path in paths:
            name_map[path.name] = keeper.name
            if path != keeper:
                try:
                    path.unlink()
                    stats.files_removed += 1
                except OSError as exc:
                    logger.debug("image cache dedupe: could not delete %s: %s", path, exc)

    # Resolve each event to its canonical on-disk file (after byte-dedupe).
    event_file: dict[str, str] = {}
    for eid, ref in index.events.items():
        if not ref.source or not ref.file:
            continue
        canonical = name_map.get(ref.file, ref.file)
        if (images_dir / canonical).is_file():
            event_file[eid] = canonical

    # One URL-hash filename per upstream source URL.
    source_file: dict[str, str] = {}
    for eid, ref in index.events.items():
        if eid not in event_file:
            continue
        ext = Path(event_file[eid]).suffix or ".jpg"
        source_file[ref.source] = file_name_for_source(ref.source, ext)

    for source, target in source_file.items():
        current_names = {event_file[eid] for eid, ref in index.events.items() if ref.source == source and eid in event_file}
        for current in current_names:
            current_path = images_dir / current
            target_path = images_dir / target
            if not current_path.is_file():
                continue
            if target_path.is_file():
                if _file_hash(target_path) == _file_hash(current_path) and current_path != target_path:
                    try:
                        current_path.unlink()
                        stats.files_removed += 1
                    except OSError:
                        pass
            else:
                try:
                    current_path.replace(target_path)
                except OSError:
                    target = current

    rebuilt: dict[str, EventImageRef] = {}
    for eid, ref in index.events.items():
        if eid not in event_file:
            continue
        target = source_file.get(ref.source, event_file[eid])
        if not (images_dir / target).is_file():
            target = event_file[eid]
        if ref.file != target:
            stats.events_relinked += 1
        rebuilt[eid] = EventImageRef(source=ref.source, file=target)

    index.events = rebuilt
    _save_index(images_dir, index)
    _refresh_events_json(output_dir)
    if stats.files_removed or stats.events_relinked:
        logger.info(
            "Image dedupe for %s: removed %s duplicate file(s), relinked %s event(s).",
            output_dir.name,
            stats.files_removed,
            stats.events_relinked,
        )
    return stats


def dedupe_images_for_all_topics(*, data_base: Path) -> int:
    """Run :func:`dedupe_existing_images` for every ``data/<topic>/images`` folder."""
    if not data_base.is_dir():
        return 0
    total = 0
    for child in sorted(data_base.iterdir()):
        if child.is_dir() and (child / IMAGES_SUBDIR).is_dir():
            total += dedupe_existing_images(output_dir=child).files_removed
    return total


def cache_thumbnails(
    resources: list[Resource], *, output_dir: Path
) -> list[Resource]:
    """Download each distinct poster URL once; many events may share one file."""
    images_dir = output_dir / IMAGES_SUBDIR
    images_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(images_dir)
    source_map = index.source_to_file()
    images_prefix = _json_images_prefix(output_dir)

    out: list[Resource] = []
    for r in resources:
        eid = (r.id or "").strip()
        url = (r.thumbnail_url or "").strip()

        if url and not url.lower().startswith("http"):
            normalized = _normalize_local_poster_url(
                url,
                eid,
                images_dir=images_dir,
                images_prefix=images_prefix,
                index=index,
            )
            out.append(
                r.model_copy(update={"thumbnail_url": normalized})
                if normalized != url
                else r
            )
            continue

        if not eid or not url:
            out.append(r)
            continue

        # Cache hit by upstream URL — no download (Task 2).
        shared_file = source_map.get(url)
        if shared_file and (images_dir / shared_file).is_file():
            index.events[eid] = EventImageRef(source=url, file=shared_file)
            out.append(
                r.model_copy(
                    update={
                        "thumbnail_url": _json_path_for(
                            shared_file, images_prefix=images_prefix
                        )
                    }
                )
            )
            continue

        prev = index.events.get(eid)
        if prev and prev.source == url and prev.file and (images_dir / prev.file).is_file():
            source_map[url] = prev.file
            out.append(
                r.model_copy(
                    update={
                        "thumbnail_url": _json_path_for(
                            prev.file, images_prefix=images_prefix
                        )
                    }
                )
            )
            continue

        result = _download(url)
        if result is None:
            index.events.pop(eid, None)
            out.append(r.model_copy(update={"thumbnail_url": None}))
            continue

        data, ext = result
        fname = file_name_for_source(url, ext)
        dest = images_dir / fname
        if not dest.is_file():
            if not _write_image_file(dest, data):
                index.events.pop(eid, None)
                out.append(r.model_copy(update={"thumbnail_url": None}))
                continue

        index.events[eid] = EventImageRef(source=url, file=fname)
        source_map[url] = fname
        out.append(
            r.model_copy(
                update={
                    "thumbnail_url": _json_path_for(fname, images_prefix=images_prefix)
                }
            )
        )

    _save_index(images_dir, index)
    return out


def garbage_collect(
    active_event_ids: Iterable[str], *, output_dir: Path
) -> int:
    """Delete poster files no longer referenced by any active event."""
    images_dir = output_dir / IMAGES_SUBDIR
    if not images_dir.exists():
        return 0

    active = {eid for eid in active_event_ids if eid}
    index = _load_index(images_dir)
    active_files = {
        ref.file for eid, ref in index.events.items() if eid in active and ref.file
    }

    removed = 0
    for path in images_dir.iterdir():
        if not _is_image_file(path):
            continue
        if path.name in active_files:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("image cache GC: could not delete %s: %s", path, exc)

    index.events = {eid: ref for eid, ref in index.events.items() if eid in active}
    _save_index(images_dir, index)
    return removed
