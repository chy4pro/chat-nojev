"""截图路径适配器：薄封装，全部转调 perception / fill，不改任何识别逻辑。"""
from __future__ import annotations

import fill
import perception


class CaptureApp:
    key = "wechat"
    display_name = "聊天应用"
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
