"""Tests for the local poster cache (Task 14) and URL deduplication (Task 2)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent import image_cache
from agent.image_cache import (
    INDEX_FILENAME,
    IMAGES_SUBDIR,
    cache_thumbnails,
    dedupe_existing_images,
    file_name_for_source,
    garbage_collect,
)
from agent.models import Resource


def _resource(eid: str, thumb: str | None) -> Resource:
    return Resource(
        id=eid,
        title=f"Act {eid} @ Venue, City",
        url=f"https://example.com/event/{eid}",
        date="2099-12-31",
        thumbnail_url=thumb,
    )


def _index(images_dir: Path) -> dict:
    p = images_dir / INDEX_FILENAME
    if not p.exists():
        return {}
    return json.loads(p.read_text(encoding="utf-8"))


def _local_url(tmp_path: Path, filename: str) -> str:
    return f"/data/images/{filename}"


def test_cache_thumbnails_writes_hash_named_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://upstream.example/poster.jpg"
    fake_bytes = b"\xff\xd8\xff\xe0fakejpeg"
    monkeypatch.setattr(image_cache, "_download", lambda u: (fake_bytes, ".jpg"))

    out = cache_thumbnails([_resource("evt-1", url)], output_dir=tmp_path)

    fname = file_name_for_source(url, ".jpg")
    assert out[0].thumbnail_url == _local_url(tmp_path, fname)
    written = tmp_path / IMAGES_SUBDIR / fname
    assert written.exists()
    assert written.read_bytes() == fake_bytes

    idx = _index(tmp_path / IMAGES_SUBDIR)
    assert idx["version"] == 2
    assert idx["events"]["evt-1"]["source"] == url
    assert idx["events"]["evt-1"]["file"] == fname


def test_cache_thumbnails_reuses_file_for_same_source_url(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://upstream.example/shared.webp"
    calls: list[str] = []
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda u: calls.append(u) or (b"webp-bytes", ".webp"),
    )

    r1 = _resource("evt-a", url)
    r2 = _resource("evt-b", url)
    out = cache_thumbnails([r1, r2], output_dir=tmp_path)

    assert len(calls) == 1
    fname = file_name_for_source(url, ".webp")
    assert out[0].thumbnail_url == _local_url(tmp_path, fname)
    assert out[1].thumbnail_url == out[0].thumbnail_url
    assert len(list((tmp_path / IMAGES_SUBDIR).glob("*.webp"))) == 1


def test_cache_thumbnails_supports_multiple_image_formats(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    formats = {
        "evt-jpg": ".jpg",
        "evt-png": ".png",
        "evt-webp": ".webp",
        "evt-gif": ".gif",
    }
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda url: (b"bytes", formats[url.split("/")[-1]]),
    )

    resources = [
        _resource(eid, f"https://upstream.example/{eid}") for eid in formats
    ]
    out = cache_thumbnails(resources, output_dir=tmp_path)

    for r, ext in zip(out, formats.values()):
        url = f"https://upstream.example/{r.id}"
        fname = file_name_for_source(url, ext)
        assert r.thumbnail_url == _local_url(tmp_path, fname)
        assert (tmp_path / IMAGES_SUBDIR / fname).exists()


def test_cache_thumbnails_sets_none_on_download_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(image_cache, "_download", lambda url: None)

    out = cache_thumbnails(
        [_resource("evt-fail", "https://upstream.example/missing.jpg")],
        output_dir=tmp_path,
    )

    assert out[0].thumbnail_url is None
    idx = _index(tmp_path / IMAGES_SUBDIR)
    assert idx.get("events") == {}


def test_cache_thumbnails_keeps_shared_file_when_one_event_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    good_url = "https://upstream.example/good.jpg"
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    fname = file_name_for_source(good_url, ".jpg")
    (images_dir / fname).write_bytes(b"shared")

    def fake_download(url: str) -> tuple[bytes, str] | None:
        if url == good_url:
            return (b"shared", ".jpg")
        return None

    monkeypatch.setattr(image_cache, "_download", fake_download)

    out = cache_thumbnails(
        [
            _resource("evt-ok", good_url),
            _resource("evt-bad", "https://upstream.example/broken.jpg"),
        ],
        output_dir=tmp_path,
    )

    assert out[0].thumbnail_url == _local_url(tmp_path, fname)
    assert out[1].thumbnail_url is None
    assert (images_dir / fname).exists()


def test_cache_thumbnails_skips_resources_without_thumbnail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda url: calls.append(url) or (b"x", ".jpg"),
    )

    out = cache_thumbnails(
        [
            _resource("evt-none", None),
            Resource(
                id="evt-empty",
                title="A @ V",
                url="https://x",
                date="",
                thumbnail_url="",
            ),
        ],
        output_dir=tmp_path,
    )

    assert out[0].thumbnail_url is None
    assert out[1].thumbnail_url == ""
    assert calls == []


def test_cache_thumbnails_passes_through_local_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        image_cache,
        "_download",
        lambda url: calls.append(url) or (b"x", ".jpg"),
    )

    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    fname = "abc123.jpg"
    (images_dir / fname).write_bytes(b"x")
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps(
            {
                "version": 2,
                "events": {
                    "evt-local": {
                        "source": "https://upstream.example/p.jpg",
                        "file": fname,
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    out = cache_thumbnails(
        [_resource("evt-local", "data/images/evt-local.jpg")],
        output_dir=tmp_path,
    )

    assert out[0].thumbnail_url == _local_url(tmp_path, fname)
    assert calls == []


def test_cache_thumbnails_is_idempotent_when_url_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    url = "https://upstream.example/poster.jpg"

    def fake_download(u: str) -> tuple[bytes, str]:
        calls.append(u)
        return (b"jpeg", ".jpg")

    monkeypatch.setattr(image_cache, "_download", fake_download)

    r = _resource("evt-cache", url)
    cache_thumbnails([r], output_dir=tmp_path)
    cache_thumbnails([r], output_dir=tmp_path)

    assert len(calls) == 1


def test_cache_thumbnails_refetches_when_source_url_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def fake_download(url: str) -> tuple[bytes, str]:
        calls.append(url)
        return (url.encode(), ".jpg")

    monkeypatch.setattr(image_cache, "_download", fake_download)

    old = "https://upstream.example/old.jpg"
    new = "https://upstream.example/new-better.jpg"
    cache_thumbnails([_resource("evt-upgrade", old)], output_dir=tmp_path)
    out = cache_thumbnails([_resource("evt-upgrade", new)], output_dir=tmp_path)

    assert calls == [old, new]
    fname = file_name_for_source(new, ".jpg")
    assert (tmp_path / IMAGES_SUBDIR / fname).read_bytes() == new.encode()
    assert out[0].thumbnail_url == _local_url(tmp_path, fname)


def test_dedupe_existing_images_merges_identical_bytes(
    tmp_path: Path,
) -> None:
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    shared = b"same-image-bytes"
    (images_dir / "alan-stivell-2026-05-26.webp").write_bytes(shared)
    (images_dir / "daft-punk-experience-2026-05-31.webp").write_bytes(shared)
    (images_dir / "unique-event.jpg").write_bytes(b"other")

    url = "https://cdn.example/category_banner.webp"
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps(
            {
                "alan-stivell-2026-05-26": url,
                "daft-punk-experience-2026-05-31": url,
                "unique-event": "https://cdn.example/unique.jpg",
            }
        ),
        encoding="utf-8",
    )

    stats = dedupe_existing_images(output_dir=tmp_path)

    assert stats.files_removed >= 1
    remaining = [p.name for p in images_dir.iterdir() if p.suffix == ".webp"]
    assert len(remaining) == 1
    target = file_name_for_source(url, ".webp")
    assert remaining[0] == target

    idx = _index(images_dir)
    assert idx["events"]["alan-stivell-2026-05-26"]["file"] == target
    assert idx["events"]["daft-punk-experience-2026-05-31"]["file"] == target


def test_garbage_collect_deletes_unreferenced_files(tmp_path: Path) -> None:
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    (images_dir / "keep.webp").write_bytes(b"keep")
    (images_dir / "drop.webp").write_bytes(b"drop")
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps(
            {
                "version": 2,
                "events": {
                    "evt-active": {
                        "source": "https://x/a",
                        "file": "keep.webp",
                    },
                    "evt-stale": {
                        "source": "https://x/s",
                        "file": "drop.webp",
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    removed = garbage_collect({"evt-active"}, output_dir=tmp_path)

    assert removed == 1
    assert (images_dir / "keep.webp").exists()
    assert not (images_dir / "drop.webp").exists()
    assert _index(images_dir)["events"] == {
        "evt-active": {"source": "https://x/a", "file": "keep.webp"}
    }


def test_garbage_collect_keeps_shared_file_while_other_events_expire(
    tmp_path: Path,
) -> None:
    images_dir = tmp_path / IMAGES_SUBDIR
    images_dir.mkdir(parents=True)
    (images_dir / "shared.webp").write_bytes(b"shared")
    url = "https://x/shared.webp"
    (images_dir / INDEX_FILENAME).write_text(
        json.dumps(
            {
                "version": 2,
                "events": {
                    "evt-a": {"source": url, "file": "shared.webp"},
                    "evt-b": {"source": url, "file": "shared.webp"},
                },
            }
        ),
        encoding="utf-8",
    )

    removed = garbage_collect({"evt-a"}, output_dir=tmp_path)

    assert removed == 0
    assert (images_dir / "shared.webp").exists()


def test_garbage_collect_no_op_when_cache_dir_missing(tmp_path: Path) -> None:
    assert garbage_collect({"evt-1"}, output_dir=tmp_path) == 0
