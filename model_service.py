from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.star import Context
from astrbot.core.provider.provider import EmbeddingProvider

from .models import EmojiRecord, normalize_tags


class EmojiModelService:
    def __init__(self, context: Context, config: Any) -> None:
        self.context = context
        self.config = config

    def _cfg_str(self, key: str) -> str:
        if not hasattr(self.config, "get"):
            return ""
        return str(self.config.get(key, "") or "").strip()

    def _configured_provider_id(self, umo: str = "") -> str:
        provider_id = self._cfg_str("provider_id")
        if provider_id:
            return provider_id
        provider = self.context.get_using_provider(umo or None)
        return provider.meta().id if provider else ""

    def _configured_selector_provider_id(self, umo: str = "") -> str:
        provider_id = self._cfg_str("selector_provider_id")
        if provider_id:
            return provider_id
        return self._configured_provider_id(umo)

    def _configured_embedding_provider_id(self) -> str:
        return self._cfg_str("embedding_provider_id")

    def _embedding_provider_info(
        self,
    ) -> tuple[EmbeddingProvider, str, str, int] | None:
        provider_id = self._configured_embedding_provider_id()
        if not provider_id:
            return None
        provider = self.context.get_provider_by_id(provider_id)
        if not isinstance(provider, EmbeddingProvider):
            logger.warning(
                "MaiBotStyleEmojiSystem embedding skipped: "
                "provider_id=%s is not an EmbeddingProvider",
                provider_id,
            )
            return None

        model = ""
        resolved_provider_id = provider_id
        try:
            meta = provider.meta()
            resolved_provider_id = meta.id or provider_id
            model = meta.model or ""
        except Exception as exc:
            logger.debug(
                "MaiBotStyleEmojiSystem embedding provider meta fallback: provider_id=%s error=%s",
                provider_id,
                exc,
            )
        if not model:
            model = provider.get_model() or str(
                provider.provider_config.get("embedding_model", "") or ""
            )
        try:
            dim = int(provider.get_dim())
        except Exception as exc:
            logger.debug(
                "MaiBotStyleEmojiSystem embedding dim unavailable: provider_id=%s error=%s",
                provider_id,
                exc,
            )
            dim = 0
        return provider, resolved_provider_id, model, dim

    def embedding_signature(self) -> tuple[str, str, int] | None:
        info = self._embedding_provider_info()
        if not info:
            return None
        _, provider_id, model, dim = info
        return provider_id, model, dim

    async def embed_texts(
        self,
        texts: list[str],
    ) -> tuple[list[list[float]], str, str, int] | None:
        if not texts:
            return [], "", "", 0
        info = self._embedding_provider_info()
        if not info:
            return None
        provider, provider_id, model, configured_dim = info
        batch_size = max(1, int(self.config.get("embedding_batch_size", 32)))
        vectors: list[list[float]] = []
        try:
            for start in range(0, len(texts), batch_size):
                batch = texts[start : start + batch_size]
                batch_vectors = await provider.get_embeddings(batch)
                if len(batch_vectors) != len(batch):
                    logger.warning(
                        "MaiBotStyleEmojiSystem embedding skipped: "
                        "provider_id=%s returned %s vectors for %s texts",
                        provider_id,
                        len(batch_vectors),
                        len(batch),
                    )
                    return None
                vectors.extend([[float(value) for value in vector] for vector in batch_vectors])
        except Exception as exc:
            logger.warning(
                "MaiBotStyleEmojiSystem embedding failed: provider_id=%s texts=%s error=%s",
                provider_id,
                len(texts),
                exc,
            )
            return None
        dim = len(vectors[0]) if vectors else configured_dim
        logger.debug(
            "MaiBotStyleEmojiSystem embedding generated: provider_id=%s texts=%s dim=%s",
            provider_id,
            len(texts),
            dim,
        )
        return vectors, provider_id, model, dim

    async def _generate(
        self,
        prompt: str,
        *,
        image_paths: list[str] | None = None,
        umo: str = "",
        provider_id: str | None = None,
    ) -> str:
        provider_id = provider_id if provider_id is not None else self._configured_provider_id(umo)
        if not provider_id:
            logger.warning(
                "MaiBotStyleEmojiSystem model skipped: no provider configured umo=%s",
                umo,
            )
            return ""
        logger.debug(
            "MaiBotStyleEmojiSystem model request: provider_id=%s images=%s umo=%s",
            provider_id,
            len(image_paths or []),
            umo,
        )
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            image_urls=image_paths or [],
        )
        text = (response.completion_text or "").strip()
        logger.debug(
            "MaiBotStyleEmojiSystem model response: provider_id=%s chars=%s",
            provider_id,
            len(text),
        )
        return text

    async def generate_selection(self, prompt: str, *, umo: str = "") -> str:
        provider_id = self._configured_selector_provider_id(umo)
        return await self._generate(prompt, umo=umo, provider_id=provider_id)

    async def audit_image(self, image_path: Path, *, umo: str = "") -> bool:
        prompt = (
            "这是一个表情包，请判断它是否适合保存到聊天机器人表情库。"
            "要求：不能包含色情、暴力、违法违规内容；不能是聊天记录截图或明显隐私截图；"
            "文字不能过多。只回答“是”或“否”。"
        )
        text = await self._generate(prompt, image_paths=[str(image_path)], umo=umo)
        if not text:
            logger.debug(
                "MaiBotStyleEmojiSystem audit fallback pass: image=%s",
                image_path,
            )
            return True
        passed = text.strip().startswith("是") or text.strip().lower().startswith("yes")
        logger.debug(
            "MaiBotStyleEmojiSystem audit result: image=%s passed=%s response=%s",
            image_path,
            passed,
            text[:80],
        )
        return passed

    async def describe_image(self, image_path: Path, *, umo: str = "") -> tuple[str, list[str]]:
        prompt = (
            "请为这个表情包生成 JSON，不要输出 JSON 之外的内容。"
            "格式：{\"description\":\"一句话描述画面和用途\","
            "\"emotions\":[\"开心\",\"疑惑\"]}。"
            "emotions 给出 1 到 5 个中文情绪或语境标签。"
        )
        text = await self._generate(prompt, image_paths=[str(image_path)], umo=umo)
        data = self._extract_json(text)
        if isinstance(data, dict):
            description = str(data.get("description") or "").strip()
            emotion_data = (
                data.get("emotions")
                if isinstance(data.get("emotions"), list)
                else data.get("emotion")
            )
            emotions = normalize_tags(emotion_data)
            logger.debug(
                "MaiBotStyleEmojiSystem describe parsed: image=%s tags=%s",
                image_path,
                ",".join(emotions),
            )
            return description or "未命名表情", emotions
        logger.debug(
            "MaiBotStyleEmojiSystem describe fallback: image=%s response=%s",
            image_path,
            text[:80],
        )
        return (text[:120] or "未命名表情"), normalize_tags(text)

    async def choose_replacement(
        self,
        *,
        new_record: EmojiRecord,
        existing: list[EmojiRecord],
        max_registered: int,
        umo: str = "",
    ) -> EmojiRecord | None:
        lines = [
            f"{idx}. {emoji.description or '无描述'} "
            f"标签:{','.join(emoji.emotion_tags or [])} "
            f"使用:{emoji.usage_count}"
            for idx, emoji in enumerate(existing, 1)
        ]
        prompt = (
            f"可发送表情包数量已满({len(existing)}/{max_registered})，需要决定是否取消注册一个旧表情包。"
            f"\n新表情描述：{new_record.description}\n新表情标签：{','.join(new_record.emotion_tags or [])}"
            "\n现有表情：\n"
            + "\n".join(lines)
            + "\n请只回答 JSON：{\"replace\":true,\"index\":1} 或 {\"replace\":false}。"
        )
        text = await self._generate(prompt, umo=umo)
        data = self._extract_json(text)
        if isinstance(data, dict) and data.get("replace"):
            try:
                index = int(data.get("index")) - 1
            except (TypeError, ValueError):
                return None
            if 0 <= index < len(existing):
                logger.info(
                    "MaiBotStyleEmojiSystem replacement selected by model: new_id=%s replace_id=%s",
                    new_record.id,
                    existing[index].id,
                )
                return existing[index]
        match = re.search(r"取消注册编号\s*(\d+)", text)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(existing):
                logger.info(
                    "MaiBotStyleEmojiSystem replacement selected by text fallback: "
                    "new_id=%s replace_id=%s",
                    new_record.id,
                    existing[index].id,
                )
                return existing[index]
        logger.info(
            "MaiBotStyleEmojiSystem replacement not selected: new_id=%s existing_count=%s",
            new_record.id,
            len(existing),
        )
        return None

    @staticmethod
    def _extract_json(text: str) -> object | None:
        if not text:
            return None
        stripped = text.strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
            stripped = re.sub(r"\s*```$", "", stripped)
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", stripped, flags=re.S)
            if match:
                try:
                    return json.loads(match.group(1))
                except json.JSONDecodeError:
                    return None
        return None
