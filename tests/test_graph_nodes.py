"""Unit tests for graph nodes (no network)."""

from pathlib import Path

import pytest

from agent.graph_nodes import node_fingerprint
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


def test_resource_roundtrip_dict() -> None:
    r = Resource(
        title="T",
        url="https://example.com",
        resource_type="course",
        price="$10",
        summary="S",
        participatory=True,
        thumbnail_url="https://example.com/t.png",
    )
    d = r.model_dump()
    from agent.models import resource_from_dict

    r2 = resource_from_dict(d)
    assert r2.title == "T"
    assert r2.resource_type == "course"
