from __future__ import annotations

import json
import random
import re
from pathlib import Path

from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont

from .model_service import EmojiModelService
from .models import EmojiRecord


class EmojiSelector:
    def __init__(self, model_service: EmojiModelService, data_dir: Path) -> None:
        self.model_service = model_service
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

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
        candidates = self._rank(records, emotion, reason)
        if not candidates:
            return None
        sample = candidates[: max(sample_size, 1)]
        if len(sample) == 1:
            return sample[0]

        grid_path = self._build_grid(sample, grid_columns=max(grid_columns, 1))
        prompt = (
            f"你需要根据上下文和当前语气选择一个合适的表情包来发送。\n"
            f"目标情绪：{emotion or '未指定'}\n理由：{reason or '未指定'}\n"
            f"图片是一张表情包拼图，每张小图左上角有序号 1 到 {len(sample)}。"
            "请只回答 JSON：{\"emoji_index\":1,\"reason\":\"简短理由\"}。"
        )
        text = await self.model_service._generate(prompt, image_paths=[str(grid_path)], umo=umo)
        index = self._parse_index(text)
        if index is not None and 0 <= index < len(sample):
            return sample[index]
        return sample[0]

    def _rank(self, records: list[EmojiRecord], emotion: str, reason: str) -> list[EmojiRecord]:
        query = f"{emotion} {reason}".strip().lower()
        if not query:
            shuffled = list(records)
            random.shuffle(shuffled)
            return shuffled

        def score(record: EmojiRecord) -> tuple[int, int, int]:
            text = f"{record.description} {' '.join(record.emotion_tags or [])}".lower()
            exact = 10 if emotion and emotion.lower() in text else 0
            overlap = sum(1 for token in re.split(r"\s+", query) if token and token in text)
            usage_penalty = -int(record.usage_count)
            return exact, overlap, usage_penalty

        return sorted(records, key=score, reverse=True)

    def _build_grid(self, records: list[EmojiRecord], *, grid_columns: int) -> Path:
        cell = 180
        rows = (len(records) + grid_columns - 1) // grid_columns
        canvas = PILImage.new("RGB", (cell * grid_columns, cell * rows), "white")
        draw = ImageDraw.Draw(canvas)
        font = ImageFont.load_default()
        for idx, record in enumerate(records):
            x = (idx % grid_columns) * cell
            y = (idx // grid_columns) * cell
            try:
                with PILImage.open(record.path) as image:
                    if getattr(image, "n_frames", 1) > 1:
                        image.seek(0)
                    image = image.convert("RGBA")
                    image.thumbnail((cell - 16, cell - 16), PILImage.Resampling.LANCZOS)
                    canvas.paste(image, (x + 8, y + 8), image if image.mode == "RGBA" else None)
            except Exception:
                pass
            draw.rectangle((x, y, x + 32, y + 24), fill="white", outline="black")
            draw.text((x + 6, y + 5), str(idx + 1), fill="black", font=font)
        path = self.data_dir / "emoji_selection_grid.jpg"
        canvas.save(path, "JPEG", quality=88)
        return path

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

