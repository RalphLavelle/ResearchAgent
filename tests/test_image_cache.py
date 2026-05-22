"""Tests for the local poster cache (Task 14).

We never hit the real network here — the single ``_download`` helper is
monkeypatched to return controlled ``(bytes, ext)`` tuples (or ``None`` for
failure cases).  This mirrors the pattern in ``tests/test_enrich.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import image_cache
from agent.image_cache import (
    INDEX_FILENAME,
    IMAGES_SUBDIR,
    cache_thumbnails,
    garbage_collect,
)
from agent.models import Resource


# ── Fixtures / helpers ────────────────────────────────────────────────────────


def _resource(eid: str, thumb: str | None) -> Resource:
    """Build a Resource with just the fields cache_thumbnails reads."""
    return Resource(
        id=eid,
        title=f"Act {eid} @ Venue, City",
        url=f"https://example.com/event/{eid}",
        date="2099-12-31",
        thumbnail_url=thumb,
    )


def _index(images_dir: Path) -> dict:
    """Read the sidecar index from disk; empty dict if not yet written."""
    p = images_dir / INDEX_FILENAME
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


# ── Successful download path ──────────────────────────────────────────────────


def test_cache_thumbnails_writes_local_file_and_rewrites_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Successful 200 + image/jpeg → file on disk, JSON URL = data/images/<id>.jpg."""
    fake_bytes = b"\xff\xd8\xff\xe0fakejpeg"
    monkeypatch.setattr(image_cache, "_download", lambda url: (fake_bytes, ".jpg"))

    r = _resource("evt-1", "https://upstream.example/poster.jpg")
    out = cache_thumbnails([r], output_dir=tmp_path)

    assert len(out) == 1
    assert out[0].thumbnail_url == "data/images/evt-1.jpg"
    written = tmp_path / IMAGES_SUBDIR / "evt-1.jpg"
    assert written.exists()
    assert written.read_bytes() == fake_bytes

    # Sidecar index records the source URL so re-runs can detect changes.
    assert _index(tmp_path / IMAGES_SUBDIR) == {
        "evt-1": "https://upstream.example/poster.jpg"
    }


def test_cache_thumbnails_supports_multiple_image_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each Content-Type maps to the right extension on disk."""
    formats = {
        "evt-jpg": ".jpg",
        "evt-png": ".png",
        "evt-webp": ".webp",
        "evt-gif": ".gif",
    }
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda url: (b"bytes-for-" + url.encode(), formats[url.split("/")[-1]]),
    )

    resources = [
        _resource(eid, f"https://upstream.example/{eid}") for eid in formats
    ]
    out = cache_thumbnails(resources, output_dir=tmp_path)

    for r, ext in zip(out, formats.values()):
        assert r.thumbnail_url == f"data/images/{r.id}{ext}"
        assert (tmp_path / IMAGES_SUBDIR / f"{r.id}{ext}").exists()


# ── Failure paths ─────────────────────────────────────────────────────────────


def test_cache_thumbnails_sets_none_on_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Failed download → thumbnail_url is None, no file written, no index entry."""
    monkeypatch.setattr(image_cache, "_download", lambda url: None)

    r = _resource("evt-fail", "https://upstream.example/missing.jpg")
    out = cache_thumbnails([r], output_dir=tmp_path)

    assert out[0].thumbnail_url is None
    images_dir = tmp_path / IMAGES_SUBDIR
    assert not list(p for p in images_dir.iterdir() if p.name != INDEX_FILENAME) \
        or images_dir.exists() and not (images_dir / "evt-fail.jpg").exists()
    assert _index(images_dir) == {}


def test_cache_thumbnails_clears_stale_file_on_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A previously-cached file gets deleted when the URL changes and re-fetch fails.

    This is the safety net for the failure-policy decision (option 2 in the
    task answer): we never serve stale-and-possibly-wrong bytes.
    """
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    stale = images_dir / "evt-x.jpg"
    stale.write_bytes(b"old bytes")
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps({"evt-x": "https://upstream.example/old.jpg"}),
        encoding="utf-8",
    )

    monkeypatch.setattr(image_cache, "_download", lambda url: None)

    r = _resource("evt-x", "https://upstream.example/new-and-broken.jpg")
    out = cache_thumbnails([r], output_dir=tmp_path)

    assert out[0].thumbnail_url is None
    assert not stale.exists()
    assert _index(images_dir) == {}


# ── Pass-through cases ────────────────────────────────────────────────────────


def test_cache_thumbnails_skips_resources_without_thumbnail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``thumbnail_url=None`` and empty strings are left untouched, no HTTP call."""
    calls: list[str] = []
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda url: calls.append(url) or (b"x", ".jpg"),
    )

    r1 = _resource("evt-none", None)
    r2 = Resource(
        id="evt-empty", title="A @ V", url="https://x", date="", thumbnail_url=""
    )
    out = cache_thumbnails([r1, r2], output_dir=tmp_path)

    assert out[0].thumbnail_url is None
    assert out[1].thumbnail_url == ""  # Resource preserves the empty string
    assert calls == []  # download never called for blank thumbnails


