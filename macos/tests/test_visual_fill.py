import sys
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import visual_fill

class VisualFillTests(unittest.TestCase):
    def test_control_characters_cannot_send_or_switch_fields(self):
        self.assertEqual(visual_fill.plain_text('你好\r\nworld\t!\x00'), '你好  world !')

    def test_changed_window_never_posts_input(self):
        with patch.object(visual_fill,'window_is_current',return_value=False), patch.object(visual_fill.Q,'CGEventPost') as post:
            ok,_=visual_fill.write_text('hello',{'window':{}},Mock())
            self.assertFalse(ok)
            post.assert_not_called()

    def test_changed_rectangle_never_posts_input(self):
        with patch.object(visual_fill,'window_is_current',return_value=True), patch.object(visual_fill,'locate_visual_input',return_value=(1,2,300,200)), patch.object(visual_fill.Q,'CGEventPost') as post:
            ok,_=visual_fill.write_text('hello',{'window':{},'visual_rect':(200,200,300,200)},Mock())
            self.assertFalse(ok)
            post.assert_not_called()

    def test_header_check_ignores_small_rendering_noise(self):
        self.assertTrue(visual_fill.same_signature(bytes([200]*100),bytes([201]*100)))
        self.assertFalse(visual_fill.same_signature(bytes([200]*100),bytes([100]*100)))
        self.assertFalse(visual_fill.same_signature(None,None))

    def prepare(self, before='', after='hello', current=True):
        from contextlib import ExitStack
        stack=ExitStack()
        self.addCleanup(stack.close)
        def mock(name, **kw):
            return stack.enter_context(patch.object(visual_fill,name,**kw))
        mock('window_is_current', return_value=current)
        mock('locate_visual_input', return_value=(0,100,600,200))
        mock('chat_signature', return_value=b'header')
        mock('input_text', side_effect=[before,after])
        mock('_LAST_ATTEMPT', new=None)
        stack.enter_context(patch.object(visual_fill.time,'sleep'))
        post=stack.enter_context(patch.object(visual_fill.Q,'CGEventPost'))
        target={'window':{'wid':7},'visual_rect':(0,100,600,200),'chat_signature':b'header'}
        return target,post

    def test_existing_draft_stops_before_mouse_or_keyboard(self):
        target,post=self.prepare(before='existing draft')
        ok,reason=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        self.assertIn('已有草稿',reason)
        post.assert_not_called()

    def test_changed_chat_stops_before_input(self):
        target,post=self.prepare()
        target['chat_signature']=b'another header'
        ok,reason=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        self.assertIn('会话已变化',reason)
        post.assert_not_called()

    def test_focus_loss_stops_before_mouse_click(self):
        target,post=self.prepare()
        with patch.object(visual_fill,'window_is_current',side_effect=[True,False]):
            ok,_=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        post.assert_not_called()

    def test_verified_fill_and_repeat_protection(self):
        target,post=self.prepare()
        app=Mock()
        app.processIdentifier.return_value=10
        ok,reason=visual_fill.write_text('hello',target,app)
        self.assertTrue(ok)
        self.assertIn('未发送',reason)
        count=post.call_count
        ok,reason=visual_fill.write_text('hello',target,app)
        self.assertFalse(ok)
        self.assertIn('避免重复',reason)
        self.assertEqual(post.call_count,count)

    def test_uncertain_readback_is_not_success(self):
        target,post=self.prepare(after='unrelated')
        app=Mock()
        app.processIdentifier.return_value=10
        ok,reason=visual_fill.write_text('hello',target,app)
        self.assertFalse(ok)
        self.assertIn('未能确认',reason)
        self.assertTrue(post.called)

    def test_all_line_separators_are_flattened(self):
        self.assertEqual(visual_fill.plain_text('a\u2028b\u2029c\x85d'),'a b c d')

    def manual_target(self, before='', after='hello'):
        from calibration import Calibration
        target,post=self.prepare()
        target['window'].update(x=0,y=0,w=600,h=600)
        target['manual_region']=Calibration(600,600,0,100,600,200)
        target['signature_rect']=(0,0,600,100)
        visual_fill.input_text.side_effect=[before,'',after]
        return target,post

    def test_manual_input_uses_confirmed_rect_and_reads_all_draft(self):
        target,post=self.manual_target()
        ok,reason=visual_fill.write_text('hello',target,Mock())
        self.assertTrue(ok)
        visual_fill.locate_visual_input.assert_not_called()
        self.assertTrue(all(c.kwargs.get('exclude_toolbar') is False
                            for c in visual_fill.input_text.call_args_list))
        self.assertEqual(post.call_count,5)  # mouse down/up + Unicode down/up + cursor restore; no Return

    def test_manual_draft_blocks_before_click(self):
        target,post=self.manual_target(before='原有草稿')
        ok,_=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        post.assert_not_called()

    def test_manual_draft_changed_after_focus_blocks_keyboard(self):
        target,post=self.manual_target()
        visual_fill.input_text.side_effect=['','刚输入的草稿']
        ok,_=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        self.assertEqual(post.call_count,3)  # focus click + cursor restore, no characters

    def test_cursor_handed_back_after_verified_fill(self):
        # #107: the synthetic click parks the cursor on the chat input; the gesture
        # must hand it back (one kCGEventMouseMoved at the pre-click spot) so the
        # next click — usually a tone switch on the panel — is where the user left it.
        target,post=self.prepare()
        app=Mock()
        app.processIdentifier.return_value=10
        with patch.object(visual_fill.Q,'CGEventGetLocation',return_value=(123,456)), \
                patch.object(visual_fill.Q,'CGEventCreateMouseEvent',return_value='restore') as mk:
            ok,_=visual_fill.write_text('hello',target,app)
        self.assertTrue(ok)
        moved=[c for c in mk.call_args_list if c.args[1]==visual_fill.Q.kCGEventMouseMoved]
        self.assertEqual(len(moved),1)
        self.assertEqual(moved[0].args[2],(123,456))
        self.assertEqual(post.call_count,5)  # click down/up + chunk down/up + restore

    def test_no_cursor_move_when_fill_stops_before_click(self):
        target,post=self.prepare()
        with patch.object(visual_fill,'window_is_current',side_effect=[True,False]), \
                patch.object(visual_fill.Q,'CGEventGetLocation') as loc, \
                patch.object(visual_fill.Q,'CGEventCreateMouseEvent') as mk:
            ok,reason=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        self.assertIn('没有获得焦点',reason)  # fails before the click is posted
        post.assert_not_called()
        mk.assert_not_called()               # cursor never moved, never restored
        loc.assert_called()                  # position is read before the click attempt

    def test_manual_size_change_blocks_input(self):
        target,post=self.manual_target()
        target['window']['w']=700
        ok,_=visual_fill.write_text('hello',target,Mock())
        self.assertFalse(ok)
        post.assert_not_called()
