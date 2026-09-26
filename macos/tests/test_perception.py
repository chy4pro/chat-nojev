"""Perception pure-function regressions on synthetic OCR: chat-title extraction and
message folding/side assignment. Run: python -B -m unittest discover -s tests -v.

No Cocoa, no screen read, no model calls; `block()` comes from tests/support_hud.py.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/: for support_hud
from perception import extract_chat_title, extract_messages
from support_hud import block


class PerceptionTests(unittest.TestCase):
    def test_short_chat_titles_survive_header_controls(self):
        for name in ('王', '张三', '李经理', 'A', '7', '项目讨论群'):
            with self.subTest(name=name):
                blocks = [block(name, .40, .94, .16, .025),
                          block('...', .92, .96, .03, .025),
                          block('口', .85, .94, .025, .025)]
                self.assertEqual(extract_chat_title(blocks), name)

    def test_title_fragments_join_without_distant_controls(self):
        blocks = [block('项目讨论', .40, .94, .12, .025),
                  block('组', .54, .945, .025, .025),
                  block('口口', .85, .95, .05, .025),
                  block('折叠聊天', .40, .905, .10, .025),
                  block('侧栏联系人', .10, .94, .15, .025),
                  block('下午开会', .40, .70, .15)]
        self.assertEqual(extract_chat_title(blocks), '项目讨论 组')
        self.assertEqual(extract_chat_title(blocks[2:]), '')

    def test_opposite_known_sides_never_fold_despite_close_edges(self):
        messages = extract_messages([block('收到的消息', .495, .70, .18),
                                     block('自己发出的消息', .51, .66, .34)])
        self.assertEqual([m.side for m in messages], ['them', 'me'])

    def test_stray_small_type_sender_line_is_dropped(self):
        # #62: a group-chat image/voice bubble renders only the sender name above
        # it; the name alone must never become a message for the judge.
        self.assertEqual(extract_messages([block('小王', .40, .60, .05, .020)]), [])
        messages = extract_messages([block('小王', .40, .60, .05, .020),
                                     block('自己发出的消息', .51, .66, .34)])
        self.assertEqual([(m.text, m.side) for m in messages],
                         [('自己发出的消息', 'me')])

    def test_small_type_sender_above_message_still_attaches(self):
        messages = extract_messages([block('小王', .40, .60, .05, .020),
                                     block('下午开会', .40, .45, .15)])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].sender, '小王')
        self.assertEqual(messages[0].text, '下午开会')

    def test_incoming_wrapped_message_and_sender_preserved(self):
        messages = extract_messages([block('小王', .40, .80, .05, .020),
                                     block('第一行正文', .40, .65, .25),
                                     block('续行正文', .405, .61, .12)])
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0].side, 'them')
        self.assertEqual(messages[0].sender, '小王')
        self.assertEqual(len(messages[0].lines), 2)

    def test_calibrated_timing_carries_upstream_capture(self):
        # #110: read_calibrated receives pixels the caller already paid for; its
        # timing must carry that capture cost, not report 抓取 0ms in the read log.
        from calibration import Calibration
        import perception
        cal = Calibration(600, 600, 120, 60, 450, 480)
        win = {'wid': 1, 'w': 600, 'h': 600, 'x': 0, 'y': 0}
        with patch('perception.ocr_image', return_value=[]), \
                patch('calibrated_messages.recover_numeric_bubbles', return_value=[]), \
                patch('calibrated_messages.extract', return_value=[]):
            res = perception.read_calibrated(object(), cal, win, capture_ms=123.0)
        t = res['timing_ms']
        self.assertEqual(t['capture'], 123.0)
        self.assertAlmostEqual(t['total'], 123.0 + t['ocr'], places=6)
        self.assertEqual(t['capture_path'], 'manual')

    def test_read_conversation_passes_capture_cost_to_calibrated(self):
        # #110: the calibrated branch must forward the capture-side wall time so the
        # read log reports real capture cost — this covers the wiring itself.
        from types import SimpleNamespace
        from calibration import Calibration
        import perception
        cal = Calibration(600, 600, 120, 60, 450, 480)
        win = SimpleNamespace(wid=1, title='t', w=600, h=600, x=0, y=0)
        with patch.object(perception, 'find_wechat_window', return_value=win), \
                patch.object(perception, 'capture_window', return_value=True), \
                patch.object(perception, '_load_png_image', return_value=object()), \
                patch.object(perception, 'read_calibrated', return_value={'ok': True}) as rc:
            res = perception.read_conversation(calibration=cal)
        self.assertEqual(res, {'ok': True})
        self.assertGreaterEqual(rc.call_args.kwargs['capture_ms'], 0.0)
        self.assertLess(rc.call_args.kwargs['capture_ms'], 1000.0)
        self.assertEqual(rc.call_args.args[2]['wid'], 1)


if __name__ == '__main__':
    unittest.main()
