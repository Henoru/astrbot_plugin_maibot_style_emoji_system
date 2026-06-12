# Changelog

## 0.2.0

- Reworked emoji sending selection to avoid VLM image-grid selection.
- Added optional embedding-based candidate ranking for emoji descriptions and tags.
- Added `selector_provider_id` for text-only LLM selection during sending.
- Added persistent SQLite embedding cache with lazy migration and cache invalidation on metadata updates.
- Kept image VLM usage limited to the existing ingestion audit/description flow.
