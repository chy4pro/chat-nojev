"""Floating HUD: a non-activating panel beside the chat app showing intent, risk and ranked replies.

Design notes
  * NSWindowStyleMaskNonactivatingPanel + floating level: the panel never steals focus
    from the chat app, and window-ID capture means it never appears in our own screenshots.
  * Poll loop: read the chat, hash the newest message, analyse only when it changes.
  * 判断和候选是同一次调用的产物（src/generate.py）：上游的判断层（本地 decider-2b /
    云端 TypeSafe Jev）和那次单独的排序调用都没了，意图、风险、具体行动和每条候选的分
    都跟着候选一起回来。所以这里也没有「预判」那条线了——消息一被看见就开跑的只剩
    generation 一条（latest-wins），停稳后直接把判断和候选一起上屏。流式那一路候选仍然
    边写边上屏（"排序中"），整条回完再带着分重画一次。
  * The panel positions itself against the chat app's window each tick, so it follows moves,
    resizes and monitor changes without any window-server hooks.
  * The HUD uses native macOS vibrancy with semantic green/amber/red accents. The
    Appearance stays pinned to Aqua so labels and controls keep the same tested contrast.
  * 「填入」 is dispatched through the per-app adapter layer (src/apps/) into the current
    chat app's input box (OCR path: accessibility writes via src/fill.py; text path: AX value-set
    with a keyboard-events fallback). No clipboard, and nothing is ever sent. It needs
    the Accessibility permission; when that is missing the HUD asks for it and reports
    the failure.
"""

from __future__ import annotations

import objc
import os
import subprocess
import threading
import time
from math import isfinite
from pathlib import Path

import AppKit
import Quartz
import sys

from AppKit import (
    NSAppearance,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBackgroundColorAttributeName,
    NSBezierPath,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSScreen,
    NSTextField,
    NSView,
    NSWindowMiniaturizeButton,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
    NSWindowZoomButton,
    NSWindowCloseButton,
)
from Foundation import (NSMutableAttributedString, NSMakeRange, NSMakeRect,
                        NSMakeSize, NSObject, NSTimer)

sys.path.insert(0, str(Path(__file__).parent))
import userconfig  # noqa: E402

userconfig.load()   # ~/.config/jev-jarvis/env -> os.environ (Finder apps inherit none)

from perception import (  # noqa: E402
    find_wechat_window,
    screen_capture_ok,
    request_screen_capture,
)
from apps.registry import APPS, UNKNOWN, frontmost_app  # noqa: E402  按前台 App 分发（各支持平台）
from generate import BUILTIN_SOURCE, Generator, load_credentials  # noqa: E402
import styles  # noqa: E402
import fill  # noqa: E402
import ui_style  # noqa: E402
import chat_context

PANEL_W, PANEL_H = 360, 614   # tall enough for 3-line candidates + the chat name row
COLLAPSED_H = 96              # height when the panel is rolled up
# The tick timer fires at FAST_TICK; a read only runs when due. A quiet screen (fingerprint
# match ⇒ no OCR) re-checks every FAST_TICK — a new message surfaces within 0.25 s instead
# of within 1 s. A read that found a change (full capture+OCR paid) first keeps a SHORT
# cadence for a few reads (a burst's next message is noticed in ~0.45 s, not after a full
# SLOW_TICK) and only settles back to SLOW_TICK if the pane keeps moving — that is the
# cadence the old fixed poll had, kept as the CPU guard for a continuously moving screen.
FAST_TICK = 0.25         # re-check cadence while the chat pane is quiet
BURST_TICK = 0.45        # short cadence right after a change: catch the burst's next message
BURST_READS = 3          # how many reads stay on BURST_TICK before falling back to SLOW_TICK
SLOW_TICK = 1.0          # re-check cadence while the chat pane keeps moving
READ_FAILURE_HIDE_S = 2.0  # do not flicker on a transient capture/window miss
EMPTY_FRAME_REUSE_S = READ_FAILURE_HIDE_S  # #58: how long an empty-OCR streak may
                                           # reuse the last read before giving up
SETTLE_S = 1.2           # upper bound on the settle wait (anti-flood; unchanged by design)
EARLY_SETTLE_S = 0.70    # the gate may open this early …
STABLE_READS = 3         # … but only after this many consecutive unchanged reads
MIN_GAP_S = 2.0          # never restart analysis faster than this
IDLE_STATUS = "等待聊天应用消息…"   # the resting status line (also set at build time)
# 上游这里还有 WARM_STATUS「判断模型加载中…」：本地 decider-2b 首次加载/下载要数分钟。
# 没有本地模型可加载了，那一行连同 applyStatus_ / applyWarmDone_ / applyWarmFailed_ 一起没了。


# Shared with the settings window so both surfaces keep one visual vocabulary.
PALETTE = ui_style.PALETTE
_rgb = ui_style.rgb

# Compact reply rows: probability rail, fully wrapped reply, then the two existing actions.
# Only the minimum is fixed. _relayout() measures each candidate and grows the row as needed.
CAND_ROW_X, CAND_ROW_W, CAND_ROW_MIN_H = 20, PANEL_W - 40, 50
CAND_PROB_X, CAND_PROB_W = 30, 44
CAND_TEXT_X, CAND_TEXT_W = 82, 144
CAND_BTN_W, CAND_BTN_H, CAND_BTN_GAP = 48, 22, 4
CAND_BTN_X = PANEL_W - 26 - (2 * CAND_BTN_W + CAND_BTN_GAP)
CAND_ROW_GAP = 4

# 话术 groups. Each group is headed by its dropdown; its candidates sit under it. The panel
# is only as tall as the groups in use, so nothing is reserved for a tone that is switched
# off (that reservation is what used to leave a dead gap in the middle).
TONE_DD_X, TONE_DD_W, TONE_DD_H, TONE_DD_GAP = 20, PANEL_W - 40, 24, 5
TONE_DD_INSET = 8         # the popup sits this far inside its field, like text in an input box
TONE_DD_FONT = 12         # compact but still the clearest interactive label in each group
                          # thing on the panel the user is meant to click
GROUP_PAD_Y = 5           # breathing room above the selector and below the final reply
GROUP_GAP = 10            # between one group's rows and the next group's dropdown
BOTTOM_PAD = 14           # below the last group


LOG_PATH = Path.home() / "Library" / "Logs" / "jev-jarvis.log"


# ---------------------------------------------------------------------------
# 判断结果 → 面板上那几行文字。**校验只在这里做。**
#
# 上游的判断层自己会兜一道：judge_jev.JevJudge.judge() 查意图表（不在 8 个标签里就退
# 「闲聊」）、把 confidence 和 risk 强转成 float（转不了就 0.0），所以面板可以直接
# v["intent"]、v["confidence"]:.0% 地用。这一版的适配层只翻译不校验（见 src/generate.py
# 的说明），坏值是原样递到这儿的——所以那道兜底搬到了用的地方，也就是下面这几个函数。
# 结果是一样的：同一份坏值，面板不炸；区别只在坏值现在显示成「暂未判断」，而不是被判断层
# 悄悄改成「闲聊」或者 0。
#
# 范围一概不查：上游给 11 分就显示「危险 11/9」，这里照旧。夹回 9 会让同一份坏值显示成
# 「9/9」——那是改行为，不是兜底。
def _judgment_text(value) -> str:
    """意图那行：模型写的那句话原样显示；没给 / 空的 / 不是字符串才「暂未判断」。

    上游这里是判断模型从 8 个固定标签里挑一个，面板显示那个标签。通用模型能直接写出
    要显示的那句话（用对话那门语言），所以面板显示的就是它写的，不再有对照表。
    """
    return value.strip() if isinstance(value, str) and value.strip() else "暂未判断"


