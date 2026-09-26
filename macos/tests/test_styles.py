"""Custom-tone loading gate (JEV_TONES quality floor). Offline, no screen, no API."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import styles


class CustomToneTests(unittest.TestCase):
    def setUp(self):
        styles.REJECTED_TONES.clear()

    def tearDown(self):
        styles.REJECTED_TONES.clear()

    def test_short_description_is_refused_with_reason(self):
        with patch.object(styles.userconfig, 'get',
                          return_value='夸我=夸我|好的=嗯'):
            out = styles._custom_tones()
        self.assertNotIn('夸我', out)
        self.assertNotIn('好的', out)
        self.assertTrue(any('「夸我」说明仅 2 字' in r for r in styles.REJECTED_TONES))

    def test_well_formed_tone_loads_and_overrides_builtin(self):
        desc = '像个资深摸鱼选手，把活推得很得体又不失礼'
        with patch.object(styles.userconfig, 'get',
                          return_value=f'摸鱼大师={desc}|夸夸=夸夸群金牌群友，夸细节不空泛，别夸成阴阳怪气'):
            out = styles._custom_tones()
        self.assertEqual(out['摸鱼大师'], desc)
        self.assertIn('夸夸', out)          # 同名覆盖内置
        self.assertEqual(styles.REJECTED_TONES, [])

    def test_exactly_at_floor_loads(self):
        with patch.object(styles.userconfig, 'get',
                          return_value='测试语气=这一句刚好十个字了吗'):
            out = styles._custom_tones()
        self.assertIn('测试语气', out)
        self.assertEqual(styles.REJECTED_TONES, [])

    def test_empty_description_is_refused_not_dropped_silently(self):
        with patch.object(styles.userconfig, 'get', return_value='空说明='):
            out = styles._custom_tones()
        self.assertNotIn('空说明', out)
        self.assertEqual(len(styles.REJECTED_TONES), 1)


class StripLabelTests(unittest.TestCase):
    """strip_label 对全部内置话术成立：加 label 只改 BUILTIN，不该再漏 strip（#70）。"""

    def test_every_builtin_label_strips_from_its_echo(self):
        for name, desc in styles.BUILTIN.items():
            echoed = f"{name}：这是一条候选"
            self.assertEqual(styles.strip_label(echoed), "这是一条候选",
                             msg=f"label「{name}」剥不掉")

    def test_middle_dot_is_optional_in_echo(self):
        # 模型 echo「狗头军师·会撩」时会把「·」当成空格一样丢掉
        self.assertEqual(styles.strip_label("狗头军师会撩：只笑不接球，这轮算我半胜"),
                         "只笑不接球，这轮算我半胜")

    def test_middle_dot_labels_strip_with_dot_and_bold(self):
        self.assertEqual(styles.strip_label("狗头军师·抽离：先去忙，想聊再找我"),
                         "先去忙，想聊再找我")
        self.assertEqual(styles.strip_label("**狗头军师·抽离**：先去忙，想聊再找我"),
                         "先去忙，想聊再找我")

    def test_existing_space_label_still_strips(self):
        self.assertEqual(styles.strip_label("贴吧老哥 v1.0：有一说一，这就去整"),
                         "有一说一，这就去整")
        self.assertEqual(styles.strip_label("贴吧老哥v1.0：有一说一，这就去整"),
                         "有一说一，这就去整")

    def test_plain_reply_is_not_touched(self):
        self.assertEqual(styles.strip_label("先心疼两句，你今天是不是被会灌满了"),
                         "先心疼两句，你今天是不是被会灌满了")


class GoutoujunshiToneTests(unittest.TestCase):
    """狗头军师三话术（#70）：能上下拉框、说明达质量门槛、三件套各自独立占槽。"""

    GOUTOUJUNSHI = ("狗头军师", "狗头军师·会撩", "狗头军师·抽离")

    def test_three_tones_shipped_with_quality_descriptions(self):
        for name in self.GOUTOUJUNSHI:
            self.assertIn(name, styles.PRESETS)
            self.assertGreaterEqual(len(styles.PRESETS[name]), styles.MIN_TONE_DESC_CHARS)

    def test_tones_appear_in_dropdown_labels(self):
        labels = styles.labels()
        for name in self.GOUTOUJUNSHI:
            self.assertIn(name, labels)

    def test_instructions_cover_the_method_distinctions(self):
        # 三件套的差异点来自狗头军师方法论，丢了关键词语气就会并成一种
        self.assertIn("只做一件事", styles.PRESETS["狗头军师"])
        self.assertIn("不油腻", styles.PRESETS["狗头军师·会撩"])
        self.assertIn("不追问", styles.PRESETS["狗头军师·抽离"])

    def test_default_slots_untouched(self):
        # 恋爱向是场景自选，默认三件套仍是职场向，存量用户零感知
        self.assertEqual(styles.DEFAULT_SLOTS, ["高情商话术", "贴吧老哥 v1.0", "阴阳怪气"])


if __name__ == '__main__':
    unittest.main()
