# -*- coding: utf-8 -*-
"""差分测试：证明「API 之后的处理」这一段，我们这版和上游一模一样。

同样的判断值、同样的候选回复，上游的 analyze() 和我们的 analyze() 必须返回同一个 dict。
上游的值来自两次 API（起草那次回一个 JSON 数组，Jev 那次回 {"answers": {...}}）；
我们的值来自一次 API，回一个合并后的 JSON 对象。产出方换了，下游一个字都不该变。

每个场景只写一份中立数据（候选、谁赢、每条的概率、7 个判断字段的值），
由两个小函数分别渲染成两种线格式，再分别喂给两边的 analyze()。

打桩位置（都不碰网络）：
  上游：换掉 core.draft.draft_candidates（直接回候选表）和 core.jev_client.ask
        （回 {"answers": <Jev 形状的答案>, "usage": {...}}），然后调 core.engine.analyze。
  我们：只换掉发 HTTP 的那个函数（core.llm.chat，core/draft.py 从它 import 进来用），
        让它回那串合并后的 JSON **原始字符串**——我们这边跑的是真解析器和真适配器，
        不是把解析好的结构直接塞进去。

两边都定义了叫 core 的包，同一个进程里 import 会打架，所以用子进程隔离：
    python3 tools/equivalence_check.py --side upstream --tree <path>
    python3 tools/equivalence_check.py --side ours     --tree <path>
各自把 sys.path 摆好、跑完全部场景、往 stdout 打一份 JSON；
不带 --side 就是驱动器：两边都跑一遍再逐键比对。

无第三方依赖。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

# ---------------------------------------------------------------------------
# 中立数据：7 个判断字段的题型（两棵树的 core/questions.py 里 JUDGE_QUESTIONS 是逐字相同的，
# 这里重打一遍是为了让场景数据不依赖任何一边；两边跑起来都会跟真表对一次）。
# ---------------------------------------------------------------------------

FIELD_KIND = {
    "literal_question": "noul",
    "true_intent": "choice",
    "danger_level": "score",
    "should_reply_now": "noul",
    "best_action": "choice",
    "she_needs": "choice",
    "tension_resolved": "noul",
}

REPLY_KEYS = ("reply_a", "reply_b", "reply_c")

# 一份「全字段都在」的判断值，场景里按需覆盖/删字段。
FULL_JUDGMENT = {
    "literal_question": {"noul": 0.85},
    "true_intent": {"choice": "vent_anger", "confidence": 0.62,
                    "probabilities": {"vent_anger": 0.62, "seek_explanation": 0.23,
                                      "casual_chat": 0.15}},
    "danger_level": {"score": 4, "confidence": 0.55,
                     "probabilities": {"3": 0.2, "4": 0.55, "5": 0.25}},
    "should_reply_now": {"noul": 0.7},
    "best_action": {"choice": "acknowledge", "confidence": 0.5,
                    "probabilities": {"acknowledge": 0.5, "apologize": 0.3, "explain": 0.2}},
    "she_needs": {"choice": "care", "confidence": 0.44,
                  "probabilities": {"care": 0.44, "apology": 0.31, "nothing": 0.25}},
    "tension_resolved": {"noul": 0.12},
}

MESSAGES = [("her", "你昨天说的那个事到底怎么样了"), ("me", "还在弄"), ("her", "你是不是忘了")]
GROUP_MESSAGES = [("her", "明天几点集合", "小林"), ("her", "我都行", "阿哲"),
                  ("her", "那你说个时间", "小林")]


def judgment(**overrides):
    """完整判断值 + 覆盖；值传 None 表示这个字段两边都不给（缺字段场景）。"""
    out = json.loads(json.dumps(FULL_JUDGMENT))
    for name, value in overrides.items():
        if value is None:
            out.pop(name, None)
        else:
            out[name] = value
    return out


def scenario(name, candidates, winner, probs, note="", *, judgment_values=None,
             reply_to=None, messages=None, normalize_upstream=True, noul_bare=False,
             expect_diff=()):
    return {
        "name": name,
        "note": note,
        "candidates": list(candidates),
        "winner": winner,              # reply_x / None（整块都没有）/ 指不到的 reply_x
        "probs": probs,                # {reply_x: 数} 或 None（整块都没有）
        "judgment": FULL_JUDGMENT if judgment_values is None else judgment_values,
        "reply_to": reply_to,
        "messages": list(messages or MESSAGES),
        # Jev 的 API 按定义回一个归一化的分布；模型自己报的分不是。
        # normalize_upstream=True 时，上游那份线数据也按归一后的分布渲染（见 REPORT）。
        "normalize_upstream": normalize_upstream,
        "noul_bare": noul_bare,        # 我们这边的 noul 写成裸数字（模型常这么给）
        "expect_diff": tuple(expect_diff),  # 已知且有解释的分歧键
    }


C3 = ["还在跟进 今晚给你信", "没忘 就是卡在对面那边", "我这就去催"]
C2 = ["没忘 今晚给你信", "我这就去催"]

SCENARIOS = [
    scenario("ordinary_three_candidates", C3, "reply_b",
             {"reply_a": 0.25, "reply_b": 0.55, "reply_c": 0.20},
             "普通情况：三条候选，赢家清楚"),
    scenario("winner_not_top_scorer", C3, "reply_c",
             {"reply_a": 0.55, "reply_b": 0.30, "reply_c": 0.15},
             "点名的不是分最高的那条：两边都必须听点名的"),
    scenario("winner_is_reply_a", C3, "reply_a",
             {"reply_a": 0.40, "reply_b": 0.35, "reply_c": 0.25},
             "赢家是第一条：验下标映射"),
    scenario("winner_is_reply_c", C3, "reply_c",
             {"reply_a": 0.20, "reply_b": 0.30, "reply_c": 0.50},
             "赢家是第三条：验下标映射"),
    scenario("two_candidates_only", C2, "reply_b",
             {"reply_a": 0.35, "reply_b": 0.65},
             "只有两条候选"),
    scenario("boundary_values_low", C3, "reply_a",
             {"reply_a": 1.0, "reply_b": 0.0, "reply_c": 0.0},
             "边界值：danger_level 0，noul 全 0.0",
             judgment_values=judgment(
                 literal_question={"noul": 0.0},
                 should_reply_now={"noul": 0.0},
                 tension_resolved={"noul": 0.0},
                 danger_level={"score": 0, "confidence": 0.9,
                               "probabilities": {"0": 0.9, "1": 0.1}})),
    scenario("boundary_values_high", C3, "reply_c",
             {"reply_a": 0.0, "reply_b": 0.0, "reply_c": 1.0},
             "边界值：danger_level 9，noul 全 1.0",
             judgment_values=judgment(
                 literal_question={"noul": 1.0},
                 should_reply_now={"noul": 1.0},
                 tension_resolved={"noul": 1.0},
                 danger_level={"score": 9, "confidence": 1.0,
                               "probabilities": {"8": 0.0, "9": 1.0}})),
    scenario("ranking_block_missing", C3, None, None,
             "上游 Jev 回答整块没有 best_reply；我们这边整块没有排序（best_reply + reply_scores 都缺）"),
    scenario("choice_names_missing_candidate", C2, "reply_c",
             {"reply_a": 0.40, "reply_b": 0.60},
             "点名的候选下标不存在（只有两条却点 reply_c）",
             expect_diff=("answers.best_reply.choice",)),
    scenario("probabilities_not_normalized", C3, "reply_b",
             {"reply_a": 2.0, "reply_b": 5.0, "reply_c": 3.0},
             "分加起来不是 1：上游那边按 Jev 的契约渲染成归一后的分布，我们这边喂原始分"),
    scenario("probabilities_not_normalized_raw", C3, "reply_b",
             {"reply_a": 2.0, "reply_b": 5.0, "reply_c": 3.0},
             "同上，但上游那份也原样喂没归一的数——这是两边唯一真正分歧的地方",
             normalize_upstream=False,
             expect_diff=("scores", "answers.best_reply.confidence",
                          "answers.best_reply.probabilities")),
    scenario("judgment_field_absent", C3, "reply_a",
             {"reply_a": 0.5, "reply_b": 0.3, "reply_c": 0.2},
             "两边都缺同一个判断字段（best_action）",
             judgment_values=judgment(best_action=None)),
    scenario("reply_to_passthrough", C3, "reply_b",
             {"reply_a": 0.2, "reply_b": 0.5, "reply_c": 0.3},
             "群聊指定回复对象：reply_to 必须原样带出来",
             reply_to="小林", messages=GROUP_MESSAGES),
    scenario("noul_as_bare_number", C3, "reply_a",
             {"reply_a": 0.6, "reply_b": 0.25, "reply_c": 0.15},
             "我们这边 noul 写成裸数字（模型常见写法），必须等价于 {\"noul\": x}",
             noul_bare=True),
    scenario("confidence_omitted", C3, "reply_b",
             {"reply_a": 0.3, "reply_b": 0.5, "reply_c": 0.2},
             "判断字段不给 confidence（Jev 一定给，模型常常漏）",
             judgment_values=judgment(
                 true_intent={"choice": "vent_anger",
                              "probabilities": {"vent_anger": 0.62, "casual_chat": 0.38}},
                 danger_level={"score": 4, "probabilities": {"4": 0.7, "5": 0.3}}),
             expect_diff=("answers.true_intent.confidence", "answers.danger_level.confidence")),
]


def _normalized(probs):
    """跟 core/draft.py 的 _ranking 同一条规则：和不是 1 就按和摊平。"""
    if not probs:
        return {}
    clipped = {k: max(0.0, v) for k, v in probs.items()}
    total = sum(clipped.values())
    if total > 0 and abs(total - 1.0) > 1e-6:
        return {k: v / total for k, v in clipped.items()}
    return clipped


# ---------------------------------------------------------------------------
# 渲染成两种线格式
# ---------------------------------------------------------------------------

def render_upstream(scn) -> dict:
    """中立数据 → Jev 判断接口回的那个 answers（core/jev_client.py 的 _answer 形状）。"""
    answers = {}
    for name, spec in scn["judgment"].items():
        kind = FIELD_KIND[name]
        if kind == "noul":
            answers[name] = {"type": "noul", "noul": spec["noul"]}
        elif kind == "choice":
            answers[name] = {"type": "choice", "choice": spec["choice"],
                             **({"confidence": spec["confidence"]} if "confidence" in spec else {}),
                             "probabilities": dict(spec["probabilities"])}
        else:
            answers[name] = {"type": "score", "score": spec["score"],
                             **({"confidence": spec["confidence"]} if "confidence" in spec else {}),
                             "probabilities": dict(spec["probabilities"])}
    if scn["winner"] is None and scn["probs"] is None:
        return answers  # 整块 best_reply 都没有
    probs = scn["probs"] or {}
    probs = _normalized(probs) if scn["normalize_upstream"] else dict(probs)
    block = {"type": "choice"}
    if scn["winner"] is not None:
        block["choice"] = scn["winner"]
    confidence = probs.get(scn["winner"])
    if confidence is not None:
        block["confidence"] = confidence
    block["probabilities"] = probs
    answers["best_reply"] = block
    return answers


def render_ours(scn) -> str:
    """中立数据 → 合并那一次调用回的原始文本（模型会怎么打就怎么打）。"""
    obj = {"replies": list(scn["candidates"])}
    if scn["winner"] is not None:
        obj["best_reply"] = scn["winner"]
    if scn["probs"] is not None:
        obj["reply_scores"] = dict(scn["probs"])
    judgment_out = {}
    for name, spec in scn["judgment"].items():
        kind = FIELD_KIND[name]
        if kind == "noul":
            judgment_out[name] = spec["noul"] if scn["noul_bare"] else {"noul": spec["noul"]}
        elif kind == "choice":
            judgment_out[name] = {"choice": spec["choice"],
                                  **({"confidence": spec["confidence"]}
                                     if "confidence" in spec else {}),
                                  "probabilities": dict(spec["probabilities"])}
        else:
            judgment_out[name] = {"score": spec["score"],
                                  **({"confidence": spec["confidence"]}
                                     if "confidence" in spec else {}),
                                  "probabilities": dict(spec["probabilities"])}
    obj["judgment"] = judgment_out
    # 模型是带着 ```json 围栏吐出来的，照着来
    return "```json\n" + json.dumps(obj, ensure_ascii=False, indent=2) + "\n```"


# ---------------------------------------------------------------------------
# 跑一边
# ---------------------------------------------------------------------------

STUB_USAGE_UPSTREAM = {"input_tokens": 137, "output_tokens": 61}


def run_upstream(tree: str) -> dict:
    sys.path.insert(0, tree)
    import core.draft as draft_mod
    import core.engine as engine
    import core.jev_client as jev_mod
    import core.questions as questions

    assert {n: q["type"] for n, q in questions.JUDGE_QUESTIONS.items()} == FIELD_KIND, \
        "上游的 JUDGE_QUESTIONS 跟测试里的题型表对不上"

    out = {}
    for scn in SCENARIOS:
        answers = render_upstream(scn)
        seen = {"draft": 0, "ask": 0}

        def fake_draft(*a, **kw):
            seen["draft"] += 1
            return list(scn["candidates"])

        def fake_ask(state, questions_, timeout=20, provider="openrouter", model=None):
            seen["ask"] += 1
            seen["questions"] = sorted(questions_)
            return {"answers": json.loads(json.dumps(answers)), "usage": dict(STUB_USAGE_UPSTREAM)}

        # engine 是 `from .draft import draft_candidates` 进来的，所以换 engine 上的名字；
        # 顺手把原模块上的也换掉，谁也别摸网络。
        draft_mod.draft_candidates = engine.draft_candidates = fake_draft
        jev_mod.ask = engine.ask = fake_ask
        result = engine.analyze(scn["messages"], "朋友", reply_to=scn["reply_to"])
        out[scn["name"]] = {"result": result, "calls": seen}
    return out


def run_ours(tree: str) -> dict:
    sys.path.insert(0, tree)
    import core.draft as draft_mod
    import core.engine as engine
    import core.questions as questions
    from core.errors import JevError
    from core.providers import LLM_ENV

    assert {n: q["type"] for n, q in questions.JUDGE_QUESTIONS.items()} == FIELD_KIND, \
        "我们的 JUDGE_QUESTIONS 跟测试里的题型表对不上"
    os.environ[LLM_ENV] = "test-key-not-a-real-key"

    out = {}
    for scn in SCENARIOS:
        content = render_ours(scn)
        seen = {"chat": 0}

        def fake_chat(*a, **kw):
            seen["chat"] += 1
            if seen["chat"] == 1:
                return content
            # 候选不足 3 条时 draft_and_judge 会追问一次；这里当作追问失败，
            # 它自己 catch 掉（extra=[]），候选就保持原样。
            raise JevError("stub: 追问不参与这次比对")

        draft_mod.chat = fake_chat
        result = engine.analyze(scn["messages"], "朋友", reply_to=scn["reply_to"])
        out[scn["name"]] = {"result": result, "calls": seen}
    return out


# ---------------------------------------------------------------------------
# 比对
# ---------------------------------------------------------------------------

def flatten(value, prefix=""):
    """dict/list → {点分路径: 叶子值}，好逐键报差异。"""
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            out.update(flatten(v, f"{prefix}.{k}" if prefix else str(k)))
        return out or {prefix: {}}
    if isinstance(value, list):
        out = {}
        for i, v in enumerate(value):
            out.update(flatten(v, f"{prefix}[{i}]"))
        return out or {prefix: []}
    return {prefix: value}


def same_number(a, b):
    """int 0 和 float 0.0 算数值相等（compare 会把它单独记成「表示不同」）。"""
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    return isinstance(a, (int, float)) and isinstance(b, (int, float)) and a == b


def covered(key: str, expected) -> bool:
    """expect_diff 写的是键前缀：scores 盖住 scores[0]，a.b 盖住 a.b.c。"""
    return any(key == p or key.startswith(p + ".") or key.startswith(p + "[")
               for p in expected)


def compare(up: dict, ours: dict) -> dict:
    """逐键比 result（usage 除外）。返回 {差异列表, 仅表示差异列表}。"""
    a = flatten({k: v for k, v in up.items() if k != "usage"})
    b = flatten({k: v for k, v in ours.items() if k != "usage"})
    diffs, repr_only = [], []
    for key in sorted(set(a) | set(b)):
        ma, mb = key in a, key in b
        if ma and mb:
            va, vb = a[key], b[key]
            if va == vb or same_number(va, vb):
                # 数值相等但 int/float 表示不同：单独记，不算失败
                if type(va) is not type(vb) and isinstance(va, (int, float)):
                    repr_only.append({"key": key, "upstream": repr(va), "ours": repr(vb)})
                continue
            diffs.append({"key": key, "upstream": va, "ours": vb})
        else:
            diffs.append({"key": key,
                          "upstream": a[key] if ma else "<absent>",
                          "ours": b[key] if mb else "<absent>"})
    return {"diffs": diffs, "repr_only": repr_only}


def drive(python: str, upstream_tree: str, ours_tree: str) -> int:
    here = os.path.abspath(__file__)

    def side(name, tree):
        proc = subprocess.run([python, here, "--side", name, "--tree", tree],
                              capture_output=True, text=True)
        if proc.returncode != 0:
            sys.stderr.write(f"--- {name} 跑挂了 ---\n{proc.stdout}\n{proc.stderr}\n")
            raise SystemExit(2)
        return json.loads(proc.stdout)

    up, ours = side("upstream", upstream_tree), side("ours", ours_tree)

    width = max(len(s["name"]) for s in SCENARIOS) + 2
    print("场景".ljust(width - 4) + "  结果   说明")
    print("-" * (width + 60))
    failures, expected = 0, 0
    details = []
    for scn in SCENARIOS:
        name = scn["name"]
        cmp_ = compare(up[name]["result"], ours[name]["result"])
        keys = [d["key"] for d in cmp_["diffs"]]
        if not keys:
            verdict, mark = "PASS", ""
        elif all(covered(k, scn["expect_diff"]) for k in keys):
            verdict, mark = "PASS*", "已知并解释的分歧: " + ", ".join(keys)
            expected += 1
        else:
            verdict, mark = "FAIL", "未预期的分歧: " + ", ".join(
                k for k in keys if not covered(k, scn["expect_diff"]))
            failures += 1
        print(name.ljust(width) + verdict.ljust(7) + (mark or scn["note"]))
        if cmp_["diffs"]:
            details.append((name, verdict, cmp_["diffs"]))
    print("-" * (width + 60))
    print(f"共 {len(SCENARIOS)} 个场景：{len(SCENARIOS) - failures - expected} 完全一致，"
          f"{expected} 有已知分歧，{failures} 失败")

    if details:
        print("\n差异明细（键 / 上游 / 我们）:")
        for name, verdict, diffs in details:
            print(f"\n  [{verdict}] {name}")
            for d in diffs:
                print(f"    {d['key']}\n      上游: {d['upstream']!r}\n      我们: {d['ours']!r}")

    repr_keys = {}
    for scn in SCENARIOS:
        for d in compare(up[scn["name"]]["result"], ours[scn["name"]]["result"])["repr_only"]:
            repr_keys.setdefault(d["key"], set()).add((d["upstream"], d["ours"]))
    if repr_keys:
        print("\n数值相等但 int/float 表示不同（不算分歧，见 REPORT）:")
        for key, pairs in sorted(repr_keys.items()):
            for a, b in sorted(pairs):
                print(f"    {key}: 上游 {a} / 我们 {b}")

    print("\nusage（按要求不参与比对，两边分别是）:")
    print(f"    上游: {json.dumps(up[SCENARIOS[0]['name']]['result']['usage'], ensure_ascii=False)}"
          "   ← 来自 Jev 判断接口的 token 计数")
    print(f"    我们: {json.dumps(ours[SCENARIOS[0]['name']]['result']['usage'], ensure_ascii=False)}"
          "   ← core/llm.chat() 只回文本，这一版没有 token 计数")

    print("\n每个场景的 API 调用次数（证明没打网络、也没多打）:")
    for scn in SCENARIOS[:3]:
        print(f"    {scn['name']}: 上游 draft={up[scn['name']]['calls']['draft']} "
              f"ask={up[scn['name']]['calls']['ask']} / 我们 chat={ours[scn['name']]['calls']['chat']}")
    two = "two_candidates_only"
    print(f"    {two}: 上游 draft={up[two]['calls']['draft']} ask={up[two]['calls']['ask']} "
          f"/ 我们 chat={ours[two]['calls']['chat']}（不足 3 条会追问一次，桩让它失败）")

    return 1 if failures else 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--side", choices=("upstream", "ours"))
    ap.add_argument("--tree", default=None)
    ap.add_argument("--upstream", default=os.environ.get("UPSTREAM_TREE"))
    ap.add_argument("--ours", default=os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "windows"))
    ap.add_argument("--python", default=sys.executable)
    args = ap.parse_args()

    if args.side == "upstream":
        json.dump(run_upstream(args.tree), sys.stdout, ensure_ascii=False)
        return 0
    if args.side == "ours":
        json.dump(run_ours(args.tree), sys.stdout, ensure_ascii=False)
        return 0
    if not args.upstream:
        ap.error("要给 --upstream <上游那棵树> 或设 UPSTREAM_TREE")
    return drive(args.python, args.upstream, args.ours)


if __name__ == "__main__":
    raise SystemExit(main())
