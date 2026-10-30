#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMAND="${1:-start}"

E2E_LOG_DIR="${E2E_LOG_DIR:-/tmp/paper-pulse-e2e-logs}"
E2E_API_HOST="${E2E_API_HOST:-127.0.0.1}"
E2E_API_PORT="${E2E_API_PORT:-8000}"
E2E_FRONTEND_HOST="${E2E_FRONTEND_HOST:-127.0.0.1}"
E2E_FRONTEND_PORT="${E2E_FRONTEND_PORT:-3000}"
E2E_LLM_HOST="${E2E_LLM_HOST:-127.0.0.1}"
E2E_LLM_PORT="${E2E_LLM_PORT:-8011}"
E2E_FRONTEND_MODE="${E2E_FRONTEND_MODE:-start}"
PYTHON_BIN="${PYTHON_BIN:-python}"

mkdir -p "$E2E_LOG_DIR/screens" "$E2E_LOG_DIR/published"

stop_stack() {
  local pid_file pid name
  shopt -s nullglob
  for pid_file in "$E2E_LOG_DIR"/*.pid; do
    name="$(basename "$pid_file" .pid)"
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $name (pid $pid)"
      kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
      for _ in {1..50}; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.2
      done
      if kill -0 "$pid" 2>/dev/null; then
        kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
      fi
    fi
    rm -f "$pid_file"
  done
}

launch_service() {
  local name="$1"
  shift
  local pid_file="$E2E_LOG_DIR/$name.pid"
  local log_file="$E2E_LOG_DIR/$name.log"
  if [[ -s "$pid_file" ]]; then
    local old_pid
    old_pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ "$old_pid" =~ ^[0-9]+$ ]] && kill -0 "$old_pid" 2>/dev/null; then
      echo "$name is already running (pid $old_pid)"
      return 1
    fi
    rm -f "$pid_file"
  fi
  if command -v setsid >/dev/null 2>&1; then
    setsid "$@" >>"$log_file" 2>&1 </dev/null &
  else
    nohup "$@" >>"$log_file" 2>&1 </dev/null &
  fi
  echo "$!" >"$pid_file"
  echo "Started $name (pid $(cat "$pid_file")); log: $log_file"
}

if [[ "$COMMAND" == "stop" ]]; then
  stop_stack
  exit 0
fi

if [[ "$COMMAND" != "start" ]]; then
  echo "Usage: $0 [start|stop]" >&2
  exit 2
fi

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required (use a disposable E2E database)." >&2
  exit 2
fi

if [[ "$E2E_FRONTEND_MODE" != "start" && "$E2E_FRONTEND_MODE" != "dev" ]]; then
  echo "E2E_FRONTEND_MODE must be start or dev." >&2
  exit 2
fi

cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR/e2e:$ROOT_DIR/src${PYTHONPATH:+:$PYTHONPATH}"
export E2E_MOCK_PUBLISHER=1
export WORKSPACE="${WORKSPACE:-$ROOT_DIR}"
export CONFIG_DIR="${CONFIG_DIR:-config}"
export JWT_SECRET="${JWT_SECRET:-e2e-only-jwt-secret-not-for-production-2026}"
LOCAL_USERS_DEFAULT='{"admin":{"password":"E2E-test-admin-2026!","name":"E2E 管理员","role":"admin"},"reviewer":{"password":"E2E-test-reviewer-2026!","name":"E2E 审核员","role":"reviewer"},"viewer":{"password":"E2E-test-viewer-2026!","name":"E2E 查看者","role":"viewer"},"limited":{"password":"E2E-test-limited-2026!","name":"E2E 受限用户","role":"limited"}}'
export LOCAL_USERS="${LOCAL_USERS:-$LOCAL_USERS_DEFAULT}"
unset LOCAL_USERS_DEFAULT
export ALLOW_PLAINTEXT_PASSWORDS="${ALLOW_PLAINTEXT_PASSWORDS:-true}"
export LLM_BASE_URL="http://${E2E_LLM_HOST}:${E2E_LLM_PORT}/v1"
export LLM_API_KEY="${LLM_API_KEY:-e2e-local-test-key}"
export LLM_MODEL="${LLM_MODEL:-e2e-mock-model}"
export DINGTALK_ENABLED="false"
export E2E_API_URL="${E2E_API_URL:-http://127.0.0.1:${E2E_API_PORT}}"
export E2E_BASE_URL="${E2E_BASE_URL:-http://localhost:${E2E_FRONTEND_PORT}}"
export BACKEND_API_URL="${BACKEND_API_URL:-$E2E_API_URL}"
export NEXT_PUBLIC_API_URL="${NEXT_PUBLIC_API_URL:-}"
export NEXT_PUBLIC_TARGET_LABEL="${NEXT_PUBLIC_TARGET_LABEL:-qMRLab}"
export NEXT_PUBLIC_DOMAIN_NAME="${NEXT_PUBLIC_DOMAIN_NAME:-qMRI}"
export E2E_REPORT="${E2E_REPORT:-$E2E_LOG_DIR/e2e-report.json}"

# These are test-only NextAuth values. The encryption key is generated once per
# log directory with Fernet so a restarted E2E stack can still read test records.
export NEXTAUTH_URL="${NEXTAUTH_URL:-$E2E_BASE_URL}"
export NEXTAUTH_SECRET="${NEXTAUTH_SECRET:-e2e-only-nextauth-secret-not-for-production-2026}"
export AUTH_SECRET="${AUTH_SECRET:-$NEXTAUTH_SECRET}"
export AUTH_TRUST_HOST="${AUTH_TRUST_HOST:-true}"
if [[ -z "${CREDENTIALS_ENCRYPTION_KEY:-}" ]]; then
  KEY_FILE="$E2E_LOG_DIR/.e2e-credentials-fernet-key"
  if [[ ! -s "$KEY_FILE" ]]; then
    umask 077
    "$PYTHON_BIN" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode("ascii"))' >"$KEY_FILE"
    chmod 600 "$KEY_FILE"
  fi
  export CREDENTIALS_ENCRYPTION_KEY="$(cat "$KEY_FILE")"
fi

launch_service mock-llm "$PYTHON_BIN" "$ROOT_DIR/e2e/mock_llm.py" --host "$E2E_LLM_HOST" --port "$E2E_LLM_PORT"
launch_service api "$PYTHON_BIN" -m uvicorn api.main:app --workers 2 --host "$E2E_API_HOST" --port "$E2E_API_PORT" --timeout-keep-alive 75
launch_service worker "$PYTHON_BIN" -m worker --concurrency 2
launch_service scheduler "$PYTHON_BIN" -m worker.scheduler

if [[ "$E2E_FRONTEND_MODE" == "start" ]]; then
  launch_service frontend bash -c 'cd "$1" && shift && exec "$@"' _ "$ROOT_DIR/frontend" npm run start -- --hostname "$E2E_FRONTEND_HOST" --port "$E2E_FRONTEND_PORT"
else
  launch_service frontend bash -c 'cd "$1" && shift && exec "$@"' _ "$ROOT_DIR/frontend" npm run dev -- --hostname "$E2E_FRONTEND_HOST" --port "$E2E_FRONTEND_PORT"
fi

echo "E2E stack started. Frontend: $E2E_BASE_URL; API: $E2E_API_URL; mock LLM: http://${E2E_LLM_HOST}:${E2E_LLM_PORT}/v1"
echo "Logs and screenshots: $E2E_LOG_DIR"
