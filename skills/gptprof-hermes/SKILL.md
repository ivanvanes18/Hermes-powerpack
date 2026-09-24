---
name: gptprof-hermes
description: Manage ChatGPT profiles through Hermes and Telegram.
version: 1.1.0
author: Ivan Vanes and Hermes Powerpack
license: MIT
platforms: [linux, macos]
tags: [chatgpt, profiles, telegram, oauth]
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: gptprof
command-arg-mode: raw
---

# gptprof-hermes

Hermes-native skill for ChatGPT profile management: Telegram card with inline buttons showing **remaining %** per profile, `/gptt` / `/mmfast` quick aliases, and autoswitch when the active profile hits its limit.

## Prerequisites

- Run the helpers with the same Python environment as Hermes; it must provide `aiohttp`, PyYAML, and `python-telegram-bot`.
- Keep bootstrap profile files and `auth.json` in the active `HERMES_HOME`; never copy credentials into the skill directory.
- The bundled Powerpack environment already includes these dependencies. A standalone file copy without that environment is unsupported.

## Commands

| Slash | Action |
|-------|--------|
| `/gptprof` | Show profile selection card with inline buttons (remaining % 5h / weekly per button) |
| `/gptt` | Switch to `gpt-6-sol-900k` via `openai-codex` provider, **persistent** (`--global`) |
| `/mmfast` | Switch back to `MiniMax-M3` with high reasoning, **persistent** (`--global`) |
| `/gptprof autoswitch` | Run autoswitch logic (switches when active 5h or weekly remaining is ≤5%, or usage/auth error appears) |

## How the Card Works

1. `send_buttons.py` reads optional bootstrap profiles from `$HERMES_HCP/*.json` and overlays canonical `credential_pool.openai-codex` entries; a normal CredentialPool entry works even when no bootstrap directory exists.
2. Fetches usage from `https://chatgpt.com/backend-api/wham/usage` in parallel and labels windows from the API duration (for example `5h` or `Week`) instead of assuming both windows exist.
3. Computes **remaining %** = `100 − used_percent` for every available window.
4. When invoked as a Hermes quick command, sends the card to the invoking chat/topic via `HERMES_QUICK_CHAT_ID` / `HERMES_QUICK_THREAD_ID`; `$GPTPROF_CHAT_ID` is only the manual fallback.
5. Generic pool buttons use `gptprof:pool:<exact-id>:<model>`; legacy bootstrap buttons retain `gptprof:<slug>:<model>`. Ambiguous IDs/slugs fail closed and are not rendered.
6. Refresh/autoswitch callbacks preserve the source topic and never expose raw helper/provider errors.
7. After pressing a profile button → recommended `/new` to reset context.

## Callback Behavior (critical)

Button presses are handled by the **Hermes Telegram adapter** (`plugins/platforms/telegram/adapter.py`), not by `send_buttons.py`.

On `gptprof:<slug>:<model>` callback, the Telegram adapter:

1. Imports or selects `gptprof:<slug>` as an owned `manual:device_code` entry in `credential_pool.openai-codex`; an existing pool entry wins over stale bootstrap tokens, while every previously imported refresh-token fingerprint remains blocked after deletion or rollback
2. **Writes global config.yaml**:
   ```python
   cfg["model"] = {
       "default": model,         # e.g. "gpt-6-sol-900k"
       "provider": "openai-codex",
   }
   ```
   This is equivalent to `/model <model> --provider openai-codex --global`.
3. Recommends `/new` so the next session uses the persisted route

**Why this matters:** Without step 2, gateway restarts would reset the model back to the pre-switch default. With step 2, the model is persisted in `config.yaml` and survives restarts.

See `references/callback-behavior.md` for full details.

## Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `TELEGRAM_BOT_TOKEN` | env | Bot token for sending cards |
| `GPTPROF_CHAT_ID` | env | Manual fallback chat ID; Hermes quick-command invocations use their source chat/topic automatically |
| `HERMES_QUICK_CHAT_ID` | injected | Source chat for the current quick-command/callback invocation |
| `HERMES_QUICK_THREAD_ID` | injected | Source Telegram topic; General topic `1` is normalized to no `message_thread_id` |
| `HERMES_HCP` | `~/.hermes/gptprof/profiles` | Profile token directory |
| `GPTPROF_ACCESS_REFRESH_SKEW` | `172800` | Freshness comparison for the optional break-glass import |
| `GPTPROF_INTEL64_OPENCLAW_SYNC` | `0` | Break-glass import from OpenClaw; not the primary path |
| `GPTPROF_AUTOSWITCH_THRESHOLD` | `5` | Switch when either 5h or weekly remaining is at/below this % |
| `GPTPROF_REFRESH_LOCK` | `$HERMES_HOME/run/gptprof-token-refresh.lock` | Hardened refresh lock |
| `GPTPROF_AUTOSWITCH_LOCK` | `$HERMES_HOME/run/gptprof-autoswitch.lock` | Hardened autoswitch lock |
| `GPTPROF_AUTOSWITCH_STATE` | `$HERMES_HOME/gptprof/autoswitch-state.json` | Atomic last-switch / no-candidate state |

## Profile Token Directory

Tokens live in `$HERMES_HCP/*.json`, one file per profile slug:

```
~/.hermes/gptprof/profiles/
├── profile1.json
├── profile2.json
└── profile3.json
```

Each file must initially contain a complete OAuth token pair. After import, the file is bootstrap/catalog data only: current tokens and refresh state live in Hermes CredentialPool. Hermes records the history of non-secret refresh-token fingerprints under `gptprof.imported_profiles`; deleting a pool entry or rolling the file back cannot resurrect any already-consumed bootstrap chain. Re-authentication is accepted when the bootstrap refresh token has never been imported before. Auth and config always use the canonical `$HERMES_HOME` paths. The active slug is stored under `gptprof.active_profile`.

## Local Token Refresh

Hermes CredentialPool is the only local owner of OAuth rotation. The helper delegates to the stable pool implementation and never writes rotated tokens back to bootstrap profile files:

```bash
/path/to/hermes/.venv/bin/python ~/.local/bin/gptprof_refresh_profiles.py
/path/to/hermes/.venv/bin/python ~/.local/bin/gptprof_refresh_profiles.py --force
```

Recommended systemd timer:

```ini
# ~/.config/systemd/user/gptprof-token-refresh.service
[Service]
Type=oneshot
Environment=GPTPROF_INTEL64_OPENCLAW_SYNC=0
ExecStart=/path/to/hermes/.venv/bin/python %h/.local/bin/gptprof_refresh_profiles.py
```

```ini
# ~/.config/systemd/user/gptprof-token-refresh.timer
[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=15min
Persistent=true
Unit=gptprof-token-refresh.service

[Install]
WantedBy=timers.target
```

`refresh_token_reused` means another process already consumed that single-use chain. Stop the other refresher and re-authenticate that profile before importing it again.

## Autoswitch cron

Run the autoswitch script every 5 minutes if you want automatic profile rotation before a limit is exhausted:

```bash
*/5 * * * * /path/to/hermes/.venv/bin/python ~/.local/bin/gptprof_autoswitch.py
```

Behavior:

- checks active profile usage for both `primary_window` (5h) and `secondary_window` (weekly);
- switches when either remaining window is `<= GPTPROF_AUTOSWITCH_THRESHOLD` (default `5`);
- also switches if the active profile returns a usage/auth error;
- chooses the healthiest available profile by highest `min(5h_left, week_left)`;
- switches **auth only** and does not change the current model/provider route;
- stays silent on no-op, printing stdout only on a switch or when no healthy alternative exists.

## config.yaml quick_commands Setup

```yaml
agent:
  reasoning_overrides:
    MiniMax-M3: high

quick_commands:
  gptt:
    type: alias
    target: /model gpt-6-sol-900k --provider openai-codex --global
  mmfast:
    type: alias
    target: /model MiniMax-M3 --provider minimax --global
  gptprof:
    type: exec
    command: /path/to/hermes/.venv/bin/python ~/.local/bin/gptprof_send_buttons.py
```

