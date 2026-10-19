"""Synthetic pixels/OCR → real WeChat reader → HUD/history/model boundaries.

上游这里查的是两个槽位（预判 _prejudge_req + 早跑 _pregen_req），判断是独立一次调用。
判断合进了候选那一次调用之后只剩早跑一条线，原来查预判槽位的断言改成查 _pregen_req，
「两个槽位拿到同一份上下文」那几句只剩一个槽位，删了；查的行为一个都没少。
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import json

import test_hud_reply as fixtures
from test_calibration import canvas, block
from calibration import Calibration
import chat_context
import perception
import questions
import Quartz as Q
from test_chat_context_requests import exercise_model_paths
import test_settings as network


def quoted_scene(style='inside', quote='明天能来开会吗', author='甲'):
    boxes = [(232, 130, 280 if style == 'inside' else 100,
              110 if style == 'inside' else 36, (.91, .91, .92)),
             (242 if style == 'inside' else 232, 173, 260, 50, (.85, .85, .85))]
    if style == 'below':
        boxes.append((238, 182, 2, 30, (.55, .55, .55)))
    blocks = [block('乙', 242, 105, 16), block('可以', 242, 140, 32),
              block((author+'：' if author else '')+quote,
                    252, 184, 216, 12)]
    return boxes, blocks


class RecognitionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        network.SettingsNetwork.setUpClass()

    @classmethod
    def tearDownClass(cls):
        network.SettingsNetwork.tearDownClass()

    def setUp(self):
        self.fixture = fixtures.HudReplyTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.h = self.fixture.h
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.h.conversations = chat_context.Conversations(Path(directory) / 'history.json')
        self.h.history_enabled = True
        self.h._show_boxes = True

    def read(self, boxes, blocks, manual, image=None, pane_left=.32):
        image = image if image is not None else canvas(boxes)
        win = perception.WindowInfo(1, 1, '微信', 0, 0, 600, 600)
        calibration = Calibration(600, 600, pane_left*600, 60, (1-pane_left)*600, 450) if manual else None
        # Capture, OCR and input-boundary detection are external system boundaries.
        with patch.object(perception, 'find_wechat_window', return_value=win), \
             patch.object(perception, 'capture_window', return_value=True), \
             patch.object(perception, '_load_png_image', return_value=image), \
             patch('input_region.input_outline', return_value=(pane_left, .85, 1-pane_left, .14)), \
             patch.object(perception, 'ocr_image', return_value=[block('测试群', 230, 20, 80), *blocks]):
            result = perception.read_conversation(calibration=calibration)
        self.assertEqual(result['chat_title'], '测试群')
        fixtures.HUD['read_conversation'].return_value = result
        self.h._input_window = result['window']
        self.h._input_next = float('inf')
        self.h._work_inner()
        return result['messages']

    def test_names_are_labels_short_bodies_and_separate_bubbles_survive(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                self.h.clear_history('测试群')
                messages = self.read([
                    (232, 130, 100, 36, (.91, .91, .92)),
                    (232, 170, 100, 36, (.91, .91, .92)),
                    (232, 260, 150, 60, (.91, .91, .92)),
                    (480, 350, 80, 36, (.6, .94, .56)),
                ], [block('成员', 242, 105, 32), block('✨乙', 280, 105, 32),
                    block('嗯', 242, 140, 16), block('1', 242, 180, 10),
                    block('张三', 242, 270, 32), block('第二行', 242, 294, 48),
                    block('好', 490, 360, 16), block('孤立昵称', 242, 420, 64),
                    block('方向未知', 390, 450, 64)], manual)
                accepted = [m for m in messages if m.side != 'unknown']
                self.assertEqual([(m.text, m.side) for m in accepted],
                                 [('嗯', 'them'), ('1', 'them'), ('张三\n第二行', 'them'), ('好', 'me')])
                self.assertEqual(accepted[0].sender, '成员 ✨乙')
                self.assertEqual(self.h._pregen_req[0], '张三\n第二行')
                self.assertNotIn('孤立昵称', str(self.h._active_context))
                self.assertNotIn('方向未知', str(self.h.conversations.history('测试群')))
                self.assertEqual(len(self.h.conversations.history('测试群')), 4)
                overlays = [p for s, p in self.fixture.queue if s == 'applyBoxes:']
                self.assertEqual(overlays[-1][1], messages)

    def test_fixed_banner_changes_do_not_become_messages(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                self.h.clear_history('测试群')
                epoch = None
                for notice in ('固定公告旧内容', '固定公告新内容', None):
                    boxes = [(232, 180, 220, 36, (.91, .91, .92)),
                             (232, 250, 220, 36, (.91, .91, .92))]
                    blocks = [block('群公告：今晚维护', 242, 190, 170),
                              block('请看一下群公告', 242, 260, 160)]
                    if notice:
                        boxes.append((196, 65, 400, 40, (.92, .92, .92)))
                        blocks.append(block(notice, 210, 77, 140))
                    messages = self.read(boxes, blocks, manual)
                    self.assertEqual([m.text for m in messages], ['群公告：今晚维护', '请看一下群公告'])
                    self.assertNotIn('固定公告', str(self.h._active_context))
                    self.assertEqual(len(self.h.conversations.history('测试群')), 2)
                    if epoch is not None: self.assertEqual(self.h._reply_epoch, epoch)
                    epoch = self.h._reply_epoch

    def test_quote_follows_body_into_context_history_and_one_box(self):
        for manual in (False, True):
            for style in ('inside', 'below'):
                with self.subTest(manual=manual, style=style):
                    self.h.clear_history('测试群')
                    messages = self.read(*quoted_scene(style), manual)
                    self.assertEqual(len(messages), 1)
                    self.assertEqual(messages[0].text, '可以')
                    self.assertEqual(messages[0].sender, '乙')
                    self.assertEqual(self.h._pregen_req[0], '可以')
                    context = self.h._pregen_req[1]
                    for part in ('当前待回复发言人：乙', '引用背景', '甲', '明天能来开会吗'):
                        self.assertIn(part, context)
                    restored = chat_context.Conversations(self.h.conversations.path)
                    history = restored.history('测试群')
                    self.assertEqual(len(history), 1)
                    self.assertEqual(chat_context.context_text(history, 0), context)
                    self.assertIn('引用', messages[0].label)
                    epoch = self.h._reply_epoch
                    self.read(*quoted_scene(style), manual)
                    self.assertEqual(self.h._reply_epoch, epoch)

    def test_mixed_content_generates_but_quote_only_and_unknown_do_not(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                self.h.clear_history('测试群')
                boxes, _ = quoted_scene()
                mixed = [block('乙', 242, 105, 16),
                         block('可以\n甲：明天能来开会吗', 252, 140, 210, 65)]
                messages = self.read(boxes, mixed, manual)
                self.assertEqual(len(messages), 1)
                self.assertEqual(messages[0].text, '可以\n甲：明天能来开会吗')
                self.assertIn('正文与引用待区分', self.h._pregen_req[1])
                self.assertIn('正文与引用待区分', messages[0].label)
                self.assertFalse(messages[0].quote)
                restored = chat_context.Conversations(self.h.conversations.path)
                self.assertIn('正文与引用待区分', chat_context.context_text(restored.history('测试群'), 0))
                for style in ('inside', 'below'):
                    self.h.clear_history('测试群')
                    boxes, blocks = quoted_scene(style)
                    self.read(boxes, blocks[2:], manual)
                    self.assertIsNone(self.h._pregen_req)
                    self.assertEqual(self.h.conversations.history('测试群'), [])
                self.read([], mixed, manual)
                self.assertIsNone(self.h._pregen_req)
                self.assertEqual(self.h.conversations.history('测试群'), [])

    def test_quote_changes_invalidate_late_results_and_refresh_saved_message(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                self.h.clear_history('测试群')
                self.read(*quoted_scene(), manual)
                old_epoch = self.h._reply_epoch
                self.h._push_reply('applyCandidates:', '过期候选', old_epoch)
                self.read(*quoted_scene(quote='后天能交付吗', author='丙'), manual)
                self.assertGreater(self.h._reply_epoch, old_epoch)
                self.assertIn('后天能交付吗', self.h._active_context)
                self.assertNotIn('明天能来开会吗', self.h._active_context)
                self.fixture.flush()
                self.h.applyCandidates_.assert_not_called()
                history = chat_context.Conversations(self.h.conversations.path).history('测试群')
                self.assertEqual(len(history), 1)
                self.assertIn('后天能交付吗', chat_context.context_text(history, 0))
                self.read(*quoted_scene(quote='后天能交付吗', author=''), manual)
                self.assertIn('作者未知', self.h._active_context)
                self.assertNotIn('丙', self.h._active_context)

    def test_all_actual_model_requests_keep_quote_or_mixed_state(self):
        with patch('generate.load_credentials', return_value=(network.SettingsNetwork.base,
                   'synthetic-key', 'synthetic-model', 'test', 'openai')), \
             patch('userconfig.get', return_value=''):
            for manual in (False, True):
                for style in ('inside', 'below', 'mixed'):
                    with self.subTest(manual=manual, style=style):
                        self.h.clear_history('测试群')
                        boxes, blocks = quoted_scene('inside' if style == 'mixed' else style)
                        if style == 'mixed':
                            blocks = [blocks[0], block('可以\n甲：明天能来开会吗', 252, 140, 210, 65)]
                        messages = self.read(boxes, blocks, manual)
                        context = self.h._pregen_req[1]
                        requests = exercise_model_paths(self.h, messages)
                        # 上游 11 次（判断、排序各有自己的请求）；合成一次之后每条路只剩一次
                        self.assertGreaterEqual(len(requests), 4)
                        for _, _, payload in requests:
                            text = payload['messages'][0]['content']
                            self.assertIn(context, text)
                            self.assertIn(messages[0].text, text)
                            self.assertEqual(text.count('明天能来开会吗'), 1)
                            self.assertIn('正文与引用待区分' if style == 'mixed' else '引用背景', text)
                            # 判断题跟着同一次请求走：题面就在这条 prompt 里，没有第二个端点
                            self.assertIn(questions.render_judgment_spec(), text)

    def test_history_switch_budget_count_and_legacy_records(self):
        # Existing records retain their exact meaning and format on reload.
        legacy = ['旧消息', 'them', '旧成员']
        self.h.conversations.path.write_text(json.dumps({'测试群': {'messages': [legacy]}}))
        self.h.conversations.reload()
        self.assertEqual(self.h.conversations.history('测试群'), [tuple(legacy)])
        for manual in (False, True):
            self.h.clear_history('测试群')
            self.h.configure_context(False, '1')
            self.read(*quoted_scene(), manual)
            self.assertIn('明天能来开会吗', self.h._active_context)
            self.assertEqual(self.h.conversations.history('测试群'), [])
            self.h.configure_context(True, '1')
            self.read(*quoted_scene(quote='很长的引用' * 3000), manual)
            context = self.h._active_context
            self.assertEqual(chat_context.model_message('可以', context), '可以')
            self.assertLessEqual(len(context)+2, chat_context.CONTEXT_CHARS)
            self.assertEqual(len(self.h.conversations.history('测试群')), 1)
            self.assertIn('很长的引用' * 3000, str(self.h.conversations.history('测试群')))

    def test_clear_mixed_clear_updates_history_and_discards_old_state(self):
        for manual in (False, True):
            self.h.clear_history('测试群')
            self.read(*quoted_scene(), manual)
            epoch = self.h._reply_epoch
            boxes, blocks = quoted_scene()
            mixed = [blocks[0], block('可以\n甲：明天能来开会吗', 252, 140, 210, 65)]
            self.read(boxes, mixed, manual)
            self.assertGreater(self.h._reply_epoch, epoch)
            history = self.h.conversations.history('测试群')
            self.assertEqual(len(history), 1)
            self.assertIn('正文与引用待区分', chat_context.context_text(history, 0))
            epoch = self.h._reply_epoch
            self.read(*quoted_scene(), manual)
            self.assertGreater(self.h._reply_epoch, epoch)
            self.assertNotIn('待区分', self.h._active_context)
            self.assertNotIn('待区分', chat_context.context_text(self.h.conversations.history('测试群'), 0))

    def test_large_bubble_preserves_reading_order_with_variable_ocr_heights(self):
        for manual in (False, True):
            blocks = [block('第一行',242,140,80,14), block('第二行',242,164,80,20)]
            blocks += [block(f'正文{i}',242,188+i*24,80) for i in range(9)]
            messages = self.read([(232,130,280,310,(.91,.91,.92))], blocks, manual)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].text, '第一行\n第二行\n'+'\n'.join(f'正文{i}' for i in range(9)))
            self.assertIsNotNone(self.h._pregen_req)

    def test_outgoing_quote_keeps_background_and_full_box_without_replying(self):
        for manual in (False, True):
            messages = self.read([(460,130,100,36,(.6,.94,.56)),
                                  (300,173,260,50,(.85,.85,.85)),
                                  (306,182,2,30,(.55,.55,.55))],
                                 [block('可以',470,140,32), block('甲：明天能来开会吗',320,184,216,12)], manual)
            self.assertEqual(len(messages), 1)
            self.assertEqual(messages[0].side, 'me')
            self.assertEqual(messages[0].quote, '明天能来开会吗')
            self.assertAlmostEqual(messages[0].x, .5)
            self.assertIsNone(self.h._pregen_req)

    def test_large_background_cannot_hide_mixed_status(self):
        self.h.save_background('测试群', '长背景' * 4000)
        boxes, blocks = quoted_scene()
        self.read(boxes, [blocks[0], block('可以\n甲：明天能来开会吗',252,140,210,65)], False)
        self.assertIn('正文与引用待区分', self.h._active_context)
        self.assertIn('当前待回复发言人：乙', self.h._active_context)
        self.assertEqual(chat_context.model_message(self.h._pregen_req[0], self.h._active_context),
                         '可以\n甲：明天能来开会吗')

    def test_quote_record_evicts_with_parent_at_history_limit(self):
        for manual in (False, True):
            prior = [['记录'+str(i), 'them', ''] for i in range(99)]
            prior.append(['可以', 'them', '乙', '明天能来开会吗', '甲', ''])
            self.h.conversations.path.write_text(json.dumps({'测试群': {'messages': prior}}))
            self.h.conversations.reload()
            boxes, blocks = quoted_scene()
            boxes += [(232,300,280,110,(.91,.91,.92)), (242,343,260,50,(.85,.85,.85))]
            blocks += [block('丁',242,275,16),block('收到',242,310,32),
                       block('丙：后天交付',252,354,120,12)]
            self.read(boxes, blocks, manual)
            restored = chat_context.Conversations(self.h.conversations.path).history('测试群')
            self.assertEqual(len(restored), 100)
            self.assertEqual(restored[0][0], '记录1')
            self.assertEqual(restored[-1][0], '收到')
            self.assertIn('引用背景（丙，不是新发言）：后天交付', chat_context.context_text(restored,99,limit=1))
            self.assertNotIn('明天能来开会吗', chat_context.context_text(restored,99,limit=1))

    def test_wide_sidebar_rounded_short_bubbles_and_ui_words_in_body(self):
        ctx=Q.CGBitmapContextCreate(None,600,600,8,2400,Q.CGColorSpaceCreateDeviceRGB(),Q.kCGImageAlphaPremultipliedLast)
        Q.CGContextSetRGBFillColor(ctx,.98,.98,.98,1)
        Q.CGContextFillRect(ctx,Q.CGRectMake(0,0,600,600))
        Q.CGContextSetRGBFillColor(ctx,.91,.91,.92,1)
        Q.CGContextFillRect(ctx,Q.CGRectMake(0,0,252,600))
        for x,y,w,h in [(300,140,230,36),(300,240,50,36)]:
            rounded=Q.CGPathCreateWithRoundedRect(Q.CGRectMake(x,600-y-h,w,h),7,7,None)
            Q.CGContextAddPath(ctx,rounded); Q.CGContextFillPath(ctx)
            Q.CGContextFillRect(ctx,Q.CGRectMake(x-5,600-y-15,5,5))
        image=Q.CGBitmapContextCreateImage(ctx)
        for manual in (False, True):
            messages=self.read([], [block('请复制对方发送的文字',310,150,180),
                                   block('好',310,250,16)], manual, image=image,pane_left=.42)
            self.assertEqual([(m.text,m.side) for m in messages],
                             [('请复制对方发送的文字','them'),('好','them')])
            self.assertEqual(self.h._pregen_req[0], '好')

    def test_background_coloured_quote_with_only_vertical_rail(self):
        boxes=[(232,130,200,36,(.93,.93,.94)), (234,180,2,20,(.85,.85,.86))]
        for manual in (False, True):
            # Vision may extend its text box a little beyond the physical rail.
            for y,height in [(183,14),(180.5,20.6)]:
                with self.subTest(manual=manual,y=y):
                    blocks=[block('乙',242,105,16),block('可以',242,140,32),
                            block('甲：请复制对方发送的文字',246,y,220,height)]
                    messages=self.read(boxes, blocks, manual)
                    self.assertEqual(len(messages),1)
                    self.assertEqual(messages[0].text,'可以')
                    self.assertEqual(messages[0].quote,'请复制对方发送的文字')
                    self.assertEqual(messages[0].quote_sender,'甲')
                    self.assertIn('引用背景（甲',self.h._active_context)
                    self.h.clear_history('测试群')
                    self.read(boxes,blocks[2:],manual)
                    self.assertIsNone(self.h._pregen_req)

    def test_clipped_text_near_input_boundary_keeps_valid_body(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                messages = self.read([(232,130,100,36,(.93,.93,.94))],
                    [block('可以',242,140,32),
                     block('边缘残字',242,508.5,32,2)], manual)
                self.assertEqual([m.text for m in messages if m.side == 'them'], ['可以'])
                self.assertEqual(self.h._pregen_req[0], '可以')

    def test_detached_quote_cannot_confirm_another_bubbles_side(self):
        for manual in (False, True):
            with self.subTest(manual=manual):
                boxes = [(270,130,250,40,(.93,.93,.94))]
                blocks = [block('归属未明的正文',280,140,180)]
                for include_quote in (False, True):
                    if include_quote:
                        boxes.append((270,300,2,22,(.85,.85,.86)))
                        blocks.append(block('甲：引用',282,302,60))
                    messages = self.read(boxes, blocks, manual)
                    self.assertEqual([(m.text,m.side) for m in messages],
                                     [('归属未明的正文','unknown')])
                    self.assertIsNone(self.h._pregen_req)
                    self.assertEqual(self.h.conversations.history('测试群'), [])


if __name__ == '__main__':
    unittest.main()
