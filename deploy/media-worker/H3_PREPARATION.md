# H3 preparation on AutoDL

Status (2026-09-08): `download_verified`; all four weights finished downloading and passed verification. The native ComfyUI node/schema check passed.

The H3 dialogue adapter and ArcReel model configuration are prepared. The configured model is `custom-1/h3-i2v-int8`, using the H3 declarative endpoint (currently `ce-2`). Cloud deployment is recorded separately in `reports/h3-dialogue-deploy.json`; a model entry alone does not prove the deployed worker is ready. The formal E2S05 sample succeeded on 2026-09-08: the real ArcReel task saved video v1 and its verified current provenance. The file is native 576x1024, 124 frames at 24 fps (5.166667 seconds), with 32 kHz stereo AAC. Full decode and the editable presentation bundle preserved its original audio and SHA-256. End-to-end technical validation passed; subjective speech accuracy, lip sync, motion quality and production acceptance still need user review. Cold-run task time was 231.28 seconds, including 217.29 seconds of ComfyUI execution. See reports/h3-formal-validation.json.

Optional H3 first/last-frame support was deployed in CPU preparation mode and published to endpoint ce-2 on 2026-09-08 at 20:09 +08:00. The live project capability response confirms first_frame=true, last_frame=true and generate_audio=true, with the same 5-second/576 settings. Local operations tests passed (115 tests, 39 subtests), and the cloud isolated CPU contract passed without creating production jobs during that deployment stage. Deployment and configuration are recorded in reports/h3-flf-deploy.json and reports/h3-flf-config.json. The existing E2S05 result remains a first-frame-only dialogue result.

The formal E1S01 first/last-frame GPU technical sample succeeded on 2026-09-08 at 20:28 +08:00, task `444a8553434644c182e63acde7de90ed`. Task time was 261.925331 seconds, including 223.47 seconds of ComfyUI execution. The saved video v1 is 576x1024, 124 frames at 24 fps, lasting 5.166667 seconds, with 32 kHz stereo native audio. Full audio/video decode, current/verified formal provenance, and presentation-bundle video SHA-256 equality passed. See `reports/h3-flf-validation.json`. At this acceptance check the service was GPU-authorized, ready and idle; this is a point-in-time observation.

E1S01 is a voiceover scene using `post_production`: no narrator audio was synthesized, the presentation retains native video audio, and its subtitles come from the script rather than ASR. The native audio peak was only 0.000470142 (approximately -66.6 dBFS), so stream presence and decoding do not establish usable ambient-sound quality. Frames 0, 60 and 123 show close-to-wide framing, with the final composition close to the bound last frame. However, the original `Push In` instruction conflicts with those close-first/wide-last images; the script was not silently changed. Full-clip viewing is still required to assess continuous motion. GPU, audio-level and visual spot-check evidence is in `reports/h3-flf-gpu-validation.json`. Combined character dialogue and last-frame control has not yet passed acceptance.

See [H3_DIALOGUE.md](H3_DIALOGUE.md) for the current contract, formal sample commands, reconciliation, and scoped rollback.

## Installed environment and fixed workflow