def test_cache_thumbnails_passes_through_local_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Already-local paths (e.g. from a previous run) survive a second pass."""
    calls: list[str] = []
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda url: calls.append(url) or (b"x", ".jpg"),
    )

    r = _resource("evt-local", "data/images/evt-local.jpg")
    out = cache_thumbnails([r], output_dir=tmp_path)

    assert out[0].thumbnail_url == "data/images/evt-local.jpg"
    assert calls == []  # local paths never trigger a download


# ── Idempotency / cache hits ──────────────────────────────────────────────────


def test_cache_thumbnails_is_idempotent_when_url_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second call with the same URL must NOT re-download — cache hit."""
    calls: list[str] = []

    def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return (b"jpeg", ".jpg")

    monkeypatch.setattr(image_cache, "_download", fake_download)

    r = _resource("evt-cache", "https://upstream.example/poster.jpg")
    cache_thumbnails([r], output_dir=tmp_path)
    cache_thumbnails([r], output_dir=tmp_path)

    assert len(calls) == 1  # second call hit the cache


def test_cache_thumbnails_refetches_when_source_url_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When ``_maybe_upgrade_poster`` swaps in a fresher URL, we redownload."""
    calls: list[str] = []

    def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        # Different bytes per URL so we can verify the file actually got rewritten.
        return (url.encode(), ".jpg")

    monkeypatch.setattr(image_cache, "_download", fake_download)

    r1 = _resource("evt-upgrade", "https://upstream.example/old.jpg")
    cache_thumbnails([r1], output_dir=tmp_path)

    r2 = _resource("evt-upgrade", "https://upstream.example/new-better.jpg")
    out = cache_thumbnails([r2], output_dir=tmp_path)

    assert calls == [
        "https://upstream.example/old.jpg",
        "https://upstream.example/new-better.jpg",
    ]
    written = tmp_path / IMAGES_SUBDIR / "evt-upgrade.jpg"
    assert written.read_bytes() == b"https://upstream.example/new-better.jpg"
    assert out[0].thumbnail_url == "data/images/evt-upgrade.jpg"


def test_cache_thumbnails_replaces_extension_on_format_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If the format changes (jpg → webp), the old file is removed."""
    calls: list[str] = []

    def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        ext = ".webp" if "v2" in url else ".jpg"
        return (b"bytes-" + ext.encode(), ext)

    monkeypatch.setattr(image_cache, "_download", fake_download)

    r = _resource("evt-fmt", "https://upstream.example/v1.jpg")
    cache_thumbnails([r], output_dir=tmp_path)

    r2 = _resource("evt-fmt", "https://upstream.example/v2.webp")
    cache_thumbnails([r2], output_dir=tmp_path)

    images_dir = tmp_path / IMAGES_SUBDIR
    assert not (images_dir / "evt-fmt.jpg").exists()
    assert (images_dir / "evt-fmt.webp").exists()


# ── Garbage collection ────────────────────────────────────────────────────────


def test_garbage_collect_deletes_stale_files(tmp_path: Path) -> None:
    """Files whose Event ID isn't active any more are removed; active ones stay."""
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    (images_dir / "evt-active.jpg").write_bytes(b"keep")
    (images_dir / "evt-stale.png").write_bytes(b"drop")
    (images_dir / "evt-other.webp").write_bytes(b"drop")
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps(
            {
                "evt-active": "https://x/a",
                "evt-stale": "https://x/s",
                "evt-other": "https://x/o",
            }
        ),
        encoding="utf-8",
    )

    removed = garbage_collect({"evt-active"}, output_dir=tmp_path)

    assert removed == 2
    assert (images_dir / "evt-active.jpg").exists()
    assert not (images_dir / "evt-stale.png").exists()
    assert not (images_dir / "evt-other.webp").exists()
    # Index pruned in lock-step.
    assert _index(images_dir) == {"evt-active": "https://x/a"}


def test_garbage_collect_no_op_when_cache_dir_missing(tmp_path: Path) -> None:
    """First run: nothing exists yet — GC must be a safe no-op."""
    assert garbage_collect({"evt-1"}, output_dir=tmp_path) == 0


def test_garbage_collect_preserves_index_file(tmp_path: Path) -> None:
    """``_index.json`` itself must not be deleted by the GC sweep."""
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps({"evt-1": "https://x/a"}), encoding="utf-8"
    )
    (images_dir / "evt-1.jpg").write_bytes(b"keep")

    garbage_collect({"evt-1"}, output_dir=tmp_path)

    assert (images_dir / INDEX_FILENAME).exists()
    assert (images_dir / "evt-1.jpg").exists()
