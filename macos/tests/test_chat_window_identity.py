"""Offline regressions for #50: which windows count as WeChat, in both directions.

Run: uv run python -B -m unittest discover -s tests

Synthetic window lists only — no screen read, no WeChat process, no permissions, no
model calls. The owner strings below were sampled live on macOS 26 + WeChat 4.1.13,
where the app reports owner='微信'; 微信读书 and 微信输入法 also own titled, window-sized
windows, which is what makes them reachable as false positives.
"""
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import perception
from perception import find_wechat_window

# Apps whose display names merely contain 微信 / WeChat but are not WeChat itself.
SIBLING_APPS = ("微信读书", "微信输入法", "企业微信", "WeChatWork")


def window(owner, title, w, h, wid=1, pid=1):
    return {'kCGWindowOwnerName': owner, 'kCGWindowName': title,
            'kCGWindowNumber': wid, 'kCGWindowOwnerPID': pid,
            'kCGWindowBounds': {'X': 0, 'Y': 0, 'Width': w, 'Height': h}}


def find_with(windows, previous_wid=None):
    with patch('Quartz.CGWindowListCopyWindowInfo', return_value=windows):
        return find_wechat_window(previous_wid)


class _FakeApp:
    def __init__(self, name, bundle):
        self._name, self._bundle = name, bundle

    def bundleIdentifier(self):
        return self._bundle

    def localizedName(self):
        return self._name


class _FakeWorkspace:
    def __init__(self, app):
        self._app = app

    def frontmostApplication(self):
        return self._app


def frontmost(name, bundle=""):
    """frontmost_app_is_wechat() against a synthetic NSWorkspace; queries no real app."""
    appkit = types.ModuleType('AppKit')
    appkit.NSWorkspace = SimpleNamespace(
        sharedWorkspace=lambda: _FakeWorkspace(_FakeApp(name, bundle)))
    with patch.dict(sys.modules, {'AppKit': appkit}):
        return perception.frontmost_app_is_wechat()


class WeChatWindowIdentityTests(unittest.TestCase):
    def setUp(self):
        # keep every window-list test hermetic: no real AX query against a live pid.
        # AX-focused tests below re-patch this with the frame they simulate.
        ax = patch.object(perception, '_ax_focused_frame', return_value=None)
        ax.start()
        self.addCleanup(ax.stop)

    def test_weixin_alias_owner_is_accepted(self):
        """The #50 case: a 4.x build reporting 'Weixin' must still be found.

        The substring test this replaces matched on 'WeChat' and '微信', so an owner of
        'Weixin' matched neither and the window list came back empty — the panel then
        just disappeared with no stated reason.
        """
        win = find_with([window('Weixin', 'Weixin', 949, 862, wid=5)])
        self.assertIsNotNone(win)
        self.assertEqual(win.wid, 5)

    def test_sibling_app_window_is_never_selected(self):
        """A titled 微信读书 window used to pass the owner filter and be OCR'd as chat."""
        for previous in (None, 9):
            with self.subTest(previous_wid=previous):
                self.assertIsNone(
                    find_with([window('微信读书', '微信读书', 1728, 990, wid=9)], previous))

    def test_siblings_are_rejected_one_by_one(self):
        for owner in SIBLING_APPS:
            with self.subTest(owner=owner):
                self.assertIsNone(find_with([window(owner, owner, 1728, 990)]))

    def test_every_declared_name_is_accepted_as_owner(self):
        """The declared list is exactly the accepted set — no name works only by accident."""
        for name in perception.WECHAT_APP_NAMES:
            with self.subTest(name=name):
                self.assertIsNotNone(find_with([window(name, name, 949, 862)]))

    def test_main_window_still_wins_over_larger_detached(self):
        """A detached WeChat window is bigger but must not outrank the main chat window."""
        detached = window('WeChat', '微信 (窗口)', 947, 679, wid=2)
        main = window('微信', '微信', 754, 593, wid=1)
        self.assertEqual(find_with([detached, main]).wid, 1)
        self.assertEqual(find_with([detached, main], previous_wid=2).wid, 1)

    def test_detached_wechat_window_still_eligible_when_main_absent(self):
        """Exact owner matching filters the app, not the window: detached windows keep
        owner 'WeChat', so the main-window-absent fallback still resolves."""
        detached = window('WeChat', '微信 (窗口)', 947, 679, wid=2)
        self.assertEqual(find_with([detached]).wid, 2)

    def test_sibling_never_revives_via_previous_wid(self):
        main = window('微信', '微信', 949, 862, wid=1)
        sibling = window('微信读书', '微信读书', 1728, 990, wid=9)
        self.assertEqual(find_with([main, sibling], previous_wid=9).wid, 1)

    def test_foreground_check_and_window_filter_accept_the_same_names(self):
        """These two call sites are what drifted apart in #50; this pins them together."""
        for name in perception.WECHAT_APP_NAMES:
            with self.subTest(accepted=name):
                self.assertTrue(frontmost(name))
                self.assertIsNotNone(find_with([window(name, name, 949, 862)]))
        for name in SIBLING_APPS:
            with self.subTest(rejected=name):
                self.assertFalse(frontmost(name))
                self.assertIsNone(find_with([window(name, name, 1728, 990)]))

    def test_bundle_id_still_identifies_wechat_under_any_display_name(self):
        self.assertTrue(frontmost('WeChat', bundle='com.tencent.xinWeChat'))
        self.assertTrue(frontmost('Some Locale Name', bundle='com.tencent.xinWeChat'))
        self.assertFalse(frontmost('微信读书', bundle='com.tencent.weread'))

    def test_default_detached_chat_window_is_eligible(self):
        """#91: WeChat 4.x's default detached chat window is 550pt wide; the old
        600pt gate excluded every one of them from the candidate set entirely."""
        self.assertIsNotNone(find_with([window('WeChat', '张三', 550, 719, wid=3)]))

    def test_ax_focused_detached_window_beats_larger_main(self):
        """#91: the window the user is reading (AX focus) outranks the bigger main
        window and the area sort — that is the whole point of the focused match."""
        detached = window('WeChat', '张三', 550, 719, wid=2)
        main = window('微信', '微信', 959, 769, wid=1)
        focused = (0.0, 0.0, 550.0, 719.0)   # the helper's windows sit at the origin
        with patch.object(perception, '_ax_focused_frame', return_value=focused):
            self.assertEqual(find_with([main, detached]).wid, 2)

    def test_ax_failure_keeps_main_window_priority(self):
        """AX reads fail routinely (permission, timing); without a usable focus the
        #50 heuristic — main window over larger detached — rules unchanged."""
        detached = window('WeChat', '微信 (窗口)', 947, 679, wid=2)
        main = window('微信', '微信', 754, 593, wid=1)
        with patch.object(perception, '_ax_focused_frame', return_value=None):
            self.assertEqual(find_with([detached, main]).wid, 1)

    def test_ax_focus_on_non_candidate_surface_falls_back(self):
        """A focused WeChat surface that is not a chat window (popup, mini program)
        matches no candidate, so selection falls back to the #50 heuristic."""
        main = window('微信', '微信', 959, 769, wid=1)
        with patch.object(perception, '_ax_focused_frame',
                          return_value=(0.0, 0.0, 360.0, 288.0)):
            self.assertEqual(find_with([main]).wid, 1)

    def test_ax_focus_beats_previous_wid_stickiness(self):
        """Focus is fresh every call: switching to another chat's window must move
        the read target immediately, not stay stuck on the previously chosen one."""
        group = window('WeChat', '白金群', 700, 640, wid=2)
        private = window('WeChat', '李四', 550, 719, wid=3)
        focused = (0.0, 0.0, 550.0, 719.0)   # matches the private window's frame only
        with patch.object(perception, '_ax_focused_frame', return_value=focused):
            self.assertEqual(find_with([group, private], previous_wid=2).wid, 3)


