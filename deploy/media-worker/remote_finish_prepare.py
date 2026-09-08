"""Queued after Wan downloads: prepare cloud TTS and isolated GPU-capable environments."""

import hashlib
import json
import os
import shutil
import subprocess
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path("/root/autodl-tmp/arcreel-worker")
for d in ["envs", "cache", "models", "repos", "reports", "manifests", "tmp"]:
    (ROOT / d).mkdir(exist_ok=True)
os.environ.update(
    {
        "HF_HOME": str(ROOT / "cache" / "hf"),
        "PIP_CACHE_DIR": str(ROOT / "cache" / "pip"),
        "UV_CACHE_DIR": str(ROOT / "cache" / "uv"),
        "TMPDIR": str(ROOT / "tmp"),
        "UV_CONCURRENT_DOWNLOADS": "1",
        "UV_CONCURRENT_BUILDS": "1",
        "UV_CONCURRENT_INSTALLS": "1",
    }
)
lock = json.loads((ROOT / "manifests" / "source-lock.json").read_text())


def run(cmd):
    subprocess.run(cmd, check=True)


def extract(archive, dest, parent, folder):
    if dest.exists():
        return
    with zipfile.ZipFile(archive) as z:
        for m in z.infolist():
            if not (parent / m.filename).resolve().is_relative_to(parent.resolve()):
                raise RuntimeError("Unsafe archive")
        z.extractall(parent)
    (parent / folder).rename(dest)


extract(
    ROOT / "cache" / "cosyvoice-source.zip",
    ROOT / "repos" / "CosyVoice",
    ROOT / "repos",
    "CosyVoice-" + lock["cosyvoice"],
)
matcha = ROOT / "repos" / "CosyVoice" / "third_party" / "Matcha-TTS"
if not (matcha / "matcha").exists():
    with zipfile.ZipFile(ROOT / "cache" / "matcha-source.zip") as z:
        z.extractall(ROOT / "cache")
    shutil.copytree(
        ROOT / "cache" / ("Matcha-TTS-" + lock["matcha"]), matcha, dirs_exist_ok=True
    )
files = json.loads((ROOT / "manifests" / "cosyvoice-models.json").read_text())
free = shutil.disk_usage(ROOT)
if free.free < sum(f["Size"] for f in files) + max(
    30 * 1024**3, int(free.total * 0.15)
):
    raise RuntimeError("Insufficient disk reserve")
for f in files:
    p = ROOT / "models" / "cosyvoice3" / f["Path"]
    p.parent.mkdir(parents=True, exist_ok=True)

    def sha(path):
        h = hashlib.sha256()
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(4 * 1024**2), b""):
                h.update(block)
        return h.hexdigest()

    if p.exists() and p.stat().st_size == f["Size"] and sha(p) == f["Sha256"]:
        continue
    part = p.with_name(p.name + ".part")
    q = urllib.parse.urlencode({"Revision": f["Revision"], "FilePath": f["Path"]})
    print("DOWNLOAD", f["Path"], flush=True)
    run(
        [
            "curl",
            "-4",
            "-fL",
            "--connect-timeout",
            "20",
            "--max-time",
            "3600",
            "--speed-time",
            "90",
            "--speed-limit",
            "1024",
            "--continue-at",
            "-",
            "--output",
            str(part),
            "https://modelscope.cn/api/v1/models/FunAudioLLM/Fun-CosyVoice3-0.5B-2512/repo?"
            + q,
        ]
    )
    if part.stat().st_size != f["Size"] or sha(part) != f["Sha256"]:
        raise RuntimeError("Hash mismatch: " + f["Path"])
    part.replace(p)
    print("VERIFIED", f["Path"], flush=True)
report = {
    "cosyvoice_weights": "verified",
    "video_weights": (ROOT / "reports" / "models-ready.json").exists(),
    "gpu_tested": False,
    "install": {},
}
(ROOT / "reports" / "preparation-status.json").write_text(json.dumps(report, indent=2))
run(["/root/miniconda3/bin/python", str(ROOT / "scripts" / "install.py")])
