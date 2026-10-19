"""编辑主菜单(#145)的结构回归:⌘C/⌘V/⌘X/⌘A 的路由靠它,改坏即红。

直接调 build_edit_main_menu()(纯构建,不触 NSApplication),断言菜单结构
与关键约束——尤其「菜单项无 target」:一旦有人加了 target,粘贴指令就会
固定发给 self,设置窗口的文本框再次收不到 ⌘V。
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from hud import build_edit_main_menu


class EditMainMenuTests(unittest.TestCase):
    def test_structure_and_responder_chain_routing(self):
        menu = build_edit_main_menu()
        self.assertEqual(menu.numberOfItems(), 1)
        item = menu.itemAtIndex_(0)
        self.assertEqual(item.title(), "编辑")
        sub = item.submenu()
        self.assertIsNotNone(sub)
        expected = [("剪切", "cut:", "x"), ("复制", "copy:", "c"),
                    ("粘贴", "paste:", "v"), ("全选", "selectAll:", "a")]
        self.assertEqual(sub.numberOfItems(), len(expected))
        for i, (title, action, key) in enumerate(expected):
            it = sub.itemAtIndex_(i)
            self.assertEqual(it.title(), title)
            self.assertEqual(it.action(), action)
            self.assertEqual(it.keyEquivalent(), key)
            # 关键约束:无 target 才会沿响应链找焦点文本框;设了 target 快捷键全废
            self.assertIsNone(it.target(), f"{title} 被设了 target,⌘ 快捷键将无法路由到文本框")


if __name__ == '__main__':
    unittest.main()