class _FakeAX:
    """A stub ApplicationServices module: enough AX surface for the helper.

    The helper imports ApplicationServices inside the call, so patching sys.modules
    keeps these tests hermetic — no accessibility query, no real pid.
    """

    def __init__(self, focus_err=0, attr_map=None, value_ok=True):
        self.kAXFocusedWindowAttribute = 'focused'
        self.kAXPositionAttribute = 'position'
        self.kAXSizeAttribute = 'size'
        self.kAXValueCGPointType = 'point-type'
        self.kAXValueCGSizeType = 'size-type'
        self.focus_err = focus_err
        self.attr_map = attr_map or {}
        self.value_ok = value_ok

    def AXUIElementCreateApplication(self, pid):
        return ('app', pid)

    def AXUIElementCopyAttributeValue(self, element, name, _None):
        if name == self.kAXFocusedWindowAttribute:
            err = self.focus_err
            return err, None if err else ('window', 1)
        return 0, self.attr_map.get(name)

    def AXValueGetValue(self, raw, value_type, _None):
        if not self.value_ok:
            return False, None
        if value_type == self.kAXValueCGPointType:
            x, y = raw
            return True, SimpleNamespace(x=x, y=y)
        w, h = raw
        return True, SimpleNamespace(width=w, height=h)


def with_fake_ax(fake):
    return patch.dict(sys.modules, {'ApplicationServices': fake})


class AXFocusedFrameTests(unittest.TestCase):
    def test_reads_focused_window_frame(self):
        fake = _FakeAX(attr_map={
            'position': (100.0, 50.0), 'size': (550.0, 719.0)})
        with with_fake_ax(fake):
            self.assertEqual(perception._ax_focused_frame(517),
                             (100.0, 50.0, 550.0, 719.0))

    def test_unanswered_focus_query_returns_none(self):
        # 微信在后台时实测 -25212；任何非零错误码都回退
        with with_fake_ax(_FakeAX(focus_err=-25212)):
            self.assertIsNone(perception._ax_focused_frame(517))

    def test_missing_position_or_size_returns_none(self):
        with with_fake_ax(_FakeAX(attr_map={'position': (1.0, 2.0)})):
            self.assertIsNone(perception._ax_focused_frame(517))

    def test_unreadable_value_returns_none(self):
        fake = _FakeAX(attr_map={'position': (1.0, 2.0), 'size': (3.0, 4.0)},
                       value_ok=False)
        with with_fake_ax(fake):
            self.assertIsNone(perception._ax_focused_frame(517))


if __name__ == '__main__':
    unittest.main()
