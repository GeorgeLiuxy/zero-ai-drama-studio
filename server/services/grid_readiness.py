"""Read actual composite geometry and usable first frames without changing assets."""

from __future__ import annotations

import json
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict

from PIL import Image

from lib.artifact_activation import active_artifact_currency_resolver, resolve_artifact_episode
from lib.artifact_input_claims import resolve_usable_storyboard_video_inputs
from lib.artifact_manifest import ArtifactManifestError
from lib.grid.layout import video_aspect_ratio_of
from lib.grid.models import GridGeneration
from lib.grid.review import inspect_grid_source, normalized_ratio, require_reviewed_grid_first_frame
from lib.path_safety import safe_join
from lib.project_manager import ProjectManager
from lib.storyboard_sequence import get_storyboard_items, resolve_storyboard_video_inputs


class GridReadiness(TypedDict):
    image_exists: bool
    geometry_valid: bool | None
    issues: list[str]
    ready_scene_ids: list[str]


def _read_mapping(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("project and script snapshots must be JSON objects")
    return value


def _ready_scenes(
    project_path: Path, project: dict, grids: Sequence[GridGeneration]
) -> dict[tuple[str, int], set[str]]:
    """Use video-input admission, including current bindings and review evidence.

    A replaced composite does not invalidate already selected first frames. An
    independent replacement is likewise judged by its own current version.
    """
    ready: dict[tuple[str, int], set[str]] = {}
    try:
        resolver = active_artifact_currency_resolver(project_path, project)
        ratio_width, ratio_height = normalized_ratio(video_aspect_ratio_of(project))
    except (OSError, ValueError, ArtifactManifestError):
        return ready
    for grid in grids:
        key = (grid.script_file, grid.episode)
        if key in ready:
            continue
        ready[key] = set()
        try:
            filename = ProjectManager.normalize_script_filename(grid.script_file)
            script = _read_mapping(safe_join(project_path / "scripts", filename))
            if resolve_artifact_episode(project=project, script=script, script_filename=filename) != grid.episode:
                continue
            items, id_field, _, _, _ = get_storyboard_items(script)
        except (OSError, ValueError, ArtifactManifestError):
            continue
        for item in items:
            if not isinstance(item, dict) or not item.get(id_field):
                continue
            resource_id = str(item[id_field])
            try:
                first_frame, _ = resolve_storyboard_video_inputs(
                    project_path=project_path, resource_id=resource_id, item=item
                )
                # Reject unreviewed legacy crops before rebuilding Manifest bases.
                require_reviewed_grid_first_frame(
                    project_path, resource_id, first_frame, video_aspect_ratio_of(project)
                )
                first_frame, _ = resolve_usable_storyboard_video_inputs(
                    project_path=project_path,
                    project=project,
                    episode=grid.episode,
                    resource_id=resource_id,
                    item=item,
                    resolver=resolver,
                )
                with Image.open(first_frame) as image:
                    if image.width * ratio_height != image.height * ratio_width:
                        continue
                    image.verify()
            except (OSError, ValueError, ArtifactManifestError):
                continue
            ready[key].add(resource_id)
    return ready


@lru_cache(maxsize=64)
def _inspect_composite(
    path: Path, file_signature: tuple[int, int, int], grid_json: str, aspect: str
) -> tuple[bool, tuple[str, ...]]:
    """Cache display checks by file revision; split confirmation always rereads bytes."""
    grid = GridGeneration.from_dict(json.loads(grid_json))
    with Image.open(path) as image:
        report = inspect_grid_source(image, grid, aspect)
    return report["valid"], tuple(report["issues"])


def describe_grid_readiness(project_path: Path, grids: Sequence[GridGeneration]) -> list[dict[str, Any]]:
    """Enrich read responses; ``completed`` alone never certifies video inputs."""
    try:
        project = _read_mapping(project_path / "project.json")
        aspect = video_aspect_ratio_of(project)
    except (OSError, ValueError):
        project, aspect = {}, None
    ready = _ready_scenes(project_path, project, grids) if aspect else {}
    result = []
    for grid in grids:
        state: GridReadiness = {
            "image_exists": False,
            "geometry_valid": None,
            "issues": [],
            "ready_scene_ids": [
                sid for sid in grid.scene_ids if sid in ready.get((grid.script_file, grid.episode), set())
            ],
        }
        try:
            if grid.grid_image_path:
                path = safe_join(project_path, grid.grid_image_path)
                if path != safe_join(project_path, f"grids/{grid.id}.png"):
                    raise ValueError("composite path does not match grid identity")
                state["image_exists"] = path.is_file()
                if state["image_exists"]:
                    if aspect is None:
                        state["issues"] = ["project_unavailable"]
                    else:
                        stat = path.stat()
                        valid, issues = _inspect_composite(
                            path,
                            (stat.st_mtime_ns, stat.st_ctime_ns, stat.st_size),
                            json.dumps(grid.to_dict(), sort_keys=True),
                            aspect,
                        )
                        state["geometry_valid"] = valid
                        state["issues"] = list(issues)
                else:
                    state["issues"] = ["grid_image_missing"]
        except (OSError, ValueError):
            state["geometry_valid"] = False
            state["issues"] = ["grid_image_unreadable"]
        result.append({**grid.to_dict(), "readiness": state})
    return result