def _risk_level(value) -> int | None:
    """风险那行的档位：能当数用就四舍五入成整档，不能就 None（面板显示「风险待判断」）。

    上游显示的是均值四舍五入后的整数（「4.7/9」读起来像测量值，「5/9」才是估计值）,
    这里一个字没改。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(round(value)) if isfinite(value) else None


def _confidence_text(verdict: dict) -> str:
    """把握那行：模型给了数才显示百分比，没给或者不是数就整行空着（不编一个 0%）。"""
    value = verdict.get("confidence")
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return ""
    return f"{value:.0%}"


def _prob_text(value) -> str:
    """候选那一行左边那个百分比。

    None = 这一条还没拿到分（流式刚上屏的那一瞬间，上游也是这个状态）。
    分是模型自己报的，可能不是数——那就显示「待定」，不按 0 处理（0% 是一句它没说过的话）。
    """
    if value is None:
        return "排序中"
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
        return "待定"
    return f"{value * 100:.0f}%"


def _actions_text(verdict: dict) -> str:
    """具体行动那行：模型写的几条原样显示，拿 · 串起来。

    上游这一行是按意图查一张静态表（judge.ACTION_MAP，V0 的注释写着「no generation
    involved」——判断模型不写字，只能查表）。意图现在是模型自己写的一句短语，查表必然
    查不到，所以这几条改由同一次调用一起写（题面见 src/questions.py，ACTION_MAP 原样
    留在那里当示例）。模型没写就是空行——跟上游查不到意图时显示的完全一样。
    """
    actions = verdict.get("actions")
    if isinstance(actions, str):            # 有的模型直接给一句话
        actions = [actions]
    if not isinstance(actions, (list, tuple)):
        return ""
    return " · ".join(a.strip() for a in actions if isinstance(a, str) and a.strip())


def _log(msg: str) -> None:
    """One line per stage: to stdout, and into ~/Library/Logs/jev-jarvis.log.

    "It feels slow" is not actionable on its own, so every analysis prints what each stage
    cost; that is the whole point of this function. Deliberately **no message text and no
    candidate text**: this file is meant to be pasted into an issue, and the app's premise
    is that chat content stays on the machine.

    Both destinations on purpose: the .app launcher already redirects stdout into this same
    file, while `./start.command` only shows a terminal — so which place held the evidence
    depended on how the user happened to launch it. The inode check stops the .app case
    from writing every line twice.
    """
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    try:
        if os.fstat(sys.stdout.fileno()).st_ino == LOG_PATH.stat().st_ino:
            return                       # stdout already IS that file (the .app case)
    except Exception:
        pass
    try:
        with open(LOG_PATH, "a") as fh:
            fh.write(line + "\n")
    except OSError:
        pass                             # a log we cannot write is not worth breaking over


class _BoxesView(NSView):
    """The YOLO overlay's canvas: paints whatever `boxes` last held.

    boxes: [(NSRect, NSColor, line_width, NSAttributedString chip), ...] in view
    coordinates, set from the main thread and followed by setNeedsDisplay_. The view
    owns no data — it only renders the controller's most recent read, which is what
    keeps the overlay honest: what you see boxed is exactly what the pipeline read.
    """

    def drawRect_(self, rect):
        for box in getattr(self, "boxes", None) or []:
            r, color, lw, chip = box[:4]
            color.set()
            NSBezierPath.setDefaultLineWidth_(lw)
            if len(box) > 4 and box[4]:
                path = NSBezierPath.bezierPathWithRect_(r)
                path.setLineWidth_(lw)
                path.setLineDash_count_phase_([6.0, 4.0], 2, 0)
                path.stroke()
            else:
                NSBezierPath.strokeRect_(r)
            chip.drawAtPoint_((r.origin.x, r.origin.y + r.size.height + 2))


class HudController(NSObject):
    def init(self):
        self = objc.super(HudController, self).init()
        if self is None:
            return None
        self._input_calibration = None
        self._input_calibration_wid = None
        self._input_calibration_saved = userconfig.get("JEV_INPUT_REGION")
        self._calibration = None
        self._calibration_wid = None
        self._calibration_saved = userconfig.get("JEV_MESSAGE_REGION")
        self._calibration_required = bool(self._calibration_saved)
        self._calibrating = False
        self.last_seen = None          # newest message text observed
        self._reply_key = None         # (conversation, incoming text), never an outgoing message
        self._reply_epoch = 0          # invalidate even if the same text reappears later
        self._reply_worker = threading.local()
        self._active_context = None
        self._context_lock = threading.RLock()
        self._context_version = 0
        self.history_enabled = userconfig.get("JEV_HISTORY") == "1"
        try:
            self.context_limit = chat_context.message_limit(userconfig.get("JEV_CONTEXT_MESSAGES") or "20")
        except ValueError:
            self.context_limit = 20
            _log("上下文条数配置无效，使用默认值 20")
        self._observed_messages = None
        self._observed_offset = 0
        self.conversations = None
        try:
            self.conversations = chat_context.Conversations()
        except (OSError, ValueError):
            _log("本地会话数据读取失败，历史和背景暂不可用")
        self.last_change_ts = 0.0      # when it last changed (burst detection)
        self.last_analyze_ts = 0.0     # rate limit for analysis starts
        self.analyzed_text = None      # what the panel currently shows
        self._read_once = False        # first OCR call includes Vision's own load
        self._last_skip_reason = None
        self.generator = Generator()
        # 话术: per-slot tone selection. A slot on 不用 contributes no request and no rows,
        # so the panel is exactly as tall as the groups actually in use.
        self.slot_tones = list(styles.DEFAULT_SLOTS) + [styles.NONE_LABEL]
        self.slot_tones = self.slot_tones[:styles.MAX_SLOTS]
        while len(self.slot_tones) < styles.MAX_SLOTS:
            self.slot_tones.append(styles.NONE_LABEL)
        self._dds: list = []
        self._dd_boxes: list = []       # the flat fields the dropdowns are drawn into
        self._group_boxes: list = []    # translucent surfaces behind active tone groups
        self._rows: list = []
        self._appearance_surfaces = []
        self._appearance_buttons = []
        self._message_expanded = False
        self._message_text = ""
        self._layout_key = None
        self._fixed: list = []          # (control, x, dy_from_top, w, h) — the rows above
        self._detail_views: list = []   # non-data chrome hidden with the expanded details
        self._risk_dots: list = []      # low / medium / high indicators, presentation only
        self._group_top = 0             # where the first group starts, from the top
        self._title_h = 28              # measured right after the panel is built
        self.cand_texts: list[str | None] = [None] * (styles.MAX_SLOTS * styles.PER_TONE)
        self._last_intent = ""          # kept so the overlay can badge the judged message
        # streaming candidates: each generation run bumps this epoch at its start and its
        # streamed lines carry the value, so a late line from a run a tone change or a new
        # message superseded is dropped instead of written into the new run's rows
        self._gen_epoch = 0
        self._stream_rows: dict[int, int] = {}   # slot -> lines already shown, per run

        self._busy = False
        self._next_read_ts = 0.0    # reads before this timestamp are skipped (quiet screen)
        self._fingerprint = None    # last chat-pane fingerprint; equal ⇒ skip OCR entirely
        self._last_full = None      # last OCR'd result, reused while the pane is unchanged
        self._analyzing = False     # the analysis runs off the tick path
        self._regenerating = False  # explicit candidate refresh; does not re-read/judge
        # Early run: the one call starts the moment a message is seen, latest-wins. It
        # carries the judgment too, so the settle window (~1 s) hides the whole latency and
        # nothing is left to compute after the gate opens.
        # Cost: a burst's intermediate messages each fire one discarded API call — cheap at
        # glm-4-flash-class pricing, and superseded results are never consumed.
        # 上游这里还有第二个常驻线程 _prejudge_loop：本地判断模型不要钱，所以消息一出现
        # 就先判一遍，停稳时判断已经在手上了。判断合进这一次调用之后它没有独立的活可干，
        # 整个删掉——判断跟着下面这个早跑一起回来。
        self._pregen_req = None              # (text, context, tones tuple, reply epoch)
        self._pregen_result = None           # (text, tones, gen dict, reply epoch)
        self._pregen_running = False         # a pre-generation request is in flight
        self._pregen_event = threading.Event()
        threading.Thread(target=self._pregen_loop, daemon=True).start()
        self._burst_left = BURST_READS       # short-cadence reads left after a change
        self._stable_n = 0                   # consecutive unchanged reads since last change
        self._collapsed = False
        self._expanded_h = None       # full height, captured the first time we collapse
        self._paused = False
        self._always_on_top = True     # menu-bar switch; preserve the historical default
        # YOLO overlay default: JEV_BOXES=1 (or true/yes/on) in the env file starts it on;
        # either way the menu-bar item flips it at runtime
        self._show_boxes = userconfig.get("JEV_BOXES").strip().lower() in (
            "1", "true", "yes", "on")
        self._last_risk = None        # newest verdict's risk, for the overlay's highlight
        self._chat_title = ""
        self._asked_permission = False
        self._win_wid = None          # sticky chat window id (per app)
        self._app = None              # 当前前台聊天应用适配器；None = 不在任何聊天应用前台
        self._asked_accessibility = False   # 文本接口路径的辅助功能授权只弹一次
        self._foreground_epoch = 0    # catches leave+return while one capture is in flight
        self._read_fail_since = None  # debounce transient foreground capture failures
        self._read_fail_hidden = False
        self._empty_frame_since = None  # empty-OCR streak start (#58): reuse the last
                                        # read, give up after EMPTY_FRAME_REUSE_S
        self._last_origin = None      # last applied panel origin
        self._pending_origin = None   # candidate origin awaiting confirmation
        self._build_panel()
        self._build_overlay()
        self._expanded_h = self.panel.frame().size.height
        return self

    # ------------------------------------------------------------------ ui
    @objc.python_method
    def _build_panel(self):
        # Closable/Miniaturizable are what actually CREATE the standard window buttons;
        # NonactivatingPanel alone gives a title bar with no controls at all.
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskNonactivatingPanel)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H), style, NSBackingStoreBuffered, False)
        self.panel.setLevel_(AppKit.NSFloatingWindowLevel if self._always_on_top
                             else AppKit.NSNormalWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setAlphaValue_(1.0)
        self.panel.setHasShadow_(True)
        # The title bar and button bezels are drawn from the appearance, not from the
        # background colour, so pin Aqua: a dark-mode system would otherwise give a dark
        # title bar above a white panel.
        self.panel.setAppearance_(NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameAqua))
        self.panel.setBackgroundColor_(NSColor.clearColor())
        self.panel.setTitle_("jev-jarvis")
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)

        # NSVisualEffectView is the native implementation of the reference's light frosted
        # material. The tint keeps text readable when the wallpaper behind it is busy.
        view = AppKit.NSVisualEffectView.alloc().initWithFrame_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H))
        view.setMaterial_(getattr(
            AppKit, "NSVisualEffectMaterialSidebar",
            getattr(AppKit, "NSVisualEffectMaterialLight", 1)))
        view.setBlendingMode_(AppKit.NSVisualEffectBlendingModeBehindWindow)
        view.setState_(AppKit.NSVisualEffectStateActive)
        view.setWantsLayer_(True)
        view.layer().setBackgroundColor_(PALETTE["bg"].CGColor())
        # A tint subview sits above the system material. Setting the effect view's
        # backing-layer color alone can be covered by macOS's accessibility fallback.
        self._solid_backdrop = ui_style.make_surface(0, NSColor.clearColor())
        self._solid_backdrop.setFrame_(view.bounds())
        self._solid_backdrop.setAutoresizingMask_(AppKit.NSViewWidthSizable | AppKit.NSViewHeightSizable)
        view.addSubview_(self._solid_backdrop)
        self.rows: dict[str, NSTextField] = {}

        # The latest master adds model settings to this same header. Keep it as a quiet,
        # standalone icon so the new control does not collide with the chat title.
        self.settings_button = self._make_button(PANEL_W - 44, 0, 32, 32,
                                                 "", "openSettings:", 0)
        settings_icon = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "gearshape", "模型设置")
        symbol_config = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
            12, AppKit.NSFontWeightRegular)
        settings_icon = settings_icon.imageWithSymbolConfiguration_(symbol_config)
        self.settings_button.setImage_(settings_icon)
        self.settings_button.setImagePosition_(AppKit.NSImageOnly)
        self.settings_button.setImageScaling_(AppKit.NSImageScaleNone)
        self.settings_button.setBordered_(False)
        self.settings_button.setContentTintColor_(PALETTE["muted"])
        self.settings_button.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
        self.settings_button.layer().setBorderWidth_(0.0)
        self.settings_button.setToolTip_("模型设置")
        self.settings_button.setAccessibilityLabel_("模型设置")
        self.settings_button.setHidden_(False)
        view.addSubview_(self.settings_button)
        self._fixed.append((self.settings_button, PANEL_W - 44, 4, 32, 32))

        self.calibration_button = self._make_button(PANEL_W - 80, 0, 32, 32,
                                                    "", "calibrateMessages:", 0)
        icon = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
            "viewfinder", "校准消息区和输入区")
        self.calibration_button.setImage_(icon.imageWithSymbolConfiguration_(symbol_config))
        self.calibration_button.setImagePosition_(AppKit.NSImageOnly)
        self.calibration_button.setImageScaling_(AppKit.NSImageScaleNone)
        self.calibration_button.setBordered_(False)
        self.calibration_button.setContentTintColor_(PALETTE["muted"])
        self.calibration_button.layer().setBackgroundColor_(NSColor.clearColor().CGColor())
        self.calibration_button.layer().setBorderWidth_(0.0)
        self.calibration_button.setToolTip_("校准消息区和输入区")
        self.calibration_button.setAccessibilityLabel_("校准消息区和输入区")
        self.calibration_button.setHidden_(False)
        view.addSubview_(self.calibration_button)
        self._fixed.append((self.calibration_button, PANEL_W - 80, 4, 32, 32))

        # Decorative surfaces are fixed; every string still comes from the existing rows.
        for surface, x, top, w, h in (
            (self._make_surface(12, PALETTE["surface"]), 14, 54, PANEL_W - 28, 62),
            (self._make_surface(12, PALETTE["surface"]), 14, 124, PANEL_W - 28, 72),
            (self._make_surface(10, PALETTE["surface"]), 14, 204, PANEL_W - 28, 34),
        ):
            if top == 54:
                self._message_surface = surface
            view.addSubview_(surface)
            self._fixed.append((surface, x, top, w, h))
            self._detail_views.append(surface)

        # Summary separators and static labels carry no model data; they only make the
        # existing intent/risk/action fields scan like the approved design.
        for x in (150, 260):
            divider = self._make_surface(0, PALETTE["edge"])
            view.addSubview_(divider)
            self._fixed.append((divider, x, 136, 1, 46))
            self._detail_views.append(divider)

        action_label = self._make_label(0, 0, 58, 16, size=11,
                                        color=PALETTE["text"], bold=True)
        action_label.setStringValue_("具体行动")
        view.addSubview_(action_label)
        self._fixed.append((action_label, 26, 213, 58, 16))
        self._detail_views.append(action_label)

        risk_title = self._make_label(0, 0, 64, 14, size=9, color=PALETTE["muted"])
        risk_title.setStringValue_("风险等级")
        view.addSubview_(risk_title)
        self._fixed.append((risk_title, 272, 132, 64, 14))
        self._detail_views.append(risk_title)
        for i, (title, color) in enumerate((
            ("低", PALETTE["green"]), ("中", PALETTE["amber"]), ("高", PALETTE["red"]))):
            center_x = 278 + i * 26
            dot = self._make_surface(4, color.colorWithAlphaComponent_(0.68))
            view.addSubview_(dot)
            self._fixed.append((dot, center_x - 4, 152, 8, 8))
            self._detail_views.append(dot)
            self._risk_dots.append(dot)
            label = self._make_label(0, 0, 20, 14, size=9, color=PALETTE["muted"])
            label.setAlignment_(AppKit.NSTextAlignmentCenter)
            label.setStringValue_(title)
            view.addSubview_(label)
            self._fixed.append((label, center_x - 10, 164, 20, 14))
            self._detail_views.append(label)

        for key, x, top, w, h, size, color, bold in (
            ("chat", 20, 14, PANEL_W - 112, 20, 15, PALETTE["accent"], True),
            ("status", 20, 36, PANEL_W - 40, 14, 10, PALETTE["muted"], False),
            ("message", 22, 82, PANEL_W - 44, 38, 14, PALETTE["text"], False),
            ("sender", 22, 62, PANEL_W - 112, 14, 10, PALETTE["muted"], False),
            ("intent", 26, 136, 116, 26, 20, PALETTE["text"], True),
            ("confidence", 26, 166, 116, 16, 11, PALETTE["muted"], False),
            ("risk", 164, 137, 92, 24, 14, PALETTE["green"], True),
            ("actions", 94, 213, 236, 16, 11, PALETTE["text"], False),
        ):
            tf = self._make_label(x, 0, w, h, size=size, color=color, bold=bold)
            if key in {"message", "actions"}:
                tf.cell().setWraps_(True)
            self.rows[key] = tf
            if key == "message":
                tf.cell().setScrollable_(False)
                tf.cell().setUsesSingleLineMode_(False)
                tf.cell().setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
                tf.setMaximumNumberOfLines_(2)
                scroll = AppKit.NSScrollView.alloc().initWithFrame_(NSMakeRect(x, 0, w, h))
                scroll.setDrawsBackground_(False)
                scroll.setHasVerticalScroller_(False)
                scroll.setAutohidesScrollers_(True)
                scroll.setScrollerStyle_(AppKit.NSScrollerStyleOverlay)
                scroll.setDocumentView_(tf)
                self._message_scroll = scroll
                view.addSubview_(scroll)
                self._fixed.append((scroll, x, top, w, h))
                self._detail_views.append(scroll)
            else:
                view.addSubview_(tf)
                self._fixed.append((tf, x, top, w, h))
            if key not in {"chat", "status"}:
                self._detail_views.append(tf)

        self._message_toggle = self._make_button(PANEL_W - 82, 0, 60, 18,
                                                  "展开 ▾", "toggleMessage:", 0)
        self._message_toggle.setAccessibilityLabel_("展开或收起完整消息")
        view.addSubview_(self._message_toggle)
        self._fixed.append((self._message_toggle, PANEL_W - 82, 60, 60, 18))
        self._detail_views.append(self._message_toggle)

        header = self._make_label(18, 0, PANEL_W - 36, 18,
                                  size=12, color=PALETTE["text"], bold=True)
        view.addSubview_(header)
        self.rows["cand_header"] = header
        self._set_candidate_header("候选回复（按合适度排序）")
        self._fixed.append((header, 18, 250, PANEL_W - 36, 18))
        self._detail_views.append(header)
        self._group_top = 274

        # ---- 话术 groups: each dropdown heads a group and its candidates sit underneath,
        # so the tone is labelled by the thing that selects it. Every group's controls exist
        # from the start; _relayout() decides which are on screen. The button tags are slot
        # arithmetic (slot * PER_TONE + row) so they never shift when a group's results are
        # still in flight.
        tone_items = styles.labels() + [styles.NONE_LABEL]
        for slot in range(styles.MAX_SLOTS):
            group_box = self._make_surface(12, PALETTE["row"], PALETTE["edge"])
            view.addSubview_(group_box)
            self._group_boxes.append(group_box)

            box = self._make_surface(8, PALETTE["field"], PALETTE["edge"])
            view.addSubview_(box)
            self._dd_boxes.append(box)

            pop = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 0, TONE_DD_W - 2 * TONE_DD_INSET, TONE_DD_H), False)
            pop.setBordered_(False)          # <- no bezel, no accent-coloured chevron
            # the one discoverability aid the flat field gets: grey-on-grey reads as text,
            # a tooltip costs nothing visually and answers "can I click this?"
            pop.setToolTip_("点这里换话术（每种一组，各出 2 条）")
            pop.setFont_(NSFont.boldSystemFontOfSize_(TONE_DD_FONT))
            pop.setContentTintColor_(PALETTE["text"])
            pop.addItemsWithTitles_(tone_items)
            pop.selectItemWithTitle_(self.slot_tones[slot])
            pop.setTarget_(self)
            pop.setAction_("toneChanged:")
            view.addSubview_(pop)
            self._dds.append(pop)

            slot_rows = []
            for row in range(styles.PER_TONE):
                tag = slot * styles.PER_TONE + row
                row_box = self._make_surface(8, PALETTE["row"], PALETTE["edge"])
                row_box.setHidden_(True)
                view.addSubview_(row_box)
                prob = self._make_label(CAND_PROB_X, 0, CAND_PROB_W, 32,
                                        size=10, color=PALETTE["green"], bold=True)
                prob.cell().setWraps_(True)
                text = self._make_label(CAND_TEXT_X, 0, CAND_TEXT_W, 18,
                                        size=11, color=PALETTE["text"])
                text.cell().setWraps_(True)
                text.cell().setLineBreakMode_(AppKit.NSLineBreakByWordWrapping)
                if hasattr(text.cell(), "setMaximumNumberOfLines_"):
                    text.cell().setMaximumNumberOfLines_(0)
                copy_btn = self._make_button(CAND_BTN_X, 0, CAND_BTN_W, CAND_BTN_H,
                                             "复制", "copyCandidate:", tag)
                fill_btn = self._make_button(CAND_BTN_X + CAND_BTN_W + CAND_BTN_GAP, 0,
                                             CAND_BTN_W, CAND_BTN_H, "填入", "fillCandidate:", tag)
                track = self._make_surface(2, PALETTE["track"])
                fill_bar = self._make_surface(2, PALETTE["green"])
                track.setHidden_(True)
                fill_bar.setHidden_(True)
                for c in (prob, text, copy_btn, fill_btn, track, fill_bar):
                    view.addSubview_(c)
                slot_rows.append({"box": row_box, "prob": prob, "text": text,
                                  "btn": copy_btn, "fill_btn": fill_btn,
                                  "track": track, "fill": fill_bar})
            self._rows.append(slot_rows)

        self.reanalyze_button = self._make_button(
            14, 0, 96, 28, "立刻分析", "reanalyze:", 0)
        self.reanalyze_button.setAccessibilityLabel_("立刻分析")
        self.reanalyze_button.setToolTip_("重新读取聊天窗口并执行完整分析")
        view.addSubview_(self.reanalyze_button)
        self._detail_views.append(self.reanalyze_button)

        self.regenerate_button = self._make_button(
            118, 0, PANEL_W - 132, 28, "重新生成推荐回答", "regenerateReply:", 0)
        self.regenerate_button.setAccessibilityLabel_("重新生成推荐回答")
        self.regenerate_button.setToolTip_("沿用当前消息和判断结果，只重新生成候选回答")
        view.addSubview_(self.regenerate_button)
        self._detail_views.append(self.regenerate_button)

        self.panel.setContentView_(view)
        self._title_h = self.panel.frame().size.height - PANEL_H   # measured, not assumed
        self._relayout()
        self.rows["status"].setStringValue_(IDLE_STATUS)
        self._wire_window_controls()
        self._install_status_item()
        AppKit.NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, "accessibilityDisplayChanged:",
            AppKit.NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification, None)
        self.applyAccessibilityAppearance_(None)

    @objc.python_method
    def _build_overlay(self):
        """A transparent, click-through window aligned to the chat app: the YOLO-style view.

        Pure visualization of what perception already returns — every message's bounding
        box and its real OCR confidence, the judged one carrying intent+risk on its chip.
        Three properties keep it safe: it is OFF by default (menu-bar toggle); clicks pass
        through (`ignoresMouseEvents`), so the chat app never gets blocked; and perception
        captures by window ID, so this window can never pollute our own OCR.
        Coordinate mapping is pure normalized geometry × window point size, so it is
        independent of the capture's pixel resolution (the old 1x-nominal assumption went
        away with #83's subprocess capture).
        """
        self._ov_panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, 200, 200), NSWindowStyleMaskBorderless,
            NSBackingStoreBuffered, False)
        self._ov_panel.setLevel_(AppKit.NSFloatingWindowLevel)
        self._ov_panel.setOpaque_(False)
        self._ov_panel.setHasShadow_(False)
        self._ov_panel.setIgnoresMouseEvents_(True)   # never steal a click meant for the chat app
        self._ov_panel.setHidesOnDeactivate_(False)
        self._ov_panel.setBackgroundColor_(NSColor.clearColor())
        view = _BoxesView.alloc().init()
        view.boxes = []
        self._ov_panel.setContentView_(view)

    @objc.python_method
    def _slot_active(self, slot: int) -> bool:
        return self.slot_tones[slot] in styles.PRESETS

    @objc.python_method
    def _relayout(self):
        """Place every control for the current tone selection and size the panel to fit.

        Two things are computed here rather than at build time. Positions are measured from
        the TOP, so when the panel grows or shrinks nothing above the change moves — only the
        bottom edge does. And the height follows the groups in use: a slot on 不用 reserves
        neither a dropdown's worth of rows nor its candidates, which is what removes the dead
        space a fixed-height panel left in the middle.
        """
        message_h, document_h, overflow = self._message_metrics()
        message_delta = message_h - 26  # sender row now precedes the body
        self._message_toggle.setHidden_(not overflow)
        self._message_toggle.setTitle_("收起 ▴" if self._message_expanded else "展开 ▾")
        self._message_scroll.setHasVerticalScroller_(document_h > message_h)
        field = self.rows["message"]
        field.setMaximumNumberOfLines_(0 if self._message_expanded else 2)
        field.setFrame_(NSMakeRect(0, 0, PANEL_W - 44, document_h))
        dy = self._group_top + message_delta
        placements = []          # (control, x, dy_from_top, w, h)
        for slot in range(styles.MAX_SLOTS):
            active = self._slot_active(slot)
            self._group_boxes[slot].setHidden_(not active)
            group_top = dy
            selector_top = dy + (GROUP_PAD_Y if active else 0)
            placements.append((self._dd_boxes[slot], TONE_DD_X, selector_top,
                               TONE_DD_W, TONE_DD_H))
            placements.append((self._dds[slot], TONE_DD_X + TONE_DD_INSET, selector_top,
                               TONE_DD_W - 2 * TONE_DD_INSET, TONE_DD_H))
            dy = selector_top + TONE_DD_H
            if active:
                dy += TONE_DD_GAP
            for row in range(styles.PER_TONE):
                r = self._rows[slot][row]
                controls = self._row_controls(slot, row)
                if active:
                    # The candidate decides its own height. Short replies keep the compact
                    # minimum; longer localized text grows without truncation.
                    text_h = self._candidate_text_height(r["text"])
                    row_h = max(CAND_ROW_MIN_H, text_h + 12)
                    text_top = dy + (row_h - text_h) / 2
                    button_top = dy + (row_h - CAND_BTN_H) / 2
                    metric_top = dy + (row_h - 40) / 2
                    progress_w = max(0.0, min(36.0, r["fill"].frame().size.width))
                    placements += [
                        (r["box"], CAND_ROW_X, dy, CAND_ROW_W, row_h),
                        (r["text"], CAND_TEXT_X, text_top, CAND_TEXT_W, text_h),
                        (r["prob"], CAND_PROB_X, metric_top, CAND_PROB_W, 32),
                        (r["btn"], CAND_BTN_X, button_top, CAND_BTN_W, CAND_BTN_H),
                        (r["fill_btn"], CAND_BTN_X + CAND_BTN_W + CAND_BTN_GAP, button_top,
                         CAND_BTN_W, CAND_BTN_H),
                        (r["track"], CAND_PROB_X + 4, metric_top + 36, 36, 4),
                        (r["fill"], CAND_PROB_X + 4, metric_top + 36, progress_w, 4),
                    ]
                    dy += row_h
                    if row < styles.PER_TONE - 1:
                        dy += CAND_ROW_GAP
                else:
                    for c in controls:
                        c.setHidden_(True)
            if active:
                dy += GROUP_PAD_Y
                placements.append((self._group_boxes[slot], 14, group_top,
                                   PANEL_W - 28, dy - group_top))
            if slot < styles.MAX_SLOTS - 1:
                dy += GROUP_GAP

        # no setHidden_(False) here: the collapsed panel keeps _detail_views hidden, and
        # _render_groups() reaches this method without a collapsed guard
        placements.append((self.reanalyze_button, 14, dy, 96, 28))
        placements.append((self.regenerate_button, 118, dy, PANEL_W - 132, 28))
        dy += 28 + BOTTOM_PAD
        content_h = dy
        view = self.panel.contentView()
        view.setFrameSize_(NSMakeSize(PANEL_W, content_h))
        fixed = []
        for ctrl, x, top, w, h in self._fixed:
            if ctrl is self._message_surface:
                h += message_delta
            elif ctrl is self._message_scroll:
                h = message_h
            elif top >= 124:
                top += message_delta
            fixed.append((ctrl, x, top, w, h))
        for ctrl, x, top, w, h in placements + fixed:
            ctrl.setFrame_(NSMakeRect(x, content_h - top - h, w, h))

        # resize the window with its TOP edge pinned: growing downwards is what the eye
        # expects here, and _position_near() anchors the panel to the chat app's top anyway
        f = self.panel.frame()
        top = f.origin.y + f.size.height
        frame_h = content_h + self._title_h
        self.panel.setFrame_display_(
            NSMakeRect(f.origin.x, top - frame_h, PANEL_W, frame_h), True)
        self._expanded_h = frame_h

    @objc.python_method
    def _wire_window_controls(self):
        """Native traffic lights, mapped to this app's actions.

        red    -> quit. A hidden panel would otherwise be unreachable: LSUIElement apps
                  have no Dock icon, so a plain order-out looks like a crash.
        yellow -> roll the panel up instead of miniaturizing, for the same reason.
        green  -> hidden: the HUD has a fixed size and nothing to zoom.
        """
        close = self.panel.standardWindowButton_(NSWindowCloseButton)
        mini = self.panel.standardWindowButton_(NSWindowMiniaturizeButton)
        zoom = self.panel.standardWindowButton_(NSWindowZoomButton)
        if close:
            close.setTarget_(self)
            close.setAction_("quitApp:")
            close.setToolTip_("退出 jev-jarvis")
        if mini:
            mini.setTarget_(self)
            mini.setAction_("collapsePanel:")
            mini.setToolTip_("收起 / 展开面板")
        if zoom:
            zoom.setHidden_(True)

    @objc.python_method
    def _install_status_item(self):
        """Menu-bar item — the standard place for a background helper's controls."""
        bar = AppKit.NSStatusBar.systemStatusBar()
        self.status_item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
        self.status_item.button().setTitle_("J")
        self.status_item.button().setToolTip_("jev-jarvis · 聊天意图助手")

        menu = AppKit.NSMenu.alloc().init()
        for title, action, key in (
            ("显示 / 收起面板", "collapsePanel:", ""),
            ("暂停读屏", "togglePause:", ""),
            ("YOLO 检测框", "toggleBoxes:", ""),
            ("固定在最前面", "toggleAlwaysOnTop:", ""),
            ("立即重新分析", "reanalyze:", ""),
            ("模型设置…", "openSettings:", ","),
            ("校准区域…", "calibrateMessages:", ""),
            ("恢复自动识别区域", "clearCalibration:", ""),
        ):
            menu.addItemWithTitle_action_keyEquivalent_(title, action, key)
        menu.addItem_(AppKit.NSMenuItem.separatorItem())
        menu.addItemWithTitle_action_keyEquivalent_("退出 jev-jarvis", "quitApp:", "q")
        for item in menu.itemArray():
            item.setTarget_(self)
        self.pause_item = menu.itemArray()[1]
        self.boxes_item = menu.itemArray()[2]
        self.always_on_top_item = menu.itemArray()[3]
        self.boxes_item.setState_(
            AppKit.NSOnState if self._show_boxes else AppKit.NSOffState)
        self.always_on_top_item.setState_(
            AppKit.NSOnState if self._always_on_top else AppKit.NSOffState)
        self.status_item.setMenu_(menu)

    @objc.python_method
    def _retire_calibration_results(self):
        self._foreground_epoch += 1
        self._reply_epoch += 1
        self._gen_epoch += 1
        self._reply_key = None
        self.last_seen = self.analyzed_text = None
        self._pregen_req = self._pregen_result = None
        self._fingerprint = self._last_full = self._layout_key = None
        self._empty_frame_since = None
        self._input_target = self._input_window = None
        self._stable_n = 0
        self._next_read_ts = 0

    def calibrateMessages_(self, sender):
        if self._calibrating:
            self.calibration_controller.window.makeKeyAndOrderFront_(None)
            return
        from calibration_ui import CalibrationController
        self._calibrating = True
        self._retire_calibration_results()
        self.applyWaiting_("正在校准消息区和输入区…")
        self.panel.orderOut_(None)
        self._ov_panel.orderOut_(None)
        try:
            self.calibration_controller = CalibrationController.alloc().init().build(
                self._calibration_finished, self._calibration_saved,
                saved_input=self._input_calibration_saved)
        except (ValueError,OSError) as e:
            self._calibrating = False
            self._show()
            self._render("status", str(e), PALETTE["red"])

    @objc.python_method
    def _calibration_finished(self, calibration, wid):
        if calibration is not None:
            message, editor = calibration
            self._input_calibration = editor
            self._input_calibration_wid = wid
            self._input_calibration_saved = editor.serialize()
            self._calibration = message
            self._calibration_wid = wid
            self._calibration_saved = message.serialize()
            self._calibration_required = True
        self._calibrating = False
        self._retire_calibration_results()
        self._show()
        self.applyWaiting_("校准完成；请回到聊天窗口。调整分栏后请重新校准。"
                           if calibration else "已取消本次校准")

    def clearCalibration_(self, sender):
        if self._calibrating: return
        from settings_config import read_document, write_settings
        path = userconfig.env_files()[0]
        try:
            write_settings(path,read_document(path),{"JEV_MESSAGE_REGION":"", "JEV_INPUT_REGION":""})
        except (ValueError,OSError) as e:
            self._render("status",str(e),PALETTE["red"])
            return
        self._calibration = None
        self._calibration_wid = None
        self._input_calibration = None
        self._input_calibration_wid = None
        self._input_calibration_saved = ""
        self._calibration_saved = ""
        self._calibration_required = False
        self._retire_calibration_results()
        self.applyWaiting_("已恢复自动识别区域")

    def openSettings_(self, sender):
        from settings import SettingsController
        if getattr(self, "settings_controller", None) and self.settings_controller.window.isVisible():
            self.settings_controller.show()
            return
        try:
            self.settings_controller = SettingsController.alloc().init().build(self)
            self.settings_controller.show()
        except OSError:
            alert = AppKit.NSAlert.alloc().init()
            alert.setMessageText_("无法读取配置文件，请检查文件权限。")
            alert.runModal()

    @objc.python_method
    def _make_surface(self, radius: float, color: NSColor,
                      border: NSColor | None = None) -> NSView:
        surface = ui_style.make_surface(radius, color, border)
        self._appearance_surfaces.append((surface, radius, color, border))
        return surface

    def accessibilityDisplayChanged_(self, notification):
        # Workspace notifications are independent of the polling/model workers.
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyAccessibilityAppearance:", None, False)

    def applyAccessibilityAppearance_(self, notification):
        reduced = AppKit.NSWorkspace.sharedWorkspace().accessibilityDisplayShouldReduceTransparency()
        self._apply_accessibility_palette(bool(reduced))

    @objc.python_method
    def _apply_accessibility_palette(self, reduced):
        def adapted(color):
            if color is None or not reduced:
                return color
            for key, replacement in ui_style.SOLID_PALETTE.items():
                if color.isEqual_(PALETTE[key]):
                    return replacement
            return color  # risk/status colors retain their semantic meaning

        background = ui_style.SOLID_PALETTE["bg"] if reduced else NSColor.clearColor()
        self._solid_backdrop.layer().setBackgroundColor_(background.CGColor())
        for surface, radius, color, original_border in self._appearance_surfaces:
            layer = surface.layer()
            layer.setBackgroundColor_(adapted(color).CGColor())
            border = adapted(original_border)
            if reduced and color.isEqual_(PALETTE["surface"]):
                border = ui_style.SOLID_PALETTE["edge"]
            layer.setBorderWidth_(0.75 if border is not None else 0)
            if border is not None:
                layer.setBorderColor_(border.CGColor())
        for button in self._appearance_buttons:
            if button is self.settings_button or button is self.calibration_button:
                continue  # the gear stays an unboxed icon
            button.layer().setBackgroundColor_(adapted(PALETTE["row"]).CGColor())
            button.layer().setBorderColor_(adapted(PALETTE["edge"]).CGColor())
        self.panel.contentView().setNeedsDisplay_(True)

    @objc.python_method
    def _make_label(self, x, y, w, h, size=13, color=None, bold=False):
        return ui_style.make_label("", x, y, w, h, size, color, bold, selectable=True)

    @objc.python_method
    def _make_button(self, x, y, w, h, title, action, tag):
        """Compact native action with a light outline over the vibrancy material."""
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        btn.setTitle_(title)
        ui_style.style_button(btn, font_size=10, radius=CAND_BTN_H / 2)
        btn.setTarget_(self)
        btn.setAction_(action)
        btn.setTag_(tag)
        btn.setHidden_(True)
        self._appearance_buttons.append(btn)
        return btn

    @objc.python_method
    def _show(self):
        if not self.panel.isVisible():
            self.panel.orderFrontRegardless()

    @objc.python_method
    def _context_line(self, sender, prev: str) -> str:
        parts = []
        if sender:
            parts.append(f"来自 {sender}")
        if prev:
            parts.append(f"上文：{prev[:26]}")
        return " · ".join(parts)

    @objc.python_method
    def _render(self, key: str, text: str, color: NSColor | None = None):
        # 上游这里还要让位给判断模型的加载/下载进度（self.judge.load_status，配一个
        # _normal_status 记住被盖住的那行）。没有本地模型可加载，状态行就只有这一个来源。
        tf = self.rows[key]
        if key == "message" and text != self._message_text:
            self._message_expanded = False
            self._message_text = text
        tf.setStringValue_(text)
        if key == "message" and not self._collapsed:
            self._relayout()
            tf.scrollRectToVisible_(NSMakeRect(0, max(0, tf.frame().size.height - 1), 1, 1))
        if color is not None:
            tf.setTextColor_(color)

    @objc.python_method
    def _message_metrics(self):
        field = self.rows["message"]
        attributed = NSAttributedString.alloc().initWithString_attributes_(
            field.stringValue() or " ", {NSFontAttributeName: field.font()})
        bounds = attributed.boundingRectWithSize_options_(
            NSMakeSize(PANEL_W - 50, 100000),
            AppKit.NSStringDrawingUsesLineFragmentOrigin | AppKit.NSStringDrawingUsesFontLeading)
        two_lines = NSAttributedString.alloc().initWithString_attributes_(
            "国\n国", {NSFontAttributeName: field.font()})
        two_bounds = two_lines.boundingRectWithSize_options_(
            NSMakeSize(PANEL_W - 50, 100000),
            AppKit.NSStringDrawingUsesLineFragmentOrigin | AppKit.NSStringDrawingUsesFontLeading)
        collapsed = float(int(two_bounds.size.height + 5.999))
        full = max(collapsed, float(int(bounds.size.height + 5.999)))
        overflow = full > collapsed
        document = full if self._message_expanded else collapsed
        return min(180, document), document, overflow

    def toggleMessage_(self, sender):
        self._message_expanded = not self._message_expanded
        self._relayout()
        field = self.rows["message"]
        field.scrollRectToVisible_(NSMakeRect(0, max(0, field.frame().size.height - 1), 1, 1))

    @objc.python_method
    def _set_candidate_header(self, text: str):
        """Keep the section title strong while treating its live status as metadata."""
        value = NSMutableAttributedString.alloc().initWithString_(text)
        value.addAttributes_range_({
            NSFontAttributeName: NSFont.systemFontOfSize_(12),
            NSForegroundColorAttributeName: PALETTE["muted"],
        }, NSMakeRange(0, len(text)))
        title_len = min(len("候选回复"), len(text))
        value.addAttributes_range_({
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(12),
            NSForegroundColorAttributeName: PALETTE["text"],
        }, NSMakeRange(0, title_len))
        self.rows["cand_header"].setAttributedStringValue_(value)

    @objc.python_method
    def _set_probability_label(self, field: NSTextField, row: int, probability: str):
        """Match the reference hierarchy: quiet rank, vivid bold probability."""
        rank = f"#{row + 1}"
        text = f"{rank}\n{probability}"
        value = NSMutableAttributedString.alloc().initWithString_(text)
        value.addAttributes_range_({
            NSFontAttributeName: NSFont.systemFontOfSize_(9),
            NSForegroundColorAttributeName: PALETTE["muted"],
        }, NSMakeRange(0, len(text)))
        value.addAttributes_range_({
            NSFontAttributeName: NSFont.boldSystemFontOfSize_(11),
            NSForegroundColorAttributeName: (
                PALETTE["muted"] if probability in ("排序中", "待定") else PALETTE["green"]),
        }, NSMakeRange(len(rank) + 1, len(probability)))
        field.setAttributedStringValue_(value)

    @objc.python_method
    def _candidate_text_height(self, field: NSTextField) -> float:
        """Measure the full rendered reply so layout never relies on a character cutoff."""
        text = field.stringValue()
        if not text:
            return 18
        attributed = NSAttributedString.alloc().initWithString_attributes_(
            text, {NSFontAttributeName: field.font()})
        options = (AppKit.NSStringDrawingUsesLineFragmentOrigin
                   | AppKit.NSStringDrawingUsesFontLeading)
        bounds = attributed.boundingRectWithSize_options_(
            NSMakeSize(CAND_TEXT_W, 10_000), options)
        return max(18, float(int(bounds.size.height + 4.999)))

    @objc.python_method
    def _set_progress(self, slot: int, row: int, value: float | None):
        """Paint the existing rank probability; the model payload is never changed."""
        r = self._rows[slot][row]
        # 分不是数就当没有（画个空条），不硬转——float("很高") 会把整条链炸在界面线程上
        usable = (isinstance(value, (int, float)) and not isinstance(value, bool)
                  and isfinite(value))
        progress = max(0.0, min(1.0, float(value))) if usable else 0.0
        frame = r["fill"].frame()
        r["fill"].setFrameSize_(NSMakeSize(36 * progress, frame.size.height or 4))

    @objc.python_method
    def _set_risk_scale(self, risk: int | None):
        selected = None if risk is None else (0 if risk <= 3 else (1 if risk <= 6 else 2))
        colors = (PALETTE["green"], PALETTE["amber"], PALETTE["red"])
        for i, dot in enumerate(self._risk_dots):
            layer = dot.layer()
            layer.removeAnimationForKey_("risk-breathe")
            alpha = 1.0 if i == selected else 0.68
            layer.setBackgroundColor_(colors[i].colorWithAlphaComponent_(alpha).CGColor())
            layer.setShadowOpacity_(0.0)
            if i == selected:
                layer.setShadowColor_(colors[i].CGColor())
                layer.setShadowOffset_(NSMakeSize(0, 0))
                layer.setShadowRadius_(4.0)
                layer.setShadowOpacity_(0.55)
                # Keep the dot itself fully saturated; only its halo breathes.
                pulse = Quartz.CABasicAnimation.animationWithKeyPath_("shadowOpacity")
                pulse.setFromValue_(0.24)
                pulse.setToValue_(0.72)
                pulse.setDuration_(1.2)
                pulse.setAutoreverses_(True)
                pulse.setRepeatCount_(float("inf"))
                pulse.setTimingFunction_(Quartz.CAMediaTimingFunction.functionWithName_(
                    Quartz.kCAMediaTimingFunctionEaseInEaseOut))
                layer.addAnimation_forKey_(pulse, "risk-breathe")

    @objc.python_method
    def _row_controls(self, slot: int, row: int):
        r = self._rows[slot][row]
        return (r["box"], r["prob"], r["text"], r["btn"], r["fill_btn"],
                r["track"], r["fill"])

    @objc.python_method
    def _render_groups(self, payload: list):
        """payload: [(slot, tone, [{"text","prob"}, ...]), ...] — one entry per active tone.

        Rows the model did not fill are emptied and their buttons hidden. Every active tone
        still reserves its two minimum rows, while a returned long reply expands only its own
        row so the complete text remains visible.
        """
        wanted = set()
        for slot, _tone, items in payload:
            for row in range(styles.PER_TONE):
                if row < len(items):
                    it = items[row]
                    wanted.add((slot, row))
                    r = self._rows[slot][row]
                    prob = _prob_text(it["prob"])
                    self._set_probability_label(r["prob"], row, prob)
                    r["text"].setStringValue_(it["text"])
                    self._set_progress(slot, row, it["prob"])
                    for c in self._row_controls(slot, row):
                        c.setHidden_(not self._slot_active(slot))
                    self.cand_texts[slot * styles.PER_TONE + row] = it["text"]
        for slot in range(styles.MAX_SLOTS):
            for row in range(styles.PER_TONE):
                if (slot, row) not in wanted and self._slot_active(slot):
                    r = self._rows[slot][row]
                    r["prob"].setStringValue_("")
                    r["text"].setStringValue_("")
                    self._set_progress(slot, row, None)
                    for c in self._row_controls(slot, row):
                        c.setHidden_(True)
                    self.cand_texts[slot * styles.PER_TONE + row] = None
        self._relayout()

    @objc.python_method
    def _clear_candidates(self):
        for slot in range(styles.MAX_SLOTS):
            for row in range(styles.PER_TONE):
                r = self._rows[slot][row]
                r["prob"].setStringValue_("")
                r["text"].setStringValue_("")
                self._set_progress(slot, row, None)
                for c in self._row_controls(slot, row):
                    c.setHidden_(True)
                self.cand_texts[slot * styles.PER_TONE + row] = None

    @objc.python_method
    def _display_height(self) -> float:
        """Height of the display whose origin is (0,0) — the Quartz<->Cocoa flip constant.

        Taking this from the *target* screen is wrong on multi-display setups: a screen
        placed above the main one has origin.y > 0 and the flip must still use the
        primary display's height.
        """
        for scr in NSScreen.screens():
            f = scr.frame()
            if f.origin.x == 0 and f.origin.y == 0:
                return f.size.height
        return NSScreen.mainScreen().frame().size.height

    @objc.python_method
    def _position_near(self, win: dict | None):
        """Dock the panel beside the chat app, on the screen it is actually on.

        Uses global Cocoa coordinates throughout. NSScreen.mainScreen() must NOT be used:
        it follows whichever display holds the key window, so relying on it made the panel
        hop ~1369 px between displays a few times a minute.
        """
        flip = self._display_height()
        panel_h = self.panel.frame().size.height or PANEL_H
        panel_w = self.panel.frame().size.width or PANEL_W
        screens = list(NSScreen.screens())
        primary = next((s for s in screens
                        if s.frame().origin.x == 0 and s.frame().origin.y == 0), screens[0])

        if win:
            # CGWindow bounds are top-left origin global pixels -> Cocoa bottom-left
            wx, wy = win["x"], win["y"]
            ww, wh = win["w"], win["h"]
            cx_win = wx + ww / 2.0
            cyan = flip - (wy + wh / 2.0)
            host = next((s for s in screens
                         if s.frame().origin.x <= cx_win <= s.frame().origin.x + s.frame().size.width
                         and s.frame().origin.y <= cyan <= s.frame().origin.y + s.frame().size.height),
                        primary)
            sf = host.frame()
            # dock right of the chat app if it fits on that screen, else left, else its right edge
            x = wx + ww + 8
            if x + panel_w > sf.origin.x + sf.size.width:
                x = wx - panel_w - 8
            if x < sf.origin.x:
                x = sf.origin.x + sf.size.width - panel_w - 12
            y = flip - wy - panel_h
            y = max(sf.origin.y + 40, min(y, sf.origin.y + sf.size.height - panel_h - 40))
        else:
            sf = primary.frame()
            x = sf.size.width - panel_w - 12
            y = sf.size.height - panel_h - 60

        # dead-band: ignore sub-2pt corrections and one-off blips, so the app's own window
        # animations (and our own numeric noise) stop nudging the panel around
        target = (round(x), round(y))
        last = self._last_origin
        if last is None:                    # first placement: apply without debounce
            self._last_origin = target
            self._pending_origin = target
            self.panel.setFrameOrigin_(target)
            return
        if abs(target[0] - last[0]) <= 2 and abs(target[1] - last[1]) <= 2:
            return
        if target != self._pending_origin:
            self._pending_origin = target
            return  # require the same target on two consecutive ticks before moving
        self._last_origin = target
        self.panel.setFrameOrigin_(target)

    # ------------------------------------------------------------ actions
    def copyCandidate_(self, sender):
        text = self.cand_texts[sender.tag()] if 0 <= sender.tag() < len(self.cand_texts) else None
        if not text:
            return
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_(text, NSPasteboardTypeString)
        self._render("status", "已复制", PALETTE["green"])

    def fillCandidate_(self, sender):
        """Write the candidate into the current chat app's input box (via self._app)."""
        if getattr(self,"_calibration_required",False) and self._input_calibration is None:
            self._render("status", "请点击右上角校准图标，确认消息区和输入区。", PALETTE["amber"])
            return
        idx = sender.tag()
        text = self.cand_texts[idx] if 0 <= idx < len(self.cand_texts) else None
        if not text:
            return
        # The status line is painted before the call because writing into the chat app
        # takes a beat; the click should look instant even though the write has not
        # happened yet.
        self._render("status", "填入中…", PALETTE["muted"])
        self.panel.displayIfNeeded()
        app = self._app
        if app is None:
            self._render("status", "填入失败：聊天应用不在前台", PALETTE["red"])
            return
        if not fill.has_accessibility():
            # First click is the moment to ask: the system dialog is the only way in.
            fill.request_accessibility()
        target = getattr(self, "_input_target", None)
        if target is None or (target["box"] is None and not target.get("visual_rect")):
            self._render("status", "填入失败：" + (target["reason"] if target else "等待输入框定位"), PALETTE["red"])
            return
        if target.get("app") != app.key:
            # 适配器刚切换、检测框还没更新：目标矩形仍属于上一个 App，写进去会
            # 填错应用——拒绝并让下一轮 locate_input 刷新目标。
            self._render("status", "填入失败：输入目标属于另一应用，请等检测框更新后重试", PALETTE["red"])
            return
        ok, reason = app.fill_text(text, target=target)
        if ok:
            self._render("status", reason, PALETTE["green"])
        else:
            self._render("status", f"填入失败：{reason}", PALETTE["red"])

    def toneChanged_(self, sender):
        """A 话术 dropdown moved: the verdict is still valid, only the writing changes."""
        picked = [p.titleOfSelectedItem() or styles.NONE_LABEL for p in self._dds]
        if picked == self.slot_tones:
            return
        self.slot_tones = picked
        self._reply_epoch += 1
        self._gen_epoch += 1
        self._pregen_req = self._pregen_result = None
        if not self.analyzed_text:
            self.last_seen = None
        # the panel is sized by how many slots are in use, so re-lay-out *before* the new
        # candidates arrive: the empty rows appear at once and nothing jumps later
        self._clear_candidates()
        self._stream_rows = {}     # the run _regenerate starts streams into fresh rows
        self._regenerate()

    @objc.python_method
    def _regenerate(self):
        """Re-run just the generation half for the message on screen.

        No re-judging and no re-reading of the screen: the intent and risk do not depend on
        the tone, and re-running them would make a dropdown click feel like a new analysis.

        判断现在是同一次调用的副产物，所以换话术那次调用也会带回一份新判断——**扔掉**。
        面板上的意图/风险仍然是这条消息那次分析的结论，点一下下拉框不会让它跳一下。
        """
        self._relayout()
        text = self.analyzed_text
        if not text:
            self._render("status", "话术已选 · 下条消息生效", PALETTE["muted"])
            return
        active = [t for t in self.slot_tones if t in styles.PRESETS]
        if not active:
            self._render("status", "没选话术 · 至少选一个", PALETTE["amber"])
            return
        self._render("status", f"换话术中…（{'、'.join(active)}）", PALETTE["muted"])
        self._set_candidate_header("候选回复 · 生成中…")
        threading.Thread(target=self._reply_task,
                         args=(self._reply_epoch, self._regen_work,
                               text, list(self.slot_tones)),
                         daemon=True).start()

    @objc.python_method
    def _payload_from_gen(self, gen: dict):
        """Generation result -> unranked [(slot, tone, items)] (prob=None ⇒ 待排序).

        None when nothing usable came back — the caller shows gen's error then.
        """
        groups = [g for g in (gen.get("groups") or []) if g.get("texts")]
        if not groups:
            return None
        return [(g["slot"], g["tone"], [{"text": t, "prob": None} for t in g["texts"]])
                for g in groups]

    @objc.python_method
    def _rank_payload(self, payload: list, scores: dict) -> list:
        """Score and reorder each group's candidates. `#1`/`#2` inside a group still means
        "the better of these two". Missing scores leave probabilities at 0 rather than
        dropping rows.

        上游这里再发一次网络/前向调用（judge.rank_candidates，把所有候选当选项问一道题）。
        这一版的分是写候选那次调用自己给的，已经在 gen["scores"] 里了，所以这里只剩「按分
        重排」这一件事——排法、缺分算 0、排完的形状都跟上游一模一样。
        口径变了一处：上游那一道题看得见所有话术的候选，分跨话术可比；现在每次调用只看得见
        自己写的那几条，分是话术内的。组内先后不受影响，只有显示的百分比不再是同一把尺子。

        分是模型自己报的，不保证是数：不是数的排到最后（sort 不能拿字符串比大小），
        显示那一步再兜一次（_render_groups）。丢是不丢的——适配层原样递上来的值一直在。
        """
        def rank_key(item):
            prob = item["prob"]
            numeric = isinstance(prob, (int, float)) and not isinstance(prob, bool)
            return (0 if numeric else 1, -prob if numeric else 0)

        out = []
        for slot, tone, items in payload:
            scored = [{"text": it["text"], "prob": scores.get(it["text"], 0.0)}
                      for it in items]
            scored.sort(key=rank_key)
            out.append((slot, tone, scored))
        return out

    @objc.python_method
    def _stream_hook(self, t0: float, label: str = ""):
        """The on_candidate callback for the generation run starting now.

        Shared by every run starter (_analyze, _regen_work, …) so the
        streaming lines follow one epoch/rows discipline no matter which path produced
        them. The callback runs on the run's worker thread; it hops to the main thread for
        every UI touch, and the first line it sees logs the latency that streaming is here
        for. The epoch check inside applyStreamLine_ is what makes a superseded run's late
        lines harmless.
        """
        reply_epoch = getattr(self._reply_worker, "epoch", self._reply_epoch)
        self._gen_epoch += 1
        epoch = self._gen_epoch
        prefix = f"{label} " if label else ""
        first_line = {"shown": False}

        def on_candidate(slot: int, _tone: str, text: str) -> None:
            if not first_line["shown"]:
                first_line["shown"] = True
                _log(f"{prefix}首条候选上屏 {(time.perf_counter() - t0) * 1000:.0f}ms（未排序）")
            self._push_reply("applyStreamLine:", (epoch, slot, text), reply_epoch)
        return on_candidate

    @objc.python_method
    def _regen_work(self, text: str, slot_tones: list[str]):
        t0 = time.perf_counter()
        try:
            if not self._reply_current():
                return
            context = getattr(self._reply_worker, "context", self._active_context)
            # intent="" —— 判断不参与起草（上游线上调用点也一直传空），这次带回来的判断扔掉
            gen = self.generator.generate(chat_context.model_message(text, context), "", slot_tones,
                                          context,
                                          self._stream_hook(t0, "换话术"))
            groups = gen.get("groups") or []
            failed = [str(g["slot"]) for g in groups if g.get("error")]
            _log(f"换话术 生成 {gen.get('elapsed_s', 0) * 1000:.0f}ms · {len(groups)} 个话术"
                 + (f" · 失败: {'; '.join(failed)}" if failed else ""))
            payload = self._payload_from_gen(gen)
            if payload is None:
                err = "服务未返回可用候选，请检查模型设置"
                _log(f"换话术无可用候选: {err}")
                self._push("applyError:", f"候选生成失败: {err}")
                return
            # 上游在这里先推一次没排序的（界面显示「排序中」），排完再推一次。现在分跟候选
            # 一起回来，没有中间态可推了——流式那一路的行早就上屏了，这一次就是带分的那次。
            ranked = self._rank_payload(payload, gen.get("scores") or {})
            _log(f"换话术 端到端 {(time.perf_counter() - t0) * 1000:.0f}ms")
            self._push("applyTones:", ranked)
        except Exception as e:
            _log(f"换话术失败 {type(e).__name__}")
            self._push("applyError:", f"换话术失败: {type(e).__name__}")

    @objc.python_method
    def _regenerate_work(self, text: str, slot_tones: list[str]):
        """Refresh candidate replies atomically, without re-reading or re-judging.

        判断跟着这次调用也会回来一份，同样扔掉：按「重新生成推荐回答」只换候选，
        面板上的意图/风险还是这条消息那次分析的结论（跟上游这个按钮的行为一致）。
        """
        t0 = time.perf_counter()
        try:
            if not self._reply_current():
                return
            context = getattr(self._reply_worker, "context", self._active_context)
            gen = self.generator.generate(chat_context.model_message(text, context), "", slot_tones, context)
            payload = self._payload_from_gen(gen)
            if payload is None:
                err = "服务未返回可用候选，请检查模型设置"
                _log(f"重新生成无可用候选: {err}")
                self._push("applyError:", f"重新生成失败: {err}")
                return
            ranked = self._rank_payload(payload, gen.get("scores") or {})
            _log(f"重新生成推荐回答 {(time.perf_counter() - t0) * 1000:.0f}ms · "
                 f"{sum(len(items) for _s, _t, items in ranked)} 条候选")
            self._push("applyRegenerated:", ranked)
        except Exception as e:
            _log(f"重新生成失败 {type(e).__name__}")
            self._push("applyError:", f"重新生成失败: {type(e).__name__}")
        finally:
            self._regenerating = False

    @objc.python_method
    def _payload_current(self, payload) -> bool:
        """False when the tone selection moved on — a late result must not repaint it.

        Generation+ranking now pushes twice (unranked, then ranked); a dropdown click
        between the two would otherwise bring back the tone the user just switched away
        from. Same guard for a 换话术 result racing a second click.
        """
        return all(self.slot_tones[slot] == tone for slot, tone, _items in payload)

    @objc.python_method
    def _cand_header(self, payload) -> str:
        pending = any(it["prob"] is None for _s, _t, items in payload for it in items)
        return "候选回复 · 排序中…" if pending else "候选回复（按合适度排序）"

    def applyTones_(self, payload):
        if not self._payload_current(payload):
            return
        self._set_candidate_header(self._cand_header(payload))
        total = sum(len(items) for _s, _t, items in payload)
        self._render("status", f"已换话术 · {total} 条", PALETTE["muted"])
        self._render_groups(payload)

    def applyRegenerated_(self, payload):
        if not self._payload_current(payload):
            return
        self._set_candidate_header(self._cand_header(payload))
        total = sum(len(items) for _s, _t, items in payload)
        self._render_groups(payload)
        self._render("status", f"已重新生成 · {total} 条", PALETTE["muted"])

    # ------------------------------------------------------------ controls
    def regenerateReply_(self, sender):
        """Regenerate candidates for the current message without re-reading or judging."""
        if self._paused:
            self._render("status", "已暂停 · 请先继续读屏", PALETTE["amber"])
            return
        if self._app is None:
            # The panel floats over every app: clicked from elsewhere this run would be
            # discarded by _reply_current() with no status update to say so — ask here.
            self._render("status", "聊天应用不在前台 · 回到聊天窗口再试", PALETTE["amber"])
            return
        if self._regenerating:
            self._render("status", "推荐回答生成中…", PALETTE["muted"])
            return
        text = self.analyzed_text
        messages = (self._last_full or {}).get("messages") or []
        newest = next((m for m in reversed(messages)
                       if m.side == "them" and m.text == text), None)
        active = [tone for tone in self.slot_tones if tone in styles.PRESETS]
        if not text or newest is None or self._reply_key is None:
            self._render("status", "当前没有可重新生成的推荐回答", PALETTE["amber"])
            return
        if not active:
            self._render("status", "没选话术 · 至少选一个", PALETTE["amber"])
            return
        self._regenerating = True
        self._set_candidate_header("候选回复 · 重新生成中…")
        self._render("status", "重新生成推荐回答…", PALETTE["muted"])
        _log("重新生成推荐回答 · 已请求")
        threading.Thread(target=self._reply_task,
                         args=(self._reply_epoch, self._regenerate_work,
                               text, list(self.slot_tones)),
                         daemon=True).start()

    def collapsePanel_(self, sender):
        self._set_collapsed(not self._collapsed)

    def togglePause_(self, sender):
        self._paused = not self._paused
        self.pause_item.setTitle_("继续读屏" if self._paused else "暂停读屏")
        if self._paused:
            self._pregen_req = None          # a paused app analyses nothing further
            self._pregen_result = None
            if self._ov_panel.isVisible():   # frozen boxes would lie about "realtime"
                self._ov_panel.orderOut_(None)
            self._render("status", "已暂停 · 不再读屏", PALETTE["amber"])
            self._render("message", "", PALETTE["text"])
            self._render("sender", "", PALETTE["muted"])
            self._render("intent", "—", PALETTE["muted"])
            self._render("confidence", "", PALETTE["muted"])
            self._render("risk", "", PALETTE["muted"])
            if hasattr(self, "_risk_dots"):
                self._set_risk_scale(None)
            self._render("actions", "", PALETTE["text"])
            self.rows["cand_header"].setStringValue_("")
            self._clear_candidates()
        else:
            self._pregen_result = None
            self.last_seen = None      # force a fresh read of whatever is on screen
            self.analyzed_text = None
            self._render("status", "已恢复 · 读屏中", PALETTE["muted"])

    def toggleAlwaysOnTop_(self, sender):
        """Toggle only the HUD's window level; the menu action takes effect immediately."""
        self._always_on_top = not self._always_on_top
        self.panel.setLevel_(AppKit.NSFloatingWindowLevel if self._always_on_top
                             else AppKit.NSNormalWindowLevel)
        self.always_on_top_item.setState_(
            AppKit.NSOnState if self._always_on_top else AppKit.NSOffState)
        if self._always_on_top:
            # Raise the already-visible panel without making it key or stealing chat focus.
            self.panel.orderFrontRegardless()

    def reanalyze_(self, sender):
        if self._paused:
            self._render("status", "已暂停 · 请先继续读屏", PALETTE["amber"])
            return
        self._reply_epoch += 1
        self._gen_epoch += 1
        self._pregen_req = None        # "re-analyze" means re-run, not reuse the early run
        self._pregen_result = None
        self.last_seen = None
        self.analyzed_text = None
        self._stream_rows = {}
        self._fingerprint = None
        self._stable_n = 0
        self._next_read_ts = 0
        self._render("status", "重新分析中…", PALETTE["muted"])
        if not self._paused and not self._busy:
            self._busy = True
            threading.Thread(target=self._work, daemon=True).start()

    def quitApp_(self, sender):
        AppKit.NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def _set_collapsed(self, collapsed: bool):
        """Roll the panel up to a title+status strip, or back to full height."""
        self._collapsed = collapsed
        controlled = ["message", "sender", "intent", "confidence", "risk", "actions",
                      "cand_header"]   # "chat" and "status" survive collapsing
        for key in controlled:
            self.rows[key].setHidden_(collapsed)
        for view in self._detail_views:
            view.setHidden_(collapsed)
        for slot in range(styles.MAX_SLOTS):
            self._dds[slot].setHidden_(collapsed)
            self._dd_boxes[slot].setHidden_(collapsed)
            self._group_boxes[slot].setHidden_(collapsed or not self._slot_active(slot))
            for row in range(styles.PER_TONE):
                has = self.cand_texts[slot * styles.PER_TONE + row] is not None
                for c in self._row_controls(slot, row):
                    c.setHidden_(collapsed or not has)
        if not collapsed:
            # re-expanding puts every control back where _relayout() wants it, and re-hides
            # the slots that are switched off — the collapse above cannot know that
            self._relayout()
            self._last_origin = None      # let the next tick re-dock cleanly
            return

        rect = self.panel.frame()
        # _expanded_h is maintained by _relayout() (it changes with the tone selection), so
        # expanding reads the current full height rather than a value captured at startup
        new_h = COLLAPSED_H if collapsed else (self._expanded_h or PANEL_H)
        self.panel.setFrame_display_(
            NSMakeRect(rect.origin.x, rect.origin.y + (rect.size.height - new_h),
                       rect.size.width, new_h), True)
        self._last_origin = None      # let the next tick re-dock cleanly

    # --------------------------------------------------------------- loop
    # 上游这里还有 _refresh_model_status：每跳把判断模型的加载/下载进度重画到状态行上
    # （tick_ 在所有门之前先调它，好让下载进度在读屏排队时也看得见）。没有本地模型，
    # 没有进度可报——整段和 tick_ 里那一行调用一起删了。
    def _set_foreground_state(self, app):
        """Apply one hard lifecycle boundary when a chat app gains/loses focus or changes.

        `app` is an adapter, None (some other app is frontmost — a real leave), or UNKNOWN
        (the query failed — not evidence of anything, so nothing changes).
        """
        if app is UNKNOWN or app is self._app:
            return False

        prev = self._app
        self._app = app
        self._foreground_epoch += 1
        self._reply_epoch += 1
        self._reply_key = None
        self.last_seen = None
        self.analyzed_text = None
        self._pregen_req = self._pregen_result = None
        self._gen_epoch += 1
        self._fingerprint = None
        self._last_full = None
        self._win_wid = None
        self._input_target = None
        self._input_window = None
        self._input_next = 0
        self._stable_n = 0
        self._burst_left = BURST_READS
        self._last_skip_reason = None
        self._read_fail_since = None
        self._read_fail_hidden = False
        self._empty_frame_since = None

        if app is None:
            _log("前台切换 · 聊天应用离开前台，隐藏面板并清空旧结果")
            self._push("applyForegroundHidden:", "聊天应用不在前台")
        else:
            self._next_read_ts = 0
            if prev is not None:
                # 适配器→适配器（切换应用等）与离开一样是一次硬边界：旧会话与旧候选
                # 必须下屏，否则点「填入」会把给前一个 App 写的回复填进当前 App。
                _log(f"前台切换 · 切换到{app.display_name}，清空面板并强制重新读屏")
                self._push("applyForegroundHidden:", f"已切换到{app.display_name}")
            else:
                _log(f"前台切换 · {app.display_name}回到前台，强制重新读屏")
        return True

    def tick_(self, timer):
        if getattr(self, "_calibrating", False):
            return
        # Check activation before pause/busy/read-cadence gates.  The timer keeps
        # firing while OCR is in flight, so a quick chat app -> browser -> chat app
        # round trip still advances _foreground_epoch and retires that capture.
        app = frontmost_app()
        if app is UNKNOWN:
            return
        self._set_foreground_state(app)
        if app is None:
            return
        # Follow the window from cheap metadata every tick, not from read results (#93):
        # in manual-calibration mode one read is a full multi-second OCR, and positioning
        # used to wait out two whole read cycles (the two-tick debounce) after a drag.
        # Metadata-only enumeration, ~1-5 ms. Screen-capture apps only — the text-interface panel
        # follows its AX read path. The read path's applyPosition stays as a backstop;
        # when both agree the dead-band absorbs the duplicate.
        if not self._paused and getattr(app, "needs_screen_capture", False):
            win = find_wechat_window(previous_wid=getattr(self, "_win_wid", None))
            if win is not None:
                self._position_near({"wid": win.wid, "x": win.x, "y": win.y,
                                     "w": win.w, "h": win.h})
        if self._paused or self._busy or time.time() < self._next_read_ts:
            return  # paused, a previous read is still running, or not due yet
        self._busy = True
        threading.Thread(target=self._work, daemon=True).start()

    @objc.python_method
    def _work(self):
        try:
            self._work_inner()
        finally:
            self._busy = False

    @objc.python_method
    def _work_inner(self):
        self.reload_conversations()
        # The panel is a global floating window. Showing it over Chrome while continuing
        # to reuse the last chat frame makes stale text look like browser OCR. Treat app
        # activation as a hard display/capture boundary before even checking permissions:
        # a missing screen grant must not keep an error panel floating over other apps.
        app = frontmost_app()
        if app is UNKNOWN:
            # A transient NSWorkspace failure is not proof that the user left the chat app.
            # Freeze both reads and UI updates for one short tick without cancelling a
            # valid in-flight reply or manufacturing a leave/return transition.
            self._next_read_ts = time.time() + FAST_TICK
            return
        self._set_foreground_state(app)
        if app is None:
            self._next_read_ts = time.time() + FAST_TICK
            return
        if app.needs_screen_capture:
            if not screen_capture_ok():
                if not self._asked_permission:
                    self._asked_permission = True
                    request_screen_capture()      # opens the system prompt
                self._push("applyError:", "需要屏幕录制权限 · 系统设置 › 隐私与安全性")
                self._next_read_ts = time.time() + SLOW_TICK
                return
        elif not fill.has_accessibility():
            # The text-interface path reads the accessibility tree: without the grant there is nothing to read.
            if not self._asked_accessibility:
                self._asked_accessibility = True
                fill.request_accessibility()
            self._push("applyError:", "需要辅助功能权限 · 系统设置 › 隐私与安全性")
            self._next_read_ts = time.time() + SLOW_TICK
            return
        if getattr(self, "_calibrating", False):
            return
        if (app.needs_screen_capture
                and getattr(self, "_calibration_required", False)
                and self._calibration is None):
            # 校准只约束截图路径；文本接口路径读界面树，无消息区可校准。
            self._push("applyWaiting:", "请点击右上角校准图标，确认消息区和输入区。")
            self._next_read_ts = time.time() + SLOW_TICK
            return
        capture_foreground_epoch = self._foreground_epoch
        capture_context_version = self._context_version
        try:
            # calibration 只被截图路径接受；文本接口适配器签名里没有它，不传。
            extra = {}
            if app.needs_screen_capture and getattr(self, "_calibration", None):
                extra = {"calibration": self._calibration}
            res = app.read_conversation(previous_wid=self._win_wid,
                                        prev_fingerprint=self._fingerprint,
                                        prev_layout=getattr(self, "_layout_key", None), **extra)
        except Exception as e:
            self._push("applyError:", f"读取失败: {type(e).__name__}")
            self._next_read_ts = time.time() + SLOW_TICK
            return
        if capture_foreground_epoch != self._foreground_epoch:
            return
        if getattr(self, "_calibration", None) and (res.get("calibration_error")
                or (res.get("window") and res["window"]["wid"] != self._calibration_wid)):
            self._calibration = None
            self._input_calibration = None
            self._input_calibration_wid = None
            self._retire_calibration_results()
            self._push("applyWaiting:", "聊天窗口已改变，请重新校准消息区域。")
            return
        # Re-check after the blocking capture/OCR.  tick_ may have observed a
        # complete leave+return while this worker was busy; in that case even a
        # currently-frontmost chat app does not make this old snapshot current.
        after = frontmost_app()
        if after is UNKNOWN:
            self._next_read_ts = time.time() + FAST_TICK
            return
        self._set_foreground_state(after)
        if after is not app or capture_foreground_epoch != self._foreground_epoch:
            self._next_read_ts = time.time() + FAST_TICK
            return
        if not res["ok"]:
            # Window enumeration/capture can miss one frame while the chat app redraws.
            # Keep the already-current HUD stable for a short grace period, then
            # hide and force rediscovery if the failure really persists.
            now_mono = time.monotonic()
            if self._read_fail_since is None:
                self._read_fail_since = now_mono
            if (not self._read_fail_hidden
                    and now_mono - self._read_fail_since >= READ_FAILURE_HIDE_S):
                self._read_fail_hidden = True
                # A persistent miss is no longer a harmless one-frame redraw.
                # Retire every reply callback before hiding; otherwise an in-flight
                # generation from the last visible frame could call _show() again.
                self._reply_epoch += 1
                self._reply_key = None
                self.last_seen = None
                self.analyzed_text = None
                self._pregen_req = self._pregen_result = None
                self._gen_epoch += 1
                self._fingerprint = None
                self._last_full = None
                self._win_wid = None
                self._push("applyForegroundHidden:", res["error"])
            self._next_read_ts = time.time() + FAST_TICK
            return

        self._read_fail_since = None
        self._read_fail_hidden = False

        # Same fingerprint ⇒ same pixels ⇒ the messages are exactly what we last read.
        # Cadence follows the screen: quiet checks back in FAST_TICK (capture+hash only,
        # ~30 ms); a change first keeps BURST_TICK for a few reads so the burst's NEXT
        # message is noticed quickly (this also feeds _stable_n, the early-settle signal),
        # and only a pane that keeps moving settles back to SLOW_TICK like the old poll.
        self._fingerprint = res.get("fingerprint")
        self._layout_key = res.get("layout")
        if res["unchanged"]:
            self._stable_n += 1
            self._burst_left = BURST_READS
            self._next_read_ts = time.time() + FAST_TICK
        elif self._burst_left > 0:
            self._burst_left -= 1
            self._stable_n = 0
            self._next_read_ts = time.time() + BURST_TICK
        else:
            self._stable_n = 0
            self._next_read_ts = time.time() + SLOW_TICK

        # Position from the read result as a backstop only — tick_ now drives
        # positioning from cheap metadata every tick (#93). This keeps the panel
        # correct when the window moved mid-read; a same-target push is absorbed
        # by the dead-band.
        live_window = res["window"]
        live_input_rect = res.get("input_rect")
        self._win_wid = res["window"]["wid"]
        self._push("applyPosition:", res["window"])
        fresh_frame = not res["unchanged"]
        if res["unchanged"] and self._last_full is not None:
            # the settle/analyze gate below still runs every read; an unchanged frame
            # just skips re-deriving the messages it would act on
            res = self._last_full
        elif (not res.get("manual_calibration") and not res["messages"] and self._last_full is not None
              and self._last_full.get("messages")):
            # Transient empty frame (#58): the window is still enumerated and the capture
            # succeeded, but OCR returned 0 blocks (4.x redraw glitch). Reuse the
            # last good read so the settle gate keeps its target and timer. Entering the
            # streak retires in-flight workers once (a candidate computed for a vanished
            # message must never surface) but keeps the reply target and re-arms both
            # prework halves at the new epoch, so the settle path stays the fast one.
            now_mono = time.monotonic()
            if (self._empty_frame_since is not None
                    and now_mono - self._empty_frame_since > EMPTY_FRAME_REUSE_S):
                # A persistently empty read is no longer a one-frame glitch: give up on
                # reuse and fall through as a real empty read (same grace as a capture
                # miss); the key change below then clears the stale target.
                _log(f"读屏为空已持续 {now_mono - self._empty_frame_since:.1f}s"
                     f" · 放弃沿用上一帧")
                self._empty_frame_since = None
                self._last_full = None
            else:
                if self._empty_frame_since is None:
                    self._empty_frame_since = now_mono
                    _log("读屏为空 · 沿用上一帧继续分析")
                    last_msgs = self._last_full.get("messages") or []
                    last_thems = [m for m in last_msgs if m.side == "them"]
                    if last_thems:
                        self._reply_epoch += 1
                        self.analyzed_text = None   # let the settle gate re-open
                        self._enqueue_prework(last_thems[-1], self._active_context,
                                              last_thems[-2].text
                                              if len(last_thems) > 1 else "")
                res = self._last_full
                fresh_frame = False
        else:
            self._empty_frame_since = None
            self._last_full = res
            # a picked-over window list was invisible in the logs and cost a whole
            # misdiagnosis (#91): say which window the reads moved to, geometry only
            if res["window"].get("wid") != getattr(self, "_read_wid", None):
                self._read_wid = res["window"].get("wid")
                _log(f"读屏窗口切换 wid={res['window'].get('wid')} "
                     f"{res['window'].get('w', 0):.0f}x{res['window'].get('h', 0):.0f}")
            self._push("applyChat:", res.get("chat_title") or "")

        res = dict(res, window=live_window, input_rect=live_input_rect)
        if res.get("manual_calibration"):
            self._input_target = None
            editor = getattr(self, "_input_calibration", None)
            if editor is not None and self._input_calibration_wid == res['window']['wid']:
                from visual_fill import chat_signature
                rect = editor.screen_rect(res['window'])
                signature_rect = self._calibration.screen_rect(res['window'])
                self._input_target = dict(box=None,rect=None,window=dict(res['window']),
                    visual_rect=rect,manual_region=editor,signature_rect=signature_rect,
                    chat_signature=chat_signature(res['window'],signature_rect),
                    app=app.key,   # 填入前复核：手动校准目标同样必须带归属标记（#105）
                    reason="手动校准输入区")
            self._input_window = dict(res["window"])
            self._input_next = float("inf")
            if not res.get("chat_title"):
                res = dict(res, messages=[])
        # AX traversal stays on the read worker, never the Cocoa drawing thread.
        now_input = time.monotonic()
        if (res["window"] != getattr(self, "_input_window", None)
                or now_input >= getattr(self, "_input_next", 0)):
            target = app.locate_input(res["window"])
            # AX can transiently return None while the chat app rebuilds its tree during a
            # foreground/window transition. Keep this frame readable and let the next
            # scheduled read retry; never let a missing target abort the read worker.
            target = dict(target) if isinstance(target, dict) else {
                "box": None, "rect": None, "window": dict(res["window"]),
                "reason": "输入框暂时不可用",
            }
            target["app"] = app.key   # 填入前复核：目标必须属于当前 App
            if target["box"] is None and app.needs_screen_capture:
                # 视觉后备要截图/OCR，只有走屏幕采集的 App 才允许进入；
                # 文本接口路径绝不截图——box 为 None 就让它保持 None（填入按钮报原因）。
                from input_region import locate_visual_input
                target["visual_rect"] = (res.get("input_rect")
                                         or locate_visual_input(res["window"]))
                if target["visual_rect"]:
                    from visual_fill import chat_signature
                    target["chat_signature"] = chat_signature(res["window"], target["visual_rect"])
            if capture_foreground_epoch != self._foreground_epoch:
                self._next_read_ts = time.time() + FAST_TICK
                return
            self._input_target = target
            self._input_window = dict(res["window"])
            self._input_next = now_input + 1.0
        with self._context_lock:
            self.reload_conversations()
            if capture_context_version != self._context_version:
                self._fingerprint = None
                self._last_full = None
                return
            msgs = res["messages"]
            # 手动校准模式下，无法确认归属的文字不进入会话历史与模型上下文。
            # 必须在 visible/observe 之前过滤——事后过滤会让 _observed_offset 的
            # 索引错位；检测框仍画原始列表（含「未确认」框），见下方 applyBoxes。
            overlay_msgs = None
            if res.get("manual_calibration"):
                overlay_msgs = msgs
                msgs = [m for m in msgs if m.side != "unknown"]
            visible = [(m.text, m.side, m.sender or "") for m in msgs]
            self._observed_messages, self._observed_offset = visible, 0
            if self.history_enabled and self.conversations and not self.conversations.error:
                try:
                    self._observed_messages, self._observed_offset = self.conversations.observe(
                        res.get("chat_title"), visible, record=fresh_frame)
                except (OSError, ValueError):
                    _log("保存会话历史失败，使用当前画面继续分析")
                    self._push("applyError:", "会话历史保存失败，请检查磁盘空间及权限")
            thems = [m for m in msgs if m.side == "them"]
            newest = thems[-1] if thems else None
            prev_text = thems[-2].text if len(thems) > 1 else ""

            context = self._context_text(msgs, newest) if newest else None
            # key 带 app.key：不同 App 的同名会话 / 同文消息不得共用一条回复纪元
            key = (app.key, res.get("chat_title") or "", newest.text, context,
                   tuple(visible)) if newest else None
            self._active_context = context
            if key != self._reply_key:
                self._reply_epoch += 1
                self._reply_key = key
                self.last_seen = None
                self.analyzed_text = None
                self._pregen_req = self._pregen_result = None
                self._gen_epoch += 1

            # YOLO overlay: repaint whenever a read produced geometry — unchanged reads reuse
            # the cached messages, so the boxes stay up even while the pane is quiet
            if self._show_boxes:
                self._push("applyBoxes:", (res["window"], overlay_msgs or msgs,
                                           newest.text if newest else None))
            if newest is None:
                self._push("applyWaiting:", "暂未确认输入区边界，暂停分析"
                           if res.get("input_unresolved") else None)
                return
            now = time.time()

            # --- anti-flood: track arrivals, never analyze mid-burst
            if newest.text != self.last_seen:
                self.last_seen = newest.text
                self.last_change_ts = now
                # only on arrival: this function runs every second, and a per-tick line would
                # bury the timing that matters
                t = res.get("timing_ms") or {}
                first_read = not self._read_once
                self._read_once = True
                # Vision loads on the first call and costs ~2x steady state; saying so keeps a
                # one-off from being read as a regression (same reason the judge line does it)
                note = "（首次，含 Vision 加载）" if first_read and t.get("ocr", 0) > 400 else ""
                _log(f"读屏 抓取 {t.get('capture', 0):.0f}ms + OCR {t.get('ocr', 0):.0f}ms"
                     f" = {t.get('total', 0):.0f}ms · 读到 {len(msgs)} 条（对方 {len(thems)} 条）"
                     f"{note}")
                _log(f"新消息 · 这次调用先跑，停稳 {SETTLE_S}s（连续 {STABLE_READS} 跳不变最早 "
                     f"{EARLY_SETTLE_S}s）后上屏（两次完整分析最小间隔 {MIN_GAP_S}s）")
                # latest-wins: overwrite the slot, retire the old result — only the newest
                # text's run can ever be consumed, and only by the settle gate below
                self._enqueue_prework(newest, context, prev_text)
                # keep the previous verdict readable; just badge that something new landed
                self._push("applyIncoming:", (newest.text, newest.sender, prev_text))

            # Anti-flood, two signals: the blind wait (SETTLE_S, unchanged upper bound) or a
            # content-stability early open — the pane went quiet for STABLE_READS consecutive
            # reads spanning at least EARLY_SETTLE_S, which is itself evidence the burst is
            # over. A burst keeps resetting _stable_n, so mid-burst opens cannot happen.
            elapsed = now - self.last_change_ts
            settled = elapsed >= SETTLE_S or (elapsed >= EARLY_SETTLE_S
                                              and self._stable_n >= STABLE_READS)
            cooled = (now - self.last_analyze_ts) >= MIN_GAP_S
            pg = self._pregen_result
            # 上游这里看的是「预判结论在不在手上」（判断是免费的本地前向，所以总是先判）。
            # 判断合进这一次调用之后，手上有没有东西只有一个问题：早跑那份结果在不在。
            # 在 = 这次分析的钱已经付过了，不用再等冷却；话术也得对得上，不然那份结果本来就作废。
            pre_hit = (pg is not None and pg[0] == newest.text
                       and pg[1] == tuple(self.slot_tones) and pg[3] == self._reply_epoch)
            if (newest.text != self.analyzed_text and settled and not self._analyzing
                    and (pre_hit or cooled)):
                self.last_analyze_ts = now
                self.analyzed_text = newest.text
                self._analyzing = True
                if pre_hit:
                    # 结果已经在手上，_analyze 直接取走就上屏；「分析中…」只会闪一下，不推。
                    _log(f"停稳 · 早跑已就位 · 这条消息出现到现在 {now - self.last_change_ts:.1f}s")
                else:
                    _log(f"开始分析 · 这条消息出现到现在 {now - self.last_change_ts:.1f}s")
                    self._push("applyPending:", (newest.text, newest.sender, prev_text))
                # off the tick path on purpose: the call takes over a second, and while it
                # runs the loop must keep reading — a message landing mid-analysis used to
                # wait the whole analysis out before anyone even saw it
                threading.Thread(target=self._reply_task,
                                 args=(self._reply_epoch, self._run_analysis,
                                       newest, msgs, prev_text), daemon=True).start()
            elif newest.text != self.analyzed_text:
                # the wait is deliberate; say so once per arrival change so "it feels slow" can
                # be told apart from "it is still waiting out the burst window"
                why = ("消息还在变" if not settled else
                       "上一条还在分析" if self._analyzing else
                       f"距上次分析不足 {MIN_GAP_S}s")
                if self._last_skip_reason != why:
                    self._last_skip_reason = why
                    _log(f"暂不分析（{why}）")
            else:
                self._last_skip_reason = None

    @objc.python_method
    def _enqueue_prework(self, newest, context, prev_text):
        """Queue the early run at the current reply epoch (latest-wins).

        Fired on arrival and re-fired when an empty-OCR frame retires the in-flight
        workers (#58): overwriting the slot means only the newest text's result can
        ever be consumed, and only by the settle gate. Tones are captured here — a
        dropdown click during the window invalidates the result at consumption time
        (checked in _take_pregen). 上游这里排两个队：判断一个、生成一个；判断合进这一次
        调用之后只剩下面这一个（`prev_text` 留着是为了保住上游的签名和调用点）。
        """
        self._pregen_req = (newest.text, context,
                            tuple(self.slot_tones), self._reply_epoch)
        self._pregen_result = None
        self._pregen_event.set()

    @objc.python_method
    def _run_analysis(self, newest, msgs, prev_text: str):
        try:
            if not self._reply_current():
                return
            self._analyze(newest, msgs, prev_text)
        except Exception as e:
            _log(f"分析失败 {type(e).__name__}")
            self._push("applyError:", f"分析失败: {type(e).__name__}")
        finally:
            self._analyzing = False

    @objc.python_method
    def _pregen_loop(self):
        """Run the call the moment a message is seen — judgment and candidates both.

        One resident worker, latest-wins: a burst overwrites the slot, so one call per
        arrival rather than one per tick, and a result survives only if its text is still
        the newest when the call returns. 上游这里只是「生成那一半」，判断另有一个
        _prejudge_loop 并排跑（本地前向）；合成一次之后这就是唯一的一条早跑线。
        """
        while True:
            try:
                self._pregen_event.wait()
                self._pregen_event.clear()
                req = self._pregen_req
                self._pregen_req = None
                if req is None:
                    continue
                text, context, tones, epoch = req
                self.reload_conversations()
                if self._paused or text != self.last_seen or epoch != self._reply_epoch:
                    continue          # superseded while queued: only the newest text counts
                self._pregen_running = True
                gen = None
                try:
                    gen = self.generator.generate(chat_context.model_message(text, context), "", list(tones), context)
                except Exception:
                    gen = None        # a failed early run just means the settle path regenerates
                # store BEFORE clearing _pregen_running, so _take_pregen never observes
                # "not running" without the result already visible
                self.reload_conversations()
                if (gen is not None and not self._paused and text == self.last_seen
                        and epoch == self._reply_epoch):
                    self._pregen_result = (text, tones, gen, epoch)
                self._pregen_running = False
            except Exception:
                self._pregen_running = False

    @objc.python_method
    def _take_pregen(self, text: str, tones: tuple) -> tuple[dict | None, float]:
        """Collect the early generation: (gen, waited_ms). gen=None ⇒ caller generates.

        A stored result counts only when BOTH the text and the tone selection match — the
        text because a newer message retired it, the tones because a dropdown click during
        the window changed what should be generated. While a matching request is in flight
        we wait for it (it started ~1 s ago at detection, so what is left is usually a few
        hundred ms — still cheaper than a fresh call, and free of a second TLS handshake).
        """
        t0 = time.perf_counter()
        deadline = time.time() + 30   # generation's own timeout; never wait longer
        while time.time() < deadline:
            if not self._reply_current():
                return None, (time.perf_counter() - t0) * 1000
            r = self._pregen_result
            if (r is not None and r[0] == text and r[1] == tones
                    and r[3] == self._reply_epoch):
                self._pregen_result = None      # spent: each result is consumed exactly once
                return r[2], (time.perf_counter() - t0) * 1000
            if (not self._pregen_running
                    and (self._pregen_req is None or self._pregen_req[0] != text)):
                return None, (time.perf_counter() - t0) * 1000
            time.sleep(0.03)
        return None, (time.perf_counter() - t0) * 1000

    @objc.python_method
    def _gen_with_pregen(self, text: str, context: str | None,
                         make_hook=None) -> dict:
        """Generate, preferring an early run already in flight or finished (full path).

        `make_hook` builds the streaming callback and is only called for a fresh call: an
        early-run hit already has all its lines, and _finish_generate paints them as soon as
        the result is in hand. Building the hook anyway would bump _gen_epoch and knock an
        in-flight 换话术 stream off its slot — upstream's _run_generation avoids that on
        purpose ("A hit never creates a hook"), and so does this.
        """
        tones = tuple(self.slot_tones)
        gen, _waited = self._take_pregen(text, tones)
        if not self._reply_current():
            return {"groups": []}
        if gen is None:
            on_candidate = make_hook() if make_hook else None
            gen = self.generator.generate(chat_context.model_message(text, context), "", list(tones), context, on_candidate)
        return gen

    # 上游这里还有 _run_generation：预判命中时那条「判断已上屏，只收生成 + 排序」的
    # 半路。判断和候选现在是同一次调用的两部分，没有半路可走——_work_inner 两条分支都直接
    # 进 _run_analysis / _analyze，早跑命中在 _take_pregen 里体现。
    @objc.python_method
    def reload_conversations(self):
        with self._context_lock:
            store = self.conversations
            if store is not None:
                store.reload()
                if store.revision != getattr(self, '_conversation_revision', 0):
                    self._conversation_revision = store.revision
                    self._context_changed()
                    if store.error:
                        _log(store.error)
                        self._push_reply("applyError:", store.error, self._reply_epoch)
            return store

    @objc.python_method
    def _context_changed(self):
        self._context_version += 1
        self._reply_epoch += 1
        self._gen_epoch += 1
        self._reply_key = None
        self.last_seen = self.analyzed_text = None
        self._pregen_req = self._pregen_result = None
        self._active_context = None
        self._observed_messages = None
        self._observed_offset = 0
        self._fingerprint = None
        self._next_read_ts = 0
        self._push_reply("applyWaiting:", None, self._reply_epoch)

    @objc.python_method
    def save_background(self, title, text):
        with self._context_lock:
            self.reload_conversations()
            if self.conversations is None:
                raise OSError("会话存储不可用")
            self.conversations.save_background(title, text)
            if title == (self._last_full or {}).get("chat_title"):
                self._context_changed()

    @objc.python_method
    def configure_context(self, enabled, limit):
        count = chat_context.message_limit(limit)
        with self._context_lock:
            if (self.history_enabled, self.context_limit) != (enabled, count):
                self.history_enabled, self.context_limit = enabled, count
                self._context_changed()

    @objc.python_method
    def clear_history(self, title=None):
        with self._context_lock:
            self.reload_conversations()
            if self.conversations is None:
                raise OSError("会话存储不可用")
            self.conversations.clear_history(title)
            self._context_changed()

    @objc.python_method
    def _context_text(self, msgs, newest) -> str | None:
        if hasattr(self._reply_worker, "context"):
            return self._reply_worker.context
        with self._context_lock:
            store = self.reload_conversations()
            title = (self._last_full or {}).get("chat_title")
            return chat_context.context_text(
                self._observed_messages if self._observed_messages is not None else
                [(m.text, m.side, m.sender or "") for m in msgs],
                self._observed_offset + next(i for i, m in enumerate(msgs) if m is newest),
                limit=self.context_limit,
                background=store.data.get(title, {}).get('background', '') if store else "")

    @objc.python_method
    def _analyze(self, newest, msgs, prev_text: str = ""):
        """One call: judgment first on screen, then the candidates.

        Runs on its own thread (started by _work_inner): it takes over a second and must
        not hold the read loop hostage.

        上游这里是一个 2 线程的池子：判断（本地前向或云端 Jev）和生成真并行，判断通常
        先回、先上屏，候选晚一秒左右跟上。合成一次之后没有可并行的东西了——判断和候选是
        同一份回答的两部分。**这是这次合并唯一一处面板变慢的地方**：意图/风险不再比候选
        早到，两边一起出现。换来的是少一次调用、少一把 key，以及判断能看到和生成一样多的
        上下文。推的顺序仍然是判断在前、候选在后，applyJudgment_ 照旧负责起新一轮候选的行号。
        """
        t0 = time.perf_counter()
        context = self._context_text(msgs, newest)
        # 早跑命中就直接用那份结果（判断也在里面）；没命中才现发一次，那一次才走流式回调
        gen = self._gen_with_pregen(newest.text, context, lambda: self._stream_hook(t0))
        verdict = gen.get("verdict")
        if verdict is not None:
            _log(f"判断 → {verdict.get('intent', '?')}"
                 f" 把握 {_confidence_text(verdict) or '?'}"
                 f" 风险 {verdict.get('risk', '?')}（与候选同一次调用）")
            self._push("applyJudgment:", (verdict, newest.sender, prev_text))
        else:
            # 判断没读出来（模型漏了、或者整段 JSON 废了）。候选照常上屏——上游判断调用
            # 挂了也是这个效果：面板留着候选，状态行说判断这次没出来。
            _log("判断失败: 这次回答里没有 judgment")
            self._push("applyError:", "判断失败: 模型这次没给出意图/风险")
        self._finish_generate(gen, t0)

    @objc.python_method
    def _finish_generate(self, gen: dict, t0: float, note: str = ""):
        """Log the generation and push the candidates, ordered by the scores that came
        back with them.

        上游这里先推一次没排序的（界面显示「排序中」），再发一次判断模型的排序调用，
        排完再推一次。这一版分跟候选是同一次调用回来的，没有中间态可推，也没有第二次
        调用可发——流式那一路的行早就上屏了（applyStreamLine_，显示「排序中」），
        这一次推的就是带分、已排好的那版。非流式那一家（anthropic 形状）这次仍是首次上屏。
        """
        if not self._reply_current():
            return
        groups = gen.get("groups") or []
        failed = [str(g["slot"]) for g in groups if g.get("error")]
        _log(f"生成 {gen.get('elapsed_s', 0) * 1000:.0f}ms{note} · {len(groups)} 个话术并发"
             f" → {sum(len(g['texts']) for g in groups)} 条候选"
             + (f" · 失败: {'; '.join(failed)}" if failed else ""))
        payload = self._payload_from_gen(gen)
        if payload is None:
            err = "服务未返回可用候选，请检查模型设置"
            _log(f"生成无可用候选: {err}")
            self._push("applyError:", f"候选生成失败: {err}")
            return
        ranked = self._rank_payload(payload, gen.get("scores") or {})
        _log(f"端到端 {(time.perf_counter() - t0) * 1000:.0f}ms"
             f" · 从分析开始到候选上屏")
        self._push("applyCandidates:", ranked)

    @objc.python_method
    def _push(self, selector: str, payload=None):
        if selector in {"applyIncoming:", "applyPending:", "applyJudgment:",
                        "applyCandidates:", "applyRegenerated:", "applyTones:",
                        "applyStreamLine:", "applyWaiting:", "applyError:"}:
            epoch = getattr(self._reply_worker, "epoch", self._reply_epoch)
            self._push_reply(selector, payload, epoch)
            return
        self.performSelectorOnMainThread_withObject_waitUntilDone_(selector, payload, False)

    @objc.python_method
    def _reply_task(self, epoch, callback, *args):
        self._reply_worker.epoch = epoch
        self._reply_worker.context = self._active_context
        try:
            return callback(*args)
        finally:
            del self._reply_worker.epoch
            del self._reply_worker.context

    @objc.python_method
    def _reply_current(self):
        self.reload_conversations()
        return (self._app is not None
                and self._reply_key is not None and not self._paused
                and getattr(self._reply_worker, "epoch", self._reply_epoch) == self._reply_epoch)

    @objc.python_method
    def _push_reply(self, selector, payload, epoch):
        self.performSelectorOnMainThread_withObject_waitUntilDone_(
            "applyReplyUpdate:", (epoch, selector, payload), False)

    def applyReplyUpdate_(self, update):
        self.reload_conversations()
        epoch, selector, payload = update
        if self._app is None or epoch != self._reply_epoch:
            return
        if selector not in {"applyWaiting:", "applyError:"} and not self._reply_current():
            return
        getattr(self, selector.replace(":", "_"))(payload)

    def applyWaiting_(self, _payload):
        self._show()
        self._last_intent = ""
        self._last_risk = None
        self._clear_candidates()
        self._stream_rows = {}
        for key in ("message", "sender", "intent", "confidence", "risk", "actions"):
            self._render(key, "", PALETTE["muted"])
        if hasattr(self, "_risk_dots"):
            self._set_risk_scale(None)
        if hasattr(self, "_set_candidate_header"):
            self._set_candidate_header("候选回复")
        else:
            # Lightweight test harnesses load this callback without constructing AppKit.
            self.rows["cand_header"].setStringValue_("候选回复")
        self._render("status", _payload or "等待可确认的对方消息…", PALETTE["muted"])

    # --- main-thread callbacks (AppKit is not thread safe)
    def applyChat_(self, title):
        self._chat_title = title
        self._render("chat", title, PALETTE["accent"])

    def applyIncoming_(self, payload):
        # a new message landed but we are not analysing yet (burst in progress):
        # keep the previous verdict visible, just badge it
        text, sender, prev = payload
        self._show()
        self._render("status", "有新消息 · 等消息停稳…", PALETTE["muted"])
        self._render("message", text, PALETTE["muted"])   # grey: not analysed yet
        self._render("sender", self._context_line(sender, prev), PALETTE["muted"])

    def applyPending_(self, payload):
        text, sender, prev = payload
        self._show()
        self._render("status", "分析中…", PALETTE["muted"])
        self._render("message", text, PALETTE["text"])    # inked: this is the one
        self._render("sender", self._context_line(sender, prev), PALETTE["muted"])
        self._clear_candidates()
        self._stream_rows = {}     # a new run starts at line zero in every slot
        self._set_candidate_header("候选回复 · 等待判断…")

    def applyJudgment_(self, payload):
        v, sender, prev = payload
        self._show()
        intent = _judgment_text(v.get("intent"))
        # kept so the overlay can label the message it judged; a verdict with no usable
        # intent leaves it empty, which is what switches the badge off (see applyBoxes_)
        self._last_intent = "" if intent == "暂未判断" else intent
        self._last_risk = v.get("risk")      # raw: every reader guards it for itself
        self._render("message", v.get("message", ""), PALETTE["text"])
        self._render("sender", self._context_line(sender, prev), PALETTE["muted"])
        # 上游这里还有一支「本地兜底 · …」：云端 Jev 挂了会切到本地 decider-2b，状态行
        # 得说清是谁给的结论。现在判断和候选是同一个模型，backend 就是那个模型名。
        backend = v.get("backend", "")
        if isinstance(backend, str) and backend.strip():
            self._render("status", f"分析完成 · {backend.strip()}", PALETTE["muted"])
        else:
            self._render("status", "分析完成", PALETTE["muted"])
        self._render("intent", intent, PALETTE["text"])
        # the intent recognition rate, read off the judged intent — same muted slot
        confidence = _confidence_text(v)
        self._render("confidence", f"意图识别率 {confidence}" if confidence else "",
                     PALETTE["muted"])
        # Rounded, so the panel does not claim a precision it has: a judge that reports a
        # mean like 4.7 out of a 10-level distribution reads as a measurement at "4.7/9"
        # and as the estimate it is at "5/9". (上游的本地模型给的就是均值，理由是它的
        # argmax 在几条意思相近的「批评」上会跳 1/3/6，而均值稳定；通用模型直接给档位，
        # 这一行的读法照旧不变。)
        risk = _risk_level(v.get("risk"))
        if risk is None:
            self._render("risk", "风险待判断", PALETTE["muted"])
        else:
            label = "安全" if risk <= 3 else ("留神" if risk <= 6 else "危险")
            color = PALETTE["green"] if risk <= 3 else (
                PALETTE["amber"] if risk <= 6 else PALETTE["red"])
            self._render("risk", f"● {label}  {risk}/9", color)
        if hasattr(self, "_risk_dots"):
            self._set_risk_scale(risk)
        self._render("actions", _actions_text(v), PALETTE["text"])
        self._set_candidate_header("候选回复 · 生成中…")
        # the verdict landing starts a new candidate run: without this reset, the streamed
        # line counters left over from the previous message would eat every new line
        # (applyStreamLine_ drops rows beyond PER_TONE) — only applyPending_ and
        # toneChanged_ used to reset it, and the pre-judged path goes through neither
        self._stream_rows = {}

    def applyCandidates_(self, payload):
        if not self._payload_current(payload):
            return
        self._set_candidate_header(self._cand_header(payload))
        self._render_groups(payload)

    def applyStreamLine_(self, payload):
        """One streamed candidate line, shown the moment it completes (not ranked yet).

        applyCandidates_ re-fills every row with scores when the full result lands, so the
        "#n" here is only "nth line of this tone" and the score slot reads as pending. A
        superseded run's lines are dropped by the epoch check — a tone change or a new
        message starting mid-stream must not write into the new run's rows.
        """
        epoch, slot, text = payload
        if epoch != self._gen_epoch or not self._slot_active(slot):
            return
        row = self._stream_rows.get(slot, 0)
        if row >= styles.PER_TONE:
            return                       # the prompt asks for PER_TONE lines; extras stray
        self._stream_rows[slot] = row + 1
        self.cand_texts[slot * styles.PER_TONE + row] = text
        if self._collapsed:
            return      # collapse keeps the data; _set_collapsed(False) puts it back up
        r = self._rows[slot][row]
        self._set_probability_label(r["prob"], row, "排序中")
        r["text"].setStringValue_(text)
        self._set_progress(slot, row, None)
        for c in self._row_controls(slot, row):
            c.setHidden_(False)
        self._relayout()

    def applyError_(self, text):
        self._show()                       # never vanish without telling the user why
        self._render("status", text, PALETTE["red"])

    # 上游这里还有 applyStatus_ / applyWarmFailed_ / applyWarmDone_ 三个回调：本地判断模型
    # 的预热要往状态行写「加载中…」、失败要写红字（#37 那台 16GB 的机器就是在这一步被系统
    # 干掉的）、成功要把那行清掉。没有本地模型可预热，三个回调一起没了。

    def applyHidden_(self, reason):
        # WeChat gone or unreadable -> take the panel away (the app "opens with the chat app")
        # 上游这里还要让位给判断模型的下载进度（load_status 非空就先不隐藏面板）；没有下载了。
        self._render("status", reason, PALETTE["muted"])
        if self.panel.isVisible():
            self.panel.orderOut_(None)
        if self._ov_panel.isVisible():
            self._ov_panel.orderOut_(None)

    def applyForegroundHidden_(self, reason):
        """Hide a global panel without leaving stale conversation state behind."""
        self._last_intent = ""
        self._last_risk = None
        self._stream_rows = {}
        self._chat_title = ""
        self._clear_candidates()
        for key in ("chat", "message", "sender", "intent", "confidence", "risk", "actions"):
            self._render(key, "", PALETTE["muted"])
        self.rows["cand_header"].setStringValue_("候选回复")
        self.applyHidden_(reason)

    def applyPosition_(self, win):
        if self._app is None or getattr(self, "_calibrating", False):
            return
        self._position_near(win)

    # --- YOLO overlay callbacks (visual only; see _build_overlay)
    def applyBoxes_(self, payload):
        """Repaint the overlay from the last read's window geometry + messages."""
        if self._app is None or getattr(self, "_calibrating", False) or not self._show_boxes:
            return
        win, msgs, newest_text = payload
        W, H = win["w"], win["h"]
        flip = self._display_height()
        # top-left (Quartz) -> bottom-left (Cocoa), covering the chat app exactly
        self._ov_panel.setFrame_display_(
            NSMakeRect(win["x"], flip - win["y"] - H, W, H), False)
        font = (NSFont.fontWithName_size_("Menlo-Bold", 10)
                or NSFont.boldSystemFontOfSize_(10))
        judged = (newest_text is not None and newest_text == self.analyzed_text
                  and bool(self._last_intent))
        risk = _risk_level(self._last_risk)
        boxes = []
        for m in msgs:
            if m.w <= 0:
                continue               # pre-overlay geometry: nothing to draw
            who = m.sender or {"them": "对方", "me": "我"}.get(m.side, "方向未确认")
            label = f"{who} {m.conf:.2f}"
            if judged and m.side == "them" and m.text == newest_text:
                # 风险读不出来（模型没给 / 给的不是数）就只挂意图，颜色退回普通那一档——
                # 不按 0 处理，那等于替模型说「安全」
                color = (PALETTE["green"] if risk is None or risk <= 3 else
                         PALETTE["amber"] if risk <= 6 else PALETTE["red"])
                lw = 2.5
                label += f" · {self._last_intent}" + ("" if risk is None else f" 风险{risk}/9")
            else:
                color = _rgb(0x576B95) if m.side == "me" else PALETTE["green"]
                lw = 1.5
            chip = NSAttributedString.alloc().initWithString_attributes_(
                label,
                {NSFontAttributeName: font,
                 NSForegroundColorAttributeName: NSColor.whiteColor(),
                 NSBackgroundColorAttributeName: color.colorWithAlphaComponent_(0.85)})
            y = H - (m.y + m.h) * H     # normalized top-origin -> view bottom-origin
            boxes.append((NSMakeRect(m.x * W, y, m.w * W, m.h * H), color, lw, chip))
        target = getattr(self, "_input_target", None)
        if target and target["window"] == win and target["rect"]:
            x, y, w, h = target["rect"]
            color = _rgb(0x2478DD)
            rect = NSMakeRect(x-win["x"], H-(y-win["y"])-h, w, h)
            label = target["reason"]
        elif target and target["window"] == win and target.get("visual_rect"):
            x, y, w, h = target["visual_rect"]
            color = PALETTE["amber"]
            rect = NSMakeRect(x-win["x"], H-(y-win["y"])-h, w, h)
            label = ("虚线：手动输入区 · 有草稿停止，不发送" if target.get('manual_region')
                     else "虚线：视觉输入区 · 点击填入后校验（不发送）")
        else:
            color = PALETTE["amber"]
            rect = NSMakeRect(12, 12, 0, 0)
            label = ("输入框：请点击右上角图标校准" if getattr(self,"_calibration_required",False)
                     else "输入框：" + (target["reason"] if target else "定位中…"))
        chip = NSAttributedString.alloc().initWithString_attributes_(label, {
            NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.whiteColor(),
            NSBackgroundColorAttributeName: color.colorWithAlphaComponent_(0.85)})
        boxes.append((rect, color, 2.0, chip, bool(target and target.get("visual_rect") and not target["rect"])))
        view = self._ov_panel.contentView()
        view.boxes = boxes
        view.setNeedsDisplay_(True)
        if not self._ov_panel.isVisible():
            self._ov_panel.orderFrontRegardless()

    def toggleBoxes_(self, sender):
        """Menu-bar switch; JEV_BOXES=1 in the env file makes it start on instead."""
        self._show_boxes = not self._show_boxes
        self.boxes_item.setState_(
            AppKit.NSOnState if self._show_boxes else AppKit.NSOffState)
        if not self._show_boxes and self._ov_panel.isVisible():
            self._ov_panel.orderOut_(None)

    # --------------------------------------------------------------- warm-up
    @objc.python_method
    def _warm_apps(self):
        """Pay each chat app's one-off read-path load (Vision for the OCR path; the text-interface path has none)."""
        for app in APPS:
            ms = app.warm()
            if ms is None:
                continue                  # 文本接口路径没有一次性加载
            if ms >= 0:
                self._read_once = True    # Vision's one-off load is paid; first read is steady-state
                _log(f"预热 {app.display_name} 读屏就绪 · {ms:.0f}ms")
            else:
                _log(f"预热 {app.display_name} 读屏失败 · 首次读屏会稍慢，不影响使用")

    @objc.python_method
    def _warm(self):
        """Pay the one-off loads in the background: each app's read path.

        The first real message used to carry them; starting them here, right after
        launch, moves them to idle time. The OCR warm-up is independent of the chat app
        entirely (a blank canvas, not a window).

        上游这里还要预热判断模型：本地 decider-2b 第一次加载 9–15 秒（含下载可能是几分钟），
        所以面板要显示「判断模型加载中…」、失败了还要红字提示，#37 那台 16GB 的机器就是
        在这一步被系统干掉的（judge.low_memory_reason 的内存守卫也是为它写的）。判断合进
        那一次网络调用之后，本地没有任何东西要加载——整段没了，连带 WARM_STATUS、
        applyStatus_ / applyWarmDone_ / applyWarmFailed_、那个内存守卫，以及 #38 那个
        「首次引导选判断方式」的对话框（_onboarding_needed / maybeOnboard_ / _record_onboarding）。
        """
        self._warm_apps()


