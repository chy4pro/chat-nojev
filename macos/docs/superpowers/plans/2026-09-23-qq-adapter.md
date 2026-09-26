# QQ 适配器实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 macOS 版 jev-jarvis 在微信之外同时支持 QQ（QQNT 6.9.x）：微信继续走截图 + OCR，QQ 走系统无障碍（AX）树；HUD 按前台 App 分发。

**Architecture:** 新增 `src/apps/` 适配器层：`base.py` 定义 `ChatApp` 协议，`wechat.py` 薄封装现有 `perception` / `fill`，`qq.py` 用 AX 树读 QQ 聊天窗口（DOM class `container--self` 判方向、`ExEditor-qq-msg-editor` 定位输入框），`registry.py` 按前台 App 的 bundle id / 名字分发。`hud.py` 用 `self._app` 取代 `self._wechat_frontmost`，读屏与填入全部经 `self._app`。设计文档：`docs/superpowers/specs/2026-09-23-qq-adapter-design.md`。

**Tech Stack:** Python 3.12、pyobjc（AppKit / ApplicationServices / Quartz）、unittest（离线回归，CI 命令 `uv run python -B -m unittest discover -s tests`）。

**工程目录：** `~/Desktop/jev-chat-qq/jev-chat-jarvis-mac`，分支 `feat/qq-adapter`。commit 用 Conventional Commits + 中文描述，末尾带 `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`。

**硬约束（AGENTS.md）：** 纯只读；「填入」是唯一写动作，AX 设值优先，后备只能用键盘事件，不发送、不用剪贴板 / Cmd+V、不覆盖草稿。轮询节奏、停稳窗口、最小分析间隔、独立分析线程一律不动。

---

## 文件结构

| 文件 | 职责 |
|---|---|
| `probe/qq_ax_probe.py`（新增） | 真机探测：打印 QQ 聊天窗口 AX 树里的消息容器、class、方向、输入框；可选 `--fill` 试 AX 设值 |
| `src/apps/__init__.py`（新增） | 空包标记 |
| `src/apps/base.py`（新增） | `ChatApp` 协议（接口契约，无实现） |
| `src/apps/wechat.py`（新增） | `WeChatApp`：转调 `perception` / `fill`，零逻辑 |
| `src/apps/qq.py`（新增） | `QQApp`：AX 读取器、纯解析函数（找编辑器、标题、消息、指纹）、窗口选择、`read_conversation`、`locate_input`、`fill_text`、CLI 自测 |
| `src/apps/registry.py`（新增） | `APPS`、`UNKNOWN` 哨兵、`frontmost_app()`、`app_by_key()` |
| `src/hud.py`（修改） | `self._app` 分发、权限门按 App 区分、文案泛化、回复 key 加 `app.key` |
| `tests/test_registry.py`（新增） | 分发规则离线测试 |
| `tests/test_qq_adapter.py`（新增） | QQ 解析 / 窗口选择 / 填入的离线测试（内存伪 AX 树） |
| `tests/support_hud.py`、`tests/test_hud_reply.py`、`tests/test_download_progress.py`（修改） | 夹具从 `frontmost_app_is_wechat` 换成 `frontmost_app` + `FAKE_APP` |
| `README.md`、`AGENTS.md`、`pyproject.toml`（修改） | 用户可见行为同步 |

**不动：** `src/perception.py`、`src/fill.py`、`src/visual_fill.py`、`src/input_region.py`、`src/judge*.py`、`src/generate.py`、`src/styles.py`、`src/settings*.py`、`src/userconfig.py`。

---

### Task 1: 真机探测脚本，复核三个待验证项

**Files:**
- Create: `probe/qq_ax_probe.py`
- Modify: `docs/superpowers/specs/2026-09-23-qq-adapter-design.md`（第 2 节「待复核」改为结论）

- [ ] **Step 1: 写探测脚本**

```python
#!/usr/bin/env python3
"""QQ AX 探测：打印 QQNT 聊天窗口无障碍树里的消息容器、DOM class、方向与输入框。

用法（QQ 聊天窗口在屏幕上时）：
    uv run python probe/qq_ax_probe.py            # 只读：窗口、输入框、消息
    uv run python probe/qq_ax_probe.py --fill 你好  # 另外试一次 AX 设值并读回（输入框须为空）

不发送、不点按键、不用剪贴板。--fill 只在输入框为空时写入，之后你自己删掉即可。
"""
from __future__ import annotations

import re
import sys
import time

import AppKit
import ApplicationServices as AS

EDITOR_CLASS = "ExEditor-qq-msg-editor"


def attr(el, name):
    err, v = AS.AXUIElementCopyAttributeValue(el, name, None)
    return v if err == 0 else None


def pt(v):
    if v is None:
        return None
    m = (re.search(r"x:([\d.-]+) y:([\d.-]+)", str(v))
         or re.search(r"w:([\d.-]+) h:([\d.-]+)", str(v)))
    return (float(m.group(1)), float(m.group(2))) if m else None


def classes(el):
    v = attr(el, "AXDOMClassList")
    return tuple(str(c) for c in (v or ()))


def walk(el, depth=0, limit=[0]):
    limit[0] += 1
    if limit[0] > 6000 or depth > 60:
        return
    yield el, depth
    for c in attr(el, AS.kAXChildrenAttribute) or []:
        yield from walk(c, depth + 1, limit)


def main():
    fill_text = None
    if "--fill" in sys.argv:
        fill_text = sys.argv[sys.argv.index("--fill") + 1]
    print("AX trusted:", AS.AXIsProcessTrusted())
    apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_("com.tencent.qq")
    if not apps:
        print("QQ 未运行")
        return 1
    pid = apps[0].processIdentifier()
    app = AS.AXUIElementCreateApplication(pid)
    AS.AXUIElementSetAttributeValue(app, "AXManualAccessibility", True)
    time.sleep(0.3)
    windows = attr(app, AS.kAXWindowsAttribute) or []
    focused = attr(app, AS.kAXFocusedWindowAttribute)
    print(f"AX windows: {len(windows)}")
    for w in windows:
        editor = next((el for el, _ in walk(w)
                       if attr(el, AS.kAXRoleAttribute) == "AXTextArea" and EDITOR_CLASS in classes(el)), None)
        print(f"- title={attr(w, AS.kAXTitleAttribute)!r} pos={pt(attr(w, AS.kAXPositionAttribute))} "
              f"size={pt(attr(w, AS.kAXSizeAttribute))} focused={w == focused} editor={'有' if editor else '无'}")
        if editor is None:
            continue
        print(f"  editor desc={attr(editor, AS.kAXDescriptionAttribute)!r} value={attr(editor, AS.kAXValueAttribute)!r}")
        wpos = pt(attr(w, AS.kAXPositionAttribute)); wsize = pt(attr(w, AS.kAXSizeAttribute))
        print("  --- 消息容器（class 含 msg-content-container）与头像（avatar-span）---")
        for el, depth in walk(w):
            cls = classes(el)
            if "avatar-span" in cls:
                print(f"  {'  ' * min(depth, 10)}avatar desc={attr(el, AS.kAXDescriptionAttribute)!r}")
            if "msg-content-container" in cls:
                p = pt(attr(el, AS.kAXPositionAttribute)); s = pt(attr(el, AS.kAXSizeAttribute))
                texts = [attr(c, AS.kAXValueAttribute) for c, _ in walk(el)
                         if attr(c, AS.kAXRoleAttribute) == "AXStaticText"]
                nx = (p[0] - wpos[0]) / wsize[0] if p else -1
                print(f"  {'  ' * min(depth, 10)}msg nx={nx:.2f} h={s[1] if s else 0:.0f} "
                      f"classes={[c for c in cls if c.startswith('container--')]} text={texts}")
        # 群聊里昵称节点：打印消息区里不属于任何容器的 AXStaticText（时间戳、昵称都会出现在这里）
        print("  --- 消息区里的裸文本（时间分隔 / 群昵称候选）---")
        for el, depth in walk(w):
            if attr(el, AS.kAXRoleAttribute) != "AXStaticText":
                continue
            parent = attr(el, AS.kAXParentAttribute)
            pcls = classes(parent) if parent is not None else ()
            if "message-content" in pcls or "会话列表" in str(attr(parent, AS.kAXDescriptionAttribute)):
                continue
            p = pt(attr(el, AS.kAXPositionAttribute))
            if p and wpos and (p[0] - wpos[0]) / wsize[0] > 0.25:
                print(f"    text={attr(el, AS.kAXValueAttribute)!r} parent_classes={list(pcls)}")
        if fill_text is not None and w == focused:
            current = attr(editor, AS.kAXValueAttribute) or ""
            if current.strip():
                print("  --fill 跳过：输入框已有草稿")
            else:
                err = AS.AXUIElementSetAttributeValue(editor, AS.kAXValueAttribute, fill_text)
                time.sleep(0.2)
                print(f"  --fill AX 设值 err={err} 读回={attr(editor, AS.kAXValueAttribute)!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: 在 QQ 里打开一个有对方消息的群聊，跑只读探测**

Run: `uv run python probe/qq_ax_probe.py`
Expected: 打印带 editor 的窗口；每条消息一行，`classes=['container--self']` 或对方消息对应的 class（记下它，预计形如 `container--others`）；群聊里昵称文本出现在「裸文本」段，记下其 `parent_classes`。

- [ ] **Step 3: 在输入框为空时试 AX 设值**

Run: `uv run python probe/qq_ax_probe.py --fill 探测文本`
Expected: 打印 `--fill AX 设值 err=0 读回='探测文本'`（生效）或 `err=-25205` / 读回仍为 `'\n'`（不生效，走键盘后备）。记录结果，然后在 QQ 里手动清掉输入框。

- [ ] **Step 4: 把三个结论写回设计文档第 2 节**

把「**待复核（实现计划第一步）：**…」那一段替换为三行实测结论，例如：

```markdown
**复核结论（2026-09-23 实测）：** 对方消息 class 为 `container--others`（若实测不同以实测为准，解析只依赖「不含 `container--self`」）；群聊昵称为独立 `AXStaticText`，父节点 class 含 `<实测值>`，不在 `message-content` 内，解析时不会混入正文；AX 对编辑器设值 <生效 / 不生效>，`fill_text` <直接走 AX / 落到键盘后备>。
```

- [ ] **Step 5: Commit**

```bash
git add probe/qq_ax_probe.py docs/superpowers/specs/2026-09-23-qq-adapter-design.md
git commit -m "chore(probe): QQ 无障碍树探测脚本，复核方向 class / 群昵称 / AX 设值

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 2: 适配器协议与微信薄封装

