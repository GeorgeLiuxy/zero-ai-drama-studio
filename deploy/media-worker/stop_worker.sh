#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/arcreel-worker
"$ROOT/envs/gateway/bin/python" - <<'PY'
import json,urllib.request
with urllib.request.urlopen('http://127.0.0.1:9000/health/ready',timeout=5) as r:s=json.load(r)
if s['busy'] or s['queued']:raise SystemExit('Refusing shutdown: GPU busy or queue nonempty')
PY
pid=$(cat "$ROOT/runtime/gateway.pid")
if [[ ! "$pid" =~ ^[0-9]+$ ]]; then exit 1; fi
if [[ -r "/proc/$pid/cmdline" ]] && tr '\0' ' ' < "/proc/$pid/cmdline" | grep -Fq 'uvicorn cloud_gateway:app'; then
  kill -TERM "$pid"
  echo 'Gateway stop requested. AutoDL billing continues until console shutdown.'
else
  echo 'Recorded PID does not match this gateway; nothing stopped.' >&2
  exit 1
fi
