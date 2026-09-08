"""Keep the pinned WeText implementation; resolve only its model snapshot locally."""

import hashlib
import json


def install_local_text_resources(root):
    import wetext.wetext as implementation

    directory = root / "models/wetext"
    files = json.loads((root / "manifests/wetext-models.json").read_text())
    for item in files:
        path = directory / item["Path"]
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != item["Sha256"]
        ):
            raise RuntimeError(
                "Missing/corrupt text frontend resource: " + item["Path"]
            )

    def local_snapshot(repo_id, *args, **kwargs):
        if repo_id != "pengzhendong/wetext":
            raise RuntimeError("Unexpected text frontend repository")
        return str(directory)

    implementation.snapshot_download = local_snapshot
