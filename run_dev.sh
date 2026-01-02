#!/usr/bin/env bash
set -e
# Activate conda env if available
if command -v conda >/dev/null 2>&1; then
  eval "$(conda shell.bash hook)"
  conda activate vora || true
fi
export OLLAMA_HOST=${OLLAMA_HOST:-http://127.0.0.1:11434}
export OLLAMA_MODEL=${OLLAMA_MODEL:-gemma3:12b-it-qat}
export SEARXNG_URL=${SEARXNG_URL:-http://127.0.0.1:8080}
export TY2A_BASE_URL=${TY2A_BASE_URL:-http://127.0.0.1:8100}
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
