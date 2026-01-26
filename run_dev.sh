#!/usr/bin/env bash
set -e

# --- แก้ไข 1: ใช้ venv ที่เราสร้าง แทน conda ---
# Activate venv (เช็ค path ให้ตรงกับเครื่องคุณ)
source ~/vora_project/VORA/VORA/vora_env/bin/activate || echo "Warning: Could not activate venv"

# --- แก้ไข 2: ตั้งค่า Model (เช็คชื่อโมเดลด้วยคำสั่ง 'ollama list') ---
export OLLAMA_HOST=${OLLAMA_HOST:-http://127.0.0.1:11434}
# ถ้าคุณมี gemma:2b ให้แก้บรรทัดล่างเป็น gemma:2b
export OLLAMA_MODEL=${OLLAMA_MODEL:-gemma:2b} 

export SEARXNG_URL=${SEARXNG_URL:-http://127.0.0.1:8080}
export TY2A_BASE_URL=${TY2A_BASE_URL:-http://127.0.0.1:8100}

# รัน Server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload