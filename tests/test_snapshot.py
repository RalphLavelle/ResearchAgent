"""Tests for snapshot fingerprinting."""

from pathlib import Path

from agent.models import Resource
from agent.snapshot import fingerprint_changed, save_snapshot


def test_fingerprint_unchanged_when_same_resources(tmp_path: Path) -> None:
    p = tmp_path / "snap.json"
    r = [
        Resource(
            title="A",
            url="https://a.com/x",
            resource_type="website",
            price="Free",
            summary="s",
        )
    ]
    fp1, ch1 = fingerprint_changed(r, p)
    assert ch1 is False
    save_snapshot(p, fp1, r)
    fp2, ch2 = fingerprint_changed(r, p)
    assert fp1 == fp2
    assert ch2 is True


def test_fingerprint_changes_when_url_changes(tmp_path: Path) -> None:
    p = tmp_path / "snap.json"
    r1 = [
        Resource(
            title="A",
            url="https://a.com/1",
            resource_type="website",
        )
    ]
    r2 = [
        Resource(
            title="A",
            url="https://a.com/2",
            resource_type="website",
        )
    ]
    fp, _ = fingerprint_changed(r1, p)
    save_snapshot(p, fp, r1)
    _, same_as_before = fingerprint_changed(r2, p)
    assert same_as_before is False
