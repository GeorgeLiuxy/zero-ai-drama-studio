"""One TTS subprocess; called only while the gateway holds the global GPU slot."""

import json
import os
import sys
from pathlib import Path

ROOT = Path("/root/autodl-tmp/arcreel-worker")
os.environ["HF_HOME"] = str(ROOT / "cache" / "hf")
os.environ["HF_HUB_OFFLINE"] = "1"
sys.path[:0] = [
    str(ROOT / "repos" / "CosyVoice"),
    str(ROOT / "repos" / "CosyVoice" / "third_party" / "Matcha-TTS"),
]
import soundfile as sf
import torch
from cosyvoice.cli.cosyvoice import AutoModel
from offline_text import install_local_text_resources

if not torch.cuda.is_available():
    raise RuntimeError("GPU required for cloud TTS; CPU fallback disabled")
install_local_text_resources(ROOT)
request = json.loads(sys.stdin.read())
voice = json.loads((ROOT / "config" / "voices.json").read_text())[request["voice"]]
reference = (ROOT / "inputs" / voice["reference"]).resolve()
if not reference.is_relative_to((ROOT / "inputs").resolve()):
    raise ValueError("Invalid voice path")
model = AutoModel(
    model_dir=str(ROOT / "models" / "cosyvoice3"),
    load_trt=False,
    load_vllm=False,
    fp16=False,
)
if model.frontend.text_frontend != "wetext":
    raise RuntimeError("Prepared WeText frontend did not initialize")
chunks = list(
    model.inference_zero_shot(
        request["input"],
        "You are a helpful assistant.<|endofprompt|>" + voice["transcript"],
        str(reference),
        stream=False,
        speed=request.get("speed", 1.0),
    )
)
if not chunks:
    raise RuntimeError("No audio returned")
audio = torch.cat([c["tts_speech"] for c in chunks], dim=1).cpu().numpy().squeeze()
path = Path(request["output"]).resolve()
if not path.is_relative_to((ROOT / "outputs").resolve()):
    raise ValueError("Invalid output path")
tmp = path.with_suffix(".tmp")
sf.write(tmp, audio, model.sample_rate, format="WAV", subtype="PCM_16")
if sf.info(tmp).frames <= 0:
    raise RuntimeError("Empty audio")
tmp.replace(path)
