"""ci/sub_issue_autoclose.py 纯函数 walk 的单元测试（口径同 test_issue_triage）。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "ci"))

from sub_issue_autoclose import MAX_DEPTH, walk


def fake_graph(parents: dict[int, int | None], open_subs: dict[int, list[int]]):
    """按父映射 + 各 issue 的 open 子列表构造 walk 所需的两个回调。"""

    def parent_of(n: int):
        return parents.get(n)

    def open_children(n: int, exclude: list[int]) -> int:
        return sum(1 for c in open_subs.get(n, []) if c not in exclude)

    return parent_of, open_children


class WalkTest(unittest.TestCase):
    def test_no_parent_returns_empty(self):
        # 无父 issue：第一步即退出，无收官对象
        parent_of, oc = fake_graph({10: None}, {})
        self.assertEqual(walk(10, parent_of, oc), [])

    def test_parent_has_other_open_sub(self):
        # 父 5 另有链外 open 子 6：关闭 10 不触发 5 收官
        parent_of, oc = fake_graph({10: 5}, {5: [6]})
        self.assertEqual(walk(10, parent_of, oc), [])

    def test_single_parent_closes(self):
        # 10 是 5 唯一未关的子：5 收官
        parent_of, oc = fake_graph({10: 5, 5: None}, {5: [10]})
        self.assertEqual(walk(10, parent_of, oc), [5])

    def test_two_levels_close_after_excluding_chain(self):
        # 10→5→2 三层链，2 只有 5 一个子：5 关后 2 也收官——
        # 关键回归点：判断 2 时须把链上已入链的 5（马上要关）排除在 open 计数外
        parent_of, oc = fake_graph({10: 5, 5: 2, 2: None}, {5: [10], 2: [5]})
        self.assertEqual(walk(10, parent_of, oc), [5, 2])

    def test_upper_level_broken_by_off_chain_open_sub(self):
        # 链上 5 可收官，但更上层 2 另有链外 open 子 9：链断在 2，不误关
        parent_of, oc = fake_graph({10: 5, 5: 2}, {5: [10], 2: [5, 9]})
        self.assertEqual(walk(10, parent_of, oc), [5])

    def test_depth_cap(self):
        # 12 层父链：上爬到 MAX_DEPTH 为顶，不越官方嵌套上限
        parents: dict[int, int | None] = {0: None}
        open_subs: dict[int, list[int]] = {}
        for child in range(1, 13):
            parents[child] = child - 1
            open_subs[child - 1] = [child]
        parent_of, oc = fake_graph(parents, open_subs)
        self.assertEqual(walk(12, parent_of, oc), list(range(11, 11 - MAX_DEPTH, -1)))


if __name__ == "__main__":
    unittest.main()
