# tests/test_qq_adapter.py
"""QQ 适配器离线回归：内存伪 AX 树，不读屏、不碰 QQ 进程、不需要权限。

节点形状按 2026-09-23 对 QQ 6.9.96 的实测（见 specs/2026-09-23-qq-adapter-design.md 第 2 节）。
Run: uv run python -B -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from apps import ax_app


class N:
    """一个伪 AX 节点。rect 为屏幕坐标 (x, y, w, h)。"""
    def __init__(self, role='AXGroup', desc='', value='', title='', classes=(), rect=None, children=()):
        self.role, self.desc, self.value, self.title = role, desc, value, title
        self.classes, self.rect, self.children = tuple(classes), rect, list(children)


class FakeAX:
    def __init__(self, windows=(), focused=None):
        self._windows, self._focused = list(windows), focused
    def role(self, el): return el.role
    def desc(self, el): return el.desc
    def value(self, el): return el.value
    def value_or_none(self, el): return el.value   # 测试里可把节点 value 设为 None 模拟读失败
    def title(self, el): return el.title
    def classes(self, el): return el.classes
    def rect(self, el): return el.rect
    def children(self, el): return el.children
    def windows(self, app_el): return self._windows
    def focused_window(self, app_el): return self._focused


WIN = (311.0, 143.0, 1106.0, 782.0)


def editor(desc='很难约的王小姐', value='\n'):
    return N('AXTextArea', desc=desc, value=value, classes=('ProseMirror', 'is-empty', 'ExEditor-qq-msg-editor'),
             rect=(311.0, 753.0, 1098.0, 169.0))


def message(text, side, y, sender=None, h=38.0, x=None, image_only=False):
    """一行消息：头像节点在前、正文容器在后（与真实 DFS 顺序一致）。"""
    x = x if x is not None else (1200.0 if side == 'me' else 400.0)
    cls = ('msg-content-container', 'mix-message__container') + (('container--self',) if side == 'me' else ())
    body = (N('AXImage', classes=('image', 'market-face-element'), rect=(x, y, 150.0, h)) if image_only
            else N('AXStaticText', value=text, rect=(x + 1, y + 1, 140.0, 16.0)))
    return [
        N(desc=sender or '', classes=('avatar-span',), rect=(1361.0 if side == 'me' else 340.0, y, 32.0, 32.0)),
        N(classes=('message-content__wrapper',), rect=(x, y, 160.0, h), children=[
            N(classes=cls, rect=(x, y, 160.0, h), children=[
                N(classes=('message-content', 'mix-message__inner'), rect=(x + 1, y + 1, 140.0, 22.0), children=[body]),
            ]),
        ]),
    ]


def chat_window(rows, title='很难约的王小…', ed=None):
    ed = ed or editor()
    return N('AXWindow', title=title, rect=WIN, children=[
        N(classes=('aio',), rect=WIN, children=[
            N('AXStaticText', value='星期一 17:43', rect=(830.0, 237.0, 71.0, 14.0)),
            *[n for row in rows for n in row],
            N(classes=('chat-input-area',), rect=(311.0, 753.0, 1098.0, 169.0), children=[ed]),
        ]),
    ])


def list_window():
    """紧凑模式的会话列表窗口：没有编辑器。"""
    return N('AXWindow', title='QQ', rect=(599.0, 260.0, 369.0, 580.0), children=[
        N(desc='会话列表', rect=(647.0, 403.0, 317.0, 433.0), children=[
            N('AXStaticText', value='杀戮尖塔pdd', rect=(711.0, 417.0, 82.0, 16.0)),
        ]),
    ])


class ParseTests(unittest.TestCase):
    def test_find_editor_only_in_chat_window(self):
        ax = FakeAX()
        self.assertIsNotNone(ax_app.find_editor(ax, chat_window([])))
        self.assertIsNone(ax_app.find_editor(ax, list_window()))

    def test_title_prefers_editor_description_then_window_title(self):
        ax = FakeAX()
        win = chat_window([])
        self.assertEqual(ax_app.chat_title(ax, win, ax_app.find_editor(ax, win)), '很难约的王小姐')
        win2 = chat_window([], ed=editor(desc=''))
        self.assertEqual(ax_app.chat_title(ax, win2, ax_app.find_editor(ax, win2)), '很难约的王小…')

    def test_sides_text_and_order(self):
        ax = FakeAX()
        win = chat_window([message('在吗', 'them', 300.0, sender='王小姐'),
                           message('在的', 'me', 360.0, sender='西行树'),
                           message('周三有空吗', 'them', 420.0, sender='王小姐')])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual([(m.side, m.text, m.sender) for m in msgs],
                         [('them', '在吗', '王小姐'), ('me', '在的', '西行树'), ('them', '周三有空吗', '王小姐')])
        self.assertTrue(all(0.0 <= m.y < 1.0 and 0.0 < m.h < 1.0 and 0.0 <= m.x < 1.0 for m in msgs))
        self.assertLess(msgs[0].y, msgs[1].y)
        self.assertEqual(msgs[0].lines, ['在吗'])
        self.assertEqual(msgs[0].conf, 1.0)

    def test_image_only_message_is_skipped(self):
        ax = FakeAX()
        win = chat_window([message('', 'them', 300.0, image_only=True), message('好', 'me', 360.0)])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['好'])

    def test_virtualized_and_offscreen_nodes_are_dropped(self):
        ax = FakeAX()
        rows = [message('滚出去的旧消息', 'them', 237.0, h=1.0),           # 高度 1：虚拟列表占位
                message('窗口外', 'them', 20.0),                          # y 在窗口顶边（143）之上
                message('可见', 'them', 420.0)]
        win = chat_window(rows)
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['可见'])

    def test_node_overlapping_editor_is_dropped(self):
        ax = FakeAX()
        win = chat_window([message('草稿回显', 'me', 800.0), message('正文', 'them', 420.0)])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['正文'])

    def test_keeps_only_newest_max_messages(self):
        ax = FakeAX()
        win = chat_window([message(f'第{i}条', 'them', 250.0 + i * 30.0) for i in range(15)])   # 最后一条 670+38 仍在编辑器（y≥753）之上
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win), max_messages=12)
        self.assertEqual(len(msgs), 12)
        self.assertEqual(msgs[0].text, '第3条')
        self.assertEqual(msgs[-1].text, '第14条')

    def test_multiple_static_texts_join_in_dom_order(self):
        ax = FakeAX()
        row = message('', 'them', 300.0)
        content = row[1].children[0].children[0]
        content.children = [N('AXStaticText', value='@缓缓', rect=(401.0, 301.0, 60.0, 16.0)),
                            N('AXStaticText', value=' 复活吧', rect=(560.0, 301.0, 40.0, 16.0))]
        win = chat_window([row])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual(msgs[0].text, '@缓缓 复活吧')

    def test_wrapped_line_keeps_dom_order_across_smaller_x(self):
        # 折行的第三段 x 更小：按 x 排序会把「明天见」搬到最前，打乱换行消息
        ax = FakeAX()
        row = message('', 'them', 300.0)
        content = row[1].children[0].children[0]
        content.children = [N('AXStaticText', value='@缓缓', rect=(401.0, 301.0, 60.0, 16.0)),
                            N('AXStaticText', value=' 复活吧', rect=(560.0, 301.0, 40.0, 16.0)),
                            N('AXStaticText', value='明天见', rect=(380.0, 321.0, 48.0, 16.0))]
        win = chat_window([row])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual(msgs[0].text, '@缓缓 复活吧明天见')

    def test_walk_prune_finds_editor_under_many_offscreen_rows(self):
        # 700 行滚出屏幕的 h=1 虚拟占位排在编辑器之前：不剪枝会在 3000 节点上限内到不了编辑器
        rows = [message(f'旧{i}', 'them', 237.0, h=1.0) for i in range(700)]
        rows += [message('可见一', 'them', 420.0), message('可见二', 'them', 460.0),
                 message('可见三', 'them', 500.0)]
        win = chat_window(rows)
        ax = FakeAX()
        ed = ax_app.find_editor(ax, win)
        self.assertIsNotNone(ed)
        self.assertEqual([m.text for m in ax_app.extract_messages(ax, win, ed)],
                         ['可见一', '可见二', '可见三'])

    def test_quote_reply_block_is_excluded_from_body(self):
        # 引用回复块（reply-element）在容器内、message-content 外：其文字不算正文
        row = [
            N(desc='王小姐', classes=('avatar-span',), rect=(340.0, 300.0, 32.0, 32.0)),
            N(classes=('msg-content-container',), rect=(400.0, 300.0, 160.0, 60.0), children=[
                N(classes=('reply-element',), rect=(405.0, 302.0, 150.0, 24.0), children=[
                    N('AXStaticText', value='被引用的话', rect=(410.0, 305.0, 80.0, 16.0)),
                ]),
                N(classes=('message-content',), rect=(405.0, 330.0, 150.0, 22.0), children=[
                    N('AXStaticText', value='回复正文', rect=(410.0, 332.0, 80.0, 16.0)),
                ]),
            ]),
        ]
        win = chat_window([row])
        msgs = ax_app.extract_messages(FakeAX(), win, ax_app.find_editor(FakeAX(), win))
        self.assertEqual([m.text for m in msgs], ['回复正文'])

    def test_nested_container_is_not_double_counted(self):
        # 引用块里嵌套的 msg-content-container 不能再产出一条消息
        row = [
            N(desc='王小姐', classes=('avatar-span',), rect=(340.0, 300.0, 32.0, 32.0)),
            N(classes=('msg-content-container',), rect=(400.0, 300.0, 160.0, 60.0), children=[
                N(classes=('reply-element',), rect=(405.0, 302.0, 150.0, 24.0), children=[
                    N(classes=('msg-content-container',), rect=(410.0, 304.0, 140.0, 20.0), children=[
                        N(classes=('message-content',), rect=(412.0, 306.0, 136.0, 16.0), children=[
                            N('AXStaticText', value='被引用的话', rect=(415.0, 307.0, 80.0, 16.0)),
                        ]),
                    ]),
                ]),
                N(classes=('message-content',), rect=(405.0, 330.0, 150.0, 22.0), children=[
                    N('AXStaticText', value='回复正文', rect=(410.0, 332.0, 80.0, 16.0)),
                ]),
            ]),
        ]
        win = chat_window([row])
        msgs = ax_app.extract_messages(FakeAX(), win, ax_app.find_editor(FakeAX(), win))
        self.assertEqual([m.text for m in msgs], ['回复正文'])

    def test_messages_sorted_by_y_not_dom_order(self):
        # DFS 先遇到 y 大的行：结果仍须按 y 升序（顶部在前）
        ax = FakeAX()
        win = chat_window([message('低处', 'them', 500.0), message('高处', 'them', 300.0)])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['高处', '低处'])
        self.assertLess(msgs[0].y, msgs[1].y)

    def test_fingerprint_tracks_title_and_messages(self):
        ax = FakeAX()
        win = chat_window([message('在吗', 'them', 300.0)])
        msgs = ax_app.extract_messages(ax, win, ax_app.find_editor(ax, win))
        a = ax_app.fingerprint('王小姐', msgs)
        self.assertEqual(a, ax_app.fingerprint('王小姐', msgs))
        self.assertNotEqual(a, ax_app.fingerprint('李经理', msgs))
        win2 = chat_window([message('在吗', 'them', 300.0), message('在', 'me', 360.0)])
        self.assertNotEqual(a, ax_app.fingerprint('王小姐', ax_app.extract_messages(ax, win2, ax_app.find_editor(ax, win2))))


class WindowTests(unittest.TestCase):
    def test_list_window_rejected_chat_window_chosen(self):
        chat = chat_window([])
        ax = FakeAX(windows=[list_window(), chat], focused=None)
        win, ed = ax_app.chat_window(ax, 'app')
        self.assertIs(win, chat)
        self.assertIsNotNone(ed)

    def test_focused_chat_window_wins_over_larger_one(self):
        small = chat_window([])
        big = chat_window([]); big.rect = (0.0, 0.0, 1500.0, 950.0)   # 放大窗口保持子树在其内
        ax = FakeAX(windows=[big, small], focused=small)
        self.assertIs(ax_app.chat_window(ax, 'app')[0], small)

    def test_without_focus_largest_wins(self):
        small = chat_window([]); small.rect = (0.0, 0.0, 800.0, 600.0)
        big = chat_window([])
        ax = FakeAX(windows=[small, big], focused=list_window())
        self.assertIs(ax_app.chat_window(ax, 'app')[0], big)

    def test_no_chat_window(self):
        ax = FakeAX(windows=[list_window()])
        self.assertEqual(ax_app.chat_window(ax, 'app'), (None, None))

    def test_window_id_lookup_matches_pid_and_bounds(self):
        cg = [{'kCGWindowOwnerPID': 5, 'kCGWindowNumber': 42,
               'kCGWindowBounds': {'X': 311, 'Y': 143, 'Width': 1106, 'Height': 782}},
              {'kCGWindowOwnerPID': 5, 'kCGWindowNumber': 7,
               'kCGWindowBounds': {'X': 599, 'Y': 260, 'Width': 369, 'Height': 580}}]
        with patch('Quartz.CGWindowListCopyWindowInfo', return_value=cg):
            self.assertEqual(ax_app.window_id(5, WIN), 42)
            self.assertEqual(ax_app.window_id(6, WIN), 0)
            self.assertEqual(ax_app.window_id(5, (0, 0, 10, 10)), 0)


class ReadConversationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(ax_app.fill, 'has_accessibility', return_value=True))
        self.enterContext(patch.object(ax_app, 'build_app', return_value=_FakeRunningApp(5)))
        self.enterContext(patch.object(ax_app, 'app_element', return_value='app-el'))
        self.enterContext(patch.object(ax_app, 'window_id', return_value=42))

    def test_reads_messages_title_window_and_input_rect(self):
        win = chat_window([message('在吗', 'them', 300.0), message('在', 'me', 360.0)])
        ax = FakeAX(windows=[list_window(), win], focused=win)
        res = ax_app.read_conversation(ax=ax)
        self.assertTrue(res['ok']); self.assertFalse(res['unchanged'])
        self.assertEqual(res['chat_title'], '很难约的王小姐')
        self.assertEqual(res['window'], {'wid': 42, 'title': '很难约的王小姐', 'x': 311.0, 'y': 143.0, 'w': 1106.0, 'h': 782.0})
        self.assertEqual([m.text for m in res['messages']], ['在吗', '在'])
        self.assertEqual(res['input_rect'], (311.0, 753.0, 1098.0, 169.0))
        self.assertFalse(res['input_unresolved'])
        self.assertEqual(res['layout'], (42, 1106.0, 782.0))
        self.assertEqual(res['timing_ms']['capture_path'], 'ax')
        self.assertEqual(res['n_blocks'], 2)

    def test_unchanged_short_circuit(self):
        win = chat_window([message('在吗', 'them', 300.0)])
        ax = FakeAX(windows=[win], focused=win)
        first = ax_app.read_conversation(ax=ax)
        again = ax_app.read_conversation(ax=ax, prev_fingerprint=first['fingerprint'], prev_layout=first['layout'])
        self.assertTrue(again['ok']); self.assertTrue(again['unchanged'])
        self.assertEqual(again['messages'], [])
        self.assertEqual(again['window'], first['window'])
        resized = ax_app.read_conversation(ax=ax, prev_fingerprint=first['fingerprint'], prev_layout=(42, 900.0, 782.0))
        self.assertFalse(resized['unchanged'])

    def test_chat_window_editor_is_reused_no_second_find(self):
        # read_conversation 直接复用 chat_window 找到的编辑器，不对同一窗口二次 find_editor
        win = chat_window([message('在吗', 'them', 300.0)])
        ax = FakeAX(windows=[list_window(), win], focused=win)
        with patch.object(ax_app, 'find_editor', wraps=ax_app.find_editor) as fe:
            res = ax_app.read_conversation(ax=ax)
        self.assertTrue(res['ok'])
        self.assertEqual(fe.call_count, 2)          # 每个窗口各一次（列表 + 聊天），仅此而已
        self.assertEqual(res['input_rect'], (311.0, 753.0, 1098.0, 169.0))

    def test_no_chat_window_error(self):
        ax = FakeAX(windows=[list_window()])
        res = ax_app.read_conversation(ax=ax)
        self.assertEqual((res['ok'], res['error'], res['messages']), (False, ax_app.ERR_NO_WINDOW, []))

    def test_empty_tree_error_and_flag_reset(self):
        ax = FakeAX(windows=[])
        with patch.object(ax_app, 'app_element') as ae:
            res = ax_app.read_conversation(ax=ax)
            self.assertEqual((res['ok'], res['error']), (False, ax_app.ERR_EMPTY_TREE))
            ae.assert_any_call(5, force=True)

    def test_no_accessibility(self):
        with patch.object(ax_app.fill, 'has_accessibility', return_value=False):
            res = ax_app.read_conversation(ax=FakeAX())
            self.assertEqual((res['ok'], res['error']), (False, ax_app.fill.REASON_NO_ACCESS))

    def test_no_qq_process(self):
        with patch.object(ax_app, 'build_app', return_value=None):
            res = ax_app.read_conversation(ax=FakeAX())
            self.assertEqual((res['ok'], res['error']), (False, ax_app.ERR_NO_APP))


class _FakeRunningApp:
    def __init__(self, pid):
        self._pid = pid
    def processIdentifier(self):
        return self._pid
    def activateWithOptions_(self, _):
        pass


class FillTests(unittest.TestCase):
    def setUp(self):
        self.win = chat_window([])
        self.editor = ax_app.find_editor(FakeAX(), self.win)
        self.ax = FakeAX(windows=[self.win], focused=self.win)
        self.enterContext(patch.object(ax_app.fill, 'has_accessibility', return_value=True))
        self.enterContext(patch.object(ax_app, 'build_app', return_value=_FakeRunningApp(5)))
        self.enterContext(patch.object(ax_app, 'app_element', return_value='app-el'))
        self.enterContext(patch.object(ax_app, '_LAST_FILL', None))
        self.window = {'wid': 42, 'x': 311.0, 'y': 143.0, 'w': 1106.0, 'h': 782.0}

    def test_locate_input_returns_editor_rect(self):
        t = ax_app.locate_input(self.window, ax=self.ax)
        self.assertIs(t['box'], self.editor)
        self.assertEqual(t['rect'], (311.0, 753.0, 1098.0, 169.0))
        self.assertEqual(t['reason'], '填入目标')

    def test_locate_input_without_permission_does_not_traverse(self):
        with patch.object(ax_app.fill, 'has_accessibility', return_value=False), \
             patch.object(ax_app, 'chat_window') as cw:
            t = ax_app.locate_input(self.window, ax=self.ax)
            self.assertIsNone(t['box']); self.assertEqual(t['reason'], ax_app.fill.REASON_NO_ACCESS)
            cw.assert_not_called()

    def test_locate_input_without_window_geometry(self):
        # 窗口字典缺几何键：返回未取得控件，而不是 KeyError
        t = ax_app.locate_input({'wid': 42}, ax=self.ax)
        self.assertIsNone(t['box'])
        self.assertEqual(t['reason'], ax_app.REASON_NO_INPUT)

    def test_locate_input_rejects_editor_outside_window(self):
        t = ax_app.locate_input(dict(self.window, x=0.0, y=0.0, w=100.0, h=100.0), ax=self.ax)
        self.assertIsNone(t['box']); self.assertIn('不在当前聊天窗口内', t['reason'])

    def _fill(self, text, set_ok=True, landed=None, typed=(False, '')):
        """桩掉 AX 设值：设值「生效」后编辑器读回 landed；不碰真实 AX。"""
        target = ax_app.locate_input(self.window, ax=self.ax)

        def fake_set(box, value):
            if landed is not None:
                box.value = landed
            return set_ok

        with patch.object(ax_app.fill, '_ax_set_value', side_effect=fake_set) as setter, \
             patch.object(ax_app, '_type_text', return_value=typed) as typer:
            result = ax_app.fill_text(text, target=target, ax=self.ax)
        return result, setter, typer

    def test_readback_needs_change_not_substring(self):
        # 草稿「好的呀」已包含回复「好」：子串命中不算已填入，必须落到键盘后备
        self.editor.value = '好的呀'
        (ok, reason), setter, typer = self._fill('好', landed='好的呀', typed=(False, ax_app.REASON_DRAFT))
        self.assertFalse(ok)
        self.assertEqual(reason, ax_app.REASON_DRAFT)
        typer.assert_called_once()

    def test_ax_write_into_empty_editor_and_verify(self):
        (ok, reason), setter, typer = self._fill('你好', landed='你好')
        self.assertEqual((ok, reason), (True, '已填入'))
        setter.assert_called_once_with(self.editor, '你好')     # 空编辑器的 "\n" 不当前缀
        typer.assert_not_called()

    def test_ax_write_appends_to_draft(self):
        self.editor.value = '草稿'
        (ok, _), setter, _ = self._fill('你好', landed='草稿你好')
        self.assertTrue(ok)
        setter.assert_called_once_with(self.editor, '草稿你好')

    def test_falls_back_to_typing_when_ax_write_does_not_land(self):
        (ok, reason), setter, typer = self._fill('你好', set_ok=True, landed='\n', typed=(True, '已填入（键盘输入，未发送）'))
        self.assertEqual((ok, reason), (True, '已填入（键盘输入，未发送）'))
        typer.assert_called_once()

    def test_changed_target_never_writes(self):
        target = ax_app.locate_input(self.window, ax=self.ax)
        other = editor(); other.rect = (311.0, 700.0, 1098.0, 222.0)
        self.win.children[0].children[-1].children = [other]
        with patch.object(ax_app.fill, '_ax_set_value') as setter:
            ok, reason = ax_app.fill_text('你好', target=target, ax=self.ax)
        self.assertFalse(ok); self.assertIn('目标已变化', reason); setter.assert_not_called()

    def test_double_click_is_ignored(self):
        self._fill('你好', landed='你好')
        (ok, reason), setter, _ = self._fill('你好', landed='你好')
        self.assertFalse(ok); self.assertEqual(reason, ax_app.fill.REASON_DUPLICATE); setter.assert_not_called()

    def test_unreadable_editor_stops_ax_fill(self):
        # 读不到编辑器内容时不能当成空编辑器盲写
        self.editor.value = None
        target = ax_app.locate_input(self.window, ax=self.ax)
        with patch.object(ax_app.fill, '_ax_set_value') as setter:
            ok, reason = ax_app.fill_text('你好', target=target, ax=self.ax)
        self.assertEqual((ok, reason), (False, ax_app.REASON_UNREADABLE))
        setter.assert_not_called()

    def test_unreadable_editor_stops_typing(self):
        self.editor.value = None
        ok, reason = ax_app._type_text('你好', self.editor, _FakeRunningApp(5), self.ax)
        self.assertEqual((ok, reason), (False, ax_app.REASON_UNREADABLE))

    def test_empty_text_and_missing_target(self):
        self.assertEqual(ax_app.fill_text('  ', target=None, ax=self.ax), (False, ax_app.fill.REASON_EMPTY))
        self.assertEqual(ax_app.fill_text('x', target=None, ax=self.ax), (False, ax_app.REASON_NO_INPUT))

    def test_typing_exception_is_caught_and_lock_released(self):
        target = ax_app.locate_input(self.window, ax=self.ax)
        with patch.object(ax_app.fill, '_ax_set_value', return_value=False), \
             patch.object(ax_app, '_type_text', side_effect=RuntimeError('boom')):
            ok, reason = ax_app.fill_text('你好', target=target, ax=self.ax)
        self.assertEqual((ok, reason), (False, '输入过程异常，请先检查草稿，勿重复点击'))
        self.assertFalse(ax_app._FILL_LOCK.locked())

    def test_typing_refuses_existing_draft(self):
        self.editor.value = '草稿'
        ok, reason = ax_app._type_text('你好', self.editor, _FakeRunningApp(5), self.ax)
        self.assertFalse(ok); self.assertEqual(reason, ax_app.REASON_DRAFT)


class TypeTextTests(unittest.TestCase):
    """键盘后备：桩掉 Quartz 键盘事件、前台应用与编辑器焦点，只验证 _type_text 的编排。

    键入事件用 keycode 0 + Unicode 字符串；断言绝无回车（0x24 ANSI / 0x4C 小键盘）。"""

    def setUp(self):
        self.win = chat_window([])
        self.editor = ax_app.find_editor(FakeAX(), self.win)
        self.ax = FakeAX(windows=[self.win], focused=self.win)
        self.typed = []      # 每段在 key up 时记一次，模拟编辑器实际收到的内容
        self.chunks = []     # 每次 CGEventKeyboardSetUnicodeString 记一次（down/up 各一次）
        self.keycodes = []

    def _type(self, text, front=None, focused=True, drop_focus_after_chunk=0):
        front = front if front is not None else _FakeRunningApp(5)
        ws = MagicMock()
        ws.sharedWorkspace.return_value.frontmostApplication.return_value = front

        def fake_create(_, keycode, down):
            self.keycodes.append(keycode)
            return ('ev', keycode, down)

        def fake_unicode(ev, n, s):
            self.chunks.append(s)
            if not ev[2]:                       # key up：这一段落进了编辑器
                self.typed.append(s)
                self.editor.value = ''.join(self.typed)
                if drop_focus_after_chunk and len(self.typed) == drop_focus_after_chunk:
                    front._pid = 999            # 下一段发出前焦点丢失

        with patch.object(ax_app.AppKit, 'NSWorkspace', ws), \
             patch.object(ax_app.fill, '_ax_attr', return_value=focused), \
             patch.object(ax_app.AS, 'AXUIElementSetAttributeValue', lambda *a: 0), \
             patch.object(ax_app.Quartz, 'CGEventCreateKeyboardEvent', fake_create), \
             patch.object(ax_app.Quartz, 'CGEventSetFlags', lambda ev, flags: None), \
             patch.object(ax_app.Quartz, 'CGEventKeyboardSetUnicodeString', fake_unicode), \
             patch.object(ax_app.Quartz, 'CGEventPost', lambda tap, ev: None), \
             patch.object(ax_app.time, 'sleep', lambda s: None):
            return ax_app._type_text(text, self.editor, _FakeRunningApp(5), self.ax)

    def test_success_types_chunks_and_verifies(self):
        result = self._type('一二三四五' * 6)          # 30 字 → 20 + 10 两段
        self.assertEqual(result, (True, '已填入（键盘输入，未发送）'))
        self.assertEqual(''.join(self.typed), '一二三四五' * 6)

    def test_keycode_is_always_zero_never_return(self):
        self._type('你好')
        self.assertTrue(self.keycodes)
        self.assertEqual(set(self.keycodes), {0})      # 绝无 0x24 / 0x4C 回车
        self.assertNotIn(0x24, self.keycodes)
        self.assertNotIn(0x4C, self.keycodes)

    def test_control_characters_never_reach_keyboard(self):
        self._type('第一行\r第二行\t尾' + '\u2028' + 'end\n')
        joined = ''.join(self.chunks)
        for ch in ('\r', '\n', '\t', '\u2028'):
            self.assertNotIn(ch, joined)

    def test_refuses_when_frontmost_is_not_qq(self):
        result = self._type('你好', front=_FakeRunningApp(999))
        self.assertEqual(result, (False, '聊天应用没有获得焦点，请先点聊天输入区再重试'))
        self.assertEqual(self.chunks, [])

    def test_refuses_when_editor_not_focused(self):
        result = self._type('你好', focused=False)
        self.assertEqual(result, (False, '输入框未获得焦点，请先点聊天输入区再重试'))
        self.assertEqual(self.chunks, [])

    def test_stops_when_focus_lost_mid_typing(self):
        # 25 字符分两段；第一段落完后前台 pid 变化 → 只发过第一段
        result = self._type('a' * 25, drop_focus_after_chunk=1)
        self.assertEqual(result, (False, '窗口或焦点变化，输入已中止；请检查草稿，勿重复点击'))
        self.assertEqual(self.typed, ['a' * 20])

    def test_emoji_chunks_stay_within_20_utf16_units(self):
        text = '😀' * 23                                    # 46 个 UTF-16 单元
        result = self._type(text)
        self.assertTrue(result[0])
        self.assertEqual(''.join(self.typed), text)
        for chunk in self.typed:
            self.assertLessEqual(len(chunk.encode('utf-16-le')) // 2, 20)


class ProbeScriptTests(unittest.TestCase):
    """probe/ax_probe.py 的两处独立缺陷：walk 计数器可变默认值、--fill 缺参数。"""

    @staticmethod
    def _probe():
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'ax_probe', ROOT / 'probe' / 'ax_probe.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_walk_counter_resets_between_calls(self):
        probe = self._probe()
        big = {'children': [{'children': ()} for _ in range(6100)]}
        small = {'children': [{'children': ()}, {'children': ()}]}
        with patch.object(probe, 'attr',
                          side_effect=lambda el, name: el['children']):
            first = sum(1 for _ in probe.walk(big))     # 顶满 6000 节点上限
            second = sum(1 for _ in probe.walk(small))  # 可变默认 limit=[0] 会让这轮一无所获
        self.assertEqual(first, 6000)
        self.assertEqual(second, 3)

    def test_fill_arg_requires_a_value(self):
        probe = self._probe()
        self.assertEqual(probe.fill_arg([]), (None, True))
        self.assertEqual(probe.fill_arg(['--fill', '你好']), ('你好', True))
        text, ok = probe.fill_arg(['probe.py', '--fill'])
        self.assertFalse(ok)
        self.assertIsNone(text)


if __name__ == '__main__':
    unittest.main()
