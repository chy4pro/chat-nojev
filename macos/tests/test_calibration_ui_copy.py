"""校准界面文案与交互逻辑回归（#89 引导改进的覆盖率门禁配套）。

build() 是原生窗口装配（需真微信与截图权限），不在此测；这里覆盖的是
用户实际会读到的文案常量、选区切换的说明联动、以及 invalidate 的进度
提示分支——AppKit 控件一律用桩替身，不起真窗口。
"""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

import calibration_ui  # noqa: E402


def stub_controller(mode='messages', regions=None, selection=(10, 20, 300, 200)):
    """一个只带交互逻辑所需属性的控制器；不碰 AppKit 窗口。"""
    c = calibration_ui.CalibrationController.alloc().init()
    c.busy = False
    c.mode = mode
    c.regions = regions if regions is not None else {'messages': None, 'input': None}
    c.canvas = SimpleNamespace(selection=selection, setNeedsDisplay_=Mock())
    c.selector = Mock()
    c.instruction = Mock()
    c.status = Mock()
    c.save_button = Mock()
    c.preview = None
    c.preview_button = Mock()
    return c


class CalibrationCopyTests(unittest.TestCase):
    def test_message_instruction_names_scope_and_auto_side(self):
        text = calibration_ui.INSTRUCTIONS['messages']
        self.assertIn('整个消息流', text)
        self.assertIn('自动判定', text)

    def test_status_flow_names_all_four_steps(self):
        for step in ('框绿区', '框蓝区', '预览识别', '确认并启用'):
            self.assertIn(step, calibration_ui.STATUS_FLOW)

    def test_region_changed_switches_instruction_with_mode(self):
        c = stub_controller()
        c.selector.selectedSegment.return_value = 1     # 切到输入区
        c.regionChanged_(None)
        self.assertEqual(c.mode, 'input')
        c.instruction.setStringValue_.assert_called_once_with(
            calibration_ui.INSTRUCTIONS['input'])
        c.canvas.setNeedsDisplay_.assert_called_once_with(True)

    def test_region_changed_keeps_segment_while_busy(self):
        c = stub_controller()
        c.busy = True
        c.mode = 'messages'
        c.selector.selectedSegment.return_value = 0
        c.regionChanged_(None)
        c.selector.setSelectedSegment_.assert_called_once_with(0)
        self.assertEqual(c.regions['messages'], None)   # 未吞选区

    def test_incomplete_selection_names_the_missing_region(self):
        c = stub_controller(regions={'messages': (0, 0, 10, 10), 'input': None})
        c.invalidate()
        text = c.status.setStringValue_.call_args[0][0]
        self.assertIn('输入区（蓝框）', text)

    def test_incomplete_selection_names_messages_when_input_ready(self):
        c = stub_controller(mode='input', regions={'messages': None,
                                                   'input': (0, 0, 10, 10)})
        c.invalidate()
        text = c.status.setStringValue_.call_args[0][0]
        self.assertIn('消息区（绿框）', text)

    def test_complete_selection_demands_repreview(self):
        c = stub_controller(regions={'messages': (0, 0, 10, 10),
                                     'input': (0, 0, 10, 10)})
        c.invalidate()
        text = c.status.setStringValue_.call_args[0][0]
        self.assertIn('预览识别', text)
        self.assertFalse(c.save_button.setEnabled_.call_args[0][0])   # 保存仍禁用


if __name__ == '__main__':
    unittest.main()
