---
name: gptprof-hermes
description: "Public Hermes skill: ChatGPT profile card with Telegram inline buttons, stable CredentialPool ownership, persistent /gptt /mmfast aliases, and autoswitch on quota exhaustion."
user-invocable: true
disable-model-invocation: true
command-dispatch: tool
command-tool: gptprof
command-arg-mode: raw
---

# gptprof-hermes

Hermes-native skill for ChatGPT profile management: Telegram card with inline buttons showing **remaining %** per profile, `/gptt` / `/mmfast` quick aliases, and autoswitch when the active profile hits its limit.

## Commands

| Slash | Action |
|-------|--------|
| `/gptprof` | Show profile selection card with inline buttons (remaining % 5h / weekly per button) |
| `/gptt` | Switch to `gpt-5.5` via `openai-codex` provider, **persistent** (`--global`) |
| `/mmfast` | Switch back to `MiniMax-M2.7` with high reasoning, **persistent** (`--global`) |
| `/gptprof autoswitch` | Run autoswitch logic (switches when active 5h or weekly remaining is ≤5%, or usage/auth error appears) |

## How the Card Works

1. `send_buttons.py` reads the bootstrap profile catalog from `$HERMES_HCP/*.json`
2. Overlays current tokens from Hermes `credential_pool.openai-codex`; the pool is canonical after first import
3. Fetches usage from `https://chatgpt.com/backend-api/wham/usage` for each profile in parallel
4. Computes **remaining %** = `100 − used_percent` for both windows
5. Sends a Telegram `InlineKeyboardMarkup` card to `$GPTPROF_CHAT_ID`
6. Each button carries `callback_data: "gptprof:<slug>:<model>"` (e.g. `gptprof:profile3:gpt-5.5`)
7. After pressing a button → recommended `/new` to reset context

## Callback Behavior (critical)

Button presses are handled by the **Hermes Telegram adapter** (`plugins/platforms/telegram/adapter.py`), not by `send_buttons.py`.

On `gptprof:<slug>:<model>` callback, the Telegram adapter:

1. Imports or selects `gptprof:<slug>` as an owned `manual:device_code` entry in `credential_pool.openai-codex`; an existing pool entry wins over stale bootstrap tokens, while every previously imported refresh-token fingerprint remains blocked after deletion or rollback
2. **Writes global config.yaml**:
   ```python
   cfg["model"] = {
       "default": model,         # e.g. "gpt-5.5"
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
| `GPTPROF_CHAT_ID` | env | Telegram chat ID for card delivery |
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
python3 ~/.local/bin/gptprof_refresh_profiles.py
python3 ~/.local/bin/gptprof_refresh_profiles.py --force
```

Recommended systemd timer:

```ini
# /etc/systemd/system/gptprof-token-refresh.service
[Service]
Type=oneshot
User=hermes
Environment=GPTPROF_INTEL64_OPENCLAW_SYNC=0
ExecStart=<venv-python> <runtime-home>/.local/bin/gptprof_refresh_profiles.py
```

```ini
# /etc/systemd/system/gptprof-token-refresh.timer
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
*/5 * * * * /opt/hermes-agent/venv/bin/python3 ~/.local/bin/gptprof_autoswitch.py
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
quick_commands:
  gptt:
    type: alias
    target: /model gpt-5.5 --provider openai-codex --global
  mmfast:
    type: alias
    target: /model MiniMax-M2.7 --provider minimax --global
  gptprof:
    type: exec
    command: /opt/hermes-agent/venv/bin/python3 ~/.local/bin/send_buttons.py
```

**Both aliases use `--global`** — this is what makes them survive gateway restarts. Without `--global`, the switch is session-only and resets on restart.

## Installation

```bash
# Clone
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes

# Binaries
install -m 700 bin/send_buttons.py       ~/.local/bin/send_buttons.py
install -m 700 bin/send_buttons.py       ~/.local/bin/gptprof_send_buttons.py
install -m 700 bin/refresh_profiles.py   ~/.local/bin/gptprof_refresh_profiles.py
install -m 700 bin/gptprof_autoswitch.py ~/.local/bin/gptprof_autoswitch.py

# Add quick_commands to config.yaml (see above)

# Restart gateway
/restart

# Verify no secrets
bash ~/gptprof-hermes/tests/smoke.sh
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

- [ ] `python3 -m py_compile bin/*.py` passes.
- [ ] `node --check plugin/index.js` passes when Node is available.
- [ ] `bash tests/smoke.sh` passes.
- [ ] Public hygiene scan finds no private operator names, chat IDs, host paths, or real-looking tokens.
- [ ] Any profile examples use placeholders such as `profile1`, not real account slugs or emails.

## Done Criteria

- [ ] Runtime scripts use canonical `$HERMES_HOME` auth/config state and portable env overrides only for non-canonical inputs (`HERMES_HCP`, `GPTPROF_CHAT_ID`).
- [ ] Public docs describe a generic install, not a private deployment.
- [ ] Smoke tests include both syntax checks and public-hygiene checks.
- [ ] Repository has no committed OAuth tokens, bot tokens, private keys, personal chat IDs, or private host paths.
