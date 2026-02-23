#!/usr/bin/env bash
set -euo pipefail

# Location of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Default host/port can be overridden via env vars
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-5000}"

# Choose Python interpreter (env PYTHON preferred)
PY_BIN="${PYTHON:-}"
if [[ -z "$PY_BIN" ]]; then
  for cand in python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1; then PY_BIN="$cand"; break; fi
  done
fi
if [[ -z "$PY_BIN" ]]; then
  echo "No Python interpreter found (tried python3.11, python3.10, python3, python)" >&2
  exit 1
fi

# If user only wants help, show it fast and exit
if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  exec "$PY_BIN" "$SCRIPT_DIR/piper_multi_server.py" -h
fi

# Create venv if missing
if [[ ! -f "$SCRIPT_DIR/venv/bin/activate" ]]; then
  echo "Creating virtualenv at: $SCRIPT_DIR/venv (using $PY_BIN)" >&2
  "$PY_BIN" -m venv "$SCRIPT_DIR/venv"
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/venv/bin/activate"
  echo "Upgrading pip/setuptools/wheel" >&2
  pip install --upgrade pip setuptools wheel
  echo "Installing speech-pipeline (editable) with server+tts+stt extras" >&2
  pip install -e "$SCRIPT_DIR[server,tts,stt]"
  if [[ -d "/home/carli/sources/piper" ]]; then
    echo "Installing Piper from local sources" >&2
    pip install -e /home/carli/sources/piper
  fi
  if [[ "${INSTALL_VC:-0}" == "1" ]]; then
    echo "Attempting to install optional VC deps (Torch + Coqui TTS)" >&2
    if [[ -n "${TORCH_INDEX_URL:-}" ]]; then
      pip install torch --index-url "$TORCH_INDEX_URL" || true
    else
      pip install torch || true
    fi
    pip install TTS || true
  fi
else
  # shellcheck disable=SC1090
  source "$SCRIPT_DIR/venv/bin/activate"
fi

# Run the multi-voice server
# If caller didn't pass --soundpath, provide a default remote template
EXTRA_ARGS=()
if [[ " $* " != *" --soundpath="* ]] && [[ " $* " != *" --soundpath "* ]]; then
  EXTRA_ARGS+=(--soundpath="https://hardlife.launix.de/files/%s/x")
fi

exec python "$SCRIPT_DIR/piper_multi_server.py" \
  --scan-dir "$SCRIPT_DIR/voices-piper" \
  --host "$HOST" \
  --port "$PORT" \
  "${EXTRA_ARGS[@]}" \
  "$@"
