"""Flatten the official Wan template's standard 20-step branch, excluding optional turbo LoRAs."""

import json
from pathlib import Path

root = Path(__file__).resolve().parent
ui = json.loads((root / "wan-ui.json").read_text(encoding="utf-8"))
nodes = {n["id"]: n for n in ui["definitions"]["subgraphs"][0]["nodes"]}
graph = {}


def node(i, kind, **inputs):
    graph[str(i)] = {"class_type": kind, "inputs": inputs}


node(95, "UNETLoader", unet_name=nodes[95]["widgets_values"][0], weight_dtype="default")
node(96, "UNETLoader", unet_name=nodes[96]["widgets_values"][0], weight_dtype="default")
node(
    84,
    "CLIPLoader",
    clip_name=nodes[84]["widgets_values"][0],
    type="wan",
    device="default",
)
node(90, "VAELoader", vae_name=nodes[90]["widgets_values"][0])
node(93, "CLIPTextEncode", clip=["84", 0], text=nodes[93]["widgets_values"][0])
node(89, "CLIPTextEncode", clip=["84", 0], text=nodes[89]["widgets_values"][0])
node(97, "LoadImage", image="input.png")
node(
    98,
    "WanImageToVideo",
    positive=["93", 0],
    negative=["89", 0],
    vae=["90", 0],
    start_image=["97", 0],
    width=640,
    height=640,
    length=81,
    batch_size=1,
)
node(104, "ModelSamplingSD3", model=["95", 0], shift=5.0)
node(103, "ModelSamplingSD3", model=["96", 0], shift=5.0)
steps = nodes[128]["widgets_values"][0]
split = nodes[127]["widgets_values"][0]
cfg = nodes[126]["widgets_values"][0]
node(
    86,
    "KSamplerAdvanced",
    model=["104", 0],
    positive=["98", 0],
    negative=["98", 1],
    latent_image=["98", 2],
    add_noise="enable",
    noise_seed=42,
    steps=steps,
    cfg=cfg,
    sampler_name="euler",
    scheduler="simple",
    start_at_step=0,
    end_at_step=split,
    return_with_leftover_noise="enable",
)
node(
    85,
    "KSamplerAdvanced",
    model=["103", 0],
    positive=["98", 0],
    negative=["98", 1],
    latent_image=["86", 0],
    add_noise="disable",
    noise_seed=0,
    steps=steps,
    cfg=cfg,
    sampler_name="euler",
    scheduler="simple",
    start_at_step=split,
    end_at_step=steps,
    return_with_leftover_noise="disable",
)
node(87, "VAEDecode", samples=["85", 0], vae=["90", 0])
node(94, "CreateVideo", images=["87", 0], fps=16.0)
node(
    108,
    "SaveVideo",
    video=["94", 0],
    filename_prefix="arcreel/job",
    format="mp4",
    **{"format.codec": "h264", "format.codec.encoding": "auto"},
)
(root / "wan-api.json").write_text(
    json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8"
)
print(
    "Built standard workflow:",
    len(graph),
    "nodes; steps",
    steps,
    "split",
    split,
    "cfg",
    cfg,
)
