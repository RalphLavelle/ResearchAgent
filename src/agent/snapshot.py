"""Persist fingerprint and last resources for idempotent Markdown rewrites."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent.models import Resource, resource_to_dict


def _canonical_fingerprint(resources: list[Resource]) -> str:
    """Stable hash from sorted URLs + titles."""
    pairs = sorted(
        (r.url.strip().lower(), r.title.strip().lower()) for r in resources if r.url
    )
    payload = json.dumps(pairs, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_snapshot(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def save_snapshot(
    path: Path,
    fingerprint: str,
    resources: list[Resource],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        "fingerprint": fingerprint,
        "saved_at_utc": datetime.now(timezone.utc).isoformat(),
        "resources": [resource_to_dict(r) for r in resources],
    }
    path.write_text(json.dumps(data, indent=2, ensure_ascii=True), encoding="utf-8")


def fingerprint_changed(resources: list[Resource], path: Path) -> tuple[str, bool]:
    """Return (new_fingerprint, unchanged_vs_disk)."""
    fp = _canonical_fingerprint(resources)
    prev = load_snapshot(path)
    if not prev:
        return fp, False
    return fp, prev.get("fingerprint") == fp
