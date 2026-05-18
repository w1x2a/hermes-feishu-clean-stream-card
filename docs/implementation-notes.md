# Implementation notes

## Design

- Keep Feishu card state/rendering isolated in `gateway/feishu_stream_card.py`.
- Keep user-facing progress cleanup isolated in `gateway/progress_sanitizer.py`.
- Route Feishu progress events into card updates instead of sending raw progress bubbles.
- Preserve non-Feishu platform behavior; do not globally sanitize Telegram/Discord/Weixin progress.

## Known pitfalls

- Feishu interactive card messages should not be updated through the normal IM message update endpoint with `msg_type="interactive"`; CardKit update paths may be required depending on SDK/API version.
- A progress card final edit is not equivalent to final text delivery. Do not suppress the normal final answer solely because the card was finalized.
- Heartbeat/start notifications should be queued into the card; otherwise the card can look frozen during long model waits.
- Always distinguish source/test state from live Gateway reload state.
