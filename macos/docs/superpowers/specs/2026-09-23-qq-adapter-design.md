# QQ 适配器设计：App 适配器层 + QQ 走无障碍树

日期：2026-09-23
分支：`feat/qq-adapter`（fork：`emptylower/jev-chat-jarvis-mac`，上游：`jev-chat/jev-chat-jarvis-mac`）
状态：设计已确认，待写实现计划

## 1. 目标与范围

在保持微信路径零改动的前提下，让 macOS 版同时支持 **微信** 与 **QQ（QQNT 6.9.x，Electron 内核）**。HUD 按前台 App 分发到对应适配器；微信继续走「窗口截图 + Vision OCR」，QQ 走「系统无障碍（AX）树直接读结构化节点」。

**做：**

- 新增 `src/apps/` 适配器层，微信逻辑原样封装，QQ 新写 AX 实现。
- `hud.py` 从「只认微信」改为「按前台 App 分发」，App 切换视为一次前台边界。
- QQ 的读消息、找输入框、一键填入，全部经 AX 完成；填入后备路径只用键盘事件。
- 离线单测覆盖 QQ 解析与分发；README / AGENTS.md / `pyproject.toml` 同步。

**不做（YAGNI）：**

- QQ 紧凑模式（效率模式）迷你聊天窗；只支持独立聊天窗口。
- 图片、表情包、文件等非文字消息的内容识别（微信 OCR 同样读不出，行为一致）。
- 多个 QQ 聊天窗口同时分析；只分析焦点窗口。
- 飞书、钉钉等其他 App。
- 向上游提 PR（做完后可选）。

## 2. 探测结论（2026-09-23，本机 QQ 6.9.96 / macOS 26.5 / Apple Silicon）

设计建立在以下实测事实上，实现前用 `probe/qq_ax_probe.py` 复核：

| 事实 | 证据 |
|---|---|
| QQ bundle id 为 `com.tencent.qq`，进程名 `QQ` | `NSRunningApplication` |
| 对 QQ 进程设 `AXManualAccessibility=True` 后，AX 树完整暴露，含 `AXDOMClassList` | 273 / 238 节点全树遍历 |
| 聊天窗口的输入框是 `AXTextArea`，DOM class 含 `ProseMirror`、`ExEditor-qq-msg-editor`，`AXDescription` 为**未截断**的完整联系人名，空时 `AXValue` 为 `"\n"` | 底部四点 hit-test 全部命中同一节点 |
| 窗口标题是**截断**的联系人名（如「很难约的王小…」） | CGWindowList / AXTitle |
| 消息容器 DOM class 含 `msg-content-container`；我方消息另含 `container--self` | 7 条我方消息全部命中 |
| 正文是 `message-content` 下的 `AXStaticText`；表情包为 `AXImage`（class `market-face-element`），无文字 | 同上 |
| 头像节点 class `avatar-span`，`AXDescription` 为发送者昵称 | 同上 |
| 时间分隔符是独立 `AXStaticText`（如「星期一 17:43」），x 居中 | 同上 |
| 虚拟列表滚出可视区的消息节点仍在树中，但高度为 1 | 同上 |
| 只开紧凑模式时，AX 只暴露 369×580 的会话列表窗口；点开聊天后多出一个带编辑器的窗口 | 两次探测对比 |

**复核结论（2026-09-23 实测）：** 对方消息 class 为 `container--others`（解析只依赖「不含 `container--self`」，实测值仅作参考）；单聊消息区没有昵称文本节点，发送者名字来自 `avatar-span` 的 `AXDescription`，消息区裸文本只有时间分隔符（父节点 class 含 `message__timestamp` / `no-copy`），不在 `message-content` 内，解析时不会混入正文；AX 对 ProseMirror 编辑器设值 **不生效**（`err=0` 但读回仍为 `"\n"`），`fill_text` 落到键盘后备。

## 3. 架构

### 3.1 适配器接口 `src/apps/base.py`

```python
class ChatApp(Protocol):
    key: str                    # "wechat" | "qq"，进日志、进回复缓存 key
    display_name: str           # "微信" | "QQ"，进面板文案
    bundle_ids: tuple[str, ...]
    app_names: tuple[str, ...]
    needs_screen_capture: bool  # 微信 True，QQ False

    def find_window(self, previous_wid: int | None) -> WindowInfo | None: ...
    def read_conversation(self, max_messages: int, previous_wid, prev_fingerprint, prev_layout) -> dict: ...
    def locate_input(self, win: dict) -> dict: ...
    def fill_text(self, text: str, target=None) -> tuple[bool, str]: ...
    def warm(self) -> None: ...
```

