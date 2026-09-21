---
name: telegram-chip
description: "Operate a user-owned Telegram/Telethon runtime safely: read chats, recover media, export history, and send messages only with explicit target clarity. Public-safe version for Hermes Powerpack."
version: 1.0.0
author: Hermes Powerpack
license: MIT
metadata:
  hermes:
    tags: [telegram, telethon, userbot, media, export, messaging, safety]
    related_skills: [hermes-agent]
---

# telegram-chip

Use this skill when Hermes needs Telegram access that the bot adapter cannot provide:

- read recent or historical Telegram messages through an existing user-owned Telethon service;
- recover reply chains, hidden text-link entities, albums, media, audio, video, or documents;
- export chat history for local analysis;
- send, edit, delete, or forward messages only after the user has given an exact target.

This public skill is intentionally generic. It does **not** ship Telegram sessions, phone numbers, API IDs/hashes, chat IDs, tokens, private hostnames, or organization-specific routes.

## Safety rules

1. Use an already-configured local Telegram runtime. Do not create or copy a second Telethon session unless the user is explicitly setting up a new one.
2. Never print, commit, or copy `.session` files, session strings, API hashes, phone numbers, cookies, or bot tokens.
3. For reads, fetch exact Telegram context before asking the user to resend: current message, replied message, nearby messages, entities, and media.
4. For writes, require exact target clarity: chat/user/channel, topic when relevant, message id when editing/deleting, and final text/media.
5. After a write, fetch back the sent/edited message and report the message id. If fetch-back fails, say so plainly.
6. Public channels, mass sends, deletions, and sensitive chats require explicit approval immediately before action.
7. Treat exported chat history as private data. Keep it local, redact it in reports, and delete it when the task no longer needs it.

## Expected local runtime contract

Powerpack does not prescribe one implementation. Any local service is fine if it exposes a narrow API like:

```text
GET  /health
GET  /me
GET  /chats/{chat_id}/messages?page=1&page_size=20
GET  /chats/{chat_id}/messages/{message_id}
GET  /chats/{chat_id}/messages/{message_id}/media?output_path=...
POST /messages/send
POST /messages/edit
POST /messages/delete
```

Keep credentials in environment variables or a local secret store outside git. Example names only:

```bash
TELEGRAM_API_ID=...
TELEGRAM_API_HASH=...
TELEGRAM_SESSION_PATH=~/.local/share/telegram-chip/user.session
TELEGRAM_CHIP_BASE_URL=http://127.0.0.1:8080
```

## Read recipe

```bash
BASE=${TELEGRAM_CHIP_BASE_URL:-http://127.0.0.1:8080}
curl -fsS "$BASE/health"
curl -fsS "$BASE/me"
curl -fsS "$BASE/chats/$CHAT_ID/messages?page=1&page_size=20"
```

## Media recovery recipe

```bash
BASE=${TELEGRAM_CHIP_BASE_URL:-http://127.0.0.1:8080}
OUT=${OUT:-$PWD/telegram-media.bin}
curl -fsS "$BASE/chats/$CHAT_ID/messages/$MESSAGE_ID/media?output_path=$OUT"
ls -lh "$OUT"
```

Prefer a task-specific output path over shared temp paths. Shared temp paths cause stale-file and symlink bugs.

## Send recipe

Only after exact target clarity:

```bash
BASE=${TELEGRAM_CHIP_BASE_URL:-http://127.0.0.1:8080}
python3 - <<'PY'
import json, os, urllib.request
base = os.environ.get('TELEGRAM_CHIP_BASE_URL', 'http://127.0.0.1:8080')
payload = {
    'chat_id': os.environ['CHAT_ID'],
    'message': os.environ['MESSAGE'],
    'parse_mode': None,
}
req = urllib.request.Request(
    base + '/messages/send',
    data=json.dumps(payload, ensure_ascii=False).encode(),
    headers={'Content-Type': 'application/json'},
    method='POST',
)
print(urllib.request.urlopen(req, timeout=20).read().decode())
PY
```

Then fetch back the returned message id.

## Telegram link parsing

- `https://t.me/c/<internal_id>/<message_id>` → chat id `-100<internal_id>`, message id final segment.
- `https://t.me/c/<internal_id>/<topic_id>/<message_id>` → chat id `-100<internal_id>`, topic root often `<topic_id>`, message id final segment.
- Public `https://t.me/<channel>/<message_id>` links may be readable through a public web fallback if the local Telegram runtime cannot access the channel.

## Output contract

When completing a Telegram task, report compactly:

- runtime used: local API, MCP, direct script, or browser fallback;
- target: chat/topic/message id, redacted if sensitive;
- action: endpoint or command;
- proof: health, identity, sent message id, export path, media path + size, or exact error;
- privacy note: what was kept local or deleted.
