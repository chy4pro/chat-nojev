#!/usr/bin/env python3
"""Capability probe: how can we read WeChat Mac's chat text?

Three candidate channels, in order of fidelity:
  A. Accessibility API (AXUIElement)  -> exact structured text, no OCR errors
  B. Screen capture + Vision OCR      -> works always, needs screen-recording permission
  C. (not probed) SQLite DB decrypt   -> out of scope, WeChat DB is SQLCipher-encrypted

This script reports what is actually available on THIS machine right now.
"""

from __future__ import annotations

import json
import sys
from collections import Counter

import Quartz
from ApplicationServices import (
    AXIsProcessTrusted,
    AXUIElementCopyAttributeValue,
    AXUIElementCreateApplication,
    kAXChildrenAttribute,
    kAXDescriptionAttribute,
    kAXRoleAttribute,
    kAXTitleAttribute,
    kAXValueAttribute,
    kAXWindowsAttribute,
)

TEXT_ROLES = {"AXStaticText", "AXTextField", "AXTextArea", "AXHeading"}
MAX_NODES = 8000
MAX_DEPTH = 40


def wechat_pids() -> list[int]:
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
    pids = []
    for w in wins:
        owner = w.get("kCGWindowOwnerName") or ""
        if "WeChat" in owner or "微信" in owner:
            pid = w.get("kCGWindowOwnerPID")
            if pid and pid not in pids:
                pids.append(pid)
    return pids


def list_windows() -> list[dict]:
    opts = Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
    rows = []
    for w in wins:
        owner = w.get("kCGWindowOwnerName") or ""
        if "WeChat" in owner or "微信" in owner or "wechat" in owner.lower():
            rows.append({
                "owner": owner,
                "pid": w.get("kCGWindowOwnerPID"),
                "wid": w.get("kCGWindowNumber"),
                "title": w.get("kCGWindowName"),
                "bounds": dict(w.get("kCGWindowBounds") or {}),
                "layer": w.get("kCGWindowLayer"),
            })
    return rows


def walk_ax(el, depth: int, acc: dict, budget: list[int]) -> None:
    """Collect role + text from an AX subtree, with hard caps."""
    if depth > MAX_DEPTH or budget[0] <= 0:
        return
    budget[0] -= 1

    def attr(name):
        err, val = AXUIElementCopyAttributeValue(el, name, None)
        return val if err == 0 else None

    role = attr(kAXRoleAttribute) or ""
    acc["roles"][role] += 1
    if role in TEXT_ROLES or role in ("AXButton", "AXLink"):
        for key, name in ((kAXValueAttribute, "value"), (kAXTitleAttribute, "title"),
                          (kAXDescriptionAttribute, "desc")):
            v = attr(name)
            if isinstance(v, str) and v.strip():
                acc["texts"].append((role.replace("AX", ""), v.strip()))
                break

    children = attr(kAXChildrenAttribute)
    if children:
        for ch in children:
            walk_ax(ch, depth + 1, acc, budget)


def probe_ax(pid: int) -> dict:
    app = AXUIElementCreateApplication(pid)
    err, windows = AXUIElementCopyAttributeValue(app, kAXWindowsAttribute, None)
    if err != 0 or not windows:
        return {"ok": False, "reason": f"kAXWindows err={err}"}
    out = []
    for wi, win in enumerate(windows[:4]):
        acc = {"roles": Counter(), "texts": []}
        walk_ax(win, 0, acc, [MAX_NODES])
        out.append({
            "window_index": wi,
            "roles": dict(acc["roles"].most_common(12)),
            "n_text_nodes": len(acc["texts"]),
            "sample_texts": acc["texts"][:25],
        })
    return {"ok": True, "windows": out}


def main() -> None:
    report: dict = {}
    report["accessibility_trusted"] = bool(AXIsProcessTrusted())
    try:
        report["screen_capture_allowed"] = bool(Quartz.CGPreflightScreenCaptureAccess())
    except Exception as e:  # older macOS: assume permitted-if-prompted
        report["screen_capture_allowed"] = f"unknown ({e})"

    report["wechat_windows"] = list_windows()
    report["wechat_pids"] = wechat_pids()

    if report["wechat_pids"] and report["accessibility_trusted"]:
        try:
            report["ax_probe"] = probe_ax(report["wechat_pids"][0])
        except Exception as e:
            report["ax_probe"] = {"ok": False, "reason": f"{type(e).__name__}: {e}"}
    elif not report["accessibility_trusted"]:
        report["ax_probe"] = {"ok": False, "reason": "no accessibility permission"}

    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    sys.exit(main())