`read_conversation` 返回字典与现有 `perception.read_conversation` **同构**：`ok`、`error`、`unchanged`、`messages`（`perception.Message` 列表，side 为 `me` / `them` / `unknown`）、`chat_title`、`window`（wid/title/x/y/w/h）、`input_rect`、`input_unresolved`、`fingerprint`、`layout`、`timing_ms`、`n_blocks`。HUD 现有取值点不改。

`WindowInfo`、`Message`、`TextBlock` 继续定义在 `perception.py`，`apps/` 只引用不搬动。

### 3.2 微信适配器 `src/apps/wechat.py`

薄封装：`find_window` → `perception.find_wechat_window`，`read_conversation` → `perception.read_conversation`，`locate_input` / `fill_text` → `fill.*`，`warm` → `perception.warm_ocr`。`perception.py`、`fill.py`、`visual_fill.py`、`input_region.py` 不改识别逻辑。

### 3.3 QQ 适配器 `src/apps/qq.py`

- **识别**：bundle `com.tencent.qq` 或名字 `QQ`。首次接触某个 pid 时设一次 `AXManualAccessibility`；pid 变化重设。
- **找窗口**：遍历 QQ 进程 `kAXWindowsAttribute`，只保留子树含 class `ExEditor-qq-msg-editor` 的 `AXTextArea` 的窗口。优先 `kAXFocusedWindowAttribute`，否则面积最大者。用 pid + 位置 + 尺寸在 `CGWindowListCopyWindowInfo` 中反查 wid，供 HUD 停靠与 layout 键使用。反查失败时 wid 取 0，不阻断读消息。
- **会话标题**：编辑器 `AXDescription`；为空时回退窗口 `AXTitle`。
- **消息抽取**：在窗口子树中收集 class 含 `msg-content-container` 的节点（遍历剪枝：子树整体滚出屏幕——高度 ≤ 1 的虚拟占位或在窗口矩形外——不下探；命中一个 `msg-content-container` 后不再下探其子树，嵌套容器是引用回复，不重复产出）。
  - side：class 含 `container--self` → `me`；否则 → `them`。（此处不产生 `unknown`。）
  - text：其下 `message-content` 内全部 `AXStaticText` 的 `AXValue` 按 DOM（DFS）顺序拼接，去首尾空白（折行段落 x 不单调，按 x 排序会打乱）；为空（纯图/表情）则跳过。
  - sender：同一消息行内 class `avatar-span` 节点的 `AXDescription`；无则 `None`。
  - 过滤：高度 ≤ 1、超出窗口矩形、或与编辑器矩形相交的节点丢弃。
  - 坐标按窗口归一化写入 `Message.x/y/w/h`（顶部原点，与 `extract_messages` 输出口径一致），YOLO 检测框照常可画。
  - 按 y 升序，保留最后 `max_messages`（默认 12）条。
- **变化检测**：`fingerprint = sha1(chat_title + "\n".join(f"{side}\t{text}"))`；`layout = (wid, w, h)`。两者都与上次相同则返回 `unchanged=True`，语义与像素指纹路径一致。
- **输入框定位**：返回编辑器 `AXTextArea` 的屏幕矩形，与 `fill.locate_input` 同结构（`box`、`rect`、`window`、`reason`）。
- **填入**：先 `AXUIElementSetAttributeValue(kAXValueAttribute)` 并读回校验（复用 `fill.py` 的重复点击守卫与原因文案）。设值不生效时，后备为：AX 置焦编辑器 + 激活 QQ + CGEvent 逐字键入（复用 `visual_fill.write_text` 的按键逻辑）+ AX 读回校验。**不发送、不用剪贴板 / Cmd+V、已有草稿时停止并提示手动复制。**
- **性能**：每 tick 一次有界遍历（`MAX_NODES = 3000`），预期几十毫秒。
- **CLI 自测**：`uv run python src/apps/qq.py` 直接打印窗口、标题、消息与耗时（AX 路径不受 CLI 截图限制）。

### 3.4 注册与分发 `src/apps/registry.py`

```python
APPS: tuple[ChatApp, ...] = (WeChatApp(), QQApp())

def frontmost_app() -> ChatApp | None:
    """查一次 NSWorkspace.frontmostApplication，先按 bundle id 再按名字匹配；
    查询失败返回 UNKNOWN 哨兵（不是离开的证据）；返回 None 表示前台是别的 App。"""

def app_by_key(key: str) -> ChatApp | None: ...
```

### 3.5 HUD 接线 `src/hud.py`

