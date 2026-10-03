#!/bin/zsh
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
AUDIO_ROOT="${JACQUES_AUDIO_ROOT:-$HOME/Music/Jacques}"

cd "$PROJECT_ROOT"
export PYTHONPATH="$PROJECT_ROOT/src"

if [[ ! -d "$AUDIO_ROOT" ]]; then
  echo "Jacques audio root not found: $AUDIO_ROOT"
  exit 0
fi

"$PROJECT_ROOT/.venv/bin/python" -m pip install -q -r "$PROJECT_ROOT/requirements-audio.txt"
"$PROJECT_ROOT/.venv/bin/python" "$PROJECT_ROOT/tools/ingest_audio_library.py"   --audio-root "$AUDIO_ROOT"