The per-model override makes `/mmfast` select high reasoning without changing the reasoning level used by other models. **Both aliases use `--global`** — this is what makes them survive gateway restarts. Without `--global`, the switch is session-only and resets on restart.

## Installation

```bash
# Use the skill bundled with the exact Hermes candidate.
SKILL_DIR=/path/to/hermes/skills/gptprof-hermes
HERMES_PYTHON=/path/to/hermes/.venv/bin/python

# Prove the selected interpreter has the required runtime dependencies.
"$HERMES_PYTHON" -c 'import aiohttp, telegram, yaml'

# Binaries
install -m 700 "$SKILL_DIR/bin/send_buttons.py"       ~/.local/bin/gptprof_send_buttons.py
install -m 700 "$SKILL_DIR/bin/refresh_profiles.py"   ~/.local/bin/gptprof_refresh_profiles.py
install -m 700 "$SKILL_DIR/bin/gptprof_autoswitch.py" ~/.local/bin/gptprof_autoswitch.py
install -m 700 "$SKILL_DIR/bin/codex-profile-manager.py" ~/.local/bin/codex-profile-manager.py

# Autoswitch imports send_buttons.py by its sibling filename. When binaries are
# renamed with a gptprof_ prefix, also install this private companion copy.
install -m 700 "$SKILL_DIR/bin/send_buttons.py" ~/.local/bin/send_buttons.py

# For a system-level multi-user autoswitch timer, set PrivateTmp=true in its
# oneshot service: the usage cache currently uses /tmp/gptprof_usage_cache.json,
# so a shared /tmp can collide with another Unix user's mode-0600 cache.

# Optional manual fallback target. Hermes `/gptprof` quick-command calls
# automatically route to the invoking chat/topic.
GPTPROF_CHAT_ID=<numeric-chat-id>

# Add quick_commands to config.yaml (see above)

# Restart gateway
/restart

# Verify no secrets
bash "$SKILL_DIR/tests/smoke.sh"
```

## Security Notes

- Zero secrets in this repo — all tokens are local to the user's machine
- Bootstrap profile files and `auth.json` must remain local and mode `0600`
- Run `bash tests/smoke.sh` to confirm no tokens were accidentally committed

## Upstream

The card and usage concepts originated in [evgyur/gptprof-public](https://github.com/evgyur/gptprof-public). Hermes runtime state and token rotation use the upstream CredentialPool implementation.

## Output Contract

When this skill is invoked, return one of:

- a rendered Telegram profile card from `bin/send_buttons.py`;
- a concise refresh/autoswitch result from `bin/refresh_profiles.py` or `bin/gptprof_autoswitch.py`;
- a setup/configuration checklist that keeps all OAuth tokens and Telegram tokens outside git.

Never print `access_token`, `refresh_token`, Telegram bot tokens, or raw `auth.json` contents in chat output.

## Quick Test Checklist

Before publishing changes:

- [ ] `/path/to/hermes/.venv/bin/python -m py_compile bin/*.py` passes.
- [ ] `node --check plugin/index.js` passes when Node is available.
- [ ] `bash tests/smoke.sh` passes.
- [ ] Public hygiene scan finds no private operator names, chat IDs, host paths, or real-looking tokens.
- [ ] Any profile examples use placeholders such as `profile1`, not real account slugs or emails.

## Done Criteria

- [ ] Runtime scripts use canonical `$HERMES_HOME` auth/config state and portable env overrides only for non-canonical inputs (`HERMES_HCP`, `GPTPROF_CHAT_ID`).
- [ ] Public docs describe a generic install, not a private deployment.
- [ ] Smoke tests include both syntax checks and public-hygiene checks.
- [ ] Repository has no committed OAuth tokens, bot tokens, private keys, personal chat IDs, or private host paths.
