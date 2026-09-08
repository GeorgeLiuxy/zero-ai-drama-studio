# H3 preparation on AutoDL

Status: weight download in progress; native ComfyUI node/schema check passed.
This is preparation only. H3 has not generated a real video or audio sample and is not enabled in the ArcReel provider catalog.

## Installed environment and fixed workflow

- Reuses the existing `envs/video` (PyTorch 2.11.0+cu130) and pinned ComfyUI source. No environment upgrade or extra CUDA compilation.
- `workflows/h3-api.json`: 15 native nodes, standard 20-step `res_multistep` / `simple` / BasicGuider branch of the pinned official I2V template.
- Initial test canvas: 864x480; 124 frames at 24 fps (approximately 5.167 seconds, not exactly 5 seconds).
- Video and audio VAEs both decode the joint latent; audio is connected to CreateVideo before MP4/H.264 saving.
- Replace node 114's image with an uploaded first-frame filename and node 104's prompt before a future GPU test. No generation is submitted during preparation.
- Optional Turbo LoRA branch and prompt embeddings are absent from this standard API workflow. The upstream UI JSON is preserved as a reference only; it still lists optional models.
- Existing Wan default, user scripts, image provider and project data are unchanged. ArcReel H3 routing/audio preservation must be integrated and tested before selecting H3 there.

## Download and verification

Cloud root: `/root/autodl-tmp/arcreel-worker`.

Four weights total 42,470,585,471 bytes (39.55 GiB): FL2VA pruned INT8 ConvRot, Qwen3-VL NVFP4 AWQ encoder, video VAE FP16, audio VAE FP32.

Official repository: https://huggingface.co/Comfy-Org/MiniMax-H3

Pinned revision: `a98869194787969724c7425d95d0ed73ce9202af`.

Downloads use the Comfy-Org ModelScope repository at per-file immutable revisions, only after its sizes and SHA-256 values match the pinned Hugging Face LFS metadata. Each completed file is streamed through SHA-256 and atomically renamed; partial files retain `.part` for resume. One process and one download at a time; process lock prevents duplicate installers. Existing mismatched final files stop the installer rather than being overwritten.

Read status without GPU:

```bash
cat /root/autodl-tmp/arcreel-worker/reports/h3-preparation.json
tail -n 10 /root/autodl-tmp/arcreel-worker/logs/h3-download.log
```

`status=download_verified` and four verified file entries mean the weight download finished. This does not mean model inference passed. `status=failed` records an error; the installer can resume with:

```bash
python3 /root/autodl-tmp/arcreel-worker/scripts/prepare_h3.py
```

`reports/h3-environment.json` records the CPU-only schema check. Its diagnostic server is stopped afterward. No weight loading or prompt submission occurs in that check.

## License and next verification

An upstream license snapshot and its immutable source revision are kept in `manifests/H3-LICENSE.txt` and `manifests/h3-license-source.json`.
The community license has territorial and commercial-use conditions. Commercial release rights are not verified by this installation. Consult the pinned license and applicable additional authorization before commercial distribution; installation is not a commercial license.

When GPU mode is enabled, verify quantized model loading, one short I2V sample, decoded non-silent audio, GPU/RAM peaks and model unloading. Integrate the optional H3 route into the shared GPU queue and verify ArcReel preserves original audio before claiming end-to-end availability. Keep Wan as the default until those checks pass.