**Files:**
- Create: `src/apps/__init__.py`、`src/apps/base.py`、`src/apps/wechat.py`
- Test: `tests/test_registry.py`（先放微信封装的委托测试，Task 3 再补分发测试）

- [ ] **Step 1: 写失败测试（微信封装逐一委托）**

```python
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


class WeChatAppTests(unittest.TestCase):
    def test_identity(self):
        from apps.wechat import WeChatApp
        app = WeChatApp()
        self.assertEqual(app.key, 'wechat')
        self.assertEqual(app.display_name, '微信')
        self.assertIn('com.tencent.xinWeChat', app.bundle_ids)
        self.assertIn('微信', app.app_names)
        self.assertTrue(app.needs_screen_capture)

    def test_delegates_to_perception_and_fill(self):
        from apps.wechat import WeChatApp
        app = WeChatApp()
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


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -B -m unittest tests.test_registry -v`
Expected: FAIL / ERROR，`ModuleNotFoundError: No module named 'apps'`

- [ ] **Step 3: 写协议与微信封装**

`src/apps/__init__.py`：

```python
"""聊天 App 适配器层：HUD 只依赖 base.ChatApp 协议，registry 按前台 App 分发。"""
```

`src/apps/base.py`：

```python
"""ChatApp 协议：一个聊天 App 需要向 HUD 提供的全部能力。

read_conversation 的返回字典必须与 perception.read_conversation 同构（ok / error /
unchanged / messages / chat_title / window / input_rect / input_unresolved /
fingerprint / layout / timing_ms / n_blocks），HUD 的取值点对所有 App 一视同仁。
"""
from __future__ import annotations

from typing import Protocol

from perception import WindowInfo


class ChatApp(Protocol):
    key: str                     # "wechat" | "qq"：进日志、进回复缓存 key
    display_name: str            # "微信" | "QQ"：进面板文案
    bundle_ids: tuple[str, ...]
    app_names: tuple[str, ...]
    needs_screen_capture: bool   # 微信 True（截图 + OCR），QQ False（只需辅助功能）

    def find_window(self, previous_wid: int | None = None) -> WindowInfo | None: ...

    def read_conversation(self, *, previous_wid=None, prev_fingerprint=None,
                          prev_layout=None) -> dict: ...

    def locate_input(self, win: dict) -> dict: ...

    def fill_text(self, text: str, target=None) -> tuple[bool, str]: ...

    def warm(self) -> float | None:
        """一次性预热耗时（ms）；负数表示预热失败；None 表示无需预热。"""
        ...
```

`src/apps/wechat.py`：

```python
"""微信适配器：薄封装，全部转调 perception / fill，不改任何识别逻辑。"""
from __future__ import annotations

import fill
import perception


class WeChatApp:
    key = "wechat"
    display_name = "微信"
    bundle_ids = (fill.WECHAT_BUNDLE_ID,)
    app_names = perception.WECHAT_APP_NAMES
    needs_screen_capture = True

    def find_window(self, previous_wid=None):
        return perception.find_wechat_window(previous_wid)

    def read_conversation(self, **kwargs):
        return perception.read_conversation(**kwargs)

    def locate_input(self, win):
        return fill.locate_input(win)

    def fill_text(self, text, target=None):
        return fill.fill_text(text, target=target)

    def warm(self):
        return perception.warm_ocr()
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -B -m unittest tests.test_registry -v`
Expected: `test_identity ... ok`、`test_delegates_to_perception_and_fill ... ok`

- [ ] **Step 5: Commit**

```bash
git add src/apps/__init__.py src/apps/base.py src/apps/wechat.py tests/test_registry.py
git commit -m "feat(apps): ChatApp 协议与微信薄封装适配器

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 3: 注册与前台分发 `registry.py`

**Files:**
- Create: `src/apps/registry.py`
- Modify: `tests/test_registry.py`（追加分发测试）

注意：`registry` 会 import `apps.qq`，Task 4 之前 `qq.py` 尚不存在。本任务先让 `qq.py` 以**最小骨架**存在（只有 `QQApp` 的身份字段），Task 4 再填实现。

- [ ] **Step 1: 追加失败测试**

在 `tests/test_registry.py` 末尾（`if __name__` 之前）追加：

```python
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

    def test_app_by_key(self):
        from apps import registry
        self.assertEqual(registry.app_by_key('qq').display_name, 'QQ')
        self.assertIsNone(registry.app_by_key('feishu'))
        self.assertEqual([a.key for a in registry.APPS], ['wechat', 'qq'])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -B -m unittest tests.test_registry -v`
Expected: `RegistryTests` 全部 ERROR，`ModuleNotFoundError: No module named 'apps.registry'`

- [ ] **Step 3: 写 QQ 骨架与 registry**

`src/apps/qq.py`（骨架，Task 4 会整体重写）：

```python
"""QQ 适配器骨架：Task 4 填充实现。"""
from __future__ import annotations

KEY = "qq"
DISPLAY_NAME = "QQ"
BUNDLE_IDS = ("com.tencent.qq",)
APP_NAMES = ("QQ",)


class QQApp:
    key = KEY
    display_name = DISPLAY_NAME
    bundle_ids = BUNDLE_IDS
    app_names = APP_NAMES
    needs_screen_capture = False
```

`src/apps/registry.py`：

```python
"""按前台 App 分发到适配器。

frontmost_app() 三种返回值必须区分：某个适配器（在聊天 App 里）、None（在别的 App 里，
是一次真正的「离开」）、UNKNOWN（NSWorkspace 查询失败，不是离开的证据——HUD 对它冻结一个
短 tick，与原来 frontmost_app_is_wechat() 返回 None 的处理一致）。
"""
from __future__ import annotations

from apps.qq import QQApp
from apps.wechat import WeChatApp

UNKNOWN = object()          # 前台查询失败的哨兵；`is` 比较
APPS = (WeChatApp(), QQApp())


def _frontmost():
    """(bundle_id, localized_name) of the frontmost app, or None when the query fails."""
    try:
        import AppKit
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return (app.bundleIdentifier() or "", app.localizedName() or "")
    except Exception:
        return None


