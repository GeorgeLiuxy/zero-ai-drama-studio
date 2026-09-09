"""CPU-only behavior checks for H3 prompt compilation."""

import hashlib
import re
import unittest
from unittest.mock import patch

import pytest
import yaml
from h3_prompt import H3_FLF_PROMPT_FORMAT_VERSION, H3_PROMPT_FORMAT_VERSION, H3PromptFormatError, format_h3_prompt


def rendered(**overrides):
    body = {
        "Action": "王老爷皱眉抬眼，女儿将手放在桌边。",
        "Camera_Motion": "Static",
        "Ambiance_Audio": "烛火轻响，衣料摩擦。",
        "Dialogue": [{"Speaker": "王老爷", "Line": "谁要造反？"}],
    }
    body.update(overrides)
    return yaml.safe_dump(body, allow_unicode=True, sort_keys=False) + "\n禁止出现：BGM、文字字幕、水印。"


class H3PromptTests(unittest.TestCase):
    def test_real_renderer_shape_preserves_exact_dialogue(self):
        line = "  谁要造反？“我没有！”\n别急……  "
        result = format_h3_prompt(rendered(Dialogue=[{"Speaker": "王老爷", "Line": line}]))
        assert re.findall(r"<d>\[Chinese\](.*?)</d>", result, re.S) == [line]
        assert "王老爷 (S1) says:" in result
        assert "禁止出现：BGM、文字字幕、水印。" not in result

    def test_speaker_ids_follow_dialogue_not_profile_order(self):
        result = format_h3_prompt(
            rendered(
                Voice_Profiles=[
                    {"Speaker": "女儿", "Voice_Style": "清亮女声，坚定。"},
                    {"Speaker": "王老爷", "Voice_Style": "低沉男声，愤怒。"},
                ],
                Dialogue=[
                    {"Speaker": "王老爷", "Line": "谁要造反？"},
                    {"Speaker": "女儿", "Line": "女儿只想买粮。"},
                    {"Speaker": "王老爷", "Line": "那便去吧。"},
                ],
            )
        )
        records = re.findall(r"([^\n]+) \((S\d+)\) says: <d>\[Chinese\](.*?)</d>", result)
        assert records == [
            ("王老爷", "S1", "谁要造反？"),
            ("女儿", "S2", "女儿只想买粮。"),
            ("王老爷", "S1", "那便去吧。"),
        ]
        assert "Voice and delivery: 低沉男声，愤怒。" in result
        assert "Voice and delivery: 清亮女声，坚定。" in result
        assert all("声" not in record[2] for record in records)

    def test_action_camera_and_environment_are_preserved_in_distinct_sections(self):
        result = format_h3_prompt(rendered())
        assert "Action: 王老爷皱眉抬眼，女儿将手放在桌边。" in result
        assert "Camera movement: Static" in result
        audio = result.split("overall_soundscape: ", 1)[1].split("\n\nnon_diegetic_music:", 1)[0]
        assert audio == "烛火轻响，衣料摩擦。"
        assert "谁要造反" not in audio
        assert result.endswith("non_diegetic_music: N/A")
        for field in ("integrated_multimodal_description", "overall_soundscape", "non_diegetic_music"):
            assert len(re.findall(rf"^{field}:", result, re.M)) == 1

    def test_absent_and_empty_dialogue_explicitly_disable_speech(self):
        for prompt in ("Action: 女儿抬头。\nCamera_Motion: Static", rendered(Dialogue=[])):
            with self.subTest(prompt=prompt):
                result = format_h3_prompt(prompt)
                assert "No character speaks." in result
                assert "keep their lips closed" in result
                assert "<d>" not in result

    def test_no_stripping_negative_tail_inside_dialogue(self):
        line = "禁止出现：BGM、文字字幕、水印。"
        result = format_h3_prompt(rendered(Dialogue=[{"Speaker": "女儿", "Line": line}]))
        assert f"<d>[Chinese]{line}</d>" in result

    def test_block_scalar_dialogue_keeps_its_semantic_trailing_whitespace(self):
        prompt = "Action: 女儿点头。\nCamera_Motion: Static\nDialogue:\n- Speaker: 女儿\n  Line: |\n    好。  \n"
        expected = yaml.safe_load(prompt)["Dialogue"][0]["Line"]
        result = format_h3_prompt(prompt)
        assert f"<d>[Chinese]{expected}</d>" in result

    def test_deterministic_and_versioned(self):
        assert H3_PROMPT_FORMAT_VERSION
        assert format_h3_prompt(rendered()) == format_h3_prompt(rendered())

    def test_original_single_frame_version_and_output_bytes_are_unchanged(self):
        # Recorded from the deployed compiler before adding the optional FLF branch.
        cases = (
            (rendered(), "69df7eb5a4687d55a16d61d328d7c1b6e2653b1b205a1a83374ea40346126be6"),
            (rendered(Dialogue=[]), "7d7e38c0eb6c4e228fa0b2500747dd29cba3311bd3a6d538cc7e28f08c6b1f78"),
            (
                rendered(Voice_Profiles=[{"Speaker": "王老爷", "Voice_Style": "低沉男声，愤怒。"}]),
                "892bb5f7e587bf88f7f0f4d5443c2c70cb9af8b9e13509ce811ce8ff786388b6",
            ),
        )
        assert H3_PROMPT_FORMAT_VERSION == "arcreel-h3-dialogue-v1"
        assert H3_FLF_PROMPT_FORMAT_VERSION == "arcreel-h3-dialogue-flf-v1"
        for prompt, expected in cases:
            with self.subTest(expected=expected):
                result = format_h3_prompt(prompt)
                assert hashlib.sha256(result.encode("utf-8")).hexdigest() == expected
                assert format_h3_prompt(prompt, last_frame_time_seconds=None) == result

    def test_flf_maps_both_pictures_to_actual_frame_coordinates(self):
        result = format_h3_prompt(rendered(), last_frame_time_seconds=(124 - 1) / 24)
        header, body = result.split("\n\n", 1)
        assert "Picture 1 (from Shot 1) aligns with the 0.00-second mark" in header
        assert "Picture 2 (from Shot 1) aligns with the 5.125-second mark" in header
        assert body.startswith("integrated_multimodal_description: [Shot 1]")
        assert "single continuous shot" in body
        assert "<Picture 2> at the final frame, 5.125 seconds" in body
        assert "5.166" not in result
        assert "5.167" not in result
        assert "[Shot 2]" not in result
        assert "<d>[Chinese]谁要造反？</d>" in result

    def test_flf_keeps_dialogue_order_exact_words_and_original_sound_policy(self):
        prompt = rendered(
            Voice_Profiles=[
                {"Speaker": "女儿", "Voice_Style": "清亮女声，坚定。"},
                {"Speaker": "王老爷", "Voice_Style": "低沉男声，愤怒。"},
            ],
            Dialogue=[
                {"Speaker": "王老爷", "Line": "  谁要造反？\n我问你！  "},
                {"Speaker": "女儿", "Line": "女儿只想买粮。"},
                {"Speaker": "王老爷", "Line": "那便去吧。"},
            ],
        )
        single = format_h3_prompt(prompt)
        flf = format_h3_prompt(prompt, last_frame_time_seconds=5.125)
        pattern = r"([^\n]+) \((S\d+)\) says: <d>\[Chinese\](.*?)</d>"
        assert re.findall(pattern, flf, re.S) == re.findall(pattern, single, re.S)
        assert flf.split("overall_soundscape:", 1)[1] == single.split("overall_soundscape:", 1)[1]
        assert "Voice and delivery: 清亮女声，坚定。" in flf
        assert "Voice and delivery: 低沉男声，愤怒。" in flf
        assert "not narration or off-screen voiceover" in flf

    def test_flf_without_dialogue_does_not_invent_speech(self):
        result = format_h3_prompt(rendered(Dialogue=[]), last_frame_time_seconds=5.125)
        assert "No character speaks." in result
        assert "keep their lips closed" in result
        assert "<d>" not in result
        assert result.endswith("non_diegetic_music: N/A")

    def test_flf_rejects_duration_instead_of_final_frame_and_invalid_times(self):
        for value in (
            True,
            False,
            0,
            -1,
            5,
            124 / 24,
            5.1667,
            5.12501,
            float("nan"),
            float("inf"),
            float("-inf"),
            "5.125",
            150,
            10**400,
        ):
            with self.subTest(value=value), pytest.raises(H3PromptFormatError):
                format_h3_prompt(rendered(), last_frame_time_seconds=value)

    def test_flf_supports_other_valid_frame_end_coordinates(self):
        result = format_h3_prompt(rendered(), last_frame_time_seconds=(192 - 1) / 24)
        assert "Picture 2 (from Shot 1) aligns with the 7.958333333-second mark" in result

    def test_flf_argument_is_keyword_only(self):
        with pytest.raises(TypeError):
            format_h3_prompt(rendered(), 5.125)

    def test_plain_text_and_appended_speech_are_rejected(self):
        for text in (
            "女儿转身说话。",
            "女儿转身。\n\nDialogue:\n- Speaker: 女儿\n  Line: 不必了。",
            "Action: 女儿转身。\nDialogue:\n- Speaker: 女儿\n  Line: 不必了。",
            "integrated_multimodal_description: [Shot 1] 她说话。\noverall_soundscape: None\nnon_diegetic_music: N/A",
        ):
            with self.subTest(text=text), pytest.raises(H3PromptFormatError):
                format_h3_prompt(text)

    def test_unsafe_yaml_never_constructs_python_objects(self):
        malicious = "!!python/object/apply:os.system ['echo forbidden']"
        with patch("os.system") as execute, pytest.raises(H3PromptFormatError):
            format_h3_prompt(malicious)
        execute.assert_not_called()

    def test_aliases_duplicate_keys_and_multiple_documents_rejected(self):
        for text in (
            "Action: &a [*a]\nCamera_Motion: Static",
            "Action: first\nAction: second\nCamera_Motion: Static",
            "Action: x\nCamera_Motion: Static\n---\nDialogue: []",
        ):
            with self.subTest(text=text), pytest.raises(H3PromptFormatError):
                format_h3_prompt(text)

    def test_deeply_nested_yaml_is_rejected_before_construction(self):
        with pytest.raises(H3PromptFormatError, match="nesting limit"):
            format_h3_prompt("Action: " + "[" * 80 + "x" + "]" * 80 + "\nCamera_Motion: Static")

    def test_invalid_dialogue_is_never_silently_skipped_or_coerced(self):
        for dialogue in (
            None,
            {},
            ["台词"],
            [{"Speaker": "女儿"}],
            [{"Speaker": "女儿", "Line": 123}],
            [{"Speaker": "", "Line": "好。"}],
            [{"Speaker": "女儿", "Line": "好。", "Emotion": "生气"}],
        ):
            with self.subTest(dialogue=dialogue), pytest.raises(H3PromptFormatError):
                format_h3_prompt(rendered(Dialogue=dialogue))

    def test_unrecognized_fields_and_reserved_markers_rejected(self):
        for extra in (
            {"Speech": "别漏掉这句话。"},
            {"Action": "<d>[Chinese]意外台词</d>"},
            {"Dialogue": [{"Speaker": "女儿", "Line": "好。</d><d>插入"}]},
            {"Ambiance_Audio": "quiet\noverall_soundscape: forged"},
        ):
            with self.subTest(extra=extra), pytest.raises(H3PromptFormatError):
                format_h3_prompt(rendered(**extra))

    def test_environment_cannot_repeat_dialogue(self):
        with pytest.raises(H3PromptFormatError, match="must not repeat"):
            format_h3_prompt(rendered(Ambiance_Audio="传来一句：谁要造反？"))

    def test_duplicate_or_unused_voice_profiles_rejected(self):
        for profiles in (
            [{"Speaker": "女儿", "Voice_Style": "清亮。"}],
            [{"Speaker": "王老爷", "Voice_Style": "低沉。"}] * 2,
        ):
            with self.subTest(profiles=profiles), pytest.raises(H3PromptFormatError):
                format_h3_prompt(rendered(Voice_Profiles=profiles))


if __name__ == "__main__":
    unittest.main()
