"""Pin and verify the small WeText assets required by CosyVoice's text frontend."""

import hashlib
import json
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/root/autodl-tmp/arcreel-worker")
manifest = ROOT / "manifests/wetext-models.json"
if manifest.exists():
    files = json.loads(manifest.read_text())
else:
    with urllib.request.urlopen(
        "https://modelscope.cn/api/v1/models/pengzhendong/wetext/repo/files?Revision=master&Recursive=true",
        timeout=30,
    ) as response:
        listing = json.load(response)["Data"]["Files"]
    names = {
        f"{lang}/tn/{name}.fst"
        for lang in ("en", "zh")
        for name in ("tagger", "verbalizer")
    }
    names.add("zh/tn/verbalizer_remove_erhua.fst")
    files = [f for f in listing if f["Path"] in names]
    assert len(files) == 5 and all(f.get("Sha256") and f.get("Revision") for f in files)
    manifest.write_text(json.dumps(files, indent=2))


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


for item in files:
    dest = ROOT / "models/wetext" / item["Path"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    if (
        dest.exists()
        and dest.stat().st_size == item["Size"]
        and sha(dest) == item["Sha256"]
    ):
        continue
    partial = dest.with_suffix(".part")
    query = urllib.parse.urlencode(
        {"Revision": item["Revision"], "FilePath": item["Path"]}
    )
    subprocess.run(
        [
            "curl",
            "-fL",
            "--connect-timeout",
            "20",
            "--max-time",
            "180",
            "--continue-at",
            "-",
            "-o",
            str(partial),
            "https://modelscope.cn/api/v1/models/pengzhendong/wetext/repo?" + query,
        ],
        check=True,
    )
    assert partial.stat().st_size == item["Size"] and sha(partial) == item["Sha256"], (
        item["Path"]
    )
    partial.replace(dest)
report = {"verified": True, "files": len(files), "bytes": sum(f["Size"] for f in files)}
(ROOT / "reports/text-resources.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