def match(bundle: str, name: str):
    """先按 bundle id、再按显示名精确匹配（精确：微信读书 / QQ音乐 不算）。"""
    for app in APPS:
        if bundle and bundle in app.bundle_ids:
            return app
    for app in APPS:
        if name in app.app_names:
            return app
    return None


def frontmost_app():
    ident = _frontmost()
    if ident is None:
        return UNKNOWN
    return match(*ident)


def app_by_key(key: str):
    for app in APPS:
        if app.key == key:
            return app
    return None
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -B -m unittest tests.test_registry -v`
Expected: 8 个测试全部 `ok`

- [ ] **Step 5: Commit**

```bash
git add src/apps/qq.py src/apps/registry.py tests/test_registry.py
git commit -m "feat(apps): registry 按前台 App 分发，UNKNOWN 哨兵区分查询失败

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 4: QQ 纯解析：AX 读取器、找编辑器、标题、消息、指纹

**Files:**
- Modify: `src/apps/qq.py`（整体重写）
- Create: `tests/test_qq_adapter.py`

所有解析函数都通过一个「读取器」对象访问 AX（`role / desc / value / title / classes / rect / children / windows / focused_window`），测试用内存树替代，不碰系统。

- [ ] **Step 1: 写失败测试（解析部分）**

```python
# tests/test_qq_adapter.py
"""QQ 适配器离线回归：内存伪 AX 树，不读屏、不碰 QQ 进程、不需要权限。

节点形状按 2026-09-23 对 QQ 6.9.96 的实测（见 specs/2026-09-23-qq-adapter-design.md 第 2 节）。
Run: uv run python -B -m unittest discover -s tests
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from apps import qq


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
        self.assertIsNotNone(qq.find_editor(ax, chat_window([])))
        self.assertIsNone(qq.find_editor(ax, list_window()))

    def test_title_prefers_editor_description_then_window_title(self):
        ax = FakeAX()
        win = chat_window([])
        self.assertEqual(qq.chat_title(ax, win, qq.find_editor(ax, win)), '很难约的王小姐')
        win2 = chat_window([], ed=editor(desc=''))
        self.assertEqual(qq.chat_title(ax, win2, qq.find_editor(ax, win2)), '很难约的王小…')

    def test_sides_text_and_order(self):
        ax = FakeAX()
        win = chat_window([message('在吗', 'them', 300.0, sender='王小姐'),
                           message('在的', 'me', 360.0, sender='西行树'),
                           message('周三有空吗', 'them', 420.0, sender='王小姐')])
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win))
        self.assertEqual([(m.side, m.text, m.sender) for m in msgs],
                         [('them', '在吗', '王小姐'), ('me', '在的', '西行树'), ('them', '周三有空吗', '王小姐')])
        self.assertTrue(all(0.0 <= m.y < 1.0 and 0.0 < m.h < 1.0 and 0.0 <= m.x < 1.0 for m in msgs))
        self.assertLess(msgs[0].y, msgs[1].y)
        self.assertEqual(msgs[0].lines, ['在吗'])
        self.assertEqual(msgs[0].conf, 1.0)

    def test_image_only_message_is_skipped(self):
        ax = FakeAX()
        win = chat_window([message('', 'them', 300.0, image_only=True), message('好', 'me', 360.0)])
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['好'])

    def test_virtualized_and_offscreen_nodes_are_dropped(self):
        ax = FakeAX()
        rows = [message('滚出去的旧消息', 'them', 237.0, h=1.0),           # 高度 1：虚拟列表占位
                message('窗口外', 'them', 20.0),                          # y 在窗口顶边（143）之上
                message('可见', 'them', 420.0)]
        win = chat_window(rows)
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['可见'])

    def test_node_overlapping_editor_is_dropped(self):
        ax = FakeAX()
        win = chat_window([message('草稿回显', 'me', 800.0), message('正文', 'them', 420.0)])
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win))
        self.assertEqual([m.text for m in msgs], ['正文'])

    def test_keeps_only_newest_max_messages(self):
        ax = FakeAX()
        win = chat_window([message(f'第{i}条', 'them', 250.0 + i * 30.0) for i in range(15)])   # 最后一条 670+38 仍在编辑器（y≥753）之上
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win), max_messages=12)
        self.assertEqual(len(msgs), 12)
        self.assertEqual(msgs[0].text, '第3条')
        self.assertEqual(msgs[-1].text, '第14条')

    def test_multiple_static_texts_join_in_x_order(self):
        ax = FakeAX()
        row = message('', 'them', 300.0)
        content = row[1].children[0].children[0]
        content.children = [N('AXStaticText', value=' 复活吧', rect=(560.0, 301.0, 40.0, 16.0)),
                            N('AXStaticText', value='@缓缓', rect=(401.0, 301.0, 60.0, 16.0))]
        win = chat_window([row])
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win))
        self.assertEqual(msgs[0].text, '@缓缓 复活吧')

    def test_fingerprint_tracks_title_and_messages(self):
        ax = FakeAX()
        win = chat_window([message('在吗', 'them', 300.0)])
        msgs = qq.extract_messages(ax, win, qq.find_editor(ax, win))
        a = qq.fingerprint('王小姐', msgs)
        self.assertEqual(a, qq.fingerprint('王小姐', msgs))
        self.assertNotEqual(a, qq.fingerprint('李经理', msgs))
        win2 = chat_window([message('在吗', 'them', 300.0), message('在', 'me', 360.0)])
        self.assertNotEqual(a, qq.fingerprint('王小姐', qq.extract_messages(ax, win2, qq.find_editor(ax, win2))))


if __name__ == '__main__':
    unittest.main()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -B -m unittest tests.test_qq_adapter -v`
Expected: 全部 ERROR，`AttributeError: module 'apps.qq' has no attribute 'find_editor'`

- [ ] **Step 3: 重写 `src/apps/qq.py`（解析部分）**

用下面内容整体替换文件：

