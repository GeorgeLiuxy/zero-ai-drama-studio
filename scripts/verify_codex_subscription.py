"""Inspect Codex without a model call, or explicitly verify one subscription image.

Run with --image to consume one image generation and verify the returned file.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import io
import json
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw

from lib.app_data_dir import app_data_dir
from lib.codex_runtime import CodexProcess, read_state, refresh_connection, run_turn, select_model, write_state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", action="store_true", help="Consume one image generation to verify image support")
    args = parser.parse_args()
    summary = refresh_connection(True)
    print(json.dumps(summary, ensure_ascii=False))
    if not summary["connected"]:
        raise SystemExit(1)
    if not args.image:
        return
    state = read_state()
    process = CodexProcess(seconds=40)
    try:
        state["runtime_version"] = process.initialize()["userAgent"]
        account = process.account()
        state["account_hash"] = hashlib.sha256(str(account.get("email", "")).encode()).hexdigest()
    finally:
        process.close()
    state["image_generation"] = True
    selected = select_model(state.get("image_agent_model"), state)
    reference = Image.new("RGB", (256, 256), "white")
    ImageDraw.Draw(reference).ellipse((70, 70, 186, 186), fill="red")
    buffer = io.BytesIO()
    reference.save(buffer, format="PNG")
    reference_uri = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")
    output = run_turn(
        model=selected.get("model") or selected["id"],
        inputs=[
            {"type": "image", "url": reference_uri},
            {
                "type": "text",
                "text": "Edit the reference: change the red circle to blue, preserving the white background. Generate one small image.",
            },
        ],
        system_prompt=None,
        schema=None,
        image=True,
        cancel=threading.Event(),
        state_reader=lambda: state,
    )
    if len(output["images"]) != 1:
        raise RuntimeError("Codex did not return exactly one image")
    with Image.open(io.BytesIO(output["images"][0]["data"])) as picture:
        picture.verify()
    artifact = app_data_dir() / ".codex-verification.png"
    artifact.write_bytes(output["images"][0]["data"])
    write_state(state)
    print(f"Image generation verified: {artifact}. Refresh the Codex connection in provider settings.")


if __name__ == "__main__":
    main()
