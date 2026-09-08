"""No model construction or CUDA allocation: validate installed preparation."""

import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path("/root/autodl-tmp/arcreel-worker")
report = {
    "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    "gpu_tested": False,
    "checks": {},
    "errors": [],
}


def check(name, operation):
    try:
        report["checks"][name] = operation()
    except (
        OSError,
        ValueError,
        KeyError,
        AssertionError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as exc:
        report["errors"].append({"check": name, "error": str(exc)})


def weights():
    status = json.loads((ROOT / "reports/preparation-status.json").read_text())
    assert status.get("cosyvoice_weights") == "verified", "TTS hashes not verified"
    wan = json.loads((ROOT / "reports/models-ready.json").read_text())
    assert wan.get("download_verified"), "Wan hashes not verified"
    count = 0
    for manifest, base in [
        ("wan-models.json", ROOT / "models"),
        ("cosyvoice-models.json", ROOT / "models/cosyvoice3"),
    ]:
        for item in json.loads((ROOT / "manifests" / manifest).read_text()):
            path = (
                item["Path"].removeprefix("split_files/")
                if manifest.startswith("wan")
                else item["Path"]
            )
            dest = base / path
            assert dest.is_file() and dest.stat().st_size == item["Size"], path
            count += 1
    return {
        "count": count,
        "sizes_match": True,
        "hashes": "verified by download records; not rehashed here",
    }


def imports(name, code):
    result = subprocess.run(
        [str(ROOT / "envs" / name / "bin/python"), "-c", code],
        cwd=ROOT / "scripts",
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={**os.environ, "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
    )
    if result.returncode:
        raise RuntimeError(result.stderr[-5000:] or result.stdout[-3000:])
    return {"import_ok": True, "output": result.stdout[-2000:]}


check("weights", weights)
check(
    "video_imports",
    lambda: imports(
        "video",
        "import torch, torchvision, torchaudio, av, safetensors, sentencepiece, transformers, aiohttp; "
        "print(torch.__version__, torch.version.cuda, av.__version__)",
    ),
)
check(
    "tts_imports",
    lambda: imports(
        "tts",
        "import sys; sys.path[:0]=['/root/autodl-tmp/arcreel-worker/repos/CosyVoice',"
        "'/root/autodl-tmp/arcreel-worker/repos/CosyVoice/third_party/Matcha-TTS']; "
        "import torch, torchaudio, onnxruntime, wetext; from cosyvoice.cli.cosyvoice import AutoModel; "
        "print(torch.__version__, torch.version.cuda, onnxruntime.get_available_providers())",
    ),
)
check(
    "gateway_imports",
    lambda: imports("gateway", "import cloud_gateway; print(cloud_gateway.ready())"),
)
check(
    "text_normalization",
    lambda: imports(
        "tts",
        """
from pathlib import Path
from offline_text import install_local_text_resources
install_local_text_resources(Path('/root/autodl-tmp/arcreel-worker'))
from wetext import Normalizer
n=Normalizer(remove_erhua=False)
zh=n.normalize('今天是2026年9月8日。')
en=n.normalize('The price is 123 dollars.')
assert '2026' not in zh and '123' not in en
print(zh, en)
""",
    ),
)
check(
    "video_tokenizer",
    lambda: imports(
        "video",
        """
import json,struct,sentencepiece
with open('/root/autodl-tmp/arcreel-worker/models/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors','rb') as f:
    n=struct.unpack('<Q',f.read(8))[0]
    assert n < 16*1024*1024
    h=json.loads(f.read(n))
    a,b=h['spiece_model']['data_offsets']
    assert 0 < b-a < 16*1024*1024
    f.seek(8+n+a)
    tokenizer=sentencepiece.SentencePieceProcessor(model_proto=f.read(b-a))
    print('local_tokenizer_pieces',tokenizer.get_piece_size())
""",
    ),
)
check(
    "tts_tokenizer_and_reference",
    lambda: imports(
        "tts",
        """
from transformers import AutoTokenizer
import torchaudio
t=AutoTokenizer.from_pretrained('/root/autodl-tmp/arcreel-worker/models/cosyvoice3/CosyVoice-BlankEN',local_files_only=True)
x,sr=torchaudio.load('/root/autodl-tmp/arcreel-worker/inputs/voices/upstream-demo.wav',backend='soundfile')
assert x.numel() > 0
print('local_tokenizer_size',len(t),'reference_duration',x.shape[-1]/sr)
""",
    ),
)


def voice():
    voices = json.loads((ROOT / "config/voices.json").read_text())
    for name, item in voices.items():
        ref = (ROOT / "inputs" / item["reference"]).resolve()
        assert ref.is_relative_to((ROOT / "inputs").resolve()) and ref.is_file(), name
        assert item["transcript"], name
    assert "alloy" in voices, "ArcReel default voice not configured"
    return {
        "ids": list(voices),
        "default_demo_only": voices["alloy"].get("demo_only", False),
    }


check("voice", voice)
report["prepared"] = not report["errors"]
(ROOT / "reports/preflight.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
raise SystemExit(0 if report["prepared"] else 1)
