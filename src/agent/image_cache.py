"""Self-host event poster bytes (Task 14).

Why this exists
---------------
The curator and the thumbnail-enrichment passes pick perfectly plausible image
URLs.  But when a static page on a *different* origin tries to ``GET`` those
bytes, a lot of things go wrong:

- Hotlink / referer protection (most CMSes, Next.js ``_next/image`` route).
- Short-lived signed/optimised URLs that expire between scrape time and view
  time (CDN caches, S3 presigned, Ticketmaster optimisers).
- Non-existent or hallucinated URLs that look real to the curator.
- Plain network unreliability and bot-challenge pages.

This module side-steps every one of those by downloading each poster *during
the pipeline run* into ``data/images/<event-id>.<ext>``.  The Angular app
then loads each image from its own origin, so cross-origin restrictions
simply don't apply any more.

Failure policy
--------------
Failed downloads are silently demoted to ``thumbnail_url = None`` so the
existing 🎸 placeholder in the Angular template renders cleanly — never a
broken-image icon.  This is by design (Task 14, decision 2): a missing image
is far better UX than a broken one.

Idempotency
-----------
A sidecar ``data/images/_index.json`` records ``event_id → source_url`` so
re-runs only refetch when the source URL has actually changed (e.g. when
``local_output._maybe_upgrade_poster`` swaps in a fresher poster).  Otherwise
a re-run is zero-cost.

Garbage collection
------------------
``garbage_collect`` deletes files in ``data/images/`` whose Event ID is no
longer in the live spreadsheet (typically because the event has aged past
``local_today()`` and ``merge_and_write`` pruned it).  The sidecar index is
pruned to match.  This keeps the folder bounded on long-running deployments.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Iterable

import httpx

from agent.models import Resource

logger = logging.getLogger(__name__)

# Mirror the User-Agent / timeout pattern already used by ``enrich._fetch_html``
# so the two networking surfaces fail in the same way for the same hosts.
USER_AGENT = (
    "Mozilla/5.0 (compatible; AIAgentResearch/0.1; +https://example.local) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = httpx.Timeout(10.0, connect=4.0)

# Cap a single poster at 4 MB.  Real event posters are typically 30–300 KB;
# anything larger is almost certainly an unoptimised hero image we don't want
# to serve as a 56×56 thumbnail anyway.
MAX_BYTES = 4 * 1024 * 1024

# All image bytes live under ``OUTPUT_DIR / IMAGES_SUBDIR``.  The sidecar
# ``_index.json`` lives in the same folder so it travels with the cache.
IMAGES_SUBDIR = "images"
INDEX_FILENAME = "_index.json"

# Path written *into* events.json.  The Angular ``<base href="/">`` plus
# angular.json's ``"input": "../data", "output": "/data"`` mapping means a
# poster on disk at ``data/images/<id>.<ext>`` is served at the same path
# from the app's own origin.  We hard-code ``data/`` here because the build
# config does too — overriding ``OUTPUT_DIR`` to a non-default folder also
# requires a matching angular.json change, which is out of scope for this
# module.
_JSON_PATH_PREFIX = f"data/{IMAGES_SUBDIR}"

# Map response Content-Type to a safe filename extension.  Anything not in
# this mapping is rejected — we don't want to save HTML error pages, SVGs
# (potential XSS surface area), or unknown formats the browser may not
# render reliably.
_CONTENT_TYPE_TO_EXT: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}

# Set of all extensions we may have written historically — used to find an
# existing cache file regardless of its extension.
_KNOWN_EXTS: tuple[str, ...] = tuple(sorted(set(_CONTENT_TYPE_TO_EXT.values())))


# ── Sidecar index ─────────────────────────────────────────────────────────────


def _load_index(images_dir: Path) -> dict[str, str]:
    """Read the ``event_id → source_url`` sidecar.

    Returns an empty dict on first run or if the file is unreadable — the
    cache will simply rebuild itself on the next pass.
    """
    p = images_dir / INDEX_FILENAME
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            return {str(k): str(v) for k, v in raw.items()}
    except Exception as exc:
        logger.warning("Could not read %s (%s); rebuilding image cache index.", p, exc)
    return {}


def _save_index(images_dir: Path, index: dict[str, str]) -> None:
    """Write the sidecar atomically (tmp + replace) so a crash can't corrupt it."""
    p = images_dir / INDEX_FILENAME
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(index, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(p)


# ── On-disk file helpers ──────────────────────────────────────────────────────


def _existing_file_for(event_id: str, images_dir: Path) -> Path | None:
    """Return the on-disk poster file for *event_id* regardless of extension."""
    for ext in _KNOWN_EXTS:
        candidate = images_dir / f"{event_id}{ext}"
        if candidate.exists():
            return candidate
    return None


def _delete_existing_for(event_id: str, images_dir: Path) -> None:
    """Remove every cached file for *event_id* (called before re-download)."""
    for ext in _KNOWN_EXTS:
        candidate = images_dir / f"{event_id}{ext}"
        try:
            candidate.unlink(missing_ok=True)
        except OSError:
            # Best-effort cleanup — a leftover stale file just means GC will
            # catch it next run.  No reason to fail the whole pipeline.
            pass


def _json_path_for(event_id: str, ext: str) -> str:
    """Build the value written into events.json for a successfully cached poster."""
    return f"{_JSON_PATH_PREFIX}/{event_id}{ext}"


# ── HTTP fetch ────────────────────────────────────────────────────────────────


def _download(url: str) -> tuple[bytes, str] | None:
    """Single GET; return ``(bytes, ext)`` for image responses or ``None``.

    Rejects:
      * non-HTTP URLs
      * non-2xx responses
      * non-image / unsupported Content-Type
      * bodies larger than :data:`MAX_BYTES`
      * any networking exception (logged at DEBUG only — a missing poster is
        a normal, expected outcome for hotlink-protected sites)
    """
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
                    url, ctype,
                )
                return None

            data = resp.content
            if len(data) > MAX_BYTES:
                logger.debug(
                    "image cache: rejecting %s — %d bytes exceeds %d cap",
                    url, len(data), MAX_BYTES,
                )
                return None

            return data, ext
    except Exception as exc:
        logger.debug("image cache: fetch failed for %s: %s", url, exc)
        return None