```python
"""QQ 适配器：通过系统无障碍（AX）树读 QQNT（Electron）聊天窗口，不截图、不 OCR。

实测事实（2026-09-23，QQ 6.9.96）见 docs/superpowers/specs/2026-09-23-qq-adapter-design.md
第 2 节：Electron 在设 AXManualAccessibility 后暴露完整树并附带 AXDOMClassList；消息容器
class 含 msg-content-container，我方另含 container--self；输入框是 class 含
ExEditor-qq-msg-editor 的 AXTextArea，其 AXDescription 是未截断的联系人名。

纯读：唯一写动作是「填入」（Task 6），AX 设值优先、后备为键盘事件；不发送、不用剪贴板。
解析函数全部经由一个读取器对象访问 AX（AXReader），测试用内存树替换。
"""
from __future__ import annotations

import hashlib

import ApplicationServices as AS

import fill
from perception import Message

KEY = "qq"
DISPLAY_NAME = "QQ"
BUNDLE_IDS = ("com.tencent.qq",)
APP_NAMES = ("QQ",)

EDITOR_CLASS = "ExEditor-qq-msg-editor"
MSG_CLASS = "msg-content-container"
SELF_CLASS = "container--self"
AVATAR_CLASS = "avatar-span"
MAX_NODES = 3000        # 一次遍历的节点上限：一个繁忙群聊每条可见消息约 8 个节点
MAX_MESSAGES = 12


class AXReader:
    """AX 属性读取的最小接口；测试用同名方法的内存树替换（tests/test_qq_adapter.py）。"""

    def _str(self, el, name) -> str:
        v = fill._ax_attr(el, name)
        return v if isinstance(v, str) else ""

    def role(self, el) -> str:
        return self._str(el, AS.kAXRoleAttribute)

    def desc(self, el) -> str:
        return self._str(el, AS.kAXDescriptionAttribute)

    def value(self, el) -> str:
        return self._str(el, AS.kAXValueAttribute)

    def title(self, el) -> str:
        return self._str(el, AS.kAXTitleAttribute)

    def classes(self, el) -> tuple[str, ...]:
        v = fill._ax_attr(el, "AXDOMClassList")
        try:
            return tuple(str(c) for c in (v or ()))
        except TypeError:
            return ()

    def rect(self, el):
        return fill._ax_rect(el)

    def children(self, el) -> list:
        v = fill._ax_attr(el, AS.kAXChildrenAttribute)
        try:
            return list(v or [])
        except TypeError:
            return []

    def windows(self, app_el) -> list:
        v = fill._ax_attr(app_el, AS.kAXWindowsAttribute)
        try:
            return list(v or [])
        except TypeError:
            return []

    def focused_window(self, app_el):
        return fill._ax_attr(app_el, AS.kAXFocusedWindowAttribute)


# ------------------------------------------------------------------ 纯解析

def walk(ax, root, limit: int = MAX_NODES):
    """有界深度优先遍历。DFS 顺序保证同一行里头像节点先于正文容器出现。"""
    stack = [root]
    seen = 0
    while stack and seen < limit:
        el = stack.pop()
        seen += 1
        yield el
        stack.extend(reversed(ax.children(el)))


def find_editor(ax, window):
    """聊天窗口的消息编辑器，或 None（会话列表窗口没有）。"""
    for el in walk(ax, window):
        if ax.role(el) == "AXTextArea" and EDITOR_CLASS in ax.classes(el):
            return el
    return None


def chat_title(ax, window, editor) -> str:
    """编辑器描述是未截断的联系人名；窗口标题（会截断成「…」）只做兜底。"""
    title = ax.desc(editor).strip() if editor is not None else ""
    return title or ax.title(window).strip()


def _intersects(a, b) -> bool:
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    return ax0 < bx0 + bw and bx0 < ax0 + aw and ay0 < by0 + bh and by0 < ay0 + ah


def _inside(inner, outer, slack: float = 3.0) -> bool:
    x, y, w, h = inner
    ox, oy, ow, oh = outer
    return (x >= ox - slack and y >= oy - slack
            and x + w <= ox + ow + slack and y + h <= oy + oh + slack)


def _texts(ax, container) -> str:
    """容器里全部 AXStaticText 按 x 顺序拼接；纯图 / 表情包没有文字，返回空串。"""
    parts = []
    for el in walk(ax, container, limit=200):
        if ax.role(el) == "AXStaticText":
            t = ax.value(el)
            if t.strip():
                r = ax.rect(el)
                parts.append((r[0] if r else 0.0, t))
    parts.sort(key=lambda p: p[0])
    return "".join(t for _, t in parts).strip()


def extract_messages(ax, window, editor, max_messages: int = MAX_MESSAGES) -> list[Message]:
    """窗口子树 → 有序消息列表（底部最新）。坐标按窗口归一化、顶部原点，与 OCR 路径口径一致。"""
    win_rect = ax.rect(window)
    if win_rect is None:
        return []
    ed_rect = ax.rect(editor) if editor is not None else None
    wx, wy, ww, wh = win_rect
    out: list[Message] = []
    sender = None
    for el in walk(ax, window):
        cls = ax.classes(el)
        if AVATAR_CLASS in cls:
            sender = ax.desc(el).strip() or None
            continue
        if MSG_CLASS not in cls:
            continue
        this_sender, sender = sender, None
        r = ax.rect(el)
        if (r is None or r[3] <= 1 or not _inside(r, win_rect)
                or (ed_rect is not None and _intersects(r, ed_rect))):
            continue
        text = _texts(ax, el)
        if not text:
            continue
        x, y, w, h = r
        ny = (y - wy) / wh
        out.append(Message(text=text, side="me" if SELF_CLASS in cls else "them",
                           y=ny, conf=1.0, h=h / wh, sender=this_sender, lines=[text],
                           x=(x - wx) / ww, w=w / ww, last_y=ny))
    out.sort(key=lambda m: m.y)
    return out[-max_messages:]


def fingerprint(title: str, msgs: list[Message]) -> bytes:
    """(标题, [(方向, 正文)…]) 的摘要，替代像素指纹做 unchanged 短路。"""
    h = hashlib.sha1(title.encode("utf-8"))
    for m in msgs:
        h.update(b"\n" + m.side.encode("ascii") + b"\t" + m.text.encode("utf-8"))
    return h.digest()


class QQApp:
    key = KEY
    display_name = DISPLAY_NAME
    bundle_ids = BUNDLE_IDS
    app_names = APP_NAMES
    needs_screen_capture = False
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -B -m unittest tests.test_qq_adapter tests.test_registry -v`
Expected: `ParseTests` 9 个 `ok`，`test_registry` 仍全绿

- [ ] **Step 5: Commit**

```bash
git add src/apps/qq.py tests/test_qq_adapter.py
git commit -m "feat(apps): QQ 无障碍树解析——编辑器定位、标题、消息方向与指纹

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 5: QQ 窗口选择、`read_conversation`、CLI 自测

**Files:**
- Modify: `src/apps/qq.py`（追加 I/O 部分）
- Modify: `tests/test_qq_adapter.py`（追加窗口选择与 read_conversation 测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_qq_adapter.py` 的 `if __name__` 之前追加：

```python
class WindowTests(unittest.TestCase):
    def test_list_window_rejected_chat_window_chosen(self):
        chat = chat_window([])
        ax = FakeAX(windows=[list_window(), chat], focused=None)
        win, ed = qq.chat_window(ax, 'app')
        self.assertIs(win, chat)
        self.assertIsNotNone(ed)

    def test_focused_chat_window_wins_over_larger_one(self):
        small = chat_window([]); small.rect = (0.0, 0.0, 800.0, 600.0)
        big = chat_window([])
        ax = FakeAX(windows=[big, small], focused=small)
        self.assertIs(qq.chat_window(ax, 'app')[0], small)

    def test_without_focus_largest_wins(self):
        small = chat_window([]); small.rect = (0.0, 0.0, 800.0, 600.0)
        big = chat_window([])
        ax = FakeAX(windows=[small, big], focused=list_window())
        self.assertIs(qq.chat_window(ax, 'app')[0], big)

    def test_no_chat_window(self):
        ax = FakeAX(windows=[list_window()])
        self.assertEqual(qq.chat_window(ax, 'app'), (None, None))

    def test_window_id_lookup_matches_pid_and_bounds(self):
        cg = [{'kCGWindowOwnerPID': 5, 'kCGWindowNumber': 42,
               'kCGWindowBounds': {'X': 311, 'Y': 143, 'Width': 1106, 'Height': 782}},
              {'kCGWindowOwnerPID': 5, 'kCGWindowNumber': 7,
               'kCGWindowBounds': {'X': 599, 'Y': 260, 'Width': 369, 'Height': 580}}]
        with patch('Quartz.CGWindowListCopyWindowInfo', return_value=cg):
            self.assertEqual(qq.window_id(5, WIN), 42)
            self.assertEqual(qq.window_id(6, WIN), 0)
            self.assertEqual(qq.window_id(5, (0, 0, 10, 10)), 0)


class ReadConversationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.object(qq.fill, 'has_accessibility', return_value=True))
        self.enterContext(patch.object(qq, 'qq_app', return_value=_FakeRunningApp(5)))
        self.enterContext(patch.object(qq, 'app_element', return_value='app-el'))
        self.enterContext(patch.object(qq, 'window_id', return_value=42))

    def test_reads_messages_title_window_and_input_rect(self):
        win = chat_window([message('在吗', 'them', 300.0), message('在', 'me', 360.0)])
        ax = FakeAX(windows=[list_window(), win], focused=win)
        res = qq.read_conversation(ax=ax)
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
        first = qq.read_conversation(ax=ax)
        again = qq.read_conversation(ax=ax, prev_fingerprint=first['fingerprint'], prev_layout=first['layout'])
        self.assertTrue(again['ok']); self.assertTrue(again['unchanged'])
        self.assertEqual(again['messages'], [])
        self.assertEqual(again['window'], first['window'])
        resized = qq.read_conversation(ax=ax, prev_fingerprint=first['fingerprint'], prev_layout=(42, 900.0, 782.0))
        self.assertFalse(resized['unchanged'])

    def test_no_chat_window_error(self):
        ax = FakeAX(windows=[list_window()])
        res = qq.read_conversation(ax=ax)
        self.assertEqual((res['ok'], res['error'], res['messages']), (False, qq.ERR_NO_WINDOW, []))

    def test_empty_tree_error_and_flag_reset(self):
        ax = FakeAX(windows=[])
        with patch.object(qq, 'app_element') as ae:
            res = qq.read_conversation(ax=ax)
            self.assertEqual((res['ok'], res['error']), (False, qq.ERR_EMPTY_TREE))
            ae.assert_any_call(5, force=True)

    def test_no_accessibility(self):
        with patch.object(qq.fill, 'has_accessibility', return_value=False):
            res = qq.read_conversation(ax=FakeAX())
            self.assertEqual((res['ok'], res['error']), (False, qq.fill.REASON_NO_ACCESS))

    def test_no_qq_process(self):
        with patch.object(qq, 'qq_app', return_value=None):
            res = qq.read_conversation(ax=FakeAX())
            self.assertEqual((res['ok'], res['error']), (False, qq.ERR_NO_APP))


class _FakeRunningApp:
    def __init__(self, pid): self._pid = pid
    def processIdentifier(self): return self._pid
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -B -m unittest tests.test_qq_adapter -v`
Expected: `WindowTests` / `ReadConversationTests` ERROR，`AttributeError: module 'apps.qq' has no attribute 'chat_window'`

