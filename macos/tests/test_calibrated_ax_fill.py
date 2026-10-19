"""手动校准模式的填入目标降级链（#142）：AX 优先，定位不到回退校准框+复制兜底。

AST 加载真实 HudController（见 tests/support_hud.py），不启动 Cocoa、不读屏、
不读凭据；AX 定位用 Mock 控制，验证降级、节流与 box 防抖。
"""
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: for support_hud
from support_hud import Harness, HUD


def fake_editor(rect=(10.0, 20.0, 300.0, 60.0)):
    """手动校准 editor/校准区的最小假对象：hud 只调 screen_rect(win)。"""
    return SimpleNamespace(screen_rect=lambda win: rect)


class CalibratedAXFillTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('input_region.locate_visual_input', return_value=None))
        self.enterContext(patch('visual_fill.chat_signature',
                                return_value=b'sig'))
        self.h = h = Harness()
        HUD['read_conversation'].reset_mock()
        HUD['read_conversation'].side_effect = None
        HUD['frontmost_app'].reset_mock()
        HUD['frontmost_app'].side_effect = None
        HUD['frontmost_app'].return_value = HUD['FAKE_APP']
        HUD['screen_capture_ok'].reset_mock()
        HUD['screen_capture_ok'].return_value = True
        HUD['fill'].has_accessibility.reset_mock()
        HUD['fill'].has_accessibility.return_value = True
        HUD['find_wechat_window'].reset_mock()
        HUD['find_wechat_window'].return_value = None
        for name, value in dict(
            _active_context=None, conversations=None, history_enabled=False, context_limit=20,
            _observed_messages=None, _observed_offset=0, _context_version=0,
            _context_lock=threading.RLock(), _reply_key=None, _reply_epoch=0, _reply_worker=threading.local(),
            last_seen=None, analyzed_text=None, _win_wid=None, _fingerprint=None,
            _burst_left=3, _last_full=None, _show_boxes=False, _read_once=True,
            _gen_epoch=0, _last_skip_reason=None,
            _pregen_req=None, _pregen_result=None, _pregen_running=False,
            _paused=False, _analyzing=False, _regenerating=False,
            _stable_n=0, last_change_ts=0, last_analyze_ts=0,
            _app=None, _asked_accessibility=False, _foreground_epoch=0,
            _read_fail_since=None, _read_fail_hidden=False, _empty_frame_since=None,
            _pregen_event=threading.Event(),
            slot_tones=['normal'], _stream_rows={}, _last_context=None,
            _calibration_required=True, _calibrating=False,
        ).items():
            setattr(h, name, value)
        h._show = Mock()
        h._render = Mock()
        h._clear_candidates = Mock()
        h.panel = Mock()
        h._set_candidate_header = Mock()
        h.rows = {'cand_header': Mock()}
        h._slot_active = lambda _: True
        h._payload_current = lambda _: True
        h.generator = Mock()
        # 上游这里还给判断层留了位子（_prejudge_* 槽位、h.judge、_model_lock、_judged_once）；
        # 判断合进了候选那一次调用，HudController 上没有这些属性了（同 tests/test_hud_reply.py）。
        for name in ['applyIncoming_', 'applyPending_', 'applyJudgment_',
                     'applyCandidates_', 'applyRegenerated_', 'applyStreamLine_', 'applyError_',
                     'applyPosition_', 'applyChat_', 'applyBoxes_', 'applyHidden_',
                     'applyForegroundHidden_']:
            setattr(h, name, Mock())
        h.performSelectorOnMainThread_withObject_waitUntilDone_ = lambda s, p, w: None
        # 手动校准已保存且窗口匹配（校准分支生效的前提）
        h._input_calibration = fake_editor()
        h._input_calibration_wid = 1
        h._calibration = fake_editor()
        h._calibration_wid = 1
        HUD['read_conversation'].return_value = {
            'ok': True, 'unchanged': False, 'fingerprint': None,
            'window': {'wid': 1}, 'chat_title': 'chat', 'messages': [],
            'manual_calibration': True, 'input_rect': None,
        }

    def read(self):
        self.h._work_inner()
        return self.h._input_target

    def test_ax_located_target_carries_box(self):
        """AX 定位到 → target 带 box，填入走 AX 写入而非复制兜底。"""
        box = object()
        HUD['FAKE_APP'].locate_input.return_value = {
            'box': box, 'rect': (1.0, 2.0, 3.0, 4.0), 'reason': '填入目标'}
        target = self.read()
        self.assertIs(target['box'], box)
        self.assertEqual(target['rect'], (1.0, 2.0, 3.0, 4.0))
        self.assertEqual(target['reason'], '手动校准输入区 · AX 可写')
        # 视觉兜底字段保持原样：AX 后续失效时 fill_text 仍可回退复制
        self.assertEqual(target['visual_rect'], (10.0, 20.0, 300.0, 60.0))

    def test_ax_missing_keeps_clipboard_fallback(self):
        """AX 定位不到 → box=None + visual_rect，与旧行为一致（复制兜底）。"""
        HUD['FAKE_APP'].locate_input.return_value = {
            'box': None, 'rect': None, 'reason': 'test'}
        target = self.read()
        self.assertIsNone(target['box'])
        self.assertEqual(target['reason'], '手动校准输入区')
        self.assertEqual(target['visual_rect'], (10.0, 20.0, 300.0, 60.0))

    def test_locate_failure_keeps_stale_target_not_none(self):
        """节流期内重读且 AX 瞬时失败 → 沿用上次的 box，不能抖回 None。"""
        HUD['FAKE_APP'].locate_input.return_value = {
            'box': object(), 'rect': (1.0, 2.0, 3.0, 4.0), 'reason': '填入目标'}
        target = self.read()
        self.assertIsNotNone(target['box'])
        # 节流窗口内（1s 刷新期）AX 突然失败：缓存沿用，box 不消失
        HUD['FAKE_APP'].locate_input.return_value = {
            'box': None, 'rect': None, 'reason': 'test'}
        target = self.read()
        self.assertIsNotNone(target['box'])
        self.assertEqual(target['reason'], '手动校准输入区 · AX 可写')

    def test_throttle_after_failure_waits_three_seconds(self):
        """失败后 3s 节流：期间不重复调用 AX 定位，过了窗口才重试。"""
        HUD['FAKE_APP'].locate_input.return_value = {
            'box': None, 'rect': None, 'reason': 'test'}
        self.read()
        calls_after_first = HUD['FAKE_APP'].locate_input.call_count
        self.read()
        self.assertEqual(HUD['FAKE_APP'].locate_input.call_count, calls_after_first)
        # 人为把节流时间拨过去 → 允许重试
        self.h._calib_ax_next -= 3.0
        self.read()
        self.assertEqual(HUD['FAKE_APP'].locate_input.call_count, calls_after_first + 1)


if __name__ == '__main__':
    unittest.main()
