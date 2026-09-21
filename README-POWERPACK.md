# Hermes Powerpack

A public, batteries-included Hermes Agent distribution for workshops and advanced users.

This repository starts from upstream [`NousResearch/hermes-agent`](https://github.com/NousResearch/hermes-agent) and adds a small public-safe Powerpack layer:

- decision and reasoning workflows;
- rigorous execution / superpowers workflow;
- presentation/document generation skill;
- PiAPI video toolkit skill;
- public-safe `telegram-chip` skill contract for user-owned Telegram runtimes;
- optional public `gptprof-hermes` profile switcher (`/gptprof`, `/gptt`, `/mmfast`) with Telegram callback handling;
- install wrapper for workshop use.

No private organization infrastructure, chat IDs, sessions, secrets, local paths, or runtime artifacts are intentionally included.

## Quick install

```bash
git clone https://github.com/evgyur/Hermes-powerpack.git
cd Hermes-powerpack
bash scripts/install-powerpack.sh
hermes setup
hermes doctor
```

## Smoke test

```bash
hermes chat -q "Say hello and list available built-in Powerpack skills"
```

## Optional GPT profile switcher

`install-powerpack.sh` installs public `gptprof-hermes` helpers and adds three quick commands without overwriting existing user commands:

- `/gptprof` — sends a Telegram profile card with inline buttons;
- `/gptt` — switches globally to `openai-codex/gpt-5.5`;
- `/mmfast` — switches globally to MiniMax fast mode.

To use `/gptprof`, configure your own local values outside git:

```bash
cat >> ~/.hermes/.env <<'EOF'
GPTPROF_CHAT_ID=123456789
EOF
mkdir -p ~/.hermes/gptprof/profiles
```

Profile JSON files belong in `~/.hermes/gptprof/profiles/<slug>.json` and must stay local.

## Telegram runtime

The bundled `telegram-chip` skill is a **safe operating contract**, not a session dump. Configure your own local Telegram runtime and keep credentials outside git:

```bash
export TELEGRAM_CHIP_BASE_URL=http://127.0.0.1:8080
```

Do not commit `.session`, `.env`, phone numbers, API IDs/hashes, bot tokens, exported chats, or media dumps.

## What this is not

- not a private operator distribution;
- not a repository of private skills or production routes;
- not a place for credentials or Telegram sessions.
## Workshop skills

See [WORKSHOP-SKILLS.md](WORKSHOP-SKILLS.md) for the public-safe skill bundle from the first three Codex + Hermes workshop lessons.

