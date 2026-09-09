"""Compile ArcReel's video YAML into the pinned H3 FL2VA prompt contract.

This module does not translate, rewrite, or synthesize speech. Unsupported input
is rejected before inference so an unrecognized dialogue section cannot vanish.
"""

from __future__ import annotations

import math
import re
import unicodedata

import yaml

H3_PROMPT_FORMAT_VERSION = "arcreel-h3-dialogue-v1"
H3_FLF_PROMPT_FORMAT_VERSION = "arcreel-h3-dialogue-flf-v1"
_NEGATIVE_TAIL = "禁止出现：BGM、文字字幕、水印。"
_ALLOWED_FIELDS = {"Action", "Camera_Motion", "Ambiance_Audio", "Dialogue", "Voice_Profiles"}
_MAX_INPUT_CHARS = 60_000
_MAX_TOKENS = 6_000
_MAX_DEPTH = 32
_RESERVED = re.compile(r"</?d(?:\s|>)|<scenetrans>|<cutoff>", re.IGNORECASE)
_CORE_FIELD = re.compile(
    r"^\s*(integrated_multimodal_description|overall_soundscape|non_diegetic_music)\s*:",
    re.MULTILINE,
)


class H3PromptFormatError(ValueError):
    """The prompt cannot be compiled without changing or losing information."""


class _StrictSafeLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise H3PromptFormatError("H3 prompt requires YAML mappings")
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                raise H3PromptFormatError("H3 prompt YAML keys must be strings")
            if key in result:
                raise H3PromptFormatError("H3 prompt contains a duplicate YAML key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _parse(prompt: str) -> dict:
    if not isinstance(prompt, str) or not prompt.strip():
        raise H3PromptFormatError("H3 prompt must be a non-empty string")
    if len(prompt) > _MAX_INPUT_CHARS:
        raise H3PromptFormatError("H3 prompt exceeds the parser size limit")
    text = prompt
    # Only remove ArcReel's exact standalone suffix, never text inside a scalar.
    tail = re.search(r"(?m)^" + re.escape(_NEGATIVE_TAIL) + r"(?:\r?\n)*\Z", text)
    if tail:
        text = text[: tail.start()]
    try:
        depth = 0
        for index, token in enumerate(yaml.scan(text)):
            if index >= _MAX_TOKENS:
                raise H3PromptFormatError("H3 prompt exceeds the YAML token limit")
            if isinstance(token, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)):
                raise H3PromptFormatError("H3 prompt YAML aliases, anchors, and explicit tags are unsupported")
            if isinstance(
                token,
                (
                    yaml.BlockMappingStartToken,
                    yaml.BlockSequenceStartToken,
                    yaml.FlowMappingStartToken,
                    yaml.FlowSequenceStartToken,
                ),
            ):
                depth += 1
                if depth > _MAX_DEPTH:
                    raise H3PromptFormatError("H3 prompt exceeds the YAML nesting limit")
            elif isinstance(token, (yaml.BlockEndToken, yaml.FlowMappingEndToken, yaml.FlowSequenceEndToken)):
                depth -= 1
        data = yaml.load(text, Loader=_StrictSafeLoader)
    except yaml.YAMLError as exc:
        raise H3PromptFormatError(
            "H3 requires structured ArcReel YAML (Action, Camera_Motion, optional Dialogue); "
            "free text with appended speech and native H3 prompts are unsupported"
        ) from exc
    if not isinstance(data, dict) or not {"Action", "Camera_Motion"}.issubset(data):
        raise H3PromptFormatError(
            "H3 requires structured ArcReel YAML with Action and Camera_Motion; "
            "unsupported prompt formats cannot be passed through safely"
        )
    if set(data) - _ALLOWED_FIELDS:
        raise H3PromptFormatError("H3 prompt contains unsupported top-level fields")
    return data


def _text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise H3PromptFormatError(f"{field} must be a {'possibly empty' if allow_empty else 'non-empty'} string")
    if _RESERVED.search(value) or _CORE_FIELD.search(value):
        raise H3PromptFormatError(f"{field} contains reserved H3 prompt syntax")
    # Keep the original string, especially dialogue whitespace and punctuation.
    return value


def _speaker(value: object, field: str) -> tuple[str, str]:
    name = _text(value, field)
    if "\n" in name or "\r" in name:
        raise H3PromptFormatError(f"{field} must be a single-line speaker name")
    return name, unicodedata.normalize("NFC", name.strip())


def _records(value: object, field: str, required_keys: set[str]) -> list[dict]:
    if not isinstance(value, list):
        raise H3PromptFormatError(f"{field} must be an array")
    for item in value:
        if not isinstance(item, dict) or set(item) != required_keys:
            raise H3PromptFormatError(f"{field} entries must contain exactly {', '.join(sorted(required_keys))}")
    return value


