# jev-chat-windows — 接续说明（OCR 版）

> 这是上游立项时写的说明，除下面标注的几处外保持原样。chat-nojev 改版把判断和起草合成了
> **一次**语言模型调用，因此少了一把 key、少了一个客户端文件；下文已按现状更正。

个人用的聊天回复辅助工具，挂在自己电脑的微信旁边：读到对方最新消息 → 判断意图/情绪 →
给出 3 条候选回复（已排序）→ 人一键**填入**微信输入框。**发送永远手动，程序不自动发。**

## 硬约束（照做，别破）

1. 只读**自己设备上、自己有权查看**的对话。
2. 采集只用**窗口级截图 + 本地离线 OCR**。不 hook、不注入、不读微信数据库、不解密、不碰微信进程。
3. **截图不落盘**：捕获得到的位图始终是内存里的对象（numpy/PIL），全程不写磁盘、不进日志、不上传。
4. **绝不自动发送**，不点发送按钮；填入输入框后停手。
5. **不碰钱**：转账、红包、收款相关界面元素一律不碰。
6. 全局只有一把 key：语言模型那把（`LLM_API_KEY`），只从环境变量 / 注册表读，任何文件不出现 key。
   （上游这里是两把，判断模型另有一把 `JEV_API_KEY`；判断合进同一次调用后不再需要。）
7. Python 读写文件一律 `encoding='utf-8'`。

## 为什么走 OCR（已实测的结论，别重测）

- 目标窗口界面自绘在一块 GPU 合成画布上（`MMUIRenderSubWindowHW`）。UIA 树只有 2 个节点、**没有控件树**——实测证伪。
- 所以唯一干净的非侵入采集路 = 截自己的微信窗口 + 本地 OCR。离线、零 token。

## 已经建好，直接用（`core/`，平台无关）

| 文件 | 作用 |
|---|---|
| `core/errors.py` | `JevError`、脱敏、读 key——`core/` 共用的管道（上游在 `jev_client.py` 里） |
| `core/questions.py` | 7 道判断题（唯一的家）+ `build_state()` + `render_judgment_spec()` |
| `core/draft.py` | 一次调用拿 3 条候选 + 判断 + 排序（默认 DeepSeek）。解析器已自测 |
| `core/engine.py` | **唯一入口** `analyze(messages, relationship)` → `{candidates, best_index, best_reply, answers, usage}` |
| `tools/demo.py` | 端到端冒烟（需 key + 联网）：`python tools/demo.py` |

`messages` 形如 `[("her","中文"),("me","中文")]`，`from` 只用 `her`/`me`，最新一条在最后。
引擎完全不关心消息怎么来的——OCR 把屏幕上的对话整理成这个 list 喂进 `analyze()` 即可。

## 待建（新会话干这些）

1. **`capture.py` — 窗口级截图到内存，不落盘**
   - 目标窗口按进程名和主窗口标题挑，不按面积。
   - 微信是 GPU 合成窗口，`PrintWindow` 容易黑屏 → **优先用 Windows Graphics Capture**
     （pip `windows-capture`，帧直接是 numpy，可捕获被遮挡/GPU 窗口）。
     兜底：`PrintWindow` 带 `PW_RENDERFULLCONTENT=2`；再兜底：区域抓屏（需窗口可见）。
   - 返回内存位图，**绝不 `.save()`**。
2. **`ocr.py` — 内存内 OCR + 分说话人**
   - RapidOCR（`rapidocr-onnxruntime`）直接吃 numpy 数组，不落文件。
   - 裁到聊天气泡区；按每段文字**框的 x 中心**分左右：右=me、左=her（可加气泡背景色过滤头像/时间戳/系统提示）。
   - **diff**：跟上一帧比，只在冒出**新的 her 消息**时才触发下游（省 token，也避免重复分析）。
   - `probe/probe_ocr.py` 里已有 x 阈值分左右的草稿，可参考。
3. **`overlay.py` — 半透明置顶悬浮窗**：显示判断摘要 + 3 条候选（★ 推荐），每条一个「填入」按钮。
   tkinter（`-topmost`/`-alpha`，stdlib）够用；要更顺再上 PySide6。
4. **`fill.py` — 填入不发送**：把选中的候选写剪贴板 → 聚焦微信输入框 → 粘贴。**绝不发回车/点发送。**
5. **`main.py` — 主循环**：capture → ocr → 检测新 her 消息 → `engine.analyze()` → overlay。
   静默期不调模型（10 分钟无新消息 = 0 次调用）。

## 技术坑备忘

- GPU 窗口截图黑屏 → 用 Windows Graphics Capture，别用普通 BitBlt/PrintWindow。
- 图片全程内存对象（numpy/PIL），OCR 引擎吃数组不吃路径，天然不落盘。
- 中文 OCR：RapidOCR 够用；不够准就换 PaddleOCR 或只 OCR 裁剪后的聊天区。
- 群聊按一对一分析会不准，先只做单聊。

## 参考项目

安卓原版 `Finderchangchang/jev-chat-JARVIS`（判断走 TypeSafe Jev，采集是安卓无障碍）。
题目口径跟 `core/questions.py` 一致；这一版换了产出方，题目原文没动。
