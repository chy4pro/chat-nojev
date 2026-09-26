#!/usr/bin/env python3
"""Issue 自动 triage：格式修复 + 自动打标签（每小时定时跑，见 .github/workflows/issue-triage.yml）。

规则依据（改规则前先对照原文，勿凭记忆）：
- .github/ISSUE_TEMPLATE/bug_report.md / feature_request.md —— 标题前缀与正文小节
- CONTRIBUTING.md「Issue 标签约定」—— 类型标签必打且恰好一个、area/* 可多打、宁可晚打不要错打

设计约束：
- stdlib-only，GitHub 读写全走 gh 子进程（runner 预装、鉴权与分页现成），零第三方依赖
- 纯函数（classify_type / classify_areas / check_title / find_missing_sections）与 gh IO
  分离，tests/test_issue_triage.py 只测纯函数
- 幂等：每轮全量扫 open issue，只对仍有缺口的 issue 动手；说明评论带
  jev-issue-triage 标记，已有则原地改写，绝不每小时重复评论；正文补齐块同样带标记，
  已带标记的正文不再追加，防止重复堆叠
- --dry-run 只打印将执行的动作清单，不做任何写操作

边界（刻意不做）：
- 不碰已关闭 issue、不碰 bot 作者的 issue（防自触发循环）
- 不改写用户已有正文内容，只追加缺失小节；question/documentation 无模板，不定义标题前缀
- 标题以「【」开头视为维护者刻意风格（【工程】【勿认领】等），不套模板前缀、不补正文小节
- issue 已有任一 area/* 标签视为维护者裁决过，机器人不再追加 area（只补类型标签）
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# 规则常量（与模板、CONTRIBUTING.md 对齐）
# ---------------------------------------------------------------------------

MARKER = "<!-- jev-issue-triage -->"

# 类型标签必打且恰好一个；顺序即多标签并存时的保留优先级
TYPE_LABELS = ("bug", "enhancement", "question", "documentation")

# 标题前缀（来自两个模板的 title 字段）；question/documentation 无模板，故无前缀
TITLE_PREFIX = {
    "bug": "[Bug] ",
    "enhancement": "[Feature] ",
}

# 正文小节（来自两个模板）；检测容忍「## 现象（补充说明）」这类带后缀的写法
TEMPLATE_SECTIONS = {
    "bug": ("现象", "复现步骤", "日志", "环境"),
    "enhancement": ("想要什么", "什么场景下用"),
}

# 补齐小节时的占位提示（引导作者填写，措辞取自模板注释）
SECTION_PLACEHOLDER = {
    "bug": {
        "现象": "<!-- 由机器人按模板补齐：一句话——做了什么操作、期望发生什么、实际发生什么 -->",
        "复现步骤": "<!-- 由机器人按模板补齐：1. … 2. … -->",
        "日志": "<!-- 由机器人按模板补齐：`tail -40 ~/Library/Logs/jev-jarvis.log` 的输出（不含消息正文，可放心贴） -->",
        "环境": "<!-- 由机器人按模板补齐：macOS 版本 / 启动方式 / 判断层与生成层（不确定写「默认」） -->",
    },
    "enhancement": {
        "想要什么": "<!-- 由机器人按模板补齐：一句话说清期望的能力或行为 -->",
        "什么场景下用": "<!-- 由机器人按模板补齐：谁、在什么情况下、现在的做法哪里不够 -->",
    },
}

# 类型分类关键词（标题前缀识别不到时的兜底）。命中即计分：标题 2 分、正文 1 分，
# 每个关键词只计一次。得分要求「最高且比第二名高 ≥2 分」才下结论，否则宁可不打
# （CONTRIBUTING：宁可晚打不要错打）。匹配统一走 _norm：小写 + 下划线转空格。
TYPE_KEYWORDS = {
    "bug": (
        "报错", "错误", "失败", "崩溃", "闪退", "卡死", "卡住", "无法", "打不开",
        "不起作用", "失效", "不生效", "误判", "异常", "退出",
        "bug", "problem", "error", "crash", "not work",
    ),
    "enhancement": (
        "建议", "希望", "支持", "能不能", "可不可以", "增加", "添加", "新增",
        "优化", "改善", "美化", "接入", "统一", "feature", "新功能",
    ),
    "question": ("请问", "如何", "怎么", "会不会", "是不是", "吗", "how to"),
    "documentation": ("文档", "readme", "faq", "说明", "未披露", "隐私政策", "贡献"),
}

# area/* 分类关键词（来源见 CONTRIBUTING「Issue 标签约定」的八个模块口径）。
# 打分：标题每个命中关键词 2 分、正文 1 分（可叠加）。门槛刻意偏严（宁可晚打）：
# 标题命中过 → 总分 ≥2 即收；纯正文命中 → 需 ≥3（FAQ/公告类横切正文遍地都是
# 模块词，实测真实 #45/#63/#64 会命中一堆 area）。按得分排序，最多取 3 个。
AREA_KEYWORDS = {
    "area/perception": (
        "ocr", "读屏", "识别", "截图", "screencapture", "vision", "yolo",
        "选窗", "主窗", "独立聊天窗", "窗口指纹", "气泡", "抓图", "sender",
    ),
    "area/judge": (
        "判断", "意图", "decider", "typesafe", "风险", "prompt", "huggingface",
        "hf hub", "本地模型", "模型下载", "模型加载", "from pretrained", "预热",
        "微调", "标注",
    ),
    "area/generate": (
        "生成", "候选", "回复", "端点", "渠道", "openrouter", "deepseek",
        "智谱", "ollama", "api key", "文案",
    ),
    "area/hud": (
        "悬浮窗", "悬浮面板", "面板", "overlay", "透明度", "吸附", "置顶",
        "进度条", "界面", "弹窗", "视觉", "样式", "校准",
    ),
    "area/fill": (
        "填入", "输入框", "输入区", "键盘事件", "草稿", "一键填", "剪贴板",
        "axuielement", "ax 输入",
    ),
    "area/config": ("userconfig", "配置文件", "配置项", "配置读取", "凭据", "env"),
    "area/packaging": (
        "打包", "安装", "启动", "构建", "发版", "发布", ".app", "zip", "签名",
        "公证", "intel", "x86", "arm64", "芯片", "brew", "homebrew", "下载",
        "build", "release", "notice", "license",
    ),
    "area/docs": ("文档", "readme", "faq", "隐私政策", "贡献指南", "contributing", "wiki"),
}

# 标题已有的 [Bug]/[Feature] 标签（大小写不敏感）；与其他标签冲突时不动标题
_TITLE_TAG_RE = re.compile(r"^\s*\[(bug|feature)\]\s*", re.IGNORECASE)


# ---------------------------------------------------------------------------
# 纯函数（可单测，不触网）
# ---------------------------------------------------------------------------

def _norm(s: str) -> str:
    """匹配口径统一：小写 + 下划线转空格（api_key → api key、build_app.sh → build app.sh）。"""
    return (s or "").lower().replace("_", " ")


def _score(title_norm: str, body_norm: str, keywords: tuple[str, ...]) -> int:
    """关键词命中计分：标题 2 分、正文 1 分，每个关键词只计一次（标题优先）。"""
    score = 0
    for kw in keywords:
        k = _norm(kw)
        if k in title_norm:
            score += 2
        elif k in body_norm:
            score += 1
    return score


def classify_type(title: str, body: str) -> str | None:
    """类型标签分类：先认标题前缀（最可靠），退化为关键词打分。

    关键词路径要求「最高分且领先第二名 ≥2 分」才返回，并列/不足宁可返回 None
    （调用方转为评论求助，不瞎打）。
    """
    m = _TITLE_TAG_RE.match(title or "")
    if m:
        return "bug" if m.group(1).lower() == "bug" else "enhancement"
    t, b = _norm(title), _norm(body)
    ranked = sorted(
        ((ty, _score(t, b, kws)) for ty, kws in TYPE_KEYWORDS.items()),
        key=lambda kv: kv[1], reverse=True,
    )
    (best, best_score), (_, second_score) = ranked[0], ranked[1]
    if best_score >= 2 and best_score - second_score >= 2:
        return best
    return None


def classify_areas(title: str, body: str, max_areas: int = 3) -> list[str]:
    """area/* 模块分类：按得分降序，最多 max_areas 个，可多打（与约定一致）。

    门槛：标题命中过关键词的模块总分 ≥2 即收；仅正文命中的需 ≥3。
    调用方须保证只对没有 area 标签的 issue 使用——已有 area 视为维护者裁决过，
    机器人不追加（真实存量里维护者挑过的 issue，机器人按关键词追加的全是噪声）。
    """
    t, b = _norm(title), _norm(body)
    picks = []
    for area, kws in AREA_KEYWORDS.items():
        title_score = sum(2 for kw in kws if _norm(kw) in t)
        body_score = sum(1 for kw in kws if _norm(kw) in b)
        score = title_score + body_score
        if score >= 3 or (score >= 2 and title_score > 0):
            picks.append((area, score))
    picks.sort(key=lambda kv: (-kv[1], kv[0]))
    return [area for area, _ in picks[:max_areas]]


def check_title(title: str, type_label: str) -> str | None:
    """返回应修正的标题；无需修正返回 None。

    - 仅 bug/enhancement 有模板前缀，question/documentation 不碰标题
    - 「【」开头是维护者刻意风格（【工程】【勿认领】等），不套前缀
    - 已带正确的 [Bug] / [Feature] 前缀 → 不动；带同义但大小写/空格不合规范的
      （如 [bug]x）→ 规范化为标准前缀；带的是另一类前缀（标题与标签冲突）→ 不动，留给人工
    """
    prefix = TITLE_PREFIX.get(type_label)
    if prefix is None:
        return None
    stripped = (title or "").strip()
    if not stripped or stripped.startswith("【"):
        return None
    if stripped.startswith(prefix):
        return None
    m = _TITLE_TAG_RE.match(stripped)
    if m:
        tag_type = "bug" if m.group(1).lower() == "bug" else "enhancement"
        if tag_type != type_label:
            return None  # 标题前缀与类型标签冲突（如 [Feature] 配 bug），留给人工
        return prefix + stripped[m.end():]  # 同类但写法不合规范（如 [bug]x）→ 规范化
    return prefix + stripped


def find_missing_sections(body: str, type_label: str) -> list[str]:
    """按模板找出正文缺失的小节。小节标题允许带后缀（如「## 现象（补充）」）。"""
    sections = TEMPLATE_SECTIONS.get(type_label)
    if sections is None:
        return []
    text = body or ""
    missing = []
    for name in sections:
        if not re.search(rf"^#{{1,6}}\s*{re.escape(name)}", text, re.MULTILINE):
            missing.append(name)
    return missing


def build_body_appendix(missing: list[str], type_label: str) -> str:
    """构造追加到正文末尾的补齐块（只加小节标题与占位提示，不改原内容）。"""
    lines = ["", MARKER, "以下小节由 issue 机器人按模板补齐（原正文未改动，编辑历史可回溯）：", ""]
    for name in missing:
        lines.append(f"## {name}")
        lines.append("")
        lines.append(SECTION_PLACEHOLDER[type_label][name])
        lines.append("")
    return "\n".join(lines)


@dataclass
class TriagePlan:
    """单个 issue 的处理计划；has_writes=True 才会有实际写操作。"""

    number: int
    add_labels: list[str] = field(default_factory=list)
    remove_labels: list[str] = field(default_factory=list)
    new_title: str | None = None
    body_appendix: str | None = None
    help_text: str | None = None

    @property
    def has_writes(self) -> bool:
        return bool(self.add_labels or self.remove_labels
                    or self.new_title or self.body_appendix or self.help_text)

    @property
    def help_only(self) -> bool:
        """只剩「类型判断不了要评论求助」这一件事（用于防重复评论）。"""
        return bool(self.help_text) and not (
            self.add_labels or self.remove_labels
            or self.new_title or self.body_appendix
        )


def plan_issue(issue: dict) -> TriagePlan:
    """对单个 issue（gh --json 形状）算出处理计划。纯函数，不触网。"""
    title = issue.get("title") or ""
    body = issue.get("body") or ""
    labels = [l["name"] for l in (issue.get("labels") or [])]
    plan = TriagePlan(number=issue["number"])

    # 1) 类型标签：必打且恰好一个
    type_present = [t for t in TYPE_LABELS if t in labels]
    if not type_present:
        guessed = classify_type(title, body)
        if guessed:
            plan.add_labels.append(guessed)
            final_type = guessed
        else:
            final_type = None
            plan.help_text = (
                "未能自动判断类型标签（bug / enhancement / question / documentation），"
                "按「宁可晚打不要错打」先不打——请在正文里补充说明问题性质，由维护者手动打上。"
            )
    elif len(type_present) == 1:
        final_type = type_present[0]
    else:
        # 多个类型标签并存：分类结果若在现有标签里就保留它，否则按约定优先级保留第一个
        guessed = classify_type(title, body)
        keep = guessed if guessed in type_present else next(
            t for t in TYPE_LABELS if t in type_present)
        plan.remove_labels = [t for t in type_present if t != keep]
        final_type = keep

    # 2) 标题前缀与正文小节（仅在类型可确定时套模板）
    if final_type:
        new_title = check_title(title, final_type)
        if new_title:
            plan.new_title = new_title
        # 【】开头的维护者风格 issue 不做正文补齐（与标题同理：刻意不套模板）
        if MARKER not in body and not title.strip().startswith("【"):
            missing = find_missing_sections(body, final_type)
            if missing:
                plan.body_appendix = build_body_appendix(missing, final_type)

    # 3) area/* 模块标签：只在完全没有 area 标签时补——已有 area 视为维护者
    #    裁决过，机器人按关键词追加只会产生噪声（真实存量实测如此）
    if not any(l.startswith("area/") for l in labels):
        for area in classify_areas(title, body):
            if area not in plan.add_labels:
                plan.add_labels.append(area)
    return plan


def render_comment(plan: TriagePlan) -> str:
    """说明评论正文：列明本轮动作，带 MARKER 供下轮识别与原地改写。"""
    lines = [MARKER, "🤖 **issue 机器人巡检**，本轮处理：", ""]
    if plan.new_title:
        lines.append(f"- 标题补模板前缀 → `{plan.new_title}`")
    if plan.add_labels:
        lines.append("- 补打标签：" + "、".join(f"`{l}`" for l in plan.add_labels))
    if plan.remove_labels:
        lines.append("- 移除多余类型标签（约定恰好一个）："
                     + "、".join(f"`{l}`" for l in plan.remove_labels))
    if plan.body_appendix:
        lines.append("- 正文按模板补齐缺失小节（只追加小节标题与占位提示，未改动原内容）")
    if plan.help_text:
        lines.append(f"- ⚠️ {plan.help_text}")
    lines += ["", "规则见 `CONTRIBUTING.md`「Issue 标签约定」，机器人逻辑见 `ci/issue_triage.py`；"
                  "有误请直接改回，issue 编辑历史可回溯。"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# gh IO 层
# ---------------------------------------------------------------------------

def gh(*args: str, timeout: int = 120) -> str:
    """跑一条 gh 命令，失败抛 RuntimeError（正文不进异常消息，避免刷屏）。"""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        head = " ".join(args[:2])
        raise RuntimeError(
            f"gh {head} … 失败（exit {proc.returncode}）：{(proc.stderr or '').strip()[:300]}")
    return proc.stdout


def fetch_open_issues() -> list[dict]:
    out = gh("issue", "list", "--state", "open", "--limit", "400",
             "--json", "number,title,body,author,labels")
    return json.loads(out)


def is_bot(issue: dict) -> bool:
    login = ((issue.get("author") or {}).get("login") or "").lower()
    return login.endswith("[bot]")


def find_marker_comment_id(number: int) -> int | None:
    """找本机器人留过的带 MARKER 评论 id；没有返回 None。"""
    out = gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}/comments",
             "--paginate", "--jq", r'.[] | [.id, .body] | @json')
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        cid, body = json.loads(line)
        if MARKER in body:
            return cid
    return None


def _write_temp(content: str) -> str:
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False,
                                     encoding="utf-8") as f:
        f.write(content)
        return f.name


def apply_plan(plan: TriagePlan, issue: dict, dry_run: bool) -> None:
    """执行处理计划。dry_run=True 只打印动作；否则逐项写 GitHub。"""
    n = str(plan.number)
    if dry_run:
        for label in plan.add_labels:
            print(f"    补标签: {label}")
        for label in plan.remove_labels:
            print(f"    移除标签: {label}")
        if plan.new_title:
            print(f"    改标题 → {plan.new_title}")
        if plan.body_appendix:
            print("    补正文小节（追加，不改原内容）")
        if plan.help_text:
            print("    评论求助（类型判断不了）")
        return

    label_flags = []
    for label in plan.add_labels:
        label_flags += ["--add-label", label]
    for label in plan.remove_labels:
        label_flags += ["--remove-label", label]
    if label_flags:
        gh("issue", "edit", n, *label_flags)
    if plan.new_title:
        gh("issue", "edit", n, "--title", plan.new_title)
    if plan.body_appendix:
        new_body = (issue.get("body") or "") + plan.body_appendix
        path = _write_temp(new_body)
        try:
            gh("issue", "edit", n, "--body-file", path)
        finally:
            os.unlink(path)

    # 说明评论：已带标记则原地改写；只剩求助一件事且已评论过 → 闭嘴，防每小时重复
    comment_id = find_marker_comment_id(plan.number)
    if plan.help_only and comment_id is not None:
        return
    comment_body = render_comment(plan)
    if comment_id is not None:
        gh("api", "-X", "PATCH", f"repos/{{owner}}/{{repo}}/issues/comments/{comment_id}",
           "-f", f"body={comment_body}")
    else:
        path = _write_temp(comment_body)
        try:
            gh("issue", "comment", n, "--body-file", path)
        finally:
            os.unlink(path)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Issue 自动 triage（格式修复 + 自动打标签）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将执行的动作清单，不写 GitHub")
    args = parser.parse_args(argv)

    issues = fetch_open_issues()
    touched, idle, failed = [], [], []
    for issue in issues:
        if is_bot(issue):  # bot 的 issue 不碰，防自触发循环
            idle.append(issue["number"])
            continue
        number = issue["number"]
        try:
            plan = plan_issue(issue)
            if not plan.has_writes:
                idle.append(number)
                continue
            print(f"== #{number} {issue.get('title') or ''}")
            apply_plan(plan, issue, args.dry_run)
            touched.append(number)
        except Exception as exc:  # 单个 issue 失败不拖垮整轮
            print(f"== #{number} 处理失败：{exc}", file=sys.stderr)
            failed.append(number)

    mode = "（dry-run，未执行写操作）" if args.dry_run else ""
    print(f"\n共 {len(issues)} 个 open issue：处理 {len(touched)} 个"
          f"{touched}，无动作 {len(idle)} 个，失败 {len(failed)} 个{mode}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
