import sys
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from astrbot_plugin_maibot_style_emoji_system.repository import EmojiRepository
from astrbot_plugin_maibot_style_emoji_system.storage import EmojiStorage


def _make_image(path: Path) -> None:
    image = Image.new("RGB", (64, 64), "#2f7d62")
    image.save(path)


def test_storage_and_repository_round_trip(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _make_image(source)

    storage = EmojiStorage(tmp_path / "data")
    record, created = storage.save_from_path(source, source_platform="aiocqhttp")
    assert created is True
    assert Path(record.path).is_file()

    repo = EmojiRepository(tmp_path / "emoji.db")
    stored = repo.upsert(record)
    assert stored.id is not None
    assert stored.file_hash == record.file_hash

    repo.update(
        stored.id,
        description="绿色表情",
        emotion_tags=["开心", "测试"],
        is_registered=True,
    )
    registered = repo.registered()
    assert len(registered) == 1
    assert registered[0].emotion_tags == ["开心", "测试"]

    repo.mark_used(registered[0].file_hash)
    used = repo.get_by_id(registered[0].id)
    assert used is not None
    assert used.usage_count == 1


def test_thumbnail_creation(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _make_image(source)

    storage = EmojiStorage(tmp_path / "data")
    record, _ = storage.save_from_path(source)
    thumbnail = storage.ensure_thumbnail(record)

    assert thumbnail.is_file()
    assert thumbnail.suffix == ".webp"