- [ ] **Step 3: 追加 I/O 实现**

在 `src/apps/qq.py` 里：顶部 import 增加 `import time`、`import AppKit`、`import Quartz`，`from perception import Message` 改为 `from perception import Message, WindowInfo`；常量区追加：

```python
ERR_NO_APP = "没找到 QQ 应用"
ERR_NO_WINDOW = "QQ 聊天窗口未找到"
ERR_EMPTY_TREE = "QQ 无障碍树为空，请重启 QQ 后重试"
```

在 `fingerprint()` 之后、`class QQApp` 之前插入：

```python
# ------------------------------------------------------------------ 进程与窗口

def qq_app():
    """运行中的 QQ：bundle id 优先，显示名兜底；没有则 None。"""
    try:
        apps = AppKit.NSRunningApplication.runningApplicationsWithBundleIdentifier_(BUNDLE_IDS[0])
        if apps and len(apps) > 0:
            return apps[0]
    except Exception:
        pass
    try:
        for app in AppKit.NSWorkspace.sharedWorkspace().runningApplications():
            if (app.localizedName() or "") in APP_NAMES:
                return app
    except Exception:
        pass
    return None


_enabled_pids: set[int] = set()


def app_element(pid: int, force: bool = False):
    """QQ 进程的 AX 根元素。Electron 只有在设过 AXManualAccessibility 后才建完整树，
    每个 pid 设一次；树为空时调用方传 force=True 重设（不重启任何进程）。"""
    el = AS.AXUIElementCreateApplication(pid)
    if force or pid not in _enabled_pids:
        try:
            AS.AXUIElementSetAttributeValue(el, "AXManualAccessibility", True)
        except Exception:
            pass
        _enabled_pids.add(pid)
    return el


def chat_window(ax, app_el):
    """带消息编辑器的窗口：焦点窗口优先，否则面积最大。返回 (window, editor) 或 (None, None)。"""
    cands = []
    for w in ax.windows(app_el):
        ed = find_editor(ax, w)
        if ed is not None:
            cands.append((w, ed))
    if not cands:
        return None, None
    focused = ax.focused_window(app_el)
    if focused is not None:
        for w, ed in cands:
            if w == focused:
                return w, ed

    def area(pair):
        r = ax.rect(pair[0])
        return r[2] * r[3] if r else 0.0
    return max(cands, key=area)


def window_id(pid: int, rect) -> int:
    """按 pid + 几何在 CGWindowList 反查窗口 id；找不到返回 0，不阻断读消息。"""
    if rect is None:
        return 0
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    try:
        wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID) or []
    except Exception:
        return 0
    for wi in wins:
        if int(wi.get("kCGWindowOwnerPID") or 0) != pid:
            continue
        b = dict(wi.get("kCGWindowBounds") or {})
        cand = (float(b.get("X", 0)), float(b.get("Y", 0)),
                float(b.get("Width", 0)), float(b.get("Height", 0)))
        if fill._same_rect(cand, tuple(rect)):
            return int(wi.get("kCGWindowNumber") or 0)
    return 0


def find_window(previous_wid=None, ax=None) -> WindowInfo | None:
    """当前 QQ 聊天窗口（previous_wid 只为接口对齐：焦点窗口优先于粘住旧窗口）。"""
    ax = ax or AXReader()
    app = qq_app()
    if app is None:
        return None
    pid = app.processIdentifier()
    win, editor = chat_window(ax, app_element(pid))
    if win is None:
        return None
    r = ax.rect(win)
    if r is None:
        return None
    return WindowInfo(wid=window_id(pid, r), pid=pid, title=chat_title(ax, win, editor),
                      x=r[0], y=r[1], w=r[2], h=r[3])


def read_conversation(max_messages: int = MAX_MESSAGES, previous_wid=None,
                      prev_fingerprint=None, prev_layout=None, ax=None) -> dict:
    """一次读取：找窗口 → 遍历 AX 树 → 消息。返回结构与 perception.read_conversation 同构。"""
    t0 = time.perf_counter()
    ax = ax or AXReader()
    if not fill.has_accessibility():
        return {"ok": False, "error": fill.REASON_NO_ACCESS, "messages": []}
    app = qq_app()
    if app is None:
        return {"ok": False, "error": ERR_NO_APP, "messages": []}
    pid = app.processIdentifier()
    app_el = app_element(pid)
    if not ax.windows(app_el):
        app_element(pid, force=True)      # 下一跳再试；不自动重启进程
        return {"ok": False, "error": ERR_EMPTY_TREE, "messages": []}
    win, editor = chat_window(ax, app_el)
    r = ax.rect(win) if win is not None else None
    if win is None or r is None:
        return {"ok": False, "error": ERR_NO_WINDOW, "messages": []}
    wid = window_id(pid, r)
    title = chat_title(ax, win, editor)
    window = {"wid": wid, "title": title, "x": r[0], "y": r[1], "w": r[2], "h": r[3]}
    ed_rect = ax.rect(editor)
    input_rect = tuple(ed_rect) if ed_rect else None
    layout = (wid, r[2], r[3])
    msgs = extract_messages(ax, win, editor, max_messages=max_messages)
    fp = fingerprint(title, msgs)
    total = (time.perf_counter() - t0) * 1000
    timing = {"capture": total, "ocr": 0.0, "total": total, "capture_path": "ax"}
    base = {"ok": True, "layout": layout, "input_rect": input_rect, "input_unresolved": False,
            "chat_title": title, "window": window, "fingerprint": fp, "timing_ms": timing}
    if layout == prev_layout and fp == prev_fingerprint:
        return dict(base, unchanged=True, messages=[], n_blocks=0)
    return dict(base, unchanged=False, messages=msgs, n_blocks=len(msgs))
```

在文件末尾（`class QQApp` 之后）追加：

```python
    def find_window(self, previous_wid=None):
        return find_window(previous_wid)

    def read_conversation(self, **kwargs):
        return read_conversation(**kwargs)

    def warm(self):
        return None      # AX 路径没有一次性加载


if __name__ == "__main__":
    res = read_conversation()
    if not res["ok"]:
        print("ERROR:", res["error"])
        raise SystemExit(1)
    w = res["window"]
    print(f"window wid={w['wid']} {w['w']:.0f}x{w['h']:.0f} title={res['chat_title']!r} "
          f"ax={res['timing_ms']['total']:.0f}ms n={res['n_blocks']}")
    print("--- messages (top to bottom) ---")
    for m in res["messages"]:
        print(f"  [{m.side:4s}] y={m.y:.3f} sender={m.sender!r} | {m.text}")
```

（`QQApp` 类体保持 `key / display_name / bundle_ids / app_names / needs_screen_capture` 五个字段在前，方法在后。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -B -m unittest tests.test_qq_adapter -v`
Expected: `ParseTests` 9 + `WindowTests` 5 + `ReadConversationTests` 6 全部 `ok`

- [ ] **Step 5: 真机 CLI 自测（QQ 聊天窗口在屏幕上）**

Run: `uv run python src/apps/qq.py`
Expected: 一行 `window wid=<非 0> 1106x782 title='<完整联系人名>' ax=<几十>ms n=<条数>`，随后每条消息 `[them]` / `[me  ]` 与正文一致。若 `wid=0`，检查 QQ 窗口是否在屏幕上（CGWindowList 只查在屏窗口）。

- [ ] **Step 6: Commit**

```bash
git add src/apps/qq.py tests/test_qq_adapter.py
git commit -m "feat(apps): QQ 窗口选择、read_conversation 与 CLI 自测

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 6: QQ 输入框定位与「填入」

**Files:**
- Modify: `src/apps/qq.py`（追加 `locate_input` / `fill_text`）
- Modify: `tests/test_qq_adapter.py`（追加填入测试）

- [ ] **Step 1: 追加失败测试**