- `frontmost_app_is_wechat()` 的三处调用改为 `registry.frontmost_app()`；新增 `self._app`（当前适配器，可为 None）。
- `_set_foreground_state(frontmost)` 的输入从 bool 改为「适配器或 None」：适配器对象变化（含 微信→QQ、QQ→微信、任一→None）即视为一次前台边界，沿用现有的隐藏面板、清空旧结果、`_foreground_epoch` 递增、强制重读。
- `read_conversation`、`fill.locate_input`、`fill.fill_text`、`warm_ocr` 调用改走 `self._app`。
- 屏幕录制权限检查只在 `self._app.needs_screen_capture` 为真时执行；QQ 路径改为检查 `fill.has_accessibility()`，缺失时提示「需要辅助功能权限 · 系统设置 › 隐私与安全性」并只请求一次。
- 回复缓存 key 从 `(chat_title, text)` 改为 `(app.key, chat_title, text)`。
- 面板文案中的「微信」改为 `app.display_name`：`IDLE_STATUS` 改为「等待微信 / QQ 消息…」，前台离开提示「微信 / QQ 不在前台」，其余按当前 App 动态拼接。菜单栏 tooltip 改为「jev-jarvis · 微信 / QQ 意图助手」。
- 轮询节奏、停稳窗口、最小分析间隔、预判/预生成、独立分析线程等**一律不动**（AGENTS.md 硬约束）。

## 4. 错误处理

| 场景 | 行为 |
|---|---|
| QQ 在前台但没有带编辑器的聊天窗口（只开紧凑列表） | `ok=False, error="QQ 聊天窗口未找到"`；HUD 走现有失败宽限 → 隐藏路径 |
| 未授予辅助功能权限（QQ） | 面板报「需要辅助功能权限 · 系统设置 › 隐私与安全性」，`fill.request_accessibility()` 只调一次 |
| 已设 `AXManualAccessibility` 但树为空 | `error="QQ 无障碍树为空，请重启 QQ 后重试"`；每 tick 重设标志，不自动重启进程 |
| 多个 QQ 聊天窗口 | 取焦点窗口；焦点变化改变 layout 键，自动重读 |
| 填入失败 | 沿用 `fill.py` 原因文案，「微信」替换为 App 名；不自动重试 |
| `NSWorkspace` 瞬时失败 | `frontmost_app()` 返回 `UNKNOWN` 哨兵时按现有「冻结一个短 tick」处理，不制造离开/返回事件；返回 None 表示前台是别的 App（真正的离开） |

## 5. 测试

**离线单测（`tests/`，CI 命令 `uv run python -B -m unittest discover -s tests` 不变）：**

- `tests/test_qq_adapter.py`：用伪造 AX 节点（role、description、value、DOM class、pos、size、children）构树，覆盖：我方/对方判定；纯图片消息跳过；高度 ≤ 1 节点丢弃；发送者取自头像；标题取编辑器描述并回退窗口标题；指纹相同 → `unchanged`、正文变化 → 指纹变化；拒绝无编辑器的会话列表窗口；超过 12 条只保留最新且按 y 排序。
- `tests/test_registry.py`：按 bundle id 命中、按名字命中、其他 App 返回 None、查询异常返回 None。
- 微信现有测试一行不改，必须全绿；`tests/test_hud_reply.py` 中涉及「微信」文案的断言改为 App 感知。

**真机验证（手工，证据看 `~/Library/Logs/jev-jarvis.log`，日志不含正文）：**

1. `uv run python probe/qq_ax_probe.py`：复核第 2 节三个待复核项。
2. `uv run python src/apps/qq.py`：QQ 聊天窗口在屏幕上时打印标题、消息方向与耗时。
3. `./start.command`，QQ 前台：消息出现 → 判断 → 候选上屏 → 一键填入（不发送）。
4. 切到微信：同一轮流程仍正常；切到 Chrome：面板隐藏。

## 6. 文件清单

**新增：** `src/apps/__init__.py`、`src/apps/base.py`、`src/apps/wechat.py`、`src/apps/qq.py`、`src/apps/registry.py`、`probe/qq_ax_probe.py`、`tests/test_qq_adapter.py`、`tests/test_registry.py`、本文档。

**修改：** `src/hud.py`（分发接线与文案）、`tests/test_hud_reply.py`（文案断言）、`README.md`（标题/描述泛化、平台支持表、QQ 权限说明、QQ 已知限制）、`AGENTS.md`（`src/apps/` 说明）、`pyproject.toml`（description）。

**不动：** `src/perception.py`、`src/fill.py`、`src/visual_fill.py`、`src/input_region.py`、`src/judge*.py`、`src/generate.py`、`src/styles.py`、`src/settings*.py`、`src/userconfig.py`。

## 7. 仓库与流程

- 工程目录 `~/Desktop/jev-chat-qq/jev-chat-jarvis-mac`，远程 `origin` 为 fork，`upstream` 为原仓库。
- 分支 `feat/qq-adapter`，commit 用 Conventional Commits + 中文描述（如 `feat(apps): …`）。
- 改动用户可见行为必须同步 README（上游约定）。
