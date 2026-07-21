# gptprof-hermes

**Public Hermes skill** для управления ChatGPT-профилями через Telegram-кнопки, `/gptt` / `/mmfast` алиасы, и автоматическое переключение при исчерпании лимита.

![gptprof Telegram profile switcher UI](assets/gptprof-telegram-status.jpg)

---

## 🇷🇺 Русский

### Зачем этот скилл

`gptprof-hermes` — Hermes-нативная карточка профилей на базе идей [gptprof-public](https://github.com/evgyur/gptprof-public). Токены после импорта принадлежат штатному `CredentialPool` Hermes.

### Возможности

| Команда | Что делает |
|---------|-----------|
| `/gptprof` | Карточка профиля с кнопками (остаток % 5ч / нед) |
| `/gptt` | Быстрый переход на `gpt-5.5` через Codex, **persistent** (`--global`) |
| `/mmfast` | Переключает обратно на MiniMax-M2.7 (high reasoning), **persistent** (`--global`) |
| Autoswitch | Автоматически переезжает на самый здоровый профиль, если у текущего 5ч или неделя ≤5% остатка |

### Установка

```bash
# 1. Склонировать репозиторий
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes

# 2. Скопировать бинарники
install -m 700 bin/send_buttons.py       ~/.local/bin/send_buttons.py
install -m 700 bin/send_buttons.py       ~/.local/bin/gptprof_send_buttons.py
install -m 700 bin/refresh_profiles.py   ~/.local/bin/gptprof_refresh_profiles.py
install -m 700 bin/gptprof_autoswitch.py ~/.local/bin/gptprof_autoswitch.py

# 3. Добавить quick_commands в config.yaml (см. ниже)

# 4. /restart — чтобы gateway подхватил новые команды
```

### config.yaml (quick_commands)

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

**Важно:** `--global` в обоих алиасах. Без него модель сбрасывается после рестарта gateway.

### Как работает отображение %

```
5ч остаток  = 100 − primary_window.used_percent
нед остаток = 100 − secondary_window.used_percent
```

Данные берутся из `https://chatgpt.com/backend-api/wham/usage` с кешированием на 15 минут.

### Autoswitch

```bash
*/5 * * * * /opt/hermes-agent/venv/bin/python3 ~/.local/bin/gptprof_autoswitch.py
```

Скрипт проверяет 5-часовое и недельное окно активного профиля. Если любое окно дошло до `≤5%` остатка или профиль вернул usage/auth error, он переключает **только OAuth-профиль** на самый здоровый доступный аккаунт. Модель/provider не меняются.

### Callback (нажатие кнопки профиля)

**Это происходит на уровне Hermes gateway**, а не в `send_buttons.py`.

При нажатии кнопки `gptprof:<slug>:<model>` Telegram adapter Hermes (`plugins/platforms/telegram/adapter.py`) выполняет:

1. Импортирует или выбирает `gptprof:<slug>` в `credential_pool.openai-codex` с источником `manual:device_code`; уже существующая запись пула имеет приоритет над bootstrap-файлом
2. **Пишет глобальный config**: `model=<model>`, `provider=openai-codex` в `config.yaml` — эквивалент `/model <model> --provider openai-codex --global`
3. Рекомендует `/new`, чтобы новая сессия разрешила сохранённый маршрут и профиль вместе

Таким образом после нажатия кнопки профиля:
- Gateway restart **не сбросит** модель обратно (config.yaml записан)
- Новая сессия использует выбранные маршрут и профиль после `/new`

### Настройка профилей

Скилл использует bootstrap-каталог профилей в `$HERMES_HCP`:

```
~/.hermes/gptprof/profiles/
├── profile1.json    ← access_token профиля
├── profile2.json
└── profile3.json
```

При первом импорте каждый JSON содержит OAuth-пару профиля:

```json
{
  "access_token": "<OAUTH_TOKEN>",
  "expires_at": 1750000000,
  "refresh_token": "<REFRESH_TOKEN>",
  "email": "profile@example.com"
}
```

После импорта актуальные токены и refresh-state хранятся только в штатном CredentialPool. В `gptprof.imported_profiles` сохраняется история несекретных отпечатков bootstrap refresh token: удаление записи пула или откат файла не может повторно импортировать любую уже израсходованную цепочку. Новая авторизация принимается, только если такой refresh token ещё не импортировался. Auth и config всегда читаются из канонического `$HERMES_HOME`. Активный профиль определяется по `auth.json`:

```json
{
  "gptprof": {
    "active_profile": "profile3"
  }
}
```

### Локальное обновление OAuth-токенов

`send_buttons.py` не обновляет OAuth-токены. Единственный владелец rotation lifecycle — штатный CredentialPool Hermes; timer только вызывает его:

```ini
# /etc/systemd/system/gptprof-token-refresh.service
[Unit]
Description=Refresh Hermes gptprof Codex OAuth tokens
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=hermes
WorkingDirectory=/home/hermes/gptprof-hermes
Environment=GPTPROF_INTEL64_OPENCLAW_SYNC=0
ExecStart=<venv-python> <runtime-home>/.local/bin/gptprof_refresh_profiles.py
```

```ini
# /etc/systemd/system/gptprof-token-refresh.timer
[Unit]
Description=Run Hermes gptprof Codex OAuth token refresh periodically

[Timer]
OnBootSec=5min
OnUnitActiveSec=6h
RandomizedDelaySec=15min
Persistent=true
Unit=gptprof-token-refresh.service

[Install]
WantedBy=timers.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gptprof-token-refresh.timer
sudo systemctl start gptprof-token-refresh.service
```

`GPTPROF_INTEL64_OPENCLAW_SYNC=1` оставлен только как одноразовый break-glass импорт. Не держите два активных refresher-процесса на одной single-use цепочке.

Refresh-lock хранится в `$HERMES_HOME/run/gptprof-token-refresh.lock`; autoswitch-lock и state — в `$HERMES_HOME/run/gptprof-autoswitch.lock` и `$HERMES_HOME/gptprof/autoswitch-state.json`. Lock-файлы не следуют по симлинкам, state записывается атомарно.

### Autoswitch (автопереключение)

Штатный helper автоматически переключает профиль, если остаток активного достиг 5% по любому окну:

```bash
python3 ~/.local/bin/gptprof_autoswitch.py
```

Логика: если `active.5h_used >= 95%` ИЛИ `active.weekly_used >= 95%`, и есть простой кандидат с остатком >5% по обоим окнам — переезжаем на него.

### Безопасность

- **Никаких токенов в репозитории** — всё локально
- OAuth client ID — публичные метаданные приложения OpenAI, не секрет
- Smoke-тест проверяет паттерны секретов при пуше

```bash
bash tests/smoke.sh
```

---

## 🇬🇧 English

### What This Is

`gptprof-hermes` is a public Hermes profile card based on ideas from [gptprof-public](https://github.com/evgyur/gptprof-public). After bootstrap import, Hermes CredentialPool owns runtime tokens and refresh rotation.

### Quick Start

```bash
git clone https://github.com/evgyur/gptprof-hermes.git ~/gptprof-hermes
install -m 700 bin/send_buttons.py       ~/.local/bin/send_buttons.py
install -m 700 bin/send_buttons.py       ~/.local/bin/gptprof_send_buttons.py
install -m 700 bin/refresh_profiles.py   ~/.local/bin/gptprof_refresh_profiles.py
install -m 700 bin/gptprof_autoswitch.py ~/.local/bin/gptprof_autoswitch.py
```

Add to `config.yaml` → `quick_commands`:

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

Then `/restart` the gateway to pick up new commands.

### Callback Behavior

Button presses (`gptprof:<slug>:<model>`) are handled by the Hermes Telegram adapter (`plugins/platforms/telegram/adapter.py`). On callback:

1. Imports or selects the owned `gptprof:<slug>` entry in `credential_pool.openai-codex`; existing pool tokens take precedence, and an already-consumed bootstrap chain is rejected until re-authentication changes its refresh token
2. **Writes global config**: `model=<model>`, `provider=openai-codex` to `config.yaml` (equivalent to `/model <model> --provider openai-codex --global`)
3. Recommends `/new` so the next session uses the persisted route

This means after pressing a profile button, gateway restarts do **not** reset the model back — the change persists in `config.yaml`.

### How Usage % Is Calculated

```
5h remaining   = 100 − primary_window.used_percent
weekly remain  = 100 − secondary_window.used_percent
```

Fetched from `https://chatgpt.com/backend-api/wham/usage` with 15-minute local cache.

### Security

- **Zero secrets in repo** — all tokens are local
- OAuth client ID is public OpenAI app metadata, not a client secret
- Run `bash tests/smoke.sh` to verify no secrets are committed

### Repository Structure

```
gptprof-hermes/
├── bin/
│   ├── refresh_profiles.py        # delegates OAuth rotation to CredentialPool
│   ├── gptprof_autoswitch.py      # quota-based pool selection
│   └── send_buttons.py            # Hermes-native card sender
├── plugin/
│   ├── index.js                   # OpenClaw plugin bridge (stub)
│   ├── openclaw.plugin.json        # Plugin manifest
│   └── package.json
├── references/
│   └── callback-behavior.md        # details on button callback handling
├── tests/
│   └── smoke.sh                    # syntax + secret-pattern test
├── assets/
│   └── gptprof-telegram-status.jpg
├── README.md
└── SKILL.md
```

---

## Команды / Commands

| Команда | Описание |
|---------|----------|
| `/gptprof` | Показать карточку с кнопками и остатком % |
| `/gptt` | Перейти на gpt-5.5 (Codex route), persistent |
| `/mmfast` | Вернуться на MiniMax-M2.7, persistent |
| `gptprof_refresh_profiles.py` | CLI: делегировать refresh штатному CredentialPool |
| `gptprof_autoswitch.py` | CLI: автопереключение при исчерпании |
