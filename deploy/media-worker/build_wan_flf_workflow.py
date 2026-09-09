"""Build the pinned official FLF2V standard branch using existing Wan assets."""

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build():
    source = ROOT / "wan-flf-upstream.json"
    ui = json.loads(source.read_text(encoding="utf-8"))
    nodes = {n["id"]: n for n in ui["nodes"]}
    graph = json.loads((ROOT / "wan-api.json").read_text(encoding="utf-8"))
    for target, original in (("95", 76), ("96", 77)):
        assert graph[target]["inputs"]["unet_name"] == nodes[original]["widgets_values"][0]
    assert graph["84"]["inputs"]["clip_name"] == nodes[72]["widgets_values"][0]
    assert graph["90"]["inputs"]["vae_name"] == nodes[79]["widgets_values"][0]
    assert nodes[81]["type"] == "WanFirstLastFrameToVideo"
    graph["99"] = {"class_type": "LoadImage", "inputs": {"image": "end-frame.png"}}
    graph["98"]["class_type"] = "WanFirstLastFrameToVideo"
    graph["98"]["inputs"]["end_image"] = ["99", 0]
    for target, original in (("104", 73), ("103", 74)):
        graph[target]["inputs"]["shift"] = nodes[original]["widgets_values"][0]
    for target, original in (("86", 84), ("85", 87)):
        values = nodes[original]["widgets_values"]
        for key, index in (
            ("add_noise", 0),
            ("steps", 3),
            ("cfg", 4),
            ("sampler_name", 5),
            ("scheduler", 6),
            ("start_at_step", 7),
            ("end_at_step", 8),
            ("return_with_leftover_noise", 9),
        ):
            graph[target]["inputs"][key] = values[index]
    output = ROOT / "wan-flf-api.json"
    output.write_text(json.dumps(graph, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    lock = json.loads((ROOT / "source-lock.json").read_text(encoding="utf-8"))
    report = {
        "workflow_revision": lock["workflow_templates"],
        "comfyui_revision": lock["comfyui"],
        "source_url": f"https://raw.githubusercontent.com/Comfy-Org/workflow_templates/{lock['workflow_templates']}/templates/video_wan2_2_14B_flf2v.json",
        "upstream_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "workflow_sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        "branch": "standard 20 steps, CFG 4, shift 8; no Turbo LoRA",
        "adaptation": "Existing gateway node IDs, request prompt, 16 fps, existing MP4 save node. Same four model files.",
        "gpu_tested": False,
    }
    (ROOT / "wan-flf-source.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(graph)} FLF2V nodes; original I2V workflow unchanged")


if __name__ == "__main__":
    build()
