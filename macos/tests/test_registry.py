# tests/test_registry.py
"""apps 适配器层离线回归：微信封装只做转调；registry 按 bundle id / 名字分发。

Run: uv run python -B -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


class CaptureAppTests(unittest.TestCase):
    def test_identity(self):
        from apps.capture_app import CaptureApp
        app = CaptureApp()
        self.assertEqual(app.key, 'wechat')
        self.assertEqual(app.display_name, '聊天应用')
        self.assertIn('com.tencent.xinWeChat', app.bundle_ids)
        self.assertIn('微信', app.app_names)
        self.assertTrue(app.needs_screen_capture)

    def test_delegates_to_perception_and_fill(self):
        from apps.capture_app import CaptureApp
        app = CaptureApp()
        with patch('perception.find_wechat_window', return_value='win') as fw, \
             patch('perception.read_conversation', return_value={'ok': True}) as rc, \
             patch('fill.locate_input', return_value={'box': None}) as li, \
             patch('fill.fill_text', return_value=(True, '已填入')) as ft, \
             patch('perception.warm_ocr', return_value=12.0) as wo:
            self.assertEqual(app.find_window(7), 'win')
            fw.assert_called_once_with(7)
            self.assertEqual(app.read_conversation(previous_wid=1, prev_fingerprint=b'x', prev_layout=(1, 2, 3)),
                             {'ok': True})
            rc.assert_called_once_with(previous_wid=1, prev_fingerprint=b'x', prev_layout=(1, 2, 3))
            self.assertEqual(app.locate_input({'wid': 1}), {'box': None})
            li.assert_called_once_with({'wid': 1})
            self.assertEqual(app.fill_text('hi', target={'box': 'b'}), (True, '已填入'))
            ft.assert_called_once_with('hi', target={'box': 'b'})
            self.assertEqual(app.warm(), 12.0)
            wo.assert_called_once_with()


class RegistryTests(unittest.TestCase):
    def test_wechat_by_bundle_id(self):
        from apps import registry
        with patch.object(registry, '_frontmost', return_value=('com.tencent.xinWeChat', 'Some Name')):
            self.assertEqual(registry.frontmost_app().key, 'wechat')

    def test_wechat_by_name_alias(self):
        from apps import registry
        for name in ('微信', 'WeChat', 'Weixin'):
            with patch.object(registry, '_frontmost', return_value=('', name)):
                self.assertEqual(registry.frontmost_app().key, 'wechat', name)

    def test_qq_by_bundle_id_and_name(self):
        from apps import registry
        with patch.object(registry, '_frontmost', return_value=('com.tencent.qq', 'QQ')):
            self.assertEqual(registry.frontmost_app().key, 'qq')
        with patch.object(registry, '_frontmost', return_value=('', 'QQ')):
            self.assertEqual(registry.frontmost_app().key, 'qq')

    def test_sibling_apps_are_not_chat_apps(self):
        from apps import registry
        for bundle, name in (('com.google.Chrome', 'Google Chrome'), ('', '微信读书'),
                             ('com.tencent.WeWorkMac', '企业微信'), ('', 'QQ音乐')):
            with patch.object(registry, '_frontmost', return_value=(bundle, name)):
                self.assertIsNone(registry.frontmost_app(), (bundle, name))

    def test_query_failure_is_unknown_not_none(self):
        from apps import registry
        with patch.object(registry, '_frontmost', return_value=None):
            self.assertIs(registry.frontmost_app(), registry.UNKNOWN)

    def test_workspace_exception_is_unknown(self):
        # NSWorkspace 本身抛异常也走 UNKNOWN：不是「前台是别的 App」的证据
        from apps import registry
        with patch('AppKit.NSWorkspace') as ws:      # PyObjC 选择器不能直接 patch，换整个名字
            ws.sharedWorkspace.side_effect = RuntimeError
            self.assertIs(registry.frontmost_app(), registry.UNKNOWN)

    def test_app_by_key(self):
        from apps import registry
        self.assertEqual(registry.app_by_key('qq').display_name, '聊天工具')
        self.assertIsNone(registry.app_by_key('feishu'))
        self.assertEqual([a.key for a in registry.APPS], ['wechat', 'qq'])


if __name__ == '__main__':
    unittest.main()
