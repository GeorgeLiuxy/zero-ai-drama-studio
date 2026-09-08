"""Run on AutoDL: prepare fixed ComfyUI source and sequential Wan downloads, no GPU calls."""

import hashlib
import json
import shutil
import subprocess
import urllib.parse
import zipfile
from pathlib import Path

ROOT = Path("/root/autodl-tmp/arcreel-worker")
for name in [
    "repos",
    "models",
    "workflows",
    "manifests",
    "logs",
    "reports",
    "runtime",
    "outputs",
    "inputs",
    "cache",
    "scripts",
]:
    (ROOT / name).mkdir(parents=True, exist_ok=True)


def run(args):
    subprocess.run(args, check=True)


lock = json.loads((ROOT / "manifests" / "source-lock.json").read_text())
rev = lock["comfyui"]
dest = ROOT / "repos" / "ComfyUI"
if not dest.exists():
    archive = ROOT / "cache" / "comfyui.zip"
    run(
        [
            "curl",
            "-4",
            "-fL",
            "--connect-timeout",
            "20",
            "--max-time",
            "180",
            f"https://codeload.github.com/Comfy-Org/ComfyUI/zip/{rev}",
            "-o",
            str(archive),
        ]
    )
    with zipfile.ZipFile(archive) as z:
        for member in z.infolist():
            p = (ROOT / "repos" / member.filename).resolve()
            if not p.is_relative_to((ROOT / "repos").resolve()):
                raise ValueError("Unsafe archive path")
        z.extractall(ROOT / "repos")
    (ROOT / "repos" / ("ComfyUI-" + rev)).rename(dest)
files = json.loads((ROOT / "manifests" / "wan-models.json").read_text())
budget = sum(f["Size"] for f in files)
space = shutil.disk_usage(ROOT)
reserve = max(30 * 1024**3, int(space.total * 0.15))
if space.free < budget + reserve:
    raise RuntimeError("Disk reserve insufficient; no downloads started")
(ROOT / "reports" / "storage.json").write_text(
    json.dumps(
        {
            "free": space.free,
            "total": space.total,
            "planned_model_bytes": budget,
            "reserve": reserve,
            "storage_type": "system overlay, not separate data mount",
        },
        indent=2,
    )
)


def sha(p):
    h = hashlib.sha256()
    with p.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024**2), b""):
            h.update(chunk)
    return h.hexdigest()


for f in files:
    p = ROOT / "models" / Path(f["Path"]).relative_to("split_files")
    p.parent.mkdir(exist_ok=True, parents=True)
    if p.exists() and p.stat().st_size == f["Size"] and sha(p) == f["Sha256"]:
        print("VERIFIED", p.name, flush=True)
        continue
    partial = p.with_name(p.name + ".part")
    q = urllib.parse.urlencode({"Revision": f["Revision"], "FilePath": f["Path"]})
    url = (
        "https://modelscope.cn/api/v1/models/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/repo?"
        + q
    )
    print("DOWNLOAD", p.name, f["Size"], flush=True)
    run(
        [
            "curl",
            "-4",
            "-fL",
            "--connect-timeout",
            "20",
            "--max-time",
            "7200",
            "--speed-time",
            "90",
            "--speed-limit",
            "1024",
            "--continue-at",
            "-",
            "--output",
            str(partial),
            url,
        ]
    )
    if partial.stat().st_size != f["Size"] or sha(partial) != f["Sha256"]:
        raise RuntimeError("Size/hash mismatch: " + p.name)
    partial.replace(p)
    print("VERIFIED", p.name, flush=True)
(dest / "extra_model_paths.yaml").write_text(
    "arcreel:\n  base_path: /root/autodl-tmp/arcreel-worker/models\n  diffusion_models: diffusion_models\n  text_encoders: text_encoders\n  vae: vae\n"
)
(ROOT / "reports" / "models-ready.json").write_text(
    json.dumps({"download_verified": True, "gpu_tested": False})
)
print("MODELS VERIFIED; GPU inference not tested", flush=True)