def warn_if_no_generation_key() -> None:
    """Say it out loud at launch when there is no key behind the one call.

    上游这句话是「候选那一半没 key」：判断跑在本地，不配 key 也有意图和风险，所以面板
    只是候选区空着。合成一次之后这把 key 是整个面板的前提——没有它，意图、风险、具体行动
    和候选一起空着。One dialog at launch is the cheapest way to say so — it cannot be
    missed the way a line of grey text in a floating panel can.

    OPENAI_* and ANTHROPIC_* are two ways to configure the same generation layer, so this
    fires only when NEITHER is set: either one on its own is a complete configuration.
    A packaged build also carries a shared default (src/builtin.py), so this dialog only
    appears when that default was deliberately emptied out.

    Drawn with osascript rather than NSAlert, which was measured to not work here: an
    accessory app cannot activate itself (NSApp.isActive stays False after
    activateIgnoringOtherApps_), and an NSAlert stayed isVisible=False even inside its own
    modal session — so the user would get nothing to click while the app sat in a modal
    loop, i.e. an app that looks hung. osascript's dialog belongs to a process that can
    activate, and Popen does not wait, so a dialog nobody dismisses cannot stall us.
    """
    if load_credentials()[1]:
        return
    path = str(userconfig.ENV_FILE).replace(str(Path.home()), "~")
    # AppleScript string escapes (\n) work inside the literal; keep it free of double quotes
    script = (
        'display alert "还没配 Key，面板会是空的" message "'
        "这一版判断和候选是同一次调用，所以意图、风险和候选回复都要它。\\n\\n"
        f"在下面的文件里填这两组中的任意一组（二选一即可），然后重启本应用：\\n{path}\\n\\n"
        "    OPENAI_API_KEY      （任意 OpenAI 兼容端点，如 DeepSeek）\\n"
        '    ANTHROPIC_API_KEY   （任意 Anthropic 兼容端点，如智谱）" as informational'
    )
    try:
        subprocess.Popen(["osascript", "-e", script],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass          # no osascript: the panel still shows the hint in the candidate area


def main() -> None:
    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)
    warn_if_no_generation_key()
    controller = HudController.alloc().init()
    # First line of every run: which backends are actually in play. Support requests
    # always need it, and it proves the log is live before the first message arrives.
    _base, _key, _model, _src, _api = load_credentials()
    _log(f"启动 · 判断+候选同一次调用 "
         f"{(_base + ' / ' + _model) if _key else '未配置 Key（面板会是空的）'}"
         + ("（内置默认）" if _src == BUILTIN_SOURCE else "")
         + (" · YOLO 框开" if controller._show_boxes else ""))
    if styles.REJECTED_TONES:
        # the dropdown silently missing a tone the user typed is a support ticket; say
        # why it was refused and what a passing description looks like, once, at startup
        _log("自定义话术未加载 · " + "；".join(styles.REJECTED_TONES))
    controller._show()
    # Warm each app's read path (Vision OCR for the capture apps) while the panel is idle,
    # so the user's first message pays only steady-state costs. 上游这里还要先弹 #38 的
    # 「选择判断方式」，再预热本地判断模型；这一版没有判断方式可选，也没有本地模型可预热。
    threading.Thread(target=controller._warm, daemon=True).start()
    timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        FAST_TICK, controller, "tick:", None, True)
    AppKit.NSRunLoop.currentRunLoop().addTimer_forMode_(timer, AppKit.NSDefaultRunLoopMode)
    app.run()


if __name__ == "__main__":
    main()