在 `tests/test_qq_adapter.py` 的 `_FakeRunningApp` 之后、`if __name__` 之前追加：

```python
class FillTests(unittest.TestCase):
    def setUp(self):
        self.win = chat_window([])
        self.editor = qq.find_editor(FakeAX(), self.win)
        self.ax = FakeAX(windows=[self.win], focused=self.win)
        self.enterContext(patch.object(qq.fill, 'has_accessibility', return_value=True))
        self.enterContext(patch.object(qq, 'qq_app', return_value=_FakeRunningApp(5)))
        self.enterContext(patch.object(qq, 'app_element', return_value='app-el'))
        self.enterContext(patch.object(qq, '_LAST_FILL', None))
        self.window = {'wid': 42, 'x': 311.0, 'y': 143.0, 'w': 1106.0, 'h': 782.0}

    def test_locate_input_returns_editor_rect(self):
        t = qq.locate_input(self.window, ax=self.ax)
        self.assertIs(t['box'], self.editor)
        self.assertEqual(t['rect'], (311.0, 753.0, 1098.0, 169.0))
        self.assertEqual(t['reason'], '填入目标')

    def test_locate_input_without_permission_does_not_traverse(self):
        with patch.object(qq.fill, 'has_accessibility', return_value=False), \
             patch.object(qq, 'chat_window') as cw:
            t = qq.locate_input(self.window, ax=self.ax)
            self.assertIsNone(t['box']); self.assertEqual(t['reason'], qq.fill.REASON_NO_ACCESS)
            cw.assert_not_called()

    def test_locate_input_rejects_editor_outside_window(self):
        t = qq.locate_input(dict(self.window, x=0.0, y=0.0, w=100.0, h=100.0), ax=self.ax)
        self.assertIsNone(t['box']); self.assertIn('不在当前 QQ 窗口内', t['reason'])

    def _fill(self, text, set_ok=True, landed=None, typed=(False, '')):
        """桩掉 AX 设值：设值「生效」后编辑器读回 landed；不碰真实 AX。"""
        target = qq.locate_input(self.window, ax=self.ax)

        def fake_set(box, value):
            if landed is not None:
                box.value = landed
            return set_ok

        with patch.object(qq.fill, '_ax_set_value', side_effect=fake_set) as setter, \
             patch.object(qq, '_type_text', return_value=typed) as typer:
            result = qq.fill_text(text, target=target, ax=self.ax)
        return result, setter, typer

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
        target = qq.locate_input(self.window, ax=self.ax)
        other = editor(); other.rect = (311.0, 700.0, 1098.0, 222.0)
        self.win.children[0].children[-1].children = [other]
        with patch.object(qq.fill, '_ax_set_value') as setter:
            ok, reason = qq.fill_text('你好', target=target, ax=self.ax)
        self.assertFalse(ok); self.assertIn('目标已变化', reason); setter.assert_not_called()

    def test_double_click_is_ignored(self):
        self._fill('你好', landed='你好')
        (ok, reason), setter, _ = self._fill('你好', landed='你好')
        self.assertFalse(ok); self.assertEqual(reason, qq.fill.REASON_DUPLICATE); setter.assert_not_called()

    def test_empty_text_and_missing_target(self):
        self.assertEqual(qq.fill_text('  ', target=None, ax=self.ax), (False, qq.fill.REASON_EMPTY))
        self.assertEqual(qq.fill_text('x', target=None, ax=self.ax), (False, qq.REASON_NO_INPUT))

    def test_typing_refuses_existing_draft(self):
        self.editor.value = '草稿'
        ok, reason = qq._type_text('你好', self.editor, _FakeRunningApp(5), self.ax)
        self.assertFalse(ok); self.assertEqual(reason, qq.REASON_DRAFT)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python -B -m unittest tests.test_qq_adapter.FillTests -v`
Expected: 全部 ERROR，`AttributeError: module 'apps.qq' has no attribute 'locate_input'`

- [ ] **Step 3: 追加填入实现**

`src/apps/qq.py` 顶部 import 增加 `import threading`、`import unicodedata`；常量区追加：

```python
REASON_NO_INPUT = "未取得可用的 QQ 输入控件"
REASON_DRAFT = "输入区已有草稿；请使用复制手动插入，避免改动现有内容"
```

在 `read_conversation()` 之后、`class QQApp` 之前插入：

```python
# ------------------------------------------------------------------ 填入（唯一写动作）

def locate_input(win: dict, ax=None) -> dict:
    """只读定位：编辑器 AXTextArea 及其屏幕矩形。结构与 fill.locate_input 相同。"""
    result = {"box": None, "rect": None, "window": win, "reason": REASON_NO_INPUT}
    if not fill.has_accessibility():
        result["reason"] = fill.REASON_NO_ACCESS
        return result
    ax = ax or AXReader()
    app = qq_app()
    if app is None:
        result["reason"] = ERR_NO_APP
        return result
    _win, editor = chat_window(ax, app_element(app.processIdentifier()))
    if editor is None:
        return result
    rect = ax.rect(editor)
    if rect is None:
        result["reason"] = "输入控件坐标不可读取"
        return result
    bounds = tuple(float(win[k]) for k in ("x", "y", "w", "h"))
    if not _inside(rect, bounds):
        result["reason"] = "输入控件不在当前 QQ 窗口内"
        return result
    result.update(box=editor, rect=tuple(rect), reason="填入目标")
    return result


_FILL_LOCK = threading.Lock()
_LAST_FILL: tuple[str, str, float] | None = None   # (text, editor content after fill, ts)


def _norm(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKC", s or "") if not c.isspace())


def _landed(current: str, text: str) -> bool:
    return bool(current) and _norm(text) in _norm(current)


def fill_text(text: str, target=None, ax=None) -> tuple[bool, str]:
    """把候选写进 QQ 输入框：AX 设值优先并读回校验；ProseMirror 拒收时退到键盘事件。
    不发送、不用剪贴板、失败不自动重试。"""
    global _LAST_FILL
    text = (text or "").strip()
    if not text:
        return False, fill.REASON_EMPTY
    if not _FILL_LOCK.acquire(blocking=False):
        return False, fill.REASON_BUSY
    try:
        if not fill.has_accessibility():
            return False, fill.REASON_NO_ACCESS
        ax = ax or AXReader()
        app = qq_app()
        if app is None:
            return False, ERR_NO_APP
        if target is None or target.get("box") is None:
            return False, REASON_NO_INPUT
        fresh = locate_input(target["window"], ax=ax)
        editor = fresh["box"]
        if (editor is None or editor != target["box"]
                or not fill._same_rect(fresh["rect"], target["rect"])):
            return False, "输入目标已变化，请等检测框更新后重试"
        current = ax.value(editor)
        if fill._duplicate_blocked(text, current, _LAST_FILL, time.monotonic()):
            return False, fill.REASON_DUPLICATE
        base = current if current.strip() else ""     # 空编辑器读出 "\n"，不能当前缀
        if fill._ax_set_value(editor, base + text):
            landed = ax.value(editor)
            if _landed(landed, text):
                _LAST_FILL = (text, landed, time.monotonic())
                return True, "已填入"
        ok, reason = _type_text(text, editor, app, ax)
        if ok:
            _LAST_FILL = (text, ax.value(editor), time.monotonic())
        return ok, reason
    finally:
        _FILL_LOCK.release()


def _type_text(text: str, editor, app, ax) -> tuple[bool, str]:
    """键盘事件后备：AX 置焦编辑器、激活 QQ、按 20 字一段发 Unicode 键入事件，再读回校验。
    已有草稿时停止（不覆盖、不追加），永远不按回车。"""
    if ax.value(editor).strip():
        return False, REASON_DRAFT
    try:
        AS.AXUIElementSetAttributeValue(editor, AS.kAXFocusedAttribute, True)
    except Exception:
        pass
    app.activateWithOptions_(AppKit.NSApplicationActivateIgnoringOtherApps)
    time.sleep(0.15)
    front = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
    if front is None or front.processIdentifier() != app.processIdentifier():
        return False, "QQ 没有获得焦点，请先点 QQ 输入区再重试"
    if not fill._ax_attr(editor, AS.kAXFocusedAttribute):
        return False, "输入框未获得焦点，请先点 QQ 输入区再重试"
    plain = text.replace("\n", " ").replace("\t", " ")
    for offset in range(0, len(plain), 20):
        chunk = plain[offset:offset + 20]
        for down in (True, False):
            ev = Quartz.CGEventCreateKeyboardEvent(None, 0, down)
            Quartz.CGEventSetFlags(ev, 0)
            Quartz.CGEventKeyboardSetUnicodeString(ev, len(chunk.encode("utf-16-le")) // 2, chunk)
            Quartz.CGEventPost(Quartz.kCGHIDEventTap, ev)
        time.sleep(0.03)
    time.sleep(0.25)
    if _landed(ax.value(editor), plain):
        return True, "已填入（键盘输入，未发送）"
    return False, "已尝试输入，未能确认；请检查草稿，勿重复点击"
```

