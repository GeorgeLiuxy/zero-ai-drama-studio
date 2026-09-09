"""Add the pinned native H3 last-frame input without changing the I2V workflow."""

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def build_graph(first_frame_graph):
    graph = copy.deepcopy(first_frame_graph)
    assert graph["104"]["class_type"] == "MiniMaxH3ImageToVideo"
    assert graph["104"]["inputs"]["first_frame"] == ["114", 0]
    assert "last_frame" not in graph["104"]["inputs"]
    assert "115" not in graph
    assert graph["91"]["inputs"]["audio"] == ["23", 0]
    graph["115"] = {"class_type": "LoadImage", "inputs": {"image": "h3-last-frame.png"}}
    graph["104"]["inputs"]["last_frame"] = ["115", 0]
    return graph


def build():
    graph = build_graph(json.loads((ROOT / "h3-api.json").read_text(encoding="utf-8")))
    (ROOT / "h3-flf-api.json").write_text(json.dumps(graph, indent=2) + "\n", encoding="utf-8")
    print(f"Built {len(graph)} H3 FLF nodes; original I2V workflow unchanged")


if __name__ == "__main__":
    build()
