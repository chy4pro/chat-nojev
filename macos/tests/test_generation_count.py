"""Candidate-count configuration and prompt regressions."""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import generate
import settings_config
import styles


class CandidateCountConfigTests(unittest.TestCase):
    def test_settings_persist_candidate_count(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            updated = settings_config.write_settings(
                path, "", {"JEV_CANDIDATES_PER_TONE": "4"})
            self.assertIn("export JEV_CANDIDATES_PER_TONE=4", updated)

    def test_settings_reject_candidate_count_outside_supported_range(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "env"
            for value in ("0", "6", "not-a-number"):
                with self.subTest(value=value), self.assertRaises(ValueError):
                    settings_config.write_settings(
                        path, "", {"JEV_CANDIDATES_PER_TONE": value})
            self.assertFalse(path.exists())


class CandidateCountGenerationTests(unittest.TestCase):
    def test_one_candidate_uses_single_candidate_instructions(self):
        generator = generate.Generator()
        with patch.object(styles, "PER_TONE", 1), \
             patch.object(generator, "_call", return_value="只给一条回复") as call:
            got = generator._one_tone("当前消息", "闲聊", "高情商话术")

        # 上游 _one_tone 回 (texts, error)；判断和分跟着同一次调用回来，所以回一个 dict
        self.assertEqual((got["texts"], got["error"]), (["只给一条回复"], ""))
        self.assertIsNone(got["verdict"])      # 纯文本回答里没有 judgment，不替它编一个
        prompt = call.call_args.args[0]
        self.assertIn("请写 1 条回复候选", prompt)
        self.assertIn("只写一条稳妥", prompt)
        self.assertNotIn("前一条稳妥", prompt)

    def test_multiple_candidates_are_limited_to_configured_count(self):
        generator = generate.Generator()
        response = "第一条\n第二条\n第三条\n第四条"
        with patch.object(styles, "PER_TONE", 3), \
             patch.object(generator, "_call", return_value=response) as call:
            got = generator._one_tone("当前消息", "闲聊", "高情商话术")

        self.assertEqual((got["texts"], got["error"]),
                         (["第一条", "第二条", "第三条"], ""))
        prompt = call.call_args.args[0]
        self.assertIn("请写 3 条回复候选", prompt)
        # 上游要求「只输出 3 行」纯文本；这一版要一个 JSON 对象，条数要求落在 replies 上
        self.assertIn("恰好 3 个字符串的数组", prompt)
        self.assertIn("前一条稳妥", prompt)


if __name__ == "__main__":
    unittest.main()
