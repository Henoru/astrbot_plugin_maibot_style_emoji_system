from __future__ import annotations

import hashlib
import shutil
from pathlib import Path

from PIL import Image as PILImage

from .models import EmojiRecord, utcnow_iso

SUPPORTED_FORMATS = {
    ".jpg": "jpg",
    ".jpeg": "jpg",
    ".png": "png",
    ".gif": "gif",
    ".webp": "webp",
    ".bmp": "bmp",
}


class EmojiStorage:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.emoji_dir = root / "emojis"
        self.thumbnail_dir = root / "thumbnails"
        self.emoji_dir.mkdir(parents=True, exist_ok=True)
        self.thumbnail_dir.mkdir(parents=True, exist_ok=True)

    def save_from_path(
        self,
        source: Path,
        *,
        source_platform: str = "",
        source_session: str = "",
        source_message_id: str = "",
    ) -> tuple[EmojiRecord, bool]:
        source = source.resolve()
        suffix = source.suffix.lower()
        image_format = SUPPORTED_FORMATS.get(suffix, "png")
        data = source.read_bytes()
        file_hash = hashlib.sha256(data).hexdigest()
        file_name = f"{file_hash}.{image_format}"
        target = self.emoji_dir / file_name
        created = False
        if not target.exists():
            shutil.copyfile(source, target)
            created = True
        record = EmojiRecord(
            id=None,
            file_hash=file_hash,
            file_name=file_name,
            path=str(target),
            format=image_format,
            source_platform=source_platform,
            source_session=source_session,
            source_message_id=source_message_id,
            record_time=utcnow_iso(),
        )
        return record, created

    def validate_image(
        self,
        path: Path,
        *,
        max_file_size_mb: float,
        min_width: int,
        min_height: int,
    ) -> tuple[bool, str]:
        if not path.is_file():
            return False, "file not found"
        if path.stat().st_size > max_file_size_mb * 1024 * 1024:
            return False, "file too large"
        try:
            with PILImage.open(path) as image:
                width, height = image.size
                if width < min_width or height < min_height:
                    return False, "image too small"
        except Exception as e:
            return False, f"invalid image: {e}"
        return True, ""

    def thumbnail_path(self, file_hash: str) -> Path:
        return self.thumbnail_dir / f"{file_hash}.webp"

    def ensure_thumbnail(self, record: EmojiRecord) -> Path:
        cache_path = self.thumbnail_path(record.file_hash)
        if cache_path.exists():
            return cache_path
        with PILImage.open(record.path) as image:
            if getattr(image, "n_frames", 1) > 1:
                image.seek(0)
            if image.mode in {"P", "PA", "LA"}:
                image = image.convert("RGBA")
            elif image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGB")
            image.thumbnail((200, 200), PILImage.Resampling.LANCZOS)
            image.save(cache_path, "WEBP", quality=80, method=6)
        return cache_path

    def delete_files(self, record: EmojiRecord) -> None:
        for path in (Path(record.path), self.thumbnail_path(record.file_hash)):
            try:
                if path.is_file():
                    path.unlink()
            except OSError:
                pass

