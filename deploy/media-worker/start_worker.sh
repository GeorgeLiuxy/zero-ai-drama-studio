#!/usr/bin/env bash
set -euo pipefail
ROOT=/root/autodl-tmp/arcreel-worker
export OMP_NUM_THREADS=4
export MKL_NUM_THREADS=4
export OPENBLAS_NUM_THREADS=4
if [[ "${1:-}" != --authorize-gpu && "${1:-}" != --prepare-only ]]; then
  echo 'Use --prepare-only for a CPU-only gateway, or --authorize-gpu to enable GPU jobs.' >&2
  exit 2
fi
if [[ -f "$ROOT/runtime/gateway.pid" ]] && kill -0 "$(cat "$ROOT/runtime/gateway.pid")" 2>/dev/null; then
  echo 'Recorded gateway process is already running.' >&2
  exit 1
fi
export ARCREEL_GPU_AUTHORIZED=0
if [[ "$1" == --authorize-gpu ]]; then
  /root/miniconda3/bin/python -c 'import json; from pathlib import Path; p=Path("/root/autodl-tmp/arcreel-worker/reports/preflight.json"); assert p.exists() and json.loads(p.read_text()).get("prepared"), "Run scripts/preflight_cloud.py successfully first"'
  nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
  for env in video tts; do
    "$ROOT/envs/$env/bin/python" "$ROOT/scripts/gpu_probe.py" --authorize-gpu --out "$ROOT/reports/gpu-$env.json"
  done
  export ARCREEL_GPU_AUTHORIZED=1
fi
export HF_HOME="$ROOT/cache/hf"
export HF_HUB_OFFLINE=1
export TMPDIR="$ROOT/tmp"
cd "$ROOT/scripts"
nohup "$ROOT/envs/gateway/bin/python" -m uvicorn cloud_gateway:app --host 127.0.0.1 --port 9000 --workers 1 --timeout-keep-alive 30 > "$ROOT/logs/gateway.log" 2>&1 < /dev/null &
echo "$!" > "$ROOT/runtime/gateway.pid"
echo 'Gateway launched on remote loopback port 9000. This does not stop instance billing.'
