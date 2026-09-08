"""Resume installation using official GPU wheel URLs and a nearby general package index."""

import html
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/root/autodl-tmp/arcreel-worker")
UV = str(ROOT / "tools/bin/uv")
MIRROR = "https://mirrors.aliyun.com/pypi/simple"
os.environ.update(
    {
        "HF_HOME": str(ROOT / "cache/hf"),
        "UV_CACHE_DIR": str(ROOT / "cache/uv"),
        "TMPDIR": str(ROOT / "tmp"),
        "UV_CONCURRENT_DOWNLOADS": "1",
        "UV_CONCURRENT_BUILDS": "1",
        "UV_CONCURRENT_INSTALLS": "1",
        "UV_INDEX_URL": MIRROR,
        "UV_HTTP_TIMEOUT": "120",
        "UV_HTTP_RETRIES": "2",
    }
)
status = ROOT / "reports/preparation-status.json"
report = json.loads(status.read_text())
assert report.get("cosyvoice_weights") == "verified" and report.get("video_weights")
report.pop("blocker", None)
report["installation_strategy"] = (
    "official GPU wheels; general dependencies from Aliyun HTTPS mirror"
)


def save():
    status.write_text(json.dumps(report, indent=2))


def run(cmd):
    print("RUN", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True)


def gpu_wheel(package, version, cuda, abi):
    index = f"https://download.pytorch.org/whl/{cuda}/{package}/"
    with urllib.request.urlopen(index, timeout=60) as response:
        document = response.read().decode()
    matches = []
    for link in re.findall(r'href="([^"]+)"', document):
        url = urllib.parse.urljoin(index, html.unescape(link))
        filename = urllib.parse.unquote(
            urllib.parse.urlsplit(url).path.rsplit("/", 1)[-1]
        )
        if (
            filename.startswith(f"{package}-{version}+{cuda}-{abi}-{abi}-")
            and "manylinux" in filename
            and filename.endswith("_x86_64.whl")
        ):
            matches.append(url)
    if len(matches) != 1:
        raise RuntimeError(
            f"Expected one official wheel for {package} {version} {cuda} {abi}: {len(matches)}"
        )
    return matches[0]


try:
    save()
    if not Path(UV).exists():
        run(
            [
                "/root/miniconda3/bin/python",
                "-m",
                "pip",
                "install",
                "--ignore-installed",
                "--prefix",
                str(ROOT / "tools"),
                "--index-url",
                MIRROR,
                "uv==0.11.8",
            ]
        )
    for name, version in [("video", "3.12"), ("tts", "3.10"), ("gateway", "3.12")]:
        if not (ROOT / "envs" / name / "bin/python").exists():
            run([UV, "venv", "--python", version, str(ROOT / "envs" / name)])
    for name, cuda, abi, versions in [
        (
            "video",
            "cu130",
            "cp312",
            {"torch": "2.11.0", "torchvision": "0.26.0", "torchaudio": "2.11.0"},
        ),
        ("tts", "cu128", "cp310", {"torch": "2.8.0", "torchaudio": "2.8.0"}),
    ]:
        urls = [gpu_wheel(p, v, cuda, abi) for p, v in versions.items()]
        (ROOT / "manifests" / f"{name}-gpu-wheels.txt").write_text(
            "\n".join(urls) + "\n"
        )
        run(
            [
                UV,
                "pip",
                "install",
                "--python",
                str(ROOT / "envs" / name / "bin/python"),
                *urls,
                "--index",
                MIRROR,
                "--index-url",
                "https://download.pytorch.org/whl/" + cuda,
            ]
        )
        report["install"][name + "_torch"] = "installed_not_gpu_tested"
        save()
    for name, requirements in [
        ("video", ROOT / "repos/ComfyUI/requirements.txt"),
        ("tts", ROOT / "manifests/tts-requirements.txt"),
    ]:
        args = [
            UV,
            "pip",
            "install",
            "--python",
            str(ROOT / "envs" / name / "bin/python"),
            "-r",
            str(requirements),
            "-c",
            str(ROOT / "manifests" / f"{name}-constraints.txt"),
        ]
        if name == "tts":
            args += [
                "--build-constraint",
                str(ROOT / "manifests/build-constraints.txt"),
            ]
        run(args)
        report["install"][name + "_dependencies"] = "installed_not_gpu_tested"
        save()
    run(
        [
            UV,
            "pip",
            "install",
            "--python",
            str(ROOT / "envs/gateway/bin/python"),
            "fastapi==0.115.6",
            "uvicorn==0.30.0",
            "httpx==0.28.1",
            "python-multipart==0.0.22",
            "Pillow==11.3.0",
        ]
    )
    lock = {
        "sources": json.loads((ROOT / "manifests/source-lock.json").read_text()),
        "gpu_tested": False,
        "environments": {},
    }
    for name in ["video", "tts", "gateway"]:
        python = str(ROOT / "envs" / name / "bin/python")
        run([UV, "pip", "check", "--python", python])
        lock["environments"][name] = subprocess.check_output(
            [UV, "pip", "freeze", "--python", python], text=True
        ).splitlines()
    (ROOT / "manifests/environment.lock.json").write_text(json.dumps(lock, indent=2))
    report["install"]["all"] = "installed_not_gpu_tested"
    save()
    run(
        ["/root/miniconda3/bin/python", str(ROOT / "scripts/prepare_text_resources.py")]
    )
    run(
        [str(ROOT / "envs/tts/bin/python"), str(ROOT / "scripts/prepare_demo_voice.py")]
    )
    run(["/root/miniconda3/bin/python", str(ROOT / "scripts/preflight_cloud.py")])
except Exception as exc:
    report["blocker"] = str(exc)
    raise
finally:
    save()
