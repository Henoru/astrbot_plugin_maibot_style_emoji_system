from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

EmojiStatus = Literal["known", "unknown", "adopted", "discarded"]


def utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_tags(raw: str | list[str] | None) -> list[str]:
    if raw is None:
        return []
    parts: list[str] = []
    if isinstance(raw, str):
        parts = [p.strip() for p in re.split(r"[,，、;；\r\n\t\s]+", raw)]
    else:
        for item in raw:
            parts.extend(normalize_tags(item))

    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(part)
    return result


@dataclass(slots=True)
class EmojiRecord:
    id: int | None
    file_hash: str
    file_name: str
    path: str
    format: str
    description: str = ""
    emotion_tags: list[str] | None = None
    query_count: int = 0
    usage_count: int = 0
    is_registered: bool = False
    is_banned: bool = False
    source_platform: str = ""
    source_session: str = ""
    source_message_id: str = ""
    record_time: str = ""
    register_time: str | None = None
    last_used_time: str | None = None
    embedding_text: str = ""
    embedding_provider_id: str = ""
    embedding_model: str = ""
    embedding_dim: int = 0
    embedding_vector: list[float] | None = None
    embedding_updated_time: str | None = None

    @property
    def status(self) -> EmojiStatus:
        if self.is_banned:
            return "discarded"
        if self.is_registered:
            return "adopted"
        if self.description.strip():
            return "known"
        return "unknown"

    @property
    def path_obj(self) -> Path:
        return Path(self.path)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "file_hash": self.file_hash,
            "file_name": self.file_name,
            "path": self.path,
            "format": self.format,
            "description": self.description,
            "emotion_tags": normalize_tags(self.emotion_tags or []),
            "query_count": self.query_count,
            "usage_count": self.usage_count,
            "is_registered": self.is_registered,
            "is_banned": self.is_banned,
            "status": self.status,
            "source_platform": self.source_platform,
            "source_session": self.source_session,
            "source_message_id": self.source_message_id,
            "record_time": self.record_time,
            "register_time": self.register_time,
            "last_used_time": self.last_used_time,
            "embedding_provider_id": self.embedding_provider_id,
            "embedding_model": self.embedding_model,
            "embedding_dim": self.embedding_dim,
            "embedding_updated_time": self.embedding_updated_time,
        }
