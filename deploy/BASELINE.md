# Zero AI Drama Studio: baseline before grid fixes

Baseline date: 2026-09-08. This commit captures the current application and deployment source before changing the grid storyboard pipeline. It is a rollback reference, not a release-quality or production-acceptance claim.

Upstream: https://github.com/ArcReel/ArcReel

Upstream starting commit: `67ec8ed0c49c25bc45be523505ac0cf7e43eb0ce`.

Included: existing local Codex text/image and Agent integration, current script review changes, frontend/backend/tests and dependency locks, AutoDL worker source/workflows/model manifests, and the deployment plan. Existing upstream history and license are retained.

Excluded: live `.env`, passwords/API tokens, Codex account/session data, project database, scripts and user-generated media under the runtime project directory, model weights, virtual environments, caches, logs and machine-specific migration reports. Code rollback does not restore those assets; back them up separately and account for database/schema compatibility before switching code.

## Known issues and validation boundary

- Grid records may specify 3x3 while the generated image contains 4x3 uneven panels. The current splitter still divides by the recorded rows/columns, and can include adjacent panels in a saved storyboard. This issue is intentionally preserved in the baseline.
- Layout acceptance, actual boundary validation, and shot-to-panel correspondence need further work. Uniform output dimensions alone do not establish correct cropping.
- Wan/CosyVoice preparation and small GPU environment checks are distinct from full video/audio generation acceptance.
- H3 weight downloading was started, and its native CPU-only node schema check passed. H3 inference and ArcReel routing are not validated. Live download status is maintained on AutoDL, not in this Git snapshot.
- This is a source snapshot. The full application release gates have not been re-run for this backup, and existing problems are not silently fixed during baseline capture.
- Backup checks: Python source parsing passed; worker gateway tests 11 passed; existing grid splitter tests 8 passed with the Windows-compatible `--timeout-method=thread` option. These splitter tests cover regular grids, not the observed malformed-grid case. Secret scanning found no local credential values; the upstream fake private-key fixture in `test_logging_utils.py` is retained. Whitespace findings were limited to copied Markdown hard breaks and the unchanged upstream H3 license.

## Worker snapshot and restoration

`deploy/media-worker/` is the versioned copy of the sibling `media-worker` deployment directory. The running application's location and the existing sibling directory have not been moved. Local worker launch/upload utilities assume this layout:

```text
workspace/
  ArcReel/       # this repository
  media-worker/  # restore a copy of deploy/media-worker here
```

For a new checkout, clone this repository into `ArcReel`, then copy the versioned worker directory into the sibling location before running its local scripts. Supply credentials through the existing prompt/environment mechanisms. Source ZIP caches are excluded; if replaying the older CosyVoice preparation uploader, recreate the CosyVoice and Matcha source archives from the immutable revisions in `source-lock.json` first.

Future worker changes must be copied into `deploy/media-worker/` before a new commit. Application code is tracked directly in this repository. Preserve a separate backup of live data before any code rollback.
