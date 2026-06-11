from __future__ import annotations

import asyncio
import base64
import binascii
import tempfile
from pathlib import Path
from typing import Any

from astrbot.api import logger, llm_tool, star
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Image
from astrbot.core.message.message_event_result import MessageChain
from astrbot.core.utils.astrbot_path import get_astrbot_plugin_data_path
from quart import jsonify, request, send_file

from .model_service import EmojiModelService
from .models import EmojiRecord, utcnow_iso
from .repository import EmojiRepository
from .selector import EmojiSelector
from .storage import EmojiStorage

PLUGIN_NAME = "astrbot_plugin_maibot_style_emoji_system"


@star.register(
    PLUGIN_NAME,
    "AstrBot",
    "MaiBot-style emoji system for AstrBot.",
    "0.1.0",
)
class EmojiSystemPlugin(star.Star):
    def __init__(self, context: star.Context, config: dict | None = None) -> None:
        super().__init__(context)
        self.config = config or {}
        self.data_dir = Path(get_astrbot_plugin_data_path()) / PLUGIN_NAME
        self.repo = EmojiRepository(self.data_dir / "emoji.db")
        self.storage = EmojiStorage(self.data_dir)
        self.model = EmojiModelService(context, self.config)
        self.selector = EmojiSelector(self.model, self.data_dir)
        self._register_lock = asyncio.Lock()
        self._maintenance_task: asyncio.Task | None = None
        self._register_web_apis()

    async def initialize(self) -> None:
        if self._cfg_bool("enabled", True):
            self._maintenance_task = asyncio.create_task(self._maintenance_loop())

    async def terminate(self) -> None:
        if self._maintenance_task:
            self._maintenance_task.cancel()
            try:
                await self._maintenance_task
            except asyncio.CancelledError:
                pass

    @filter.platform_adapter_type(filter.PlatformAdapterType.ALL, priority=-100)
    async def capture_images(self, event: AstrMessageEvent) -> None:
        if not self._cfg_bool("enabled", True):
            return
        if not self._platform_allowed(event):
            return
        if not self._session_allowed(event.unified_msg_origin):
            return

        for component in event.get_messages():
            if not isinstance(component, Image):
                continue
            try:
                image_path = Path(await component.convert_to_file_path())
                await self._register_image_path(image_path, event)
            except Exception as exc:
                logger.debug("EmojiSystem skipped image capture: %s", exc)

    @filter.command_group("emoji")
    async def emoji(self, event: AstrMessageEvent) -> None:
        event.set_result(self._help_text())

    @emoji.command("random")
    async def emoji_random(self, event: AstrMessageEvent) -> None:
        record = await self.selector.select(
            self.repo.registered(),
            emotion="随机表情",
            reason="用户请求随机发送一个表情包。",
            sample_size=int(self.config.get("sample_size", 20)),
            grid_columns=int(self.config.get("grid_columns", 5)),
            umo=event.unified_msg_origin,
        )
        await self._send_record(event, record)

    @emoji.command("search")
    async def emoji_search(self, event: AstrMessageEvent, keyword: str = "") -> None:
        records, _ = self.repo.list(query=keyword.strip(), page_size=10)
        if not records:
            event.set_result("没有找到匹配的表情。")
            return
        lines = [
            f"#{record.id} {', '.join(record.emotion_tags) or '未标注'} - {record.description[:36]}"
            for record in records
        ]
        event.set_result("\n".join(lines))

    @emoji.command("stats")
    async def emoji_stats(self, event: AstrMessageEvent) -> None:
        stats = self._stats_payload()
        event.set_result(
            "\n".join(
                [
                    f"注册表情: {stats['registered']}/{stats['max_registered']}",
                    f"已收录: {stats['total']}",
                    f"已禁用: {stats['banned']}",
                    f"情绪标签: {', '.join(stats['emotions'][:12]) or '无'}",
                ]
            )
        )

    @emoji.command("ban")
    async def emoji_ban(self, event: AstrMessageEvent, emoji_id: int) -> None:
        self.repo.update(emoji_id, is_banned=True, is_registered=False)
        event.set_result(f"已禁用表情 #{emoji_id}。")

    @emoji.command("adopt")
    async def emoji_adopt(self, event: AstrMessageEvent, emoji_id: int) -> None:
        record = self.repo.get_by_id(emoji_id)
        if not record:
            event.set_result(f"未找到表情 #{emoji_id}。")
            return
        await self._adopt_record(record)
        event.set_result(f"已领养表情 #{emoji_id}。")

    @emoji.command("reload")
    async def emoji_reload(self, event: AstrMessageEvent) -> None:
        removed = self.repo.cleanup_missing_files()
        event.set_result(f"已重新扫描表情库，清理缺失记录 {removed} 条。")

    @llm_tool("send_emoji")
    async def send_emoji_tool(
        self,
        event: AstrMessageEvent,
        emotion: str,
        reason: str = "",
    ) -> str:
        """Send a local image emoji that matches the requested emotion.

        Args:
            emotion(string): The target emotion, reaction, or meme style.
            reason(string): Why this emoji should be sent in the current conversation.
        """
        if not self._cfg_bool("enabled", True):
            return "Emoji system is disabled."
        record = await self.selector.select(
            self.repo.registered(),
            emotion=emotion,
            reason=reason,
            sample_size=int(self.config.get("sample_size", 20)),
            grid_columns=int(self.config.get("grid_columns", 5)),
            umo=event.unified_msg_origin,
        )
        if not record:
            return "No matching emoji is available."
        await self._send_record(event, record)
        return f"Sent emoji #{record.id}: {record.description}"

    async def _send_record(
        self,
        event: AstrMessageEvent,
        record: EmojiRecord | None,
    ) -> None:
        if not record:
            event.set_result("没有可用表情。")
            return
        self.repo.mark_used(record.file_hash)
        await event.send(MessageChain([Image.fromFileSystem(record.path)]))

    async def _register_image_path(
        self,
        image_path: Path,
        event: AstrMessageEvent | None = None,
        *,
        force_registered: bool = False,
    ) -> EmojiRecord | None:
        async with self._register_lock:
            ok, reason = self.storage.validate_image(
                image_path,
                max_file_size_mb=float(self.config.get("max_file_size_mb", 8)),
                min_width=int(self.config.get("min_width", 32)),
                min_height=int(self.config.get("min_height", 32)),
            )
            if not ok:
                raise ValueError(reason)
            record, created = self.storage.save_from_path(
                image_path,
                source_platform=event.get_platform_name() if event else "web",
                source_session=event.unified_msg_origin if event else "web",
                source_message_id=self._message_id(event),
            )
            existing = self.repo.get_by_hash(record.file_hash)
            if existing:
                record.id = existing.id
                if existing.is_banned or existing.is_registered:
                    return existing
                record.query_count = existing.query_count
                record.usage_count = existing.usage_count
            elif created:
                self.repo.upsert(record)
                stored = self.repo.get_by_hash(record.file_hash)
                if stored:
                    record.id = stored.id

            if self._cfg_bool("audit_enabled", True):
                passed = await self.model.audit_image(
                    Path(record.path),
                    umo=event.unified_msg_origin if event else "",
                )
                if not passed:
                    record.is_banned = True
                    record.is_registered = False
                    self.repo.upsert(record)
                    return record

            if not record.description or not record.emotion_tags:
                description, tags = await self.model.describe_image(Path(record.path))
                record.description = description or record.description
                record.emotion_tags = tags or record.emotion_tags

            await self._adopt_record(record, force=force_registered)
            self.repo.upsert(record)
            return record

    async def _adopt_record(self, record: EmojiRecord, *, force: bool = True) -> None:
        if record.is_banned:
            return
        max_registered = int(self.config.get("max_registered", 500))
        registered_count = self.repo.count_registered()
        if registered_count >= max_registered and not record.is_registered:
            candidate = await self.model.choose_replacement(
                new_record=record,
                existing=self.repo.registered()[:max_registered],
                max_registered=max_registered,
            )
            if candidate:
                self.repo.update(candidate.id, is_registered=False)
            elif not force:
                record.is_registered = False
                return
        record.is_registered = True
        record.register_time = record.register_time or utcnow_iso()

    async def _maintenance_loop(self) -> None:
        while True:
            minutes = max(5, int(self.config.get("maintenance_interval_minutes", 360)))
            await asyncio.sleep(minutes * 60)
            try:
                removed = self.repo.cleanup_missing_files()
                if removed:
                    logger.info("EmojiSystem cleaned %s missing records.", removed)
            except Exception as exc:
                logger.warning("EmojiSystem maintenance failed: %s", exc)

    def _register_web_apis(self) -> None:
        prefix = f"/{PLUGIN_NAME}"
        self.context.register_web_api(
            f"{prefix}/emojis",
            self.api_list_emojis,
            ["GET"],
            "List emoji records.",
        )
        self.context.register_web_api(
            f"{prefix}/stats",
            self.api_stats,
            ["GET"],
            "Get emoji statistics.",
        )
        self.context.register_web_api(
            f"{prefix}/thumbnail/<int:emoji_id>",
            self.api_thumbnail,
            ["GET"],
            "Get emoji thumbnail.",
        )
        self.context.register_web_api(
            f"{prefix}/upload",
            self.api_upload,
            ["POST"],
            "Upload and register an emoji.",
        )
        self.context.register_web_api(
            f"{prefix}/update/<int:emoji_id>",
            self.api_update,
            ["POST"],
            "Update emoji metadata.",
        )
        self.context.register_web_api(
            f"{prefix}/adopt/<int:emoji_id>",
            self.api_adopt,
            ["POST"],
            "Register an emoji.",
        )
        self.context.register_web_api(
            f"{prefix}/ban/<int:emoji_id>",
            self.api_ban,
            ["POST"],
            "Ban an emoji.",
        )
        self.context.register_web_api(
            f"{prefix}/delete/<int:emoji_id>",
            self.api_delete,
            ["POST"],
            "Delete an emoji.",
        )
        self.context.register_web_api(
            f"{prefix}/maintenance",
            self.api_maintenance,
            ["POST"],
            "Run emoji maintenance.",
        )

    async def api_list_emojis(self) -> Any:
        query = request.args.get("q") or None
        status = request.args.get("status") or None
        limit = min(int(request.args.get("limit", "100")), 500)
        offset = max(int(request.args.get("offset", "0")), 0)
        page = offset // limit + 1
        records, total = self.repo.list(
            query=query or "",
            status=status or "",
            page=page,
            page_size=limit,
        )
        return jsonify(
            {
                "ok": True,
                "data": [record.to_dict() for record in records],
                "total": total,
            }
        )

    async def api_stats(self) -> Any:
        return jsonify({"ok": True, "data": self._stats_payload()})

    async def api_thumbnail(self, emoji_id: int) -> Any:
        record = self.repo.get_by_id(emoji_id)
        if not record:
            return jsonify({"ok": False, "error": "not found"}), 404
        thumbnail = self.storage.ensure_thumbnail(record)
        return await send_file(thumbnail)

    async def api_upload(self) -> Any:
        payload = await request.get_json(force=True)
        data_url = str(payload.get("data", ""))
        name = str(payload.get("name") or "upload.png")
        raw = self._decode_data_url(data_url)
        suffix = Path(name).suffix or ".png"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temp:
            temp.write(raw)
            temp_path = Path(temp.name)
        try:
            record = await self._register_image_path(temp_path, force_registered=True)
        finally:
            temp_path.unlink(missing_ok=True)
        return jsonify({"ok": True, "data": record.to_dict() if record else None})

    async def api_update(self, emoji_id: int) -> Any:
        payload = await request.get_json(force=True)
        updates: dict[str, Any] = {}
        if "description" in payload:
            updates["description"] = str(payload.get("description") or "")
        if "emotion_tags" in payload:
            tags = payload.get("emotion_tags")
            if isinstance(tags, str):
                tags = [item.strip() for item in tags.split(",") if item.strip()]
            updates["emotion_tags"] = tags or []
        if "is_registered" in payload:
            updates["is_registered"] = bool(payload.get("is_registered"))
        if "is_banned" in payload:
            updates["is_banned"] = bool(payload.get("is_banned"))
        self.repo.update(emoji_id, **updates)
        record = self.repo.get_by_id(emoji_id)
        return jsonify({"ok": True, "data": record.to_dict() if record else None})

    async def api_adopt(self, emoji_id: int) -> Any:
        record = self.repo.get_by_id(emoji_id)
        if not record:
            return jsonify({"ok": False, "error": "not found"}), 404
        await self._adopt_record(record)
        self.repo.upsert(record)
        return jsonify({"ok": True, "data": record.to_dict()})

    async def api_ban(self, emoji_id: int) -> Any:
        self.repo.update(emoji_id, is_banned=True, is_registered=False)
        record = self.repo.get_by_id(emoji_id)
        return jsonify({"ok": True, "data": record.to_dict() if record else None})

    async def api_delete(self, emoji_id: int) -> Any:
        record = self.repo.get_by_id(emoji_id)
        if not record:
            return jsonify({"ok": False, "error": "not found"}), 404
        self.storage.delete_files(record)
        self.repo.delete(emoji_id)
        return jsonify({"ok": True})

    async def api_maintenance(self) -> Any:
        removed = self.repo.cleanup_missing_files()
        return jsonify({"ok": True, "data": {"removed": removed}})

    def _stats_payload(self) -> dict[str, Any]:
        all_records: list[EmojiRecord] = []
        page = 1
        while True:
            records, _ = self.repo.list(page=page, page_size=200)
            if not records:
                break
            all_records.extend(records)
            page += 1
        return {
            "total": len(all_records),
            "registered": sum(1 for item in all_records if item.is_registered),
            "banned": sum(1 for item in all_records if item.is_banned),
            "max_registered": int(self.config.get("max_registered", 500)),
            "emotions": self.repo.emotions(),
        }

    def _cfg_bool(self, key: str, default: bool) -> bool:
        return bool(self.config.get(key, default))

    def _platform_allowed(self, event: AstrMessageEvent) -> bool:
        platforms = self.config.get("capture_platforms") or ["aiocqhttp"]
        normalized = {str(item).lower() for item in platforms}
        return event.get_platform_name().lower() in normalized

    def _session_allowed(self, umo: str) -> bool:
        allowed = [str(item).strip() for item in self.config.get("allowed_sessions", [])]
        return not allowed or umo in allowed

    def _message_id(self, event: AstrMessageEvent | None) -> str:
        if not event:
            return ""
        return str(getattr(event.message_obj, "message_id", "") or "")

    def _decode_data_url(self, value: str) -> bytes:
        if "," in value and value.startswith("data:"):
            value = value.split(",", 1)[1]
        try:
            return base64.b64decode(value, validate=True)
        except binascii.Error as exc:
            raise ValueError("invalid base64 image data") from exc

    def _help_text(self) -> str:
        return "\n".join(
            [
                "emoji random - 随机发送一个表情",
                "emoji search <关键词> - 搜索表情",
                "emoji stats - 查看表情库状态",
                "emoji adopt <id> - 领养表情",
                "emoji ban <id> - 禁用表情",
                "emoji reload - 清理缺失文件记录",
            ]
        )