# ── Public API ────────────────────────────────────────────────────────────────


def cache_thumbnails(
    resources: list[Resource], *, output_dir: Path
) -> list[Resource]:
    """Download each resource's poster once and rewrite the URL to a local path.

    The transformation per resource:

    - remote http URL **and** download succeeds  →  ``data/images/<id>.<ext>``
    - remote http URL **and** download fails     →  ``None``  (placeholder)
    - already a local path (``data/images/...``) →  unchanged  (idempotent)
    - ``None`` / empty                            →  unchanged

    Args:
        resources: Spreadsheet-backed events about to be serialised to JSON.
        output_dir: Usually ``config.OUTPUT_DIR`` (default ``data/``).
            Posters are written under ``output_dir / "images" /``.

    Returns:
        A new list with ``thumbnail_url`` rewritten.  The input list is not
        mutated; each rewritten resource is produced via ``model_copy``.
    """
    images_dir = output_dir / IMAGES_SUBDIR
    images_dir.mkdir(parents=True, exist_ok=True)
    index = _load_index(images_dir)

    out: list[Resource] = []
    for r in resources:
        eid = (r.id or "").strip()
        url = (r.thumbnail_url or "").strip()

        # Pass-through: already a local path (re-running cache_thumbnails on
        # spreadsheet output where the previous run already cached).  This
        # keeps the function idempotent for tests and ad-hoc reruns.
        if url and not url.lower().startswith("http"):
            out.append(r)
            continue

        # Nothing to do for empty thumbnails or rows without an Event ID.
        if not eid or not url:
            out.append(r)
            continue

        existing = _existing_file_for(eid, images_dir)
        if existing and index.get(eid) == url:
            # Cache hit — same source URL as last time, file already on disk.
            out.append(
                r.model_copy(
                    update={"thumbnail_url": _json_path_for(eid, existing.suffix)}
                )
            )
            continue

        # Cache miss (new event) or invalidated (source URL changed).
        result = _download(url)
        if result is None:
            # Drop the broken URL so Angular shows the placeholder instead of
            # a broken-image icon.  Also clear any stale on-disk file + index
            # entry so we don't keep the old (possibly wrong) bytes around.
            if existing:
                _delete_existing_for(eid, images_dir)
            index.pop(eid, None)
            out.append(r.model_copy(update={"thumbnail_url": None}))
            continue

        data, ext = result
        # Remove the previous extension variant in case a re-fetch returns a
        # different format (e.g. the upstream now serves WebP where they used
        # to serve JPEG).  Otherwise we'd accumulate one file per format.
        _delete_existing_for(eid, images_dir)

        dest = images_dir / f"{eid}{ext}"
        try:
            tmp = dest.with_suffix(dest.suffix + ".tmp")
            tmp.write_bytes(data)
            tmp.replace(dest)
        except OSError as exc:
            # Disk full / permission issue — degrade to placeholder rather
            # than crashing the entire pipeline run.
            logger.warning("image cache: could not write poster for %s: %s", eid, exc)
            out.append(r.model_copy(update={"thumbnail_url": None}))
            continue

        index[eid] = url
        out.append(
            r.model_copy(update={"thumbnail_url": _json_path_for(eid, ext)})
        )

    _save_index(images_dir, index)
    return out


def garbage_collect(
    active_event_ids: Iterable[str], *, output_dir: Path
) -> int:
    """Delete cached poster files whose Event ID is no longer active.

    Run after the spreadsheet merge so expired (past-event) rows don't leave
    orphan bytes behind.  The sidecar index is pruned to match.

    Args:
        active_event_ids: Event IDs currently in the spreadsheet.
        output_dir: Usually ``config.OUTPUT_DIR``.

    Returns:
        The number of files removed.  ``0`` when the cache folder doesn't
        exist yet (first run).
    """
    images_dir = output_dir / IMAGES_SUBDIR
    if not images_dir.exists():
        return 0

    active = {eid for eid in active_event_ids if eid}
    index = _load_index(images_dir)

    removed = 0
    for path in images_dir.iterdir():
        if not path.is_file():
            continue
        if path.name == INDEX_FILENAME:
            continue
        # Files are named <event-id><ext> — the stem is the Event ID.
        if path.stem in active:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:
            logger.debug("image cache GC: could not delete %s: %s", path, exc)

    # Always prune the index in lock-step with the active set, even if no
    # files were removed (the index can drift out of sync if files were
    # deleted manually between runs).
    new_index = {eid: url for eid, url in index.items() if eid in active}
    if new_index != index:
        _save_index(images_dir, new_index)

    return removed
