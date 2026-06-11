from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import EmojiRecord, normalize_tags, utcnow_iso


class EmojiRepository:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS emojis (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    file_hash TEXT NOT NULL UNIQUE,
                    file_name TEXT NOT NULL,
                    path TEXT NOT NULL,
                    format TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    emotion_tags_json TEXT NOT NULL DEFAULT '[]',
                    query_count INTEGER NOT NULL DEFAULT 0,
                    usage_count INTEGER NOT NULL DEFAULT 0,
                    is_registered INTEGER NOT NULL DEFAULT 0,
                    is_banned INTEGER NOT NULL DEFAULT 0,
                    source_platform TEXT NOT NULL DEFAULT '',
                    source_session TEXT NOT NULL DEFAULT '',
                    source_message_id TEXT NOT NULL DEFAULT '',
                    record_time TEXT NOT NULL,
                    register_time TEXT,
                    last_used_time TEXT
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emojis_registered ON emojis(is_registered, is_banned)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_emojis_hash ON emojis(file_hash)")

    def _row_to_record(self, row: sqlite3.Row) -> EmojiRecord:
        tags_raw = row["emotion_tags_json"] or "[]"
        try:
            tags = json.loads(tags_raw)
        except json.JSONDecodeError:
            tags = []
        return EmojiRecord(
            id=row["id"],
            file_hash=row["file_hash"],
            file_name=row["file_name"],
            path=row["path"],
            format=row["format"],
            description=row["description"] or "",
            emotion_tags=normalize_tags(tags),
            query_count=int(row["query_count"] or 0),
            usage_count=int(row["usage_count"] or 0),
            is_registered=bool(row["is_registered"]),
            is_banned=bool(row["is_banned"]),
            source_platform=row["source_platform"] or "",
            source_session=row["source_session"] or "",
            source_message_id=row["source_message_id"] or "",
            record_time=row["record_time"] or "",
            register_time=row["register_time"],
            last_used_time=row["last_used_time"],
        )

    def upsert(self, record: EmojiRecord) -> EmojiRecord:
        tags_json = json.dumps(normalize_tags(record.emotion_tags or []), ensure_ascii=False)
        now = record.record_time or utcnow_iso()
        register_time = record.register_time
        if record.is_registered and not register_time:
            register_time = utcnow_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO emojis (
                    file_hash, file_name, path, format, description, emotion_tags_json,
                    query_count, usage_count, is_registered, is_banned, source_platform,
                    source_session, source_message_id, record_time, register_time,
                    last_used_time
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(file_hash) DO UPDATE SET
                    file_name=excluded.file_name,
                    path=excluded.path,
                    format=excluded.format,
                    description=excluded.description,
                    emotion_tags_json=excluded.emotion_tags_json,
                    is_registered=excluded.is_registered,
                    is_banned=excluded.is_banned,
                    source_platform=excluded.source_platform,
                    source_session=excluded.source_session,
                    source_message_id=excluded.source_message_id,
                    register_time=COALESCE(emojis.register_time, excluded.register_time)
                """,
                (
                    record.file_hash,
                    record.file_name,
                    record.path,
                    record.format,
                    record.description,
                    tags_json,
                    record.query_count,
                    record.usage_count,
                    int(record.is_registered),
                    int(record.is_banned),
                    record.source_platform,
                    record.source_session,
                    record.source_message_id,
                    now,
                    register_time,
                    record.last_used_time,
                ),
            )
        found = self.get_by_hash(record.file_hash)
        if not found:
            raise RuntimeError("failed to reload upserted emoji")
        return found

    def get_by_hash(self, file_hash: str) -> EmojiRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM emojis WHERE file_hash = ?", (file_hash,)).fetchone()
        return self._row_to_record(row) if row else None

    def get_by_id(self, emoji_id: int) -> EmojiRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM emojis WHERE id = ?", (emoji_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def list(
        self,
        *,
        status: str = "",
        query: str = "",
        page: int = 1,
        page_size: int = 40,
    ) -> tuple[list[EmojiRecord], int]:
        clauses: list[str] = []
        params: list[object] = []
        if status == "adopted":
            clauses.append("is_registered = 1 AND is_banned = 0")
        elif status == "discarded":
            clauses.append("is_banned = 1")
        elif status == "known":
            clauses.append("is_registered = 0 AND is_banned = 0 AND TRIM(description) <> ''")
        elif status == "unknown":
            clauses.append("is_registered = 0 AND is_banned = 0 AND TRIM(description) = ''")
        if query:
            clauses.append("(description LIKE ? OR emotion_tags_json LIKE ? OR file_hash LIKE ?)")
            q = f"%{query}%"
            params.extend([q, q, q])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        page = max(page, 1)
        page_size = min(max(page_size, 1), 200)
        offset = (page - 1) * page_size
        with self._connect() as conn:
            total = int(conn.execute(f"SELECT COUNT(*) FROM emojis {where}", params).fetchone()[0])
            rows = conn.execute(
                f"SELECT * FROM emojis {where} ORDER BY id DESC LIMIT ? OFFSET ?",
                [*params, page_size, offset],
            ).fetchall()
        return [self._row_to_record(row) for row in rows], total

    def registered(self) -> list[EmojiRecord]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM emojis WHERE is_registered = 1 AND is_banned = 0 ORDER BY usage_count ASC, id DESC"
            ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def count_registered(self) -> int:
        with self._connect() as conn:
            return int(
                conn.execute(
                    "SELECT COUNT(*) FROM emojis WHERE is_registered = 1 AND is_banned = 0"
                ).fetchone()[0]
            )

    def emotions(self) -> list[str]:
        tags: list[str] = []
        for record in self.registered():
            tags.extend(record.emotion_tags or [])
        return normalize_tags(tags)

    def update(
        self,
        emoji_id: int,
        *,
        description: str | None = None,
        emotion_tags: list[str] | None = None,
        is_registered: bool | None = None,
        is_banned: bool | None = None,
    ) -> EmojiRecord | None:
        fields: list[str] = []
        params: list[object] = []
        if description is not None:
            fields.append("description = ?")
            params.append(description)
        if emotion_tags is not None:
            fields.append("emotion_tags_json = ?")
            params.append(json.dumps(normalize_tags(emotion_tags), ensure_ascii=False))
        if is_registered is not None:
            fields.append("is_registered = ?")
            params.append(int(is_registered))
            if is_registered:
                fields.append("register_time = COALESCE(register_time, ?)")
                params.append(utcnow_iso())
        if is_banned is not None:
            fields.append("is_banned = ?")
            params.append(int(is_banned))
        if not fields:
            return self.get_by_id(emoji_id)
        params.append(emoji_id)
        with self._connect() as conn:
            conn.execute(f"UPDATE emojis SET {', '.join(fields)} WHERE id = ?", params)
        return self.get_by_id(emoji_id)

    def delete(self, emoji_id: int) -> EmojiRecord | None:
        record = self.get_by_id(emoji_id)
        if not record:
            return None
        with self._connect() as conn:
            conn.execute("DELETE FROM emojis WHERE id = ?", (emoji_id,))
        return record

    def mark_used(self, file_hash: str) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE emojis
                SET usage_count = usage_count + 1,
                    query_count = query_count + 1,
                    last_used_time = ?
                WHERE file_hash = ?
                """,
                (utcnow_iso(), file_hash),
            )

    def cleanup_missing_files(self) -> int:
        removed = 0
        records, _ = self.list(page_size=200)
        page = 1
        while records:
            for record in records:
                if not Path(record.path).is_file():
                    self.delete(record.id or 0)
                    removed += 1
            page += 1
            records, _ = self.list(page=page, page_size=200)
        return removed

