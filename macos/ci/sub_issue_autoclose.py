#!/usr/bin/env python3
"""子 issue 收官：父 issue 的子 issue 全部关闭时，自动关闭父 issue。

规则依据（改行为前先对照，勿凭记忆）：
- GitHub 官方 sub-issue 只在父 issue 上维护进度条，「子全关 → 关父」没有内置
  自动化，由本脚本补上（.github/workflows/sub-issue-autoclose.yml 在
  issues.closed 事件后调起；dispatch 手动演练用 --dry-run）
- 官方限制：单父 issue 子 issue 上限 100、嵌套上限 8 层 → 子列表一页取全、
  父链上爬以此为顶
- workflow 文件只有默认分支上的版本才会对 issues 事件生效（GitHub Actions
  既定行为），合入 master 前本脚本不自动跑

设计约束：
- stdlib-only，GitHub 读写全走 gh 子进程（runner 预装、鉴权现成，与
  ci/issue_triage.py 同款；REST 路径的 {owner}/{repo} 占位符由 gh 展开）
- 纯函数 walk() 与 gh IO 分离，tests/test_sub_issue_autoclose.py 只测纯函数
- walk 模拟「边关边爬」：判断上层父 issue 能否收官时，本链上即将被关闭的
  issue（start 与已入链的父）不计入 open 子数——否则除 start 一层外永远
  爬不上去
- 并发让位：两个子 issue 几乎同时关闭会有两个 run 竞争同一父链，收官逐层
  执行，每层关前先查状态、已关闭即让位结束（gh issue close 对已关闭 issue
  不报错且会重复评论，不能靠它失败来让位），保证每层至多一条收官评论

边界（刻意不做）：
- 不区分关闭原因：completed / not planned 都算终态
- 子 issue 超 100/父、嵌套超 8 层是官方硬上限，不处理分页与更深
- 不给未达收官条件的父 issue 留进度评论（免噪音）；不重开已关闭 issue
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

MAX_DEPTH = 8  # 官方 sub-issue 嵌套上限，父链上爬以此为顶

CLOSE_COMMENT = "父 issue 的子 issue 已全部关闭，按规则自动收官（触发：关闭 #{start}）"


# ---------------------------------------------------------------------------
# 纯函数
# ---------------------------------------------------------------------------

def parse_parent(payload: str) -> int | None:
    """从 GET /issues/{n}/parent 的响应解析父 issue 号；无父（404）返回 None。"""
    payload = payload.strip()
    if not payload:
        return None
    number = json.loads(payload).get("number")
    return int(number) if number else None


def walk(start: int, parent_of, open_children, max_depth: int = MAX_DEPTH) -> list[int]:
    """自下而上找出「可自动收官的父 issue」链（纯函数，便于测试）。

    parent_of(n) -> int | None：n 的父 issue 号，无父返回 None。
    open_children(n, exclude) -> int：n 的 open 子 issue 数，但排除 exclude
    里的 issue 号（exclude = 已入链的父 + start，它们马上就会被关闭）。
    返回自浅到深的父号列表（不含 start）；空列表表示无可收官对象。
    """
    chain: list[int] = []
    n = start
    for _ in range(max_depth):
        p = parent_of(n)
        if p is None:
            break
        if open_children(p, chain + [start]) > 0:
            break
        chain.append(p)
        n = p
    return chain


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


def parent_of(number: int) -> int | None:
    """查 issue 的父 issue 号；无父（gh 报 HTTP 404）返回 None，其余失败照常抛。"""
    try:
        out = gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}/parent")
    except RuntimeError as exc:
        # 依赖 gh 的 stderr 形状「… (HTTP 404)」区分「无父」与真失败
        if "HTTP 404" in str(exc):
            return None
        raise
    return parse_parent(out)


def open_children(number: int, exclude: list[int]) -> int:
    """number 的 open 子 issue 数，排除 exclude 中的号（官方上限 100，一页取全）。"""
    out = gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}/sub_issues?per_page=100")
    return sum(1 for it in json.loads(out)
               if it.get("state") == "open" and it.get("number") not in exclude)


def issue_state(number: int) -> str:
    """查 issue 当前状态（open / closed）。"""
    out = gh("api", f"repos/{{owner}}/{{repo}}/issues/{number}", "--jq", ".state")
    return out.strip()


def close_with_comment(number: int, comment: str) -> bool:
    """关闭 issue 并留评论；失败（常见：并发 run 抢先关闭）返回 False 不抛。"""
    proc = subprocess.run(["gh", "issue", "close", str(number), "--comment", comment],
                          capture_output=True, text=True, timeout=120)
    return proc.returncode == 0


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="子 issue 全关后自动关闭父 issue")
    parser.add_argument("--dry-run", action="store_true",
                        help="只打印将执行的动作清单，不写 GitHub")
    parser.add_argument("--start", type=int, default=None,
                        help="起始 issue 号（缺省读 START_ISSUE 环境变量，由 workflow 注入）")
    args = parser.parse_args(argv)

    if args.start is not None:
        start = args.start
    else:
        env = os.environ.get("START_ISSUE", "").strip()
        if not env.isdigit():
            parser.error("未指定起始 issue（--start 或 START_ISSUE）")
        start = int(env)

    chain = walk(start, parent_of=parent_of, open_children=open_children)
    mode = "（dry-run，未执行写操作）" if args.dry_run else ""
    if not chain:
        print(f"#{start} 沿父链无可收官的父 issue，结束{mode}")
        return 0

    print(f"#{start} 的父链可收官，自浅到深：{chain}{mode}")
    for number in chain:
        if args.dry_run:
            print(f"将关闭 #{number} 并留收官评论")
            continue
        # gh issue close 对已关闭 issue 不报错且会重复评论（v2.87 实测），关前先查；
        # 查与关之间仍有极小竞态窗口，后果仅是多一条收官评论，可接受
        if issue_state(number) == "closed":
            print(f"#{number} 已关闭（并发 run 已收官），让位结束")
            break
        if not close_with_comment(number, CLOSE_COMMENT.format(start=start)):
            print(f"#{number} 关闭未成功，让位结束")
            break
        print(f"已关闭 #{number}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
