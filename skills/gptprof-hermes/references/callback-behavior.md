# Callback Behavior — gptprof-hermes

## Overview

When a user presses a profile button in the Telegram card, the callback `gptprof:<slug>:<model>` is handled by **Hermes gateway**, not by `send_buttons.py`.

The gateway code lives in:
```
hermes-agent/plugins/platforms/telegram/adapter.py
  → _handle_gptprof_callback(query, profile, model)
```

## Callback Data Format

**Old format (broken):** `gptprof:profile3`
**Current format (correct):** `gptprof:profile3:gpt-5.5`

The `:model` suffix is required — without it, the gateway cannot determine which GPT model to switch to.

## send_buttons.py Callback Data

The card must use:

```python
callback_data=f"gptprof:{slug}:{model}"
```

Where `model` is a Hermes/OpenAI-Codex model such as `gpt-5.5`, `gpt-5.4`, or `gpt-5.4-mini`.

## Gateway Handler Flow

When `callback_data = "gptprof:profile3:gpt-5.5"` is received:

```python
parts = data.split(":")
# ["gptprof", "profile3", "gpt-5.5"]
_, profile, model = parts[:3]
await _handle_gptprof_callback(query, profile, model)
```

Inside `_handle_gptprof_callback`:

1. **Select the pool entry** — imports `gptprof:<profile>` as an owned `manual:device_code` credential on first use, then prefers current CredentialPool tokens. The stored fingerprint history prevents any previously imported bootstrap chain from being resurrected; only a refresh token absent from that history is treated as explicit re-authentication.

2. **Write global config.yaml** (critical step):
   ```python
   cfg["model"] = {
       "default": model,         # "gpt-5.5"
       "provider": "openai-codex",
   }
   save_config(cfg, preserve_keys={("model", "default"), ("model", "provider")})
   ```
   This persists the model switch across gateway restarts.

3. **Confirm to user** — shows an alert and recommends `/new` so the next session resolves the persisted route.
   ```
   ✅ Профиль активирован
   `profile3` (Plus)
   Модель: `gpt-5.5`
   ✅ Сохранено глобально в `config.yaml`.
   Нажми `/new` для новой сессии с выбранным GPT.
   ```

## Why Global Config Write Matters

Without step 2, the model switch would only survive until the next gateway restart. After restart, Hermes would read `config.yaml` → `model: minimax` (or whatever the default is) and reset the model.

With step 2, `config.yaml` now contains:
```yaml
model:
  default: gpt-5.5
  provider: openai-codex
```

So after any restart, Hermes starts with the selected GPT model, not the old default.

## Session vs Global Distinction

| Action | Survives Restart? |
|--------|------------------|
| `config.yaml` write (step 2) | ✅ — persists |
| CredentialPool selection (step 1) | ✅ — pool state is stored in `auth.json` and survives restarts |

The callback intentionally does not mutate an already-running session. Use `/new` after switching so route and credentials are resolved together from persisted state.

## Gateway Restart Caveat

The user should `/new` for a clean session after pressing a button. A gateway restart is not required for the persisted route itself.

## Related Files

- `plugins/platforms/telegram/adapter.py` — `_handle_gptprof_callback`
- `/opt/hermes-agent/gateway/run.py` — `/model --global` persistence logic
- `bin/send_buttons.py` — upstream card sender (callback_data format only)
