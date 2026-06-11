from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from astrbot.api.star import Context

from .models import EmojiRecord, normalize_tags


class EmojiModelService:
    def __init__(self, context: Context, config: Any) -> None:
        self.context = context
        self.config = config

    def _configured_provider_id(self, umo: str = "") -> str:
        provider_id = ""
        if hasattr(self.config, "get"):
            provider_id = str(self.config.get("provider_id", "") or "").strip()
        if provider_id:
            return provider_id
        provider = self.context.get_using_provider(umo or None)
        return provider.meta().id if provider else ""

    async def _generate(
        self,
        prompt: str,
        *,
        image_paths: list[str] | None = None,
        umo: str = "",
    ) -> str:
        provider_id = self._configured_provider_id(umo)
        if not provider_id:
            return ""
        response = await self.context.llm_generate(
            chat_provider_id=provider_id,
            prompt=prompt,
            image_urls=image_paths or [],
        )
        return (response.completion_text or "").strip()

    async def audit_image(self, image_path: Path, *, umo: str = "") -> bool:
        prompt = (
            "这是一个表情包，请判断它是否适合保存到聊天机器人表情库。"
            "要求：不能包含色情、暴力、违法违规内容；不能是聊天记录截图或明显隐私截图；"
            "文字不能过多。只回答“是”或“否”。"
        )
        text = await self._generate(prompt, image_paths=[str(image_path)], umo=umo)
        if not text:
            return True
        return text.strip().startswith("是") or text.strip().lower().startswith("yes")

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
            emotions = normalize_tags(data.get("emotions") if isinstance(data.get("emotions"), list) else data.get("emotion"))
            return description or "未命名表情", emotions
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
            f"{idx}. {emoji.description or '无描述'} 标签:{','.join(emoji.emotion_tags or [])} 使用:{emoji.usage_count}"
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
                return existing[index]
        match = re.search(r"取消注册编号\s*(\d+)", text)
        if match:
            index = int(match.group(1)) - 1
            if 0 <= index < len(existing):
                return existing[index]
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

