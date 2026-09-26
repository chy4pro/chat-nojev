"""issue_triage 规则引擎回归（不触网，只测纯函数；gh IO 不在此测）。

用例锚定真实存量 issue：#86（零标签）、#37（标题 "problem"）、#95（缺前缀、
小节标题带后缀）、#63/#45（【】开头维护者风格）。改 AREA_KEYWORDS /
TYPE_KEYWORDS 规则时跑：uv run python -m unittest tests.test_issue_triage
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ci import issue_triage as t  # noqa: E402

# 真实 #86 正文（四小节齐全、零标签的典型）
ISSUE_86_BODY = (
    "## 现象\n"
    "仓库的 NOTICE 与 README 都要求分发时保留 NOTICE 和 LICENSE。"
    "当前 packaging/build_app.sh 明确将 LICENSE 复制到 .app 资源目录，"
    "但没有复制或检查 NOTICE。\n\n"
    "## 复现步骤\n1. 在 macOS 上运行 ./packaging/build_app.sh。\n"
    "2. 查看生成的 .app/Contents/Resources/app/ 目录。\n\n"
    "## 日志\n静态审阅脚本，未在本机执行 macOS 打包。\n\n"
    "## 环境\n- macOS 版本：未执行打包\n- 启动方式：.app 构建\n"
)

# 真实 #95 标题：无前缀、正文小节带后缀「## 现象（…）」
ISSUE_95_TITLE = (
    "国内网络下载/启动被 huggingface.co 拖垮："
    "首次下载超时拦截新用户，已缓存用户每次启动仍联网检查"
)
ISSUE_95_BODY = (
    "## 现象（2026-09-24 用户反馈 + 真机日志）\n\n"
    "1. **首次下载**：离线模型（~3.8 GB）走 huggingface.co，国内网络不可达\n"
    "2. **已缓存用户**：每次启动仍向 huggingface.co 发 revision 检查请求\n"
)


def make_issue(number, title, body, labels=()):
    return {
        "number": number,
        "title": title,
        "body": body,
        "labels": [{"name": l} for l in labels],
    }


class ClassifyTypeTest(unittest.TestCase):
    def test_prefix_bug(self):
        self.assertEqual(t.classify_type("[Bug] 打包缺 NOTICE", ""), "bug")

    def test_prefix_case_insensitive(self):
        self.assertEqual(t.classify_type("[BUG] 打包缺 NOTICE", ""), "bug")

    def test_prefix_feature(self):
        self.assertEqual(t.classify_type("[Feature] 支持 openrouter", ""), "enhancement")

    def test_keyword_bug_from_real_37(self):
        # 真实 #37：标题 "problem"（无前缀），正文「静默退出 / 无任何错误弹窗」
        body = "首次启动 jev-jarvis，进程静默退出，无任何错误弹窗或提示。"
        self.assertEqual(t.classify_type("problem", body), "bug")

    def test_weak_evidence_returns_none_from_real_95(self):
        # 真实 #95：标题无任何类型信号，正文也只有一个弱信号词 → 宁可不打
        self.assertIsNone(t.classify_type(ISSUE_95_TITLE, ISSUE_95_BODY))

    def test_tie_returns_none(self):
        # 「怎么」(question 2 分) vs「希望/支持」(enhancement 2 分) 并列 → None
        self.assertIsNone(t.classify_type("怎么自定义提示词？", "希望支持这个能力"))


class ClassifyAreasTest(unittest.TestCase):
    def test_real_86_packaging_only(self):
        # 真实 #86：打包/.app/NOTICE → 只应命中 area/packaging
        self.assertEqual(
            t.classify_areas("[Bug] 打包的 .app 未包含 NOTICE", ISSUE_86_BODY),
            ["area/packaging"],
        )

    def test_real_95_judge_hit(self):
        # 真实 #95：huggingface → area/judge 必命中；下载/启动 → packaging 也在
        areas = t.classify_areas(ISSUE_95_TITLE, ISSUE_95_BODY)
        self.assertIn("area/judge", areas)
        self.assertIn("area/packaging", areas)

    def test_no_hit_returns_empty(self):
        self.assertEqual(t.classify_areas("随便聊聊", "没有任何模块信号"), [])

    def test_body_only_soup_resisted(self):
        # 横切正文（FAQ/公告类）提一嘴各模块太常见：纯正文 2 分不收，需 ≥3
        body = "正文里提到生成、回复，也提到判断和读屏、识别，还涉及打包与安装。"
        self.assertEqual(t.classify_areas("一段没有模块词的标题", body), [])

    def test_max_three_areas(self):
        text = " ".join(k for kws in t.AREA_KEYWORDS.values() for k in kws)
        self.assertLessEqual(len(t.classify_areas(text, text)), 3)


class PlanIssueAreaGuardTest(unittest.TestCase):
    def test_existing_area_blocks_additions(self):
        # 真实 #78 形状：维护者已挑 area/perception，正文再提判断/回复也不追加
        issue = make_issue(78, "[Bug] yolo模型有概率识别对方最新消息",
                           "识别偶尔出错，导致判断和候选回复用到错误内容。\n\n## 现象\nx\n",
                           labels=["bug", "area/perception"])
        plan = t.plan_issue(issue)
        self.assertEqual([l for l in plan.add_labels if l.startswith("area/")], [])

    def test_bracket_title_no_section_append(self):
        # 真实 #63 形状：【】开头的维护者治理 issue 不套模板、不补正文小节
        issue = make_issue(63, "【工程】治理并行协作冲突：tests/ 按模块拆分", "随便写的说明",
                           labels=["enhancement", "area/docs"])
        plan = t.plan_issue(issue)
        self.assertIsNone(plan.body_appendix)
        self.assertIsNone(plan.new_title)


class CheckTitleTest(unittest.TestCase):
    def test_real_37_adds_prefix(self):
        self.assertEqual(t.check_title("problem", "bug"), "[Bug] problem")

    def test_real_95_adds_prefix(self):
        self.assertEqual(t.check_title(ISSUE_95_TITLE, "bug"), "[Bug] " + ISSUE_95_TITLE)

    def test_already_prefixed_untouched(self):
        self.assertIsNone(t.check_title("[Bug] 打包缺 NOTICE", "bug"))

    def test_lowercase_prefix_normalized(self):
        self.assertEqual(t.check_title("[bug]x", "bug"), "[Bug] x")

    def test_conflicting_tag_untouched(self):
        # 标题 [Feature] 与类型标签 bug 冲突 → 留给人工，不动
        self.assertIsNone(t.check_title("[Feature] 自定义提示词", "bug"))

    def test_maintainer_bracket_style_untouched(self):
        # 真实 #63/#45：【】开头的维护者刻意风格不套前缀
        self.assertIsNone(t.check_title("【工程】治理并行协作冲突", "enhancement"))
        self.assertIsNone(t.check_title("【勿认领】隐私政策声明", "documentation"))

    def test_question_documentation_have_no_prefix(self):
        self.assertIsNone(t.check_title("怎么配置 env？", "question"))
        self.assertIsNone(t.check_title("FAQ 补充", "documentation"))


class FindMissingSectionsTest(unittest.TestCase):
    def test_complete_bug_body(self):
        self.assertEqual(t.find_missing_sections(ISSUE_86_BODY, "bug"), [])

    def test_missing_section_detected(self):
        body = "## 现象\n闪退\n\n## 复现步骤\n1. 打开\n"
        self.assertEqual(t.find_missing_sections(body, "bug"), ["日志", "环境"])

    def test_suffixed_heading_not_missing(self):
        # 真实 #95 写法：小节标题带后缀，不算缺失
        missing = t.find_missing_sections(ISSUE_95_BODY, "bug")
        self.assertNotIn("现象", missing)

    def test_enhancement_sections(self):
        self.assertEqual(t.find_missing_sections("", "enhancement"),
                         ["想要什么", "什么场景下用"])

    def test_type_without_template(self):
        self.assertEqual(t.find_missing_sections("", "question"), [])


class BuildBodyAppendixTest(unittest.TestCase):
    def test_contains_marker_and_sections(self):
        text = t.build_body_appendix(["环境", "日志"], "bug")
        self.assertIn(t.MARKER, text)
        self.assertIn("## 环境", text)
        self.assertIn("## 日志", text)


class PlanIssueTest(unittest.TestCase):
    def test_real_86_unlabeled(self):
        plan = t.plan_issue(make_issue(86, "[Bug] 打包的 .app 未包含 NOTICE", ISSUE_86_BODY))
        self.assertIn("bug", plan.add_labels)
        self.assertIn("area/packaging", plan.add_labels)
        self.assertIsNone(plan.new_title)  # 前缀已合规
        self.assertIsNone(plan.body_appendix)  # 四小节齐全
        self.assertTrue(plan.has_writes)

    def test_multiple_type_labels_converge(self):
        issue = make_issue(1, "[Bug] 读屏空帧", "## 现象\n读屏空帧\n",
                           labels=["bug", "enhancement", "area/perception"])
        plan = t.plan_issue(issue)
        self.assertEqual(plan.remove_labels, ["enhancement"])  # 恰好保留一个类型
        self.assertNotIn("bug", plan.add_labels)

    def test_unclassifiable_help_only(self):
        plan = t.plan_issue(make_issue(2, "随便聊聊", "看不出是什么性质的反馈"))
        self.assertTrue(plan.help_only)
        self.assertIn("类型标签", plan.help_text)

    def test_marker_body_not_re_appended(self):
        body = "正文被机器人补齐过\n" + t.MARKER  # 缺「现象」等小节但已带标记
        plan = t.plan_issue(make_issue(3, "[Bug] 读屏空帧", body, labels=["bug"]))
        self.assertIsNone(plan.body_appendix)

    def test_no_writes_when_compliant(self):
        body = ISSUE_86_BODY + "\n补充：构建日志见上。"
        issue = make_issue(4, "[Bug] 打包的 .app 未包含 NOTICE", body,
                           labels=["bug", "area/packaging"])
        plan = t.plan_issue(issue)
        self.assertFalse(plan.has_writes)


class RenderCommentTest(unittest.TestCase):
    def test_lists_actions_and_marker(self):
        plan = t.plan_issue(make_issue(86, "[Bug] 打包的 .app 未包含 NOTICE", ISSUE_86_BODY))
        text = t.render_comment(plan)
        self.assertIn(t.MARKER, text)
        self.assertIn("area/packaging", text)


if __name__ == "__main__":
    unittest.main()
