<div align="center">

# chat-nojev

**同一个对话副驾，把两次模型调用并成一次：判断和候选回复出自同一个模型、同一次请求，你只配一把 key。**

[![许可](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF-MIT-blue?style=flat-square)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%201903%2B%20%2F%2011-0078D4?style=flat-square&logo=windows&logoColor=white)](#windows)
[![Android](https://img.shields.io/badge/Android-11%2B-3DDC84?style=flat-square&logo=android&logoColor=white)](#android)
[![macOS](https://img.shields.io/badge/macOS-13%2B-000000?style=flat-square&logo=apple&logoColor=white)](#macos)
[![差分测试](https://img.shields.io/badge/%E5%B7%AE%E5%88%86%E6%B5%8B%E8%AF%95-47%20%E4%B8%AA%E5%9C%BA%E6%99%AF%20%C2%B7%200%20%E5%A4%B1%E8%B4%A5-brightgreen?style=flat-square)](#与上游的等价)
[![模型调用](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B%E8%B0%83%E7%94%A8-2%E2%80%933%20%E6%AC%A1%20%E2%86%92%201%20%E6%AC%A1-1f6feb?style=flat-square)](#它把什么并成了一次)

<!-- 构建徽章放这里：等 .github/workflows 落地后再加。现在没有工作流，不挂一个必然是红的徽章。 -->

[它改了什么](#它把什么并成了一次) · [快速开始](#快速开始) · [差分测试](#与上游的等价) · [Windows 变体](windows/) · [Android 变体](android/) · [macOS 变体](macos/) · [上游](https://github.com/jev-chat) · [NOTICE](NOTICE)

</div>

## 它把什么并成了一次

上游的链路是两段：生成模型起草 3 条候选回复，专门的判断模型（TypeSafe Jev）回答对方的真实意图、紧张度、该不该马上回；macOS 上还要再来一次，让判断模型给候选打分排序。两路接口、两把 key。

这一版只发一次请求。同一个模型写候选、答那几道判断题、给每条候选打分，一个 JSON 回来。题面原文、选项集合、0–9 的紧张度档位都是从上游原样搬过来的，**换的只是产出方**；悬浮窗读到的字段、类型和渲染那段代码一行没动。

<div align="center">

<img src="docs/images/flow.png" width="860" alt="上游两到三次调用、这一版一次调用，送到面板的是同一批字段" />

</div>

## 为什么这么改

- **少一把 key。** 上游要判断和起草两路接口各配一把；这里只有一把，配置页也就少了一整节。
- **少一次把整段对话发出去。** 判断调用要把同一段对话再发一遍。现在发一次。
- **判断题一个字没改。** 7 道题（macOS 是它自己的 2 道）、选项 key 和描述、0–9 的档位都照搬上游，仍然框着这道题该怎么答。
- **下游代码没动，而且有东西盯着。** 每个变体都带一套差分测试，把同一批场景同时喂给上游那棵树和这一棵，逐字段比对处理结果；每套都自带负控——故意改坏一处、必须变红。
- **上游的边界原样保留。** 只读你自己屏幕上的对话，不 hook、不改包、不解密数据库，只把回复填进输入框，发送键永远你自己按。这几条是上游的设计，这一版没有碰。
- **代价是明写的。** `confidence` / `probabilities` 现在是模型的自评而不是校准过的概率，macOS 的离线判断被删掉了。都在[合并掉了什么](#合并掉了什么)。

## 三个变体

三份代码不是同一个东西的三次移植：Windows 和 Android 问的是同一套 7 道判断题，macOS 问的是它自己的两道（8 类意图 + 0–9 风险），候选按话术分组生成。所以「合并」这件事在三处的做法和代价都不一样。

| 变体 | 来自 | 采集方式 | 上游的调用 | 这一版 | 配置 | 差分结果 |
|---|---|---|---|---|---|---|
| [`windows/`](windows/) | [jev-chat-windows](https://github.com/jev-chat/jev-chat-windows) | 微信 Windows 4.x 窗口截图 + 本地离线 OCR | 起草 1 + 判断 1 | **1 次** | 一把 key | 15 个场景全一致 |
| [`android/`](android/) | [jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis)（最早的那一版） | 无障碍读气泡节点，飞书等走 ML Kit 离线 OCR | 判断 1 + 起草 1 + 排序 1（分属两路接口） | **1 次** | 「模型接口」一把 key | 16 个场景全一致 |
| [`macos/`](macos/) | [jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac) | 抓微信窗口 + Vision OCR | 判断 1 + 排序 1 + 每个话术一次生成 | **每个话术一次生成** | 一把 key | 16 个场景 11 一致、5 处已记录分歧 |

各自的完整比对写在 [`windows/docs/EQUIVALENCE.md`](windows/docs/EQUIVALENCE.md)、[`android/docs/EQUIVALENCE.md`](android/docs/EQUIVALENCE.md)、[`macos/docs/EQUIVALENCE.md`](macos/docs/EQUIVALENCE.md)。

## 快速开始

**先说清楚现在是什么状态：三个变体都还没有成品可装。** Windows 没有打包好的 release，Android 没有 APK，macOS 没有 `.app`——这台机器上没有 Android SDK、没有 Mac、也没有能显示 PySide6 窗口的桌面，三个 App 一个都没有在真机上跑起来过。跑过并且反复跑的是下面那三套差分测试。要用只能从源码跑。

### Windows

```bash
git clone https://github.com/chy4pro/chat-nojev.git
cd chat-nojev/windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

要求 Windows 10 1903+ / 11、微信 Windows 4.x。首次启动弹设置页，「模型」卡片只有一节（上游是判断、起草两节）：填一把 OpenAI 兼容端点的 key，选你们的关系，保存。key 进 Windows 用户环境变量 `LLM_API_KEY`（注册表 `HKCU\Environment`），不落文件；其余设置写 `config.json`。

打包是上游的 `build.bat` / `pyinstaller --noconfirm --clean jev.spec`，本仓库没有跑过，也没有出过 zip。

### Android

```bash
cd android
./gradlew assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
```

JDK 17 + Android SDK（platform 35 / build-tools 35）。**本仓库不发 APK**，`apk/` 目录也没有从上游复制过来；上游 Releases 里签好名的包是上游那一版，不含这里的改动。

装上之后：设置 →「接口」只有两张卡（模型 / 视觉），填「模型接口」一把 key 就能用，视觉留空自动继承它。权限还是上游那三项——无障碍、悬浮窗、自启动 + 省电无限制。

### macOS

```bash
cd macos
./start.command
```

macOS 13+，微信在运行，终端已授予「屏幕录制」；「填入」另需「辅助功能」。配置是一个 env 文件，只有一把 key：

```bash
mkdir -p ~/.config/jev-jarvis
cat > ~/.config/jev-jarvis/env <<'ENV'
export OPENAI_API_KEY="sk-你的key"
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_MODEL="deepseek-chat"
ENV
chmod 600 ~/.config/jev-jarvis/env
```

不配 key 就什么都没有：上游那条「判断层不填 key 就走本地 decider-2b」的路在这一版被删掉了。`.app` 也没有打过，`packaging/build_app.sh` 是上游的脚本，这里没有 Mac 去跑它。

## 与上游的等价

### 标准

要求**不是**中间数据和上游逐字节一样，而是**同一段下游代码对它的处理结果一样**：`core/engine.py` + `app/overlay.py`（Windows）、`OverlayController.render()`（Android）、`src/hud.py`（macOS）从这一版的一次调用里得到的，必须和它们从上游的两三次调用里得到的一样。

这是个更弱的要求，也是对的那个。上游的下游代码本来就得扛住判断模型回垃圾，所以它在用的时候才校验：一个不在分类表里的标签本来就显示成标签本身，一个超出 0..9 的紧张度上游就是显示成「危险 11/9」。**适配层只做翻译，就翻译到这里为止**——再校验一遍不是保护，是改行为：把 11 夹回 9，显示的东西就跟上游不一样了。模型压根没给的值就留空，不替它编一个。

### 跑一遍

```bash
python tools/equivalence_check.py         --upstream /path/to/jev-chat-windows
python tools/equivalence_check_macos.py   --upstream /path/to/jev-chat-jarvis-mac
bash   tools/equivalence_check_android.sh --upstream /path/to/jev-chat-jarvis --probe /path/to/toolchain
```

每套都把场景写成一份中立数据，再渲染成两种线上格式——给上游的是判断接口的 `answers`，给这一版的是合并后的那个 JSON——两边各起一个进程跑真实代码，逐字段比对。上游只在它的起草和判断调用处打桩，这一版只在 HTTP 调用处打桩，所以我们的解析器和适配层是拿着模型原始字符串真跑的。

Android 那套要 JDK 和 kotlinc，**不要 Android SDK**：参与编译的文件除了三个 Android 类型（`Rect` / `Log` / `Context`）之外不碰框架，那三个由五十行手写桩顶上。

负控（故意改坏、必须变红）：

| 变体 | 负控 | 结果 |
|---|---|---|
| `windows/` | 把 `_REPLY_IDX` 反过来 | 15 个里 14 个红 |
| `android/` | `--negative-control keys`：reply 键顺序倒过来 | 16 个里 15 个红（剩下那个所有分都是 0，顺序本来就无所谓） |
| `android/` | `--negative-control clamp`：适配层把紧张度夹回 0..9 | `score_out_of_range` 红，其余不动 |
| `macos/` | 适配层把 risk 夹进 0..9 | `risk_outside_its_range` 红 |
| `macos/` | 组内排序键取反 | 16 个里 15 个红 |

### 一次真实运行

下面是 Android 那套在这个容器里跑出来的原始输出（上游树 = jev-chat-jarvis）。最后几行是重点：同一个场景，上游打三次接口，这边打一次。

```
--- 编译 upstream（/home/agent/upstream/jev-chat-jarvis）
--- 编译 ours（/work/chat-nojev/android）

--- 跑两边（各自一个 JVM）
[upstream] 写出 /tmp/tmp.dQTt3LMOPd/up.json
[ours] 写出 /tmp/tmp.dQTt3LMOPd/ours.json

场景                            结果   说明
----------------------------------------------------------------------------------------------
ordinary_three_candidates       PASS   普通情况：三条候选，赢家清楚
winner_not_top_scorer           PASS   点名的不是分最高的那条：两边都只按分排序
winner_is_reply_a               PASS   赢家是第一条：验键到下标的映射
winner_is_reply_c               PASS   赢家是第三条：验键到下标的映射
two_candidates_only             PASS   模型只给两条：两边都补足三条（占位文案相同）
boundary_values_low             PASS   边界值：danger_level 0，noul 全 0.0
boundary_values_high            PASS   边界值：danger_level 9，noul 全 1.0
ranking_block_missing           PASS   排序整块都没有：两边都全记 0、顺序不动
choice_names_missing_candidate  PASS   点名的候选不存在（只有两条却点 reply_c）
probabilities_not_normalized    PASS   分加起来不是 1：两边都原样递下去
score_out_of_range              PASS   紧张度 11（题目只有 0..9）：不夹范围，原样递下去
intent_outside_taxonomy         PASS   题目里没有的说法：不查表，原样递下去
judgment_field_absent           PASS   两边都缺同一个判断字段（best_action）
confidence_omitted              PASS   判断字段不给 confidence（判断模型一定给，通用模型常常漏）
noul_as_bare_number             PASS   我们这边 noul 写成裸数字 / true-false，必须等价于 {"noul": x}
score_legend_from_api           PASS   上游那边带 legend（接口就是这么回的），我们没有：档数必须一样
----------------------------------------------------------------------------------------------
共 16 个场景：16 完全一致，0 有已知分歧，0 失败

每个场景的接口调用（证明没打网络，也证明调用次数变了）:
    ordinary_three_candidates: 上游 ["判断接口","回复接口","判断接口"] / 我们 ["回复接口"]
    winner_not_top_scorer: 上游 ["判断接口","回复接口","判断接口"] / 我们 ["回复接口"]
    winner_is_reply_a: 上游 ["判断接口","回复接口","判断接口"] / 我们 ["回复接口"]

latencyMs 不参与比对（墙上时间，不是任何一边算出来的值）。
```

Windows 那套 15 个场景全一致（`usage` 不参与比对：上游报判断接口的 token 计数，这一版的 `core/llm.chat()` 只回文本）。macOS 那套 16 个里 11 个一致、5 个是下面这五处。

### macOS 的五处分歧

这五处是同一个决定的五次现身：**上游的判断层在把答案交出去的路上悄悄改过它，这一版不改。**

Windows 和 Android 的下游代码本来就硬，适配层可以原样递过去；macOS 的硬化写在判断适配器自己身上（`JevJudge.judge` 里的查表兜底、`float(... or 0.0)`、`ACTION_MAP` 查表），面板那边没有任何防护。所以要么适配层继续替模型做主，要么把防护挪到用的地方。**挪了**——`src/hud.py` 长出四个只管自己那一行的读取函数。

| 场景 | 上游 | 这一版 |
|---|---|---|
| `intent_outside_the_taxonomy` | 落回「闲聊」，`actions` 跟着查表 | 显示模型写的那句话，配它自己写的行动建议 |
| `intent_is_an_empty_string` | `"" or "闲聊"` → 闲聊 | 「暂未判断」；不替它编 |
| `risk_not_a_number` | `0.0` → 面板显示「● 安全 0/9」 | 原样递下去 → 「风险待判断」。模型没给的值，说「安全」比说「不知道」更糟 |
| `confidence_omitted` | `float(None or 0.0)` → 「意图识别率 0%」 | 键缺席，这一行留空 |
| `judgment_fails_entirely_generation_succeeds` | 排序跟判断一起死（同一个模型），每条候选的 `prob` 永远是 `None`，行上一直写着「排序中」 | 分数是跟候选一起回来的，判断挂了排序照样在 |

前四条是那个决定，第五条是合并顺手带来的好处。两边都不夹范围：上游对 11 分的风险显示「● 危险 11/9」，这一版显示一样的东西。

## 合并掉了什么

- **macOS 的离线判断没了。** `Mapika/decider-2b` 不要 key、不联网也能判意图和风险。它是第二个判断模型，按这个项目的主张就该走；但它同时也是一个真实功能，删掉它是产品上的损失，不只是少了一个模型。`macos/src/questions.py` 顶上记着完整的账，包括要恢复得做什么。
- **macOS 候选的百分比变成了「组内占比」。** 上游一次排序看得见所有话术的所有候选，百分比跨话术可比；现在每次调用只看得见自己写的那两条。面板实际用的排序没变（它本来就在组内排，`#1`/`#2` 一直都是「这两条里更好的那条」），变的是那个数字的含义。
- **判断不再抢在候选前面上屏。** 上游判断约 1 秒先把面板画出来，候选后到；现在它们是一个答案的两半，一起到。Android 那边同理：上游那张「判断已到、回复还在生成中」的半张面板不存在了。
- **一半成功一半失败不再可能。** 上游可以判断成了、起草挂了，面板显示判断加一行「回复接口出错」；现在一次调用要么全成要么全败，面板显示那一个错误。
- **`confidence` / `probabilities` 是模型的自评。** 同一个模型写了候选、又给自己打了分、还评了自己的把握度。它们不是分类器校准过的输出，下游任何地方都不该当成那种东西读。

## 常见问题

<details>
<summary><b>判断是不是变差了？</b></summary>

不知道，因为没有在真机上量过，这一版也不拿上游的数字充数。上游 macOS 那个「8 类意图 86.4%」是 `decider-2b` 在 22 条消息上测出来的（`judge_zh_test.py`，跟着模型一起删了），它说不了一个通用模型答同一道题会怎么样。差分测试保证的是「同样的值送到面板上是同一个结果」，不是「模型判得一样准」——后者要拿你自己的真实对话去标一批才知道。

</details>

<details>
<summary><b>它会替我发消息吗？</b></summary>

不会。程序只把选中的回复填进输入框，发送键永远你自己按，转账红包一律不碰。这是上游的设计，合并调用没有碰它。

</details>

<details>
<summary><b>还能离线用吗？</b></summary>

不能了，而且只有 macOS 曾经能。Windows 和 Android 本来就要联网（两路接口都在云上），macOS 那条「不填 key 走本地 decider-2b」的路在这一版被删掉了。介意的话用上游那一版。

</details>

<details>
<summary><b>那为什么不干脆把判断模型留着？</b></summary>

留着就是两把 key、两路配置、两次把整段对话发出去，而它答的那几道题通用模型也答得了——这个仓库就是去验证这句话成不成立，验证的方式是那三套差分测试，不是这段说明。不成立的地方都记在各自的 `docs/EQUIVALENCE.md` 里。

</details>

<details>
<summary><b>显示的字变了吗？</b></summary>

变了一处，是有意的。上游那几道选择题由分类器作答，只能从一组固定的英文 key 里挑，所以面板带着一张表把 key 翻成固定的中文。通用模型不需要这个封闭集合：题面现在要求它**用对话所用的语言**把选中的那一项说成一个短语，不合适就自己写一个，那几张翻译表删掉了，面板显示模型写的那句。英文 key 和描述没动，仍然框着题。后果是中文对话显示得跟上游很接近但不逐字相同，英文对话显示英文——现在没有任何地方把显示语言钉死。数值类的（紧张度、分数）完全不受影响。

</details>

## 目录

```
windows/   Windows 变体（PySide6 + 本地 OCR）
android/   Android 变体（Kotlin，无障碍采集）
macos/     macOS 变体（Python + PyObjC + Vision OCR）
tools/     三套差分测试和它们的桩
docs/      这份说明用到的图
NOTICE     上游署名，以及从上游继承下来的条款
```

## 上游与致谢

这是一份派生作品。它只改「判断由谁产出」这一件事，其余全部是上游的工作——采集、OCR、悬浮窗、填入、题面本身，一样都不是这里写的。三个上游都以 MIT 开源：

- **[jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis)**（Jev 聊天助手，Android，最早的那一版；判断题面和分类体系出自这里）—— Finderchangchang 与 jev-chat 贡献者，`android/` 的直接来源
- **[jev-chat-windows](https://github.com/jev-chat/jev-chat-windows)**（Jev 聊天助手 Windows 版）—— rezoch340 与 jev-chat 贡献者，`windows/` 的直接来源
- **[jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac)**（macOS 版）—— eatmoreduck，`macos/` 的直接来源

分发或商用时请保留 LICENSE 与 [NOTICE](NOTICE) 并写明来源。本仓库与原作者没有任何关系，也**不要**用「Jev 聊天助手」「jev-chat」这些名字或 chatjevs.com 域名暗示由他们出品或背书。上游的第三方组件条款（包括 Windows 打包件会受约束的 PySide6-Fluent-Widgets GPLv3）原样保留在 `windows/NOTICE`。

## 许可与免责

MIT，继承上游版权，见 [LICENSE](LICENSE)。

本项目只处理**你自己设备上、你自己有权查看的**聊天：不注入、不 hook、不解密数据库、不自动发送任何消息。请遵守微信等各软件的许可协议与当地法律法规，作者不对使用后果负责。聊天内容只在触发分析的那一刻发给你自己配置的模型接口，这里没有任何自建服务器。
