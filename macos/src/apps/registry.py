"""按前台 App 分发到适配器。

frontmost_app() 三种返回值必须区分：某个适配器（在聊天 App 里）、None（在别的 App 里，
是一次真正的「离开」）、UNKNOWN（NSWorkspace 查询失败，不是离开的证据——HUD 对它冻结一个
短 tick，与原来 frontmost_app_is_wechat() 返回 None 的处理一致）。
"""
from __future__ import annotations

from apps.ax_app import AXApp
from apps.capture_app import CaptureApp

UNKNOWN = object()          # 前台查询失败的哨兵；`is` 比较
APPS = (CaptureApp(), AXApp())


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
    """先按 bundle id、再按显示名精确匹配——名字相近的兄弟应用不算。"""
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