def _last_frame_label(value: float) -> str:
    """Validate a 24-fps last-frame coordinate, not the clip's total duration."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise H3PromptFormatError("last_frame_time_seconds must be a finite positive number")
    try:
        valid_number = math.isfinite(value) and 0 < value < 150
    except OverflowError:
        valid_number = False
    if not valid_number:
        raise H3PromptFormatError("last_frame_time_seconds must be a finite positive number below 150")
    frame_index = value * 24
    integer_index = round(frame_index)
    # The pinned node anchors the last image at frame_count - 1. A total
    # duration of frame_count / 24 is one frame too late and fails this grid.
    if not math.isclose(frame_index, integer_index, rel_tol=0, abs_tol=1e-7) or (integer_index + 1) % 17 != 5:
        raise H3PromptFormatError(
            "last_frame_time_seconds must equal (frame_count - 1) / 24 for a 17k+5 frame count; "
            "do not pass the clip duration"
        )
    # Keep the exact 5.125-second anchor. Rounding to two decimals would move
    # the prompt away from frame 123; recurring thirds use nanosecond precision.
    return f"{integer_index / 24:.9f}".rstrip("0").rstrip(".")


def format_h3_prompt(prompt: str, *, last_frame_time_seconds: float | None = None) -> str:
    """Compile canonical video YAML, preserving dialogue order and exact text.

    Speaker IDs follow first dialogue appearance, independent of profile order.
    The same speaker always reuses its ID within this clip; this is not a claim
    that FL2VA can lock a voice across separately generated clips.

    A last frame uses its actual frame coordinate: (124 - 1) / 24 = 5.125
    seconds for the current clip, not the 124 / 24 clip duration. Without it,
    the original v1 prompt bytes remain unchanged for persistent task identity.
    """
    last_frame_label = _last_frame_label(last_frame_time_seconds) if last_frame_time_seconds is not None else None
    data = _parse(prompt)
    action = _text(data["Action"], "Action")
    camera = _text(data["Camera_Motion"], "Camera_Motion", allow_empty=True)
    ambiance = _text(data.get("Ambiance_Audio", ""), "Ambiance_Audio", allow_empty=True)
    profiles: dict[str, str] = {}
    for item in _records(data.get("Voice_Profiles", []), "Voice_Profiles", {"Speaker", "Voice_Style"}):
        _, key = _speaker(item["Speaker"], "Voice_Profiles.Speaker")
        if key in profiles:
            raise H3PromptFormatError("Voice_Profiles contains duplicate speaker profiles")
        profiles[key] = _text(item["Voice_Style"], "Voice_Profiles.Voice_Style")

    dialogue = []
    identities: dict[str, tuple[str, str]] = {}
    for item in _records(data.get("Dialogue", []), "Dialogue", {"Speaker", "Line"}):
        name, key = _speaker(item["Speaker"], "Dialogue.Speaker")
        line = _text(item["Line"], "Dialogue.Line")
        if line.strip() in ambiance:
            raise H3PromptFormatError("Ambiance_Audio must not repeat dialogue text")
        if key not in identities:
            identities[key] = (name, f"S{len(identities) + 1}")
        dialogue.append((key, line))
    if set(profiles) - set(identities):
        raise H3PromptFormatError("Voice_Profiles contains a speaker without any Dialogue")

    header = (
        "For the target video, at 0.00 seconds into the target video, <Picture 1> "
        "(from [Shot 1]) is fully referenced.\n\n"
    )
    description = [
        "[Shot 1] Continue from <Picture 1> in a single continuous shot, preserving "
        "the characters' appearance, clothing, composition, and spatial relationships.",
        f"Action: {action}",
        f"Camera movement: {camera or 'Static shot.'}",
    ]
    if last_frame_label is not None:
        header = (
            "How the reference pictures align with the target video — "
            "Picture 1 (from Shot 1) aligns with the 0.00-second mark of the target video; "
            f"Picture 2 (from Shot 1) aligns with the {last_frame_label}-second mark of the target video.\n\n"
        )
        description[0] = (
            "[Shot 1] Begin in the pose and composition established by <Picture 1>, "
            "then move naturally in a single continuous shot toward <Picture 2>, "
            "keeping each character's identity and clothing consistent."
        )
        description.append(
            "Develop the requested actions through observable intermediate motion, "
            "gradually reaching the final poses, object positions, spacing, and framing "
            f"shown in <Picture 2> at the final frame, {last_frame_label} seconds. "
            "Keep body, hand, prop, and camera movement continuous throughout; do not "
            "cut, crossfade, teleport, or substitute a sudden final-frame jump."
        )
    if dialogue:
        for key, (name, speaker_id) in identities.items():
            voice = profiles.get(key)
            description.append(
                f"On-screen speaker {name} ({speaker_id})." + (f" Voice and delivery: {voice}" if voice else "")
            )
        description.append(
            "The following dialogue is spoken in order, with no added or repeated lines. "
            "Each speaker's lips synchronize with their own audible words; other characters "
            "listen with their lips closed. Finish all words naturally before the shot ends. "
            "These are on-screen character voices, not narration or off-screen voiceover."
        )
        for key, line in dialogue:
            name, speaker_id = identities[key]
            description.append(f"{name} ({speaker_id}) says: <d>[Chinese]{line}</d>")
    else:
        description.append(
            "No character speaks. All visible characters keep their lips closed. "
            "No dialogue, narration, or off-screen voiceover."
        )
    description.append("No on-screen text, subtitles, or watermarks.")
    soundscape = ambiance or "Subtle non-verbal room tone and natural sounds from the visible actions."
    return (
        header
        + "integrated_multimodal_description: "
        + "\n".join(description)
        + "\n\noverall_soundscape: "
        + soundscape
        + "\n\nnon_diegetic_music: N/A"
    )