`QQApp` 类追加两个方法（放在 `read_conversation` 与 `warm` 之间）：

```python
    def locate_input(self, win):
        return locate_input(win)

    def fill_text(self, text, target=None):
        return fill_text(text, target=target)
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python -B -m unittest tests.test_qq_adapter -v`
Expected: `FillTests` 10 个 `ok`，其余不变

- [ ] **Step 5: 跑全套离线回归**

Run: `uv run python -B -m unittest discover -s tests`
Expected: 末行 `OK`（原有测试尚未受 HUD 改动影响）

- [ ] **Step 6: Commit**

```bash
git add src/apps/qq.py tests/test_qq_adapter.py
git commit -m "feat(apps): QQ 输入框定位与填入——AX 设值优先，键盘事件后备

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 7: HUD 按前台 App 分发

**Files:**
- Modify: `src/hud.py`（import、init、`_set_foreground_state`、`tick_`、`_work_inner`、`fillCandidate_`、`_warm`、`_refresh_model_status`、`_reply_current`、`applyReplyUpdate_`、`applyWarmFailed_`、`applyPosition_`、`applyBoxes_`、文案）
- Modify: `tests/support_hud.py`、`tests/test_hud_reply.py`、`tests/test_download_progress.py`

- [ ] **Step 1: 先改测试夹具（先红）**

`tests/support_hud.py`：把 `scope = {...}` 里的 `'frontmost_app_is_wechat': Mock(return_value=True),` 一行删掉，并把 `'fill': SimpleNamespace(locate_input=...)` 与 `'read_conversation': Mock(),` 改成下面这样（其余键不动）：

```python
    from apps.registry import UNKNOWN
    locate = Mock(return_value={'box': None, 'rect': None, 'reason': 'test'})
    read_conv = Mock()
    fake_app = SimpleNamespace(key='wechat', display_name='微信', needs_screen_capture=True,
                               read_conversation=read_conv, locate_input=locate,
                               fill_text=Mock(return_value=(True, '已填入')), warm=Mock(return_value=0.0))
    scope = {'fill': SimpleNamespace(locate_input=locate, has_accessibility=Mock(return_value=True),
                                     request_accessibility=Mock()),
             'time': time, 'threading': threading, '_log': lambda *_: None,
             'frontmost_app': Mock(return_value=fake_app), 'FAKE_APP': fake_app, 'UNKNOWN': UNKNOWN,
             'APPS': (fake_app,),
             'screen_capture_ok': Mock(return_value=True), 'request_screen_capture': Mock(),
             'read_conversation': read_conv,
             'PALETTE': {'muted': None}, 'CONTEXT_TURNS': 8, 'JUDGE_TURNS': 4,
             'SLOW_TICK': 1, 'BURST_TICK': .45, 'FAST_TICK': .25, 'BURST_READS': 3,
             'READ_FAILURE_HIDE_S': 2,
             'SETTLE_S': 1.2, 'STABLE_READS': 3, 'EARLY_SETTLE_S': .7, 'MIN_GAP_S': 2}
```

`tests/test_hud_reply.py` 用 sed 批量替换：

```bash
cd ~/Desktop/jev-chat-qq/jev-chat-jarvis-mac
sed -i '' \
  -e "s/HUD\['frontmost_app_is_wechat'\]\.return_value = True/HUD['frontmost_app'].return_value = HUD['FAKE_APP']/" \
  -e "s/HUD\['frontmost_app_is_wechat'\]\.return_value = False/HUD['frontmost_app'].return_value = None/" \
  -e "s/HUD\['frontmost_app_is_wechat'\]\.return_value = None/HUD['frontmost_app'].return_value = HUD['UNKNOWN']/" \
  -e "s/HUD\['frontmost_app_is_wechat'\]/HUD['frontmost_app']/g" \
  -e "s/_wechat_frontmost=None, _foreground_epoch=0,/_app=None, _asked_accessibility=False, _foreground_epoch=0,/" \
  -e "s/self\.h\._wechat_frontmost = False/self.h._app = None/" \
  -e "s/self\.h\._wechat_frontmost = True/self.h._app = HUD['FAKE_APP']/" \
  -e "s/states = iter(\[True, False\])/states = iter([HUD['FAKE_APP'], None])/" \
  -e "s/self\.h\._set_foreground_state(False)/self.h._set_foreground_state(None)/" \
  -e "s/self\.h\._set_foreground_state(True)/self.h._set_foreground_state(HUD['FAKE_APP'])/" \
  -e "s/self\.assertEqual(self\.h\._reply_key, ('李经理', '下午开会'))/self.assertEqual(self.h._reply_key, ('wechat', '李经理', '下午开会'))/" \
  tests/test_hud_reply.py
grep -n "_wechat_frontmost\|frontmost_app_is_wechat" tests/test_hud_reply.py
```

Expected: 最后的 grep 无输出（全部替换干净）。

`tests/test_download_progress.py`：把 `'frontmost_app_is_wechat': lambda: None,` 改为 `'frontmost_app': lambda: UNKNOWN, 'UNKNOWN': UNKNOWN,`，并在该文件顶部 `sys.path` 插入之后加一行 `from apps.registry import UNKNOWN`；把 `h._wechat_frontmost = None` 改为 `h._app = None`。注释「None 表示检查不了」改为「UNKNOWN 表示检查不了」。

- [ ] **Step 2: 跑 HUD 测试确认失败**

Run: `uv run python -B -m unittest tests.test_hud_reply tests.test_download_progress -v`
Expected: 多个 ERROR，`NameError: name 'frontmost_app' is not defined` / `AttributeError: ... '_wechat_frontmost'`

- [ ] **Step 3: 改 `src/hud.py`：import 与状态**

第 70-72 行的 perception import 改为：

```python
from perception import screen_capture_ok, request_screen_capture  # noqa: E402
from apps.registry import APPS, UNKNOWN, frontmost_app  # noqa: E402  按前台 App 分发（微信 / QQ）
```

第 99 行：`IDLE_STATUS = "等待微信消息…"` → `IDLE_STATUS = "等待微信 / QQ 消息…"`。

第 266-267 行：

```python
        self._win_wid = None          # sticky chat window id (per app)
        self._app = None              # 当前前台聊天应用适配器；None = 不在任何聊天应用前台
        self._asked_accessibility = False   # QQ 路径的辅助功能授权只弹一次
```

第 659 行：`"jev-jarvis · 微信意图助手"` → `"jev-jarvis · 微信 / QQ 意图助手"`。

- [ ] **Step 4: 改 `_set_foreground_state`（第 1296-1329 行）**

把方法开头与结尾改成：

```python
    def _set_foreground_state(self, app):
        """Apply one hard lifecycle boundary when a chat app gains/loses focus or changes.

        `app` is an adapter, None (some other app is frontmost — a real leave), or UNKNOWN
        (the query failed — not evidence of anything, so nothing changes).
        """
        if app is UNKNOWN or app is self._app:
            return False

        self._app = app
        ...（中间的重置语句原样保留）...

        if app is not None:
            self._next_read_ts = 0
            _log(f"前台切换 · {app.display_name}回到前台，强制重新读屏")
        else:
            _log("前台切换 · 聊天应用离开前台，隐藏面板并清空旧结果")
            self._push("applyForegroundHidden:", "微信 / QQ 不在前台")
        return True
```

- [ ] **Step 5: 改 `tick_` 与 `_work_inner`**

`tick_`（第 1337-1342 行）：

```python
        app = frontmost_app()
        if app is UNKNOWN:
            return
        self._set_foreground_state(app)
        if app is None:
            return
