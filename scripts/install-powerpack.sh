#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"

"$PYTHON_BIN" -m venv "$VENV_DIR"
# shellcheck source=/dev/null
source "$VENV_DIR/bin/activate"
python -m pip install --upgrade pip
python -m pip install -e .

mkdir -p "$HOME/.local/bin"
ln -sf "$ROOT/$VENV_DIR/bin/hermes" "$HOME/.local/bin/hermes"

# gptprof-hermes optional runtime helpers. These are public-safe wrappers; they
# read tokens only from the user's own ~/.hermes/gptprof/profiles/*.json.
if [ -d "$ROOT/skills/gptprof-hermes/bin" ]; then
  install -m 700 "$ROOT/skills/gptprof-hermes/bin/codex-profile-manager.py" "$HOME/.local/bin/codex-profile-manager.py"
  install -m 700 "$ROOT/skills/gptprof-hermes/bin/send_buttons.py" "$HOME/.local/bin/gptprof_send_buttons.py"
  install -m 700 "$ROOT/skills/gptprof-hermes/bin/refresh_profiles.py" "$HOME/.local/bin/gptprof_refresh_profiles.py"
  install -m 700 "$ROOT/skills/gptprof-hermes/bin/gptprof_autoswitch.py" "$HOME/.local/bin/gptprof_autoswitch.py"
fi

# Add Powerpack quick commands without overwriting existing user commands.
HERMES_HOME_FOR_CONFIG="${HERMES_HOME:-$HOME/.hermes}"
mkdir -p "$HERMES_HOME_FOR_CONFIG"
export VENV_DIR
"$ROOT/$VENV_DIR/bin/python" - <<'PY'
from pathlib import Path
import os
import yaml
home = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes"))).expanduser()
config_path = home / "config.yaml"
try:
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
except Exception:
    cfg = {}
if not isinstance(cfg, dict):
    cfg = {}
quick = cfg.setdefault("quick_commands", {})
if not isinstance(quick, dict):
    quick = {}
    cfg["quick_commands"] = quick
python_bin = str(Path.cwd() / os.environ.get("VENV_DIR", ".venv") / "bin" / "python")
def add(name, value):
    if name not in quick:
        quick[name] = value
add("gptt", {"type": "alias", "target": "/model gpt-5.5 --provider openai-codex --global"})
add("mmfast", {"type": "alias", "target": "/model MiniMax-M2.7 --provider minimax --global"})
add("gptprof", {"type": "exec", "command": f"{python_bin} ~/.local/bin/gptprof_send_buttons.py"})
config_path.write_text(yaml.safe_dump(cfg, sort_keys=False, allow_unicode=True), encoding="utf-8")
PY

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc" ;;
esac

echo "Hermes Powerpack installed. Next:"
echo "  export PATH=\"$HOME/.local/bin:\$PATH\""
echo "  hermes setup"
echo "  hermes doctor"
echo "  optional gptprof: set GPTPROF_CHAT_ID in ~/.hermes/.env and add profiles under ~/.hermes/gptprof/profiles/"