- Reuses the existing `envs/video` (PyTorch 2.11.0+cu130) and ComfyUI revision `5bbdf8a76678e2c7cfb519a49a9c3a7137fd6280`. No video-environment upgrade or extra CUDA compilation is required by this adapter.
- The cloud `envs/gateway` additionally requires `PyYAML==6.0.3` for safe prompt compilation. This dependency belongs in the gateway environment, not a new local model environment.
- `workflows/h3-api.json`: 15 native nodes, standard 20-step `res_multistep` / `simple` / BasicGuider branch of the pinned official I2V template.
- `workflows/h3-flf-api.json` is the separate optional-last-frame workflow. It reuses the same four weights, adds the last-image connection, and preserves joint audio. Requests without a last frame continue using `h3-api.json`; its contents, original single-frame prompt version, and existing task hashes remain unchanged.
- The stored workflow's original preparation canvas is 864x480. The gateway overrides it for each request: 576x1024 (9:16), 1024x576 (16:9), or 576x576 (1:1). These are native inference dimensions, not a 720p generation label or an automatic upscale. Both axes are multiples of 32; both supplied keyframes must match the requested aspect ratio exactly, without silent stretching or cropping.
- The current endpoint accepts nominal 5 seconds only: 124 frames at 24 fps, approximately 5.167 seconds of actual video. It reports actual duration. It does not accept the Wan 3-second option.
- Video and audio VAEs both decode the joint latent; audio is connected to CreateVideo before MP4/H.264 saving.
- The gateway replaces node 114's image, node 104's canvas/length/prompt, and the seed/output prefix. `h3_prompt.py` converts ArcReel canonical YAML into H3's structured audiovisual prompt before queueing; exact dialogue text and ordered speaker IDs are preserved. Unknown formats are rejected rather than silently losing dialogue.
- With a last frame, the compiler uses `arcreel-h3-dialogue-flf-v1` and maps Picture 1 to 0 seconds and Picture 2 to the actual final-frame time `(124 - 1) / 24 = 5.125` seconds. This is distinct from the 5.166667-second clip duration. It describes one continuous motion path between keyframes while retaining the same dialogue/audio policy. Without a last frame, `arcreel-h3-dialogue-v1` and the original output bytes are preserved.
- Optional Turbo LoRA branch and prompt embeddings are absent from this standard API workflow. The upstream UI JSON is preserved as a reference only; it still lists optional models.
- H3 uses the existing shared GPU queue. Its output carries native audio, which downstream processing must preserve. It does not use CosyVoice to replace the characters' generated speech.
- Project `proj-8fbe5aa8` has a scoped H3 I2V selection with native audio enabled and prompt-based voice descriptions; this is recorded in `reports/h3-project-config.json`. The FLF update adds an optional last frame after deployment/configuration; it does not add reference audio, extra reference images, voice cloning, or guaranteed cross-clip voice identity. These limits describe this route, not every upstream H3 variant.

## CPU preparation and GPU startup

`start_worker.sh --prepare-only` launches only the gateway with GPU authorization disabled. It does not load H3, start ComfyUI, or submit inference. In this mode `/health/live` can be healthy while `/health/ready` reports `gpu_authorized=false` and `ready=false`; this is expected. Do not run a second gateway if one is already listening.

Before a later GPU test, if the preparation gateway is still running, first confirm that it is idle, run `stop_worker.sh`, and wait for the recorded process and port 9000 to stop. Only then run `start_worker.sh --authorize-gpu`. Changing the AutoDL instance to GPU mode does not change an already-running preparation gateway's authorization. See [H3_DIALOGUE.md](H3_DIALOGUE.md) for the commands.

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

`status=download_verified` and four verified file entries mean the weight download finished; this is the completed preparation state. It does not mean model inference passed. Do not start another download when the report is already verified. If a later interrupted installation records `status=failed`, inspect its error and resume the same installer with:

```bash
python3 /root/autodl-tmp/arcreel-worker/scripts/prepare_h3.py
```

`reports/h3-environment.json` records the CPU-only schema check. Its diagnostic server is stopped afterward. No weight loading or prompt submission occurs in that check.

## License and next verification

An upstream license snapshot and its immutable source revision are kept in `manifests/H3-LICENSE.txt` and `manifests/h3-license-source.json`.
The community license has territorial and commercial-use conditions. Commercial release rights are not verified by this installation. Consult the pinned license and applicable additional authorization before commercial distribution; installation is not a commercial license.

E1S01 has completed the formal FLF GPU technical path with its existing reviewed keyframes and 5-second contract. It remains a voiceover scene with no synthesized narration and cannot prove character dialogue or lip sync. The successful E2S05 single-first-frame dialogue result cannot substitute for that combined test either. A dialogue-plus-last-frame acceptance needs a suitable dialogue scene with an explicitly reviewed last frame. Do not invent that binding or resubmit either existing successful sample.

For E1S01, full-clip review must still check continuous intermediate motion, closed-mouth behavior and ambient-sound usability; its low audio peak and conflicting camera instruction remain explicit limitations. A future dialogue-plus-last-frame sample must also verify both visual endpoints, exact aspect ratio, decoded native audio, actual duration and formal provenance, then review exact spoken words and visible speaker/lip synchronization. Poll the existing task after any uncertain response; do not submit again or change the seed to bypass an `unknown` result. Keep any new test scope to one clip until its technical and audiovisual acceptance is recorded.
