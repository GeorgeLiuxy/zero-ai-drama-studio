"""Prepare the pinned upstream reference strictly as a technical demo voice."""

import hashlib
import json
import shutil
from pathlib import Path

import soundfile as sf

ROOT = Path("/root/autodl-tmp/arcreel-worker")
source = ROOT / "repos/CosyVoice/asset/zero_shot_prompt.wav"
duration = sf.info(source).duration
assert duration > 0
dest = ROOT / "inputs/voices/upstream-demo.wav"
dest.parent.mkdir(parents=True, exist_ok=True)
shutil.copy2(source, dest)
config = ROOT / "config/voices.json"
voices = json.loads(config.read_text()) if config.exists() else {}
voices.setdefault(
    "alloy",
    {
        "label": "CosyVoice upstream technical demo - not approved production voice",
        "reference": "voices/upstream-demo.wav",
        "transcript": "希望你以后能够做的比我还好呦。",
        "demo_only": True,
    },
)
config.write_text(json.dumps(voices, ensure_ascii=False, indent=2), encoding="utf-8")
report = {
    "demo_only": True,
    "duration": duration,
    "sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
    "source_commit": json.loads((ROOT / "manifests/source-lock.json").read_text())[
        "cosyvoice"
    ],
    "production_voice_approved": False,
}
(ROOT / "reports/demo-voice.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
