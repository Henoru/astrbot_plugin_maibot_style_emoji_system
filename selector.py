from __future__ import annotations

import json
import math
import random
import re
from pathlib import Path

from astrbot.api import logger

from .model_service import EmojiModelService
from .models import EmojiRecord, normalize_description_document, normalize_tags
from .repository import EmojiRepository


class EmojiSelector:
    def __init__(
        self,
        model_service: EmojiModelService,
        data_dir: Path | None = None,
        repository: EmojiRepository | None = None,
    ) -> None:
        self.model_service = model_service
        self.data_dir = data_dir
        self.repository = repository

    async def select(
        self,
        records: list[EmojiRecord],
        *,
        emotion: str = "",
        reason: str = "",
        sample_size: int = 20,
        grid_columns: int = 5,
        umo: str = "",
    ) -> EmojiRecord | None:
        candidates = await self._rank_candidates(records, emotion, reason)
        if not candidates:
            logger.info(
                "MaiBotStyleEmojiSystem selector found no candidates: emotion=%s reason=%s",
                emotion,
                reason,
            )
            return None
        sample = candidates[: max(sample_size, 1)]
        if len(sample) == 1:
            logger.debug(
                "MaiBotStyleEmojiSystem selector single candidate: emoji_id=%s",
                sample[0][0].id,
            )
            return sample[0][0]

        prompt = self._build_selection_prompt(sample, emotion, reason)
        text = await self.model_service.generate_selection(prompt, umo=umo)
        index = self._parse_index(text)
        if index is not None and 0 <= index < len(sample):
            record = sample[index][0]
            logger.info(
                "MaiBotStyleEmojiSystem selector chose by text model: "
                "emoji_id=%s index=%s sample_size=%s",
                record.id,
                index + 1,
                len(sample),
            )
            return record
        logger.info(
            "MaiBotStyleEmojiSystem selector fallback first candidate: emoji_id=%s sample_size=%s",
            sample[0][0].id,
            len(sample),
        )
        return sample[0][0]

    async def _rank_candidates(
        self,
        records: list[EmojiRecord],
        emotion: str,
        reason: str,
    ) -> list[tuple[EmojiRecord, float | None]]:
        embedded = await self._rank_by_embedding(records, emotion, reason)
        if embedded is not None:
            return embedded
        return [(record, None) for record in self._rank(records, emotion, reason)]

    async def _rank_by_embedding(
        self,
        records: list[EmojiRecord],
        emotion: str,
        reason: str,
    ) -> list[tuple[EmojiRecord, float]] | None:
        query_text = self._query_embedding_text(emotion, reason)
        if not query_text.strip():
            return None
        signature = self.model_service.embedding_signature()
        if not signature:
            return None
        provider_id, model, dim = signature

        vectors_by_hash: dict[str, list[float]] = {}
        missing: list[tuple[EmojiRecord, str]] = []
        for record in records:
            embedding_text = self._record_embedding_text(record)
            vector = self._cached_embedding(record, embedding_text, provider_id, model, dim)
            if vector is None:
                missing.append((record, embedding_text))
            else:
                vectors_by_hash[record.file_hash] = vector

        texts = [query_text, *[embedding_text for _, embedding_text in missing]]
        embedded = await self.model_service.embed_texts(texts)
        if embedded is None:
            return None
        vectors, provider_id, model, dim = embedded
        if not vectors:
            return None
        query_vector = vectors[0]

        for (record, embedding_text), vector in zip(missing, vectors[1:], strict=False):
            self._set_record_embedding(record, embedding_text, provider_id, model, vector)
            vectors_by_hash[record.file_hash] = vector

        scored: list[tuple[EmojiRecord, float]] = []
        for record in records:
            score = self._cosine_similarity(query_vector, vectors_by_hash.get(record.file_hash))
            if score is not None:
                scored.append((record, score))
        if not scored:
            return None
        logger.debug(
            "MaiBotStyleEmojiSystem selector ranked by embedding: candidates=%s dim=%s",
            len(scored),
            dim,
        )
        return sorted(
            scored,
            key=lambda item: (item[1], -int(item[0].usage_count), int(item[0].id or 0)),
            reverse=True,
        )

    def _cached_embedding(
        self,
        record: EmojiRecord,
        embedding_text: str,
        provider_id: str,
        model: str,
        dim: int,
    ) -> list[float] | None:
        vector = record.embedding_vector or []
        if not vector:
            return None
        if record.embedding_text != embedding_text:
            return None
        if record.embedding_provider_id != provider_id:
            return None
        if record.embedding_model != model:
            return None
        if dim and record.embedding_dim != dim:
            return None
        return vector

    def _set_record_embedding(
        self,
        record: EmojiRecord,
        embedding_text: str,
        provider_id: str,
        model: str,
        vector: list[float],
    ) -> None:
        record.embedding_text = embedding_text
        record.embedding_provider_id = provider_id
        record.embedding_model = model
        record.embedding_dim = len(vector)
        record.embedding_vector = vector
        if not self.repository or record.id is None:
            return
        try:
            updated = self.repository.update_embedding(
                record.id,
                embedding_text=embedding_text,
                provider_id=provider_id,
                model=model,
                vector=vector,
            )
            if updated:
                record.embedding_updated_time = updated.embedding_updated_time
        except Exception as exc:
            logger.warning(
                "MaiBotStyleEmojiSystem selector failed to persist embedding: emoji_id=%s error=%s",
                record.id,
                exc,
            )

    def _rank(self, records: list[EmojiRecord], emotion: str, reason: str) -> list[EmojiRecord]:
        query = f"{emotion} {reason}".strip().lower()
        if not query:
            shuffled = list(records)
            random.shuffle(shuffled)
            return shuffled

        def score(record: EmojiRecord) -> tuple[int, int, int]:
            text = " ".join(
                [
                    record.description_document,
                    record.description,
                    " ".join(record.emotion_tags or []),
                ]
            ).lower()
            exact = 10 if emotion and emotion.lower() in text else 0
            overlap = sum(1 for token in re.split(r"\s+", query) if token and token in text)
            usage_penalty = -int(record.usage_count)
            return exact, overlap, usage_penalty

        return sorted(records, key=score, reverse=True)

    def _build_selection_prompt(
        self,
        candidates: list[tuple[EmojiRecord, float | None]],
        emotion: str,
        reason: str,
    ) -> str:
        lines = []
        for idx, (record, score) in enumerate(candidates, 1):
            tags = ", ".join(normalize_tags(record.emotion_tags or [])) or "无"
            similarity = "未知" if score is None else f"{score:.3f}"
            description = record.description_document or record.description or "未描述"
            lines.append(
                f"{idx}. ID #{record.id}；描述文档：{self._clip(description)}；"
                f"标签：{tags}；使用次数：{record.usage_count}；相似度：{similarity}"
            )
        return (
            "你需要根据上下文和当前语气选择一个合适的表情包来发送。\n"
            f"目标情绪：{emotion or '未指定'}\n"
            f"理由：{reason or '未指定'}\n"
            "下面只提供候选表情包的文字描述文档、标签、使用次数和相似度。\n"
            "候选表情：\n"
            + "\n".join(lines)
            + "\n请只回答 JSON：{\"emoji_index\":1,\"reason\":\"简短理由\"}。"
        )

    @staticmethod
    def _query_embedding_text(emotion: str, reason: str) -> str:
        return f"目标情绪: {emotion.strip()}\n理由: {reason.strip()}".strip()

    @staticmethod
    def _record_embedding_text(record: EmojiRecord) -> str:
        return EmojiSelector.build_record_embedding_text(
            description_document=record.description_document,
            description=record.description,
            emotion_tags=record.emotion_tags or [],
        )

    @staticmethod
    def build_record_embedding_text(
        *,
        description_document: str,
        description: str,
        emotion_tags: list[str],
    ) -> str:
        document = normalize_description_document(description_document)
        tags = ", ".join(normalize_tags(emotion_tags or [])) or "无"
        if document:
            return f"描述文档: {document}\n标签: {tags}"
        description = description.strip() or "未描述"
        return f"描述: {description}\n标签: {tags}"

    @staticmethod
    def _clip(value: str, limit: int = 120) -> str:
        value = " ".join(value.split())
        if len(value) <= limit:
            return value
        return value[: limit - 1] + "…"

    @staticmethod
    def _cosine_similarity(
        left: list[float] | None,
        right: list[float] | None,
    ) -> float | None:
        if not left or not right or len(left) != len(right):
            return None
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
        if not left_norm or not right_norm:
            return None
        return sum(a * b for a, b in zip(left, right, strict=True)) / (
            left_norm * right_norm
        )

    @staticmethod
    def _parse_index(text: str) -> int | None:
        if not text:
            return None
        cleaned = text.strip()
        if cleaned.startswith("```"):
            cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
            cleaned = re.sub(r"\s*```$", "", cleaned)
        try:
            data = json.loads(cleaned)
            return int(data.get("emoji_index")) - 1
        except Exception:
            pass
        match = re.search(r"\d+", cleaned)
        return int(match.group(0)) - 1 if match else None
