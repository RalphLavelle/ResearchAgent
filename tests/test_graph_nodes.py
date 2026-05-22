"""Unit tests for graph nodes (no network)."""

from pathlib import Path

import pytest

from agent.graph_nodes import (
    CRAWL_SECTION_MARKER,
    _dedupe_curator_resources,
    _truncate_preserving_same_site_crawl,
    node_fingerprint,
)
from agent.models import Resource


def test_fingerprint_empty_stable_after_save(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("agent.config.SNAPSHOT_PATH", tmp_path / "snap.json")
    from agent.snapshot import save_snapshot
    from agent import config

    out = node_fingerprint({"resources": []})
    fp = out["fingerprint"]
    assert out["fingerprint_unchanged"] is False
    save_snapshot(config.SNAPSHOT_PATH, fp, [])
    out2 = node_fingerprint({"resources": []})
    assert out2["fingerprint_unchanged"] is True


def test_truncate_preserving_crawl_keeps_listing_block() -> None:
    head = "HEADER" * 20_000  # long search-like prefix
    tail = f"{CRAWL_SECTION_MARKER}\n" + ("crawl-line\n" * 5_000)
    blob = head + tail
    out = _truncate_preserving_same_site_crawl(blob, 50_000)
    assert CRAWL_SECTION_MARKER in out
    assert len(out) <= 50_000


def test_dedupe_allows_same_listing_url_multiple_gigs() -> None:
    """Many rows can share a calendar URL if act or date differs."""
    a = Resource(title="Buzz Lovers @ Club, Miami", url="https://ex.com/concerts", date="2026-05-10")
    b = Resource(title="Baggy Trousers @ Pub, Miami", url="https://ex.com/concerts", date="2026-05-11")
    c = Resource(title="Buzz Lovers @ Club, Miami", url="https://ex.com/concerts", date="2026-05-10")
    out = _dedupe_curator_resources([a, b, c])
    assert len(out) == 2


def test_resource_roundtrip_dict() -> None:
    r = Resource(
        title="T",
        url="https://example.com",
        summary="S",
        thumbnail_url="https://example.com/t.png",
    )
    d = r.model_dump()
    from agent.models import resource_from_dict

    r2 = resource_from_dict(d)
    assert r2.id == r.id
    assert r2.title == "T"
