# YouTube and TikTok publishing

Use `scripts.social_release prepare` and require a probe receipt containing both audio
and video. Before uploading, require explicit artist approval of platform, metadata,
privacy, and exact file. Upload privately by default.

- YouTube: use resumable `videos.insert` with `youtube.upload`, save the video ID, and
  inspect processing status. An unaudited project may be private-only.
- TikTok: query creator info first, enforce its privacy/duration constraints, upload
  valid chunks, save `publish_id`, and fetch status. Do not add watermarks.
- Never call a plan an upload. A provider ID proves upload; a successful provider
  status proves readiness; artist approval is required before public visibility.
