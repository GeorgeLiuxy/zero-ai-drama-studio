"""Small explicit GPU check, not a model production benchmark."""

import argparse
import json
import time
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--authorize-gpu", action="store_true")
p.add_argument("--out", required=True)
a = p.parse_args()
if not a.authorize_gpu:
    raise SystemExit("Explicit GPU authorization required")
import torch

assert torch.cuda.is_available()
r = {
    "torch": torch.__version__,
    "cuda_runtime": torch.version.cuda,
    "gpu": torch.cuda.get_device_name(0),
    "total_vram": torch.cuda.get_device_properties(0).total_memory,
    "operations": {},
}
for dtype in [torch.float16, torch.bfloat16]:
    start = time.perf_counter()
    x = torch.randn(1, 2, 32, 64, device="cuda", dtype=dtype)
    y = torch.nn.functional.scaled_dot_product_attention(x, x, x)
    assert torch.isfinite(y).all()
    torch.cuda.synchronize()
    r["operations"][str(dtype)] = time.perf_counter() - start
Path(a.out).write_text(json.dumps(r, indent=2))
print(json.dumps(r, indent=2))
