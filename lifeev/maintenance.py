"""Explicit maintenance operations for pruning and snapshot recovery."""
from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tempfile

from cryptography.fernet import Fernet, InvalidToken

from .storage import Store


def candidates(store, pins=None) -> list[dict]:
    """Return nodes ordered from lowest to highest usefulness; never chooses for caller."""
    pinned = set(pins or ())
    nodes = store.list_nodes(include_archived=True, summary=True)
    children: dict[str, int] = {}
    for node in nodes:
        parent = node.get("parent_id")
        if parent:
            children[parent] = children.get(parent, 0) + 1
    now = datetime.now(timezone.utc)
    result = []
    for node in nodes:
        identifier = node["id"]
        try:
            created = datetime.fromisoformat(node["created_at"])
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - created).total_seconds() / 86400)
        except (KeyError, TypeError, ValueError):
            age_days = 36500.0
        # Lower is less useful: older, fewer revisions, no children, no explicit pin.
        child_count = children.get(identifier, 0)
        is_pinned = identifier in pinned
        score = (1.0 / (1.0 + age_days / 30.0)) + 0.25 * max(0, node.get("version", 1) - 1)
        score += 2.0 * child_count + (1000.0 if is_pinned else 0.0)
        reasons = ["older" if age_days > 30 else "recent", f"version {node.get('version', 1)}"]
        reasons.append(f"{child_count} child" + ("" if child_count == 1 else "ren"))
        if is_pinned:
            reasons.append("pinned")
        result.append({"id": identifier, "score": round(score, 6), "reason": "; ".join(reasons)})
    return sorted(result, key=lambda item: (item["score"], item["id"]))


def prune(store, ids, pins=None):
    """Archive only explicitly selected IDs, taking a pre-prune snapshot first."""
    selected = list(ids)
    if len(selected) != len(set(selected)):
        raise ValueError("Duplicate node ID")
    pinned = set(pins or ())
    if any(identifier in pinned for identifier in selected):
        raise ValueError("Cannot prune a pinned node")
    # Validate all selections before making any mutation.
    for identifier in selected:
        store.get(identifier)
    return [store.archive(identifier) for identifier in selected]


def restore_snapshot(snapshot_path, destination, key):
    """Decrypt and validate a Store snapshot into a new destination, never overwriting."""
    snapshot_path, destination = Path(snapshot_path), Path(destination)
    if destination.exists():
        raise FileExistsError(destination)
    try:
        raw = Fernet(key).decrypt(snapshot_path.read_bytes())
    except (InvalidToken, ValueError, TypeError) as exc:
        raise ValueError("Unable to decrypt snapshot with supplied key") from exc

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
        check_path = Path(temporary) / "verify.sqlite"
        check_path.write_bytes(raw)
        try:
            db = sqlite3.connect(str(check_path))
            try:
                if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Snapshot database is corrupt")
            finally:
                db.close()
            Store(check_path, key)  # Also validates the encrypted Store metadata/key.
        except (sqlite3.DatabaseError, ValueError) as exc:
            raise ValueError("Snapshot is not a valid Store database") from exc

    # Exclusive create prevents races from replacing an existing destination.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return Store(destination, key)
