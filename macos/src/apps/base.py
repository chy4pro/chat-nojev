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
    display_name: str            # 进面板文案的显示名
    bundle_ids: tuple[str, ...]
    app_names: tuple[str, ...]
    needs_screen_capture: bool   # 微信 True（截图 + OCR），QQ False（只需辅助功能）

    def find_window(self, previous_wid: int | None = None) -> WindowInfo | None: ...

    def read_conversation(self, max_messages: int = 12, previous_wid=None,
                          prev_fingerprint=None, prev_layout=None) -> dict: ...

    def locate_input(self, win: dict) -> dict: ...

    def fill_text(self, text: str, target=None) -> tuple[bool, str]: ...

    def warm(self) -> float | None:
        """一次性预热耗时（ms）；负数表示预热失败；None 表示无需预热。"""
        ...
