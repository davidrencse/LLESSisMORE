"""Encrypted local persistence. Keep the Fernet key outside the database directory."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import hashlib
import math
from pathlib import Path
import sqlite3
import tempfile
import threading
import uuid

from cryptography.fernet import Fernet, InvalidToken


def _now():
    return datetime.now(timezone.utc)


class VersionConflict(ValueError):
    """The editor's source version is no longer current."""


class SnapshotCapacityError(ValueError):
    """A backup would exceed its file-count or storage budget."""


class SnapshotThrottled(ValueError):
    def __init__(self, retry_after):
        self.retry_after = max(1, math.ceil(retry_after))
        super().__init__(f'Manual snapshot cooldown: retry in {self.retry_after} seconds')


class Store:
    def __init__(self, path, key, *, snapshot_max_files=256,
                 snapshot_max_bytes=512 * 1024 * 1024,
                 snapshot_max_file_bytes=64 * 1024 * 1024):
        for limit in (snapshot_max_files, snapshot_max_bytes, snapshot_max_file_bytes):
            if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
                raise ValueError('Snapshot limits must be positive integers')
        self.snapshot_max_files = snapshot_max_files
        self.snapshot_max_bytes = snapshot_max_bytes
        self.snapshot_max_file_bytes = snapshot_max_file_bytes
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cipher = Fernet(key)
        self._lock = threading.RLock()
        with self._connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value BLOB NOT NULL);
                CREATE TABLE IF NOT EXISTS nodes (id TEXT, version INTEGER, payload BLOB NOT NULL,
                    PRIMARY KEY(id, version));
                CREATE TABLE IF NOT EXISTS objects (kind TEXT, id TEXT, payload BLOB NOT NULL,
                    PRIMARY KEY(kind, id));
                CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY, payload BLOB NOT NULL);
            """)
            check = db.execute("SELECT value FROM metadata WHERE key='check'").fetchone()
            if check:
                if self._decode(check[0]) != {"format": 1}:
                    raise ValueError("Invalid database format")
            else:
                db.execute("INSERT INTO metadata VALUES ('check', ?)", (self._encode({"format": 1}),))

    @contextmanager
    def _connection(self):
        db = sqlite3.connect(str(self.path), timeout=30)
        try:
            with db:
                yield db
        finally:
            db.close()

    def _encode(self, value):
        try:
            return self._cipher.encrypt(json.dumps(value, allow_nan=False).encode())
        except (TypeError, ValueError) as exc:
            raise ValueError("Payload must be finite JSON data") from exc

    def _seal(self, value):
        """Encrypt value; also return its JSON-normalised copy so callers need not decrypt it again."""
        try:
            text = json.dumps(value, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ValueError("Payload must be finite JSON data") from exc
        return self._cipher.encrypt(text.encode()), json.loads(text)

    def _decode(self, value):
        try:
            return json.loads(self._cipher.decrypt(value))
        except (InvalidToken, ValueError, TypeError) as exc:
            raise ValueError("Unable to decrypt stored data with supplied key") from exc

    def _event(self, db, action, identifier=None, reason=None):
        db.execute("INSERT INTO events(payload) VALUES (?)", (self._encode({
            "action": action, "id": identifier, "created_at": _now().isoformat(),
            **({'reason': reason} if reason else {})}),))

    def _daily(self):
        # Scheduler and request threads must share the check-and-snapshot lock.
        with self._lock:
            self._daily_locked()

    def _daily_locked(self):
        with self._connection() as db:
            row = db.execute("SELECT value FROM metadata WHERE key='snapshot_day'").fetchone()
        day = _now().date().isoformat()
        if not row or self._decode(row[0]) != day:
            self.snapshot("daily")
            with self._connection() as db:
                db.execute("INSERT OR REPLACE INTO metadata VALUES ('snapshot_day', ?)", (self._encode(day),))

    def _get(self, db, identifier, version=None):
        if version is None:
            row = db.execute("SELECT payload FROM nodes WHERE id=? ORDER BY version DESC LIMIT 1", (identifier,)).fetchone()
        else:
            row = db.execute("SELECT payload FROM nodes WHERE id=? AND version=?", (identifier, version)).fetchone()
        if not row:
            raise KeyError(identifier)
        result = self._decode(row[0])
        archived = db.execute("SELECT payload FROM objects WHERE kind='archive' AND id=?", (identifier,)).fetchone()
        result['archived_at'] = self._decode(archived[0]) if archived else None
        return result

    def get(self, id, version=None):
        with self._connection() as db:
            return self._get(db, id, version)

    def create(self, document, parent_id=None, parent_version=None):
        if not isinstance(document, dict):
            raise ValueError("Document must be an object")
        if parent_version is not None and (isinstance(parent_version, bool) or not isinstance(parent_version, int) or parent_version < 1 or not parent_id):
            raise ValueError('parent_version requires a parent ID and positive integer version')
        with self._lock:
            self._daily()
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                parent = self._get(db, parent_id, parent_version) if parent_id else None
                if parent and parent['archived_at']:
                    raise ValueError("Parent is archived")
                if parent:
                    # Freeze the immediate context; ancestry remains version-addressable.
                    parent = {k: v for k, v in parent.items() if k != 'frozen_parent'}
                result = dict(id=str(uuid.uuid4()), version=1, parent_id=parent_id,
                              parent_version=parent['version'] if parent else None,
                              created_at=_now().isoformat(), document=document,
                              frozen_parent=parent, archived_at=None)
                from .stats import analyze, CALCULATION_VERSION
                result['statistics_snapshot'] = analyze(document)
                result['calculation_version'] = CALCULATION_VERSION
                token, copy = self._seal(result)
                db.execute('INSERT INTO nodes VALUES (?, ?, ?)', (result['id'], 1, token))
                self._event(db, 'create', result['id'])
                return copy

    def revise(self, id, document, source_version=None):
        if not isinstance(document, dict):
            raise ValueError("Document must be an object")
        with self._lock:
            self._daily()
            with self._connection() as db:
                db.execute('BEGIN IMMEDIATE')
                result = self._get(db, id)
                if source_version is not None:
                    if isinstance(source_version, bool) or not isinstance(source_version, int) or source_version < 1:
                        raise ValueError('source_version must be a positive integer')
                    if source_version != result['version']:
                        raise VersionConflict('Decision changed since this editor was opened; reload before saving')
                if result['archived_at']:
                    raise ValueError("Restore archived node before revising")
                result.update(version=result['version'] + 1, document=document, created_at=_now().isoformat())
                from .stats import analyze, CALCULATION_VERSION
                result['statistics_snapshot'] = analyze(document)
                result['calculation_version'] = CALCULATION_VERSION
                token, copy = self._seal(result)
                db.execute('INSERT INTO nodes VALUES (?, ?, ?)', (id, result['version'], token))
                self._event(db, 'revise', id)
                return copy

    def _latest_nodes(self, include_archived, skip=None):
        """(id, version, node) for each id's newest version, ordered by id; node is None when skip(id, version)."""
        query = ("SELECT n.id, n.version, n.payload, a.payload FROM nodes n "
                 "LEFT JOIN objects a ON a.kind='archive' AND a.id=n.id "
                 "WHERE n.version=(SELECT MAX(version) FROM nodes WHERE id=n.id) ORDER BY n.id")
        with self._connection() as db:
            for identifier, version, payload, archived in db.execute(query):
                archived_at = self._decode(archived) if archived else None
                if archived_at and not include_archived:
                    continue
                if skip is not None and skip(identifier, version):
                    yield identifier, version, None
                    continue
                node = self._decode(payload)
                node['archived_at'] = archived_at
                yield identifier, version, node

    def list_nodes(self, include_archived=False, summary=False):
        return [self._summary(node) if summary else node
                for _, _, node in self._latest_nodes(include_archived)]

    @staticmethod
    def _summary(record):
        """Picker metadata only; full documents remain available by ID."""
        return {**{key: record[key] for key in ('id', 'version', 'parent_id', 'parent_version',
                    'created_at', 'archived_at', 'status', 'round', 'round_cap') if key in record},
                'document': {'title': record.get('document', {}).get('title', '')}}

    def list_nodes_stamped(self, skip):
        """Active nodes as (id, version, node); a node already known (skip(id, version)) is not decrypted and is None."""
        return list(self.iter_nodes_stamped(skip))

    def iter_nodes_stamped(self, skip):
        """Stream current nodes without retaining the corpus's encrypted payloads."""
        return self._latest_nodes(False, skip)

    def list_objects_stamped(self, kind, skip):
        """(id, stamp, value) in insertion order; stamp changes whenever the stored payload does, and a value is
        decrypted only when skip(id, stamp) is false (else None)."""
        return list(self.iter_objects_stamped(kind, skip))

    def iter_objects_stamped(self, kind, skip):
        """Stream objects with fixed-size content digests instead of payload stamps."""
        with self._connection() as db:
            for identifier, payload in db.execute('SELECT id, payload FROM objects WHERE kind=? ORDER BY rowid', (kind,)):
                stamp = hashlib.sha256(payload).digest()
                yield identifier, stamp, None if skip(identifier, stamp) else self._decode(payload)

    def _save(self, kind, value):
        if not isinstance(value, dict):
            raise ValueError("Payload must be an object")
        result = dict(value)
        result.setdefault('id', str(uuid.uuid4()))
        if not isinstance(result['id'], str) or not result['id']:
            raise ValueError("ID must be a nonempty string")
        with self._lock:
            self._daily()
            with self._connection() as db:
                token, copy = self._seal(result)
                db.execute('INSERT OR REPLACE INTO objects VALUES (?, ?, ?)', (kind, result['id'], token))
                self._event(db, 'save_' + kind, result['id'])
        return copy

    def _list(self, kind, summary=False):
        with self._connection() as db:
            records = (self._decode(row[0]) for row in db.execute('SELECT payload FROM objects WHERE kind=? ORDER BY rowid', (kind,)))
            return [self._summary(record) if summary else record for record in records]

    def save_session(self, value):
        return self._save('session', value)

    def get_session(self, id):
        with self._connection() as db:
            row = db.execute("SELECT payload FROM objects WHERE kind='session' AND id=?", (id,)).fetchone()
            if not row:
                raise KeyError(id)
            return self._decode(row[0])

    def list_sessions(self, summary=False):
        return self._list('session', summary)

    def add_memory(self, value):
        return self._save('memory', value)

    def list_memory(self):
        return self._list('memory')

    def save_profile(self, value):
        return self._save('profile', value)

    def list_profiles(self):
        return self._list('profile')

    def snapshot(self, reason):
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError('Snapshot reason is required')
        with self._lock:
            manual = reason not in ('daily', 'preprune')
            with self._connection() as db:
                if manual:
                    last = db.execute("SELECT value FROM metadata WHERE key='manual_snapshot_at'").fetchone()
                    if last:
                        elapsed = (_now() - datetime.fromisoformat(self._decode(last[0]))).total_seconds()
                        if elapsed < 60:
                            raise SnapshotThrottled(60 - elapsed)
                raw_size = db.execute('PRAGMA page_count').fetchone()[0] * db.execute('PRAGMA page_size').fetchone()[0]
            directory = self.path.parent / (self.path.name + '.snapshots')
            # Fernet: padded AES ciphertext + 57-byte header/MAC, base64 encoded.
            def encrypted_size(size):
                return 4 * ((57 + 16 * (size // 16 + 1) + 2) // 3)
            current_files, current_bytes = 0, 0
            for existing in directory.glob('*.fernet'):
                current_files += 1
                current_bytes += existing.stat().st_size
                if current_files >= self.snapshot_max_files or current_bytes >= self.snapshot_max_bytes:
                    raise SnapshotCapacityError('Snapshot budget reached; move older backups to secure offline storage before retrying. No backups were deleted.')
            def check_size(size):
                if size > self.snapshot_max_file_bytes or current_bytes + size > self.snapshot_max_bytes:
                    raise SnapshotCapacityError('Snapshot exceeds storage budget; move older backups to secure offline storage or adjust Store limits. No backups were deleted.')
            check_size(encrypted_size(raw_size))
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / (str(uuid.uuid4()) + '.fernet')
            # SQLite backup yields a consistent image; the temporary image already has encrypted payloads.
            with tempfile.TemporaryDirectory() as temporary:
                backup_path = Path(temporary) / 'backup.sqlite'
                with self._connection() as db:
                    backup = sqlite3.connect(str(backup_path))
                    try:
                        db.backup(backup)
                    finally:
                        backup.close()
                # Recheck actual image size before loading/encrypting it into RAM.
                check_size(encrypted_size(backup_path.stat().st_size))
                target.write_bytes(self._cipher.encrypt(backup_path.read_bytes()))
            with self._connection() as db:
                self._event(db, 'snapshot', reason=reason if reason in ('daily', 'preprune') else 'manual')
                if manual:
                    db.execute("INSERT OR REPLACE INTO metadata VALUES ('manual_snapshot_at', ?)",
                               (self._encode(_now().isoformat()),))
            return str(target)

    def archive(self, id):
        with self._lock:
            # Invalid and repeated requests must be side-effect free, including backups.
            node = self.get(id)
            if node['archived_at']:
                return node
            self._daily()
            self.snapshot('preprune')
            with self._connection() as db:
                node = self._get(db, id)
                if not node['archived_at']:
                    db.execute("INSERT INTO objects VALUES ('archive', ?, ?)", (id, self._encode(_now().isoformat())))
                    self._event(db, 'archive', id)
                return self._get(db, id)

    def restore(self, id):
        with self._lock:
            self._daily()
            with self._connection() as db:
                node = self._get(db, id)
                if node['archived_at'] and _now() - datetime.fromisoformat(node['archived_at']) >= timedelta(days=7):
                    raise ValueError('Recovery window expired')
                db.execute("DELETE FROM objects WHERE kind='archive' AND id=?", (id,))
                self._event(db, 'restore', id)
                return self._get(db, id)

    def purge(self, id):
        with self._lock:
            node = self.get(id)
            if not node['archived_at'] or _now() - datetime.fromisoformat(node['archived_at']) < timedelta(days=7):
                raise ValueError('Node must be archived for at least seven days')
            self.snapshot('preprune')  # Any backup failure aborts the deletion.
            with self._connection() as db:
                db.execute('DELETE FROM nodes WHERE id=?', (id,))
                db.execute("DELETE FROM objects WHERE kind='archive' AND id=?", (id,))
                self._event(db, 'purge', id)

    def audit(self, action=None, limit=None):
        """Events oldest-first, optionally only `action` and only the newest `limit` matches (decrypts just those scanned)."""
        if limit is not None and limit < 1:
            raise ValueError('limit must be at least 1')
        matched = []
        with self._connection() as db:
            for row in db.execute('SELECT payload FROM events ORDER BY seq DESC'):
                event = self._decode(row[0])
                if action and event.get('action') != action:
                    continue
                matched.append(event)
                if limit is not None and len(matched) >= limit:
                    break
        matched.reverse()
        return matched
