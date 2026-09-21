# telegram-chip runtime API

This reference describes a minimal public-safe Telegram runtime API for the `telegram-chip` skill. It is a contract, not a bundled service.

## Endpoints

- `GET /health` — service status.
- `GET /me` — authenticated Telegram account identity, without secrets.
- `GET /chats/{chat_id}/messages?page=&page_size=` — recent messages.
- `GET /chats/{chat_id}/messages/{message_id}` — exact message with reply/entity metadata when available.
- `GET /chats/{chat_id}/messages/{message_id}/media?output_path=` — download message media to a local path.
- `POST /messages/send` — send a message after explicit target clarity.

## Credential boundary

Never commit Telegram sessions, API IDs/hashes, phone numbers, tokens, cookies, `.env`, or exported private chat history. Keep all runtime state outside the repository.