```

`_work_inner` 开头到 `read_conversation` 调用（第 1361-1384 行）改为：

```python
        app = frontmost_app()
        if app is UNKNOWN:
            # A transient NSWorkspace failure is not proof that the user left the chat app.
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
            # QQ reads the accessibility tree: without the grant there is nothing to read.
            if not self._asked_accessibility:
                self._asked_accessibility = True
                fill.request_accessibility()
            self._push("applyError:", "需要辅助功能权限 · 系统设置 › 隐私与安全性")
            self._next_read_ts = time.time() + SLOW_TICK
            return
        capture_foreground_epoch = self._foreground_epoch
        try:
            res = app.read_conversation(previous_wid=self._win_wid,
                                        prev_fingerprint=self._fingerprint,
                                        prev_layout=getattr(self, "_layout_key", None))
```

紧接着的抓图后复核（原 1388-1398 行）改为：

```python
        after = frontmost_app()
        if after is UNKNOWN:
            self._next_read_ts = time.time() + FAST_TICK
            return
        self._set_foreground_state(after)
        if after is not app or capture_foreground_epoch != self._foreground_epoch:
            self._next_read_ts = time.time() + FAST_TICK
            return
```

原 1469 行 `self._input_target = fill.locate_input(res["window"])` → `self._input_target = app.locate_input(res["window"])`。

原 1485 行 `key = (res.get("chat_title") or "", newest.text) if newest else None` → `key = (app.key, res.get("chat_title") or "", newest.text) if newest else None`。

- [ ] **Step 6: 其余 `_wechat_frontmost` 引用与填入、预热**

```bash
cd ~/Desktop/jev-chat-qq/jev-chat-jarvis-mac
sed -i '' \
  -e 's/self\._wechat_frontmost is not True/self._app is None/g' \
  -e 's/self\._wechat_frontmost is True/self._app is not None/g' \
  src/hud.py
grep -n '_wechat_frontmost' src/hud.py
```

Expected: grep 无输出。

`fillCandidate_`（原 1025-1047 行）：docstring 改为 `"""Write the candidate into the current chat app's input box (via self._app)."""`；`if not fill.has_accessibility():` 之前插入：

```python
        app = self._app
        if app is None:
            self._render("status", "填入失败：微信 / QQ 不在前台", PALETTE["red"])
            return
```

`ok, reason = fill.fill_text(text, target=target)` → `ok, reason = app.fill_text(text, target=target)`。

`_warm`（原 2161-2166 行）把

```python
        ocr_ms = warm_ocr()
        if ocr_ms >= 0:
            self._read_once = True    # Vision's one-off load is paid; first read is steady-state
            _log(f"预热 OCR 就绪 · {ocr_ms:.0f}ms")
        else:
            _log("预热 OCR 失败 · 首次读屏会稍慢，不影响使用")
```

改为：

```python
        for app in APPS:
            ms = app.warm()
            if ms is None:
                continue                  # QQ：AX 路径没有一次性加载
            if ms >= 0:
                self._read_once = True    # Vision's one-off load is paid; first read is steady-state
                _log(f"预热 {app.display_name} 读屏就绪 · {ms:.0f}ms")
            else:
                _log(f"预热 {app.display_name} 读屏失败 · 首次读屏会稍慢，不影响使用")
```

- [ ] **Step 7: 跑全套离线回归**

Run: `uv run python -B -m unittest discover -s tests`
Expected: 末行 `OK`。若某个 HUD 测试仍红，按错误信息对照上面各步（最常见：sed 漏替换某行，或 `_asked_accessibility` 未在 setUp 里初始化）。

- [ ] **Step 8: 真机验证（QQ 与微信各一轮）**

Run: `./start.command`（终端需已授予辅助功能 + 屏幕录制）

1. QQ 聊天窗口置前台，让对方发一条消息 → 面板出现意图 / 风险 / 候选 → 点「填入」→ 文字进入 QQ 输入框，未发送。日志 `tail -40 ~/Library/Logs/jev-jarvis.log` 里应有「前台切换 · QQ回到前台」。
2. 切到微信 → 日志「前台切换 · 微信回到前台」，同一轮流程照常。
3. 切到 Chrome → 面板隐藏，日志「聊天应用离开前台」。

Expected: 三步均如上；否则先看日志分阶段耗时定位到读屏 / 判断 / 生成哪一层。

- [ ] **Step 9: Commit**

```bash
git add src/hud.py tests/support_hud.py tests/test_hud_reply.py tests/test_download_progress.py
git commit -m "feat(hud): 按前台 App 分发到适配器，微信 / QQ 共用一套面板

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
```

---

### Task 8: 文档与描述同步

**Files:**
- Modify: `README.md`、`AGENTS.md`、`pyproject.toml`

- [ ] **Step 1: README**

1. 第 1 行标题下的引导句「微信弹出一条消息 → …」改为「微信或 QQ 弹出一条消息 → 悬浮窗立刻告诉你**这句话的真实意图**、**风险几级**、**该怎么回**。」
2. 「它能做什么」之前新增一节：

```markdown
## 平台支持

| 平台 | 状态 | 采集方式 | 需要的权限 | 备注 |
|---|---|---|---|---|
| 微信 macOS 4.x | ✅ | 窗口截图 + Vision OCR | 屏幕录制（读）+ 辅助功能（填入） | 布局常量按微信 4.1 校准 |
| QQ macOS 6.9.x（QQNT） | ✅ | 系统无障碍树直接读结构化节点 | 辅助功能（读 + 填入） | 不截图、不 OCR；我方 / 对方按节点 class 判定 |

两者共用同一套判断、生成与悬浮窗；悬浮窗跟随当前在前台的那个应用。
```

3. 「用法」里权限段落末尾追加一句：「**只用 QQ** 的话不需要屏幕录制权限，辅助功能一项即可。」
4. 「已知限制」列表末尾追加三条：

```markdown
- QQ：只支持独立聊天窗口，紧凑模式（效率模式）的迷你聊天窗不支持；同时开多个聊天窗口时只分析有焦点的那个
- QQ：图片、表情包、文件等无文字消息读不出内容（与微信 OCR 一致）；引用回复按普通文本处理
- QQ 改版若变更界面 class 名（`container--self` / `ExEditor-qq-msg-editor`），`src/apps/qq.py` 顶部常量需同步；`uv run python probe/qq_ax_probe.py` 可直接看当前真实取值
```

5. 「开发者」的分层自测代码块里追加一行 `uv run python src/apps/qq.py                    # QQ 感知层：AX 读到的消息（CLI 里可验）`；「架构一句话」改为「微信在前台时，进程内抓其窗口 → Vision OCR（只扫聊天区）；QQ 在前台时，读其无障碍树 → 同一条管线：本地 decider-2b 出意图/风险 → LLM 并发出候选 → 本地排序 → 悬浮窗 NSPanel。」

- [ ] **Step 2: AGENTS.md**

首段「微信悬浮窗助手（macOS）：OCR 读微信窗口 → …」改为「微信 / QQ 悬浮窗助手（macOS）：微信走 OCR 读窗口、QQ 走系统无障碍树 → 本地模型判意图/风险 → LLM 生成候选回复 → 悬浮窗展示/一键填入。」

「目录与命令」第一条在 `src/perception.py 抓图+OCR+抽消息；` 之后插入：`src/apps/ 聊天 App 适配器层（base.py 协议、wechat.py 转调 perception/fill、qq.py 无障碍树读 QQNT、registry.py 按前台 App 分发）；`。分层自测代码块追加 `uv run python src/apps/qq.py  # QQ 感知层（AX 路径，CLI 里可验）`。

「已知的坑」追加一条：`- **QQ 走 AX 不走 OCR**：CLI 进程里能直接验；改 QQ 解析先跑 probe/qq_ax_probe.py 看真实 class 名，再改 src/apps/qq.py 顶部常量。填入后备是键盘事件，同样不许改回剪贴板。`

- [ ] **Step 3: pyproject.toml**

`description = "微信消息意图识别悬浮窗：本地判断意图与风险，生成回复候选（macOS）"` → `description = "微信 / QQ 消息意图识别悬浮窗：本地判断意图与风险，生成回复候选（macOS）"`。

- [ ] **Step 4: 最终回归与提交**

Run: `uv run python -B -m unittest discover -s tests`
Expected: 末行 `OK`

```bash
git add README.md AGENTS.md pyproject.toml
git commit -m "docs: 平台支持表与 QQ 权限 / 限制说明，AGENTS 补 apps 适配器层

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
git log --oneline upstream/master..HEAD
```

Expected: 8 个提交（设计文档 + 7 个任务）。推送 `git push -u origin feat/qq-adapter` 前先向用户确认。
