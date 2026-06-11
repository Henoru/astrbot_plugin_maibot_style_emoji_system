# AstrBot MaiBot Style Emoji System

MaiBot EmojiSystem 的 AstrBot 插件化实现原型，用于自动采集、识别、管理并按情绪发送图片表情包。

## 功能范围

- 自动监听图片消息，并按平台、会话白名单过滤采集来源。
- 将图片保存到 `data/plugin_data/astrbot_plugin_maibot_style_emoji_system/emojis`，元数据写入 SQLite。
- 调用 AstrBot 当前或指定的 LLM/VLM Provider 进行内容审核、描述和情绪标签生成。
- 达到注册容量上限时，请模型判断是否替换旧表情。
- 提供 `send_emoji` LLM 工具，让 Agent 根据情绪和上下文发送本地表情。
- 提供 `emoji` 命令组：`random`、`search`、`stats`、`adopt`、`ban`、`reload`。
- 提供插件管理页 `manager`，支持上传、搜索、编辑、领养、禁用、删除和维护。

## 数据目录

插件数据保存在 AstrBot 数据目录下：

```text
data/plugin_data/astrbot_plugin_maibot_style_emoji_system/
  emoji.db
  emojis/
  thumbnails/
```

## 配置要点

- `enabled`: 是否启用插件。
- `capture_platforms`: 自动采集的平台，默认只启用 `aiocqhttp`。
- `allowed_sessions`: 为空时采集所有会话；填写统一会话 ID 后只采集指定会话。
- `provider_id`: 为空时使用会话当前 Provider；需要 VLM 能力时建议指定支持图片输入的 Provider。
- `audit_enabled`: 是否启用内容审核。
- `max_registered`: 可被自动发送的表情容量。
- `sample_size` / `grid_columns`: 发送选择时构建候选拼图的规模。

## 迁移 MaiBot EmojiSystem

MaiBot 原始 EmojiSystem 包含表情文件库、数据库元信息、VLM 描述/审核、容量替换、WebUI 管理和 `send_emoji` 工具。本插件将这些能力拆到 AstrBot 的插件生命周期、Provider、消息组件、LLM Tool 和 Plugin Pages 上，避免修改 AstrBot 核心。

仍可继续补齐的兼容点：

- 将 MaiBot 现有 `data/emoji` 和图片数据库导入为本插件 SQLite 结构。
- 增强对 MaiBot 原有 prompt 的兼容和多语言配置。
- 增加批量导入、批量标注和导出功能。
