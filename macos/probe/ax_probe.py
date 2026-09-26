#!/usr/bin/env python3
"""QQ AX 探测：打印 QQNT 聊天窗口无障碍树里的消息容器、DOM class、方向与输入框。

用法（QQ 聊天窗口在屏幕上时）：
    uv run python probe/ax_probe.py            # 只读：窗口、输入框、消息
    uv run python probe/ax_probe.py --fill 你好  # 另外试一次 AX 设值并读回（输入框须为空）

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


def walk(el, depth=0, limit=None):
    if limit is None:                 # 不能用可变默认值：上一轮的计数会带进下一轮
        limit = [0]
    limit[0] += 1
    if limit[0] > 6000 or depth > 60:
        return
    yield el, depth
    for c in attr(el, AS.kAXChildrenAttribute) or []:
        yield from walk(c, depth + 1, limit)


def fill_arg(argv):
    """(--fill 的文本, 用法是否正确)；--fill 不带参数时第二个值为 False。"""
    if "--fill" not in argv:
        return None, True
    i = argv.index("--fill")
    if i + 1 >= len(argv):
        return None, False
    return argv[i + 1], True


def main():
    fill_text, usage_ok = fill_arg(sys.argv)
    if not usage_ok:
        print("用法: ax_probe.py [--fill 文本]  （--fill 需要跟一个文本参数）")
        return 2
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
