<div align="center">

# chat-nojev

**[jev-chat](https://github.com/jev-chat) 三个版本（Windows / Android / macOS）的派生，只改了一件事：
判断和排序不再交给专门的判断模型，而是由写候选回复的那个模型在同一次调用里一并给出。
于是只要配一把 key、只发一次请求，其余是原样的原版代码，连同它的 bug。**

[![许可](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF-MIT-blue?style=flat-square)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%201903%2B%20%2F%2011-0078D4?style=flat-square&logo=windows&logoColor=white)](#windows)
[![Android](https://img.shields.io/badge/Android-11%2B-3DDC84?style=flat-square&logo=android&logoColor=white)](#android)
[![macOS](https://img.shields.io/badge/macOS-13%2B-000000?style=flat-square&logo=apple&logoColor=white)](#macos)
[![真机验证](https://img.shields.io/badge/%E7%9C%9F%E6%9C%BA%E9%AA%8C%E8%AF%81-%E6%97%A0-9e9e9e?style=flat-square)](#用之前要知道的)
[![模型调用](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B%E8%B0%83%E7%94%A8-2%E2%80%933%20%E6%AC%A1%20%E2%86%92%201%20%E6%AC%A1-1f6feb?style=flat-square)](#改了什么)

[![测试](https://img.shields.io/github/actions/workflow/status/chy4pro/chat-nojev/test.yml?branch=main&style=flat-square&label=%E6%B5%8B%E8%AF%95)](https://github.com/chy4pro/chat-nojev/actions/workflows/test.yml)

[快速开始](#快速开始) · [用之前要知道的](#用之前要知道的) · [改了什么](#改了什么) · [和原版不一样的地方](#和原版不一样的地方) · [Windows 变体](windows/) · [Android 变体](android/) · [macOS 变体](macos/) · [jev-chat](https://github.com/jev-chat) · [NOTICE](NOTICE)

</div>

## 快速开始

三个变体各发了 `v0.1.0`，由仓库自己的 workflow 在 GitHub 的 runner 上编译、打包、发布，**没有一个在真机上跑过**——装之前先看一眼[用之前要知道的](#用之前要知道的)。

### Windows

**[下载 windows-v0.1.0](https://github.com/chy4pro/chat-nojev/releases/tag/windows-v0.1.0)** —— `jev-chat-windows-v0.1.0.zip`（165.5 MB）。解压到一个固定目录，双击里面的 `jev-chat-windows.exe`。**这是 PyInstaller 的 onedir 打包，整个文件夹里带着离线 OCR 模型和 Qt，所以体积大；exe 没签名，SmartScreen 会拦一下，走「更多信息」→「仍要运行」。**

要求 Windows 10 1903+ / 11、微信 Windows 4.x。首次启动弹设置页，「模型」卡片只有一节（原版是判断、起草两节）：填一把 OpenAI 兼容端点的 key，选你们的关系，保存。key 进 Windows 用户环境变量 `LLM_API_KEY`（注册表 `HKCU\Environment`），不落文件；其余设置写 `config.json`。

```bash
# 从源码跑
git clone https://github.com/chy4pro/chat-nojev.git
cd chat-nojev/windows
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
python main.py          # 自己打包：build.bat 或 pyinstaller --noconfirm --clean jev.spec
```

### Android

**[下载 android-v0.1.0](https://github.com/chy4pro/chat-nojev/releases/tag/android-v0.1.0)** —— `jev-chat-android-v0.1.0-unsigned.apk`（24.5 MB，另附 `.sha256`）。**这个 APK 没有签名，就装不上**——安卓要求每个包都带有效签名，这跟「允许未知来源」是两回事，任何来源都绕不过。自己签一次就行（自签即可，`keytool` 生成一把）：

`apksigner sign --ks 你的.jks --out signed.apk jev-chat-android-v0.1.0-unsigned.apk`

装上之后：设置 →「接口」只有两张卡（模型 / 视觉），填「模型接口」一把 key 就能用，视觉留空自动继承它。权限还是原版那三项——无障碍、悬浮窗、自启动 + 省电无限制。

```bash
# 从源码打包
cd android
./gradlew assembleDebug  # app/build/outputs/apk/debug/app-debug.apk；JDK 17 + Android SDK 35
```

### macOS

**[下载 macos-v0.1.0](https://github.com/chy4pro/chat-nojev/releases/tag/macos-v0.1.0)** —— `jev-jarvis-macos-v0.1.0.zip`（0.1 MB，同一个 Release 下还有别名 `jev-jarvis-macos-latest.zip` 和一份 `SHA256SUMS`；zip 这么小是因为它只是个启动器，首次运行再联网拉 Python 依赖，这是原版的设计）。**没有代码签名、也没有公证，Gatekeeper 第一次会拦下来**：右键（或按住 Control 点）→ 打开 → 再点一次「打开」；提示「已损坏」就 `xattr -d com.apple.quarantine /Applications/jev-jarvis.app`。构建 runner 是 Apple Silicon（arm64）。

要求 macOS 13+，微信在运行，终端已授予「屏幕录制」；「填入」另需「辅助功能」。配置是一个 env 文件，只有一把 key：

```bash
# 配置；从源码跑见末尾一行
mkdir -p ~/.config/jev-jarvis
cat > ~/.config/jev-jarvis/env <<'ENV'
export OPENAI_API_KEY="sk-你的key"
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_MODEL="deepseek-chat"
ENV
chmod 600 ~/.config/jev-jarvis/env

cd macos && ./start.command   # 从源码跑；自己打包：packaging/build_app.sh，发布走 packaging/release.sh
```

## 用之前要知道的

- **没有一个变体在真实设备上跑过。** 这台机器上没有 Android SDK、没有 Mac、也没有能显示 PySide6 窗口的桌面；三个 `v0.1.0` 是 CI 打出来的，证明的只是「编译得过、打得出包」。谁装谁就是第一个试的人。
- **发送键永远你自己按。** 程序只把选中的回复填进输入框，转账红包一律不碰。这是 jev-chat 的边界，这一版没有碰它，也同样没有在真机上验证过它。
- **Android 这个包装在原版旁边，不是替换它。** 应用 id 是 `com.jev.probe.nojev`，启动器名字是「对话副驾」——为的就是能和 `jev-chat-jarvis` 装在同一台手机上对着比。
- **macOS 丢了本地判断模型。** 原版的判断层可以完全在本机跑（`Mapika/decider-2b`，不要 key、不联网），合并之后判断和候选是同一次请求，这条路没有了。丢的主要是隐私，不是离线能力：生成端点指向本地 Ollama 时整条链路照样不出网，没有了的是「用云端端点写候选、同时让判断留在本机」这一种组合，而那是原版的默认配置。介意就用 [jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac)。
- **`confidence` / `probabilities` 是模型的自评**，不是分类器校准过的概率。面板只把它当参考顺序显示，代码里没有地方拿它做阈值判断；下游也不该把它当成那种东西读。
- **其余全是原版的代码，连同它的 bug。** 读屏、OCR、起草的提示词、悬浮窗、填入输入框，这个仓库既没有审过也没有修过——除了[下面那几处](#和原版不一样的地方)不修就过不去的。

## 改了什么

原版的链路是两段：生成模型起草 3 条候选回复，专门的判断模型（TypeSafe Jev）回答对方的真实意图、紧张度、该不该马上回；macOS 上还要再来一次，让判断模型给候选打分排序。两路接口、两把 key。这一版只发一次请求：同一个模型写候选、答那几道判断题、给每条候选打分，一个 JSON 回来。题面原文、选项集合、0–9 的紧张度档位都是从原版原样搬过来的，**换的只是产出方**；悬浮窗读到的字段、类型和渲染那段代码一行没动。

<div align="center">

<img src="docs/images/flow.png" width="860" alt="jev-chat 两到三次调用、这一版一次调用，送到面板的是同一批字段" />

</div>

三份代码不是同一个东西的三次移植：Windows 和 Android 问的是同一套 7 道判断题，macOS 问的是它自己的两道，候选还按话术分组生成——所以「合并」在三处的做法和代价都不一样（[逐处](docs/MERGE.md#三个变体为什么各不一样)）。

这道接缝有差分测试盯着：同一批场景同时喂给原版那棵树和这一棵，两边各起一个进程跑真实代码，逐字段比对同一段下游代码处理出来的结果，每套都自带负控。

```bash
python tools/equivalence_check.py         --upstream /path/to/jev-chat-windows
python tools/equivalence_check_macos.py   --upstream /path/to/jev-chat-jarvis-mac
bash   tools/equivalence_check_android.sh --upstream /path/to/jev-chat-jarvis --probe /path/to/toolchain
```

逐场景的结果和判据在 [`windows/docs/EQUIVALENCE.md`](windows/docs/EQUIVALENCE.md)、[`android/docs/EQUIVALENCE.md`](android/docs/EQUIVALENCE.md)、[`macos/docs/EQUIVALENCE.md`](macos/docs/EQUIVALENCE.md)：**那是关于一道接缝的证据，不是对这个 App 的质量判断。** 为什么这么改、判断为什么不该因此变差、每处代价为什么躲不掉，都在 [`docs/MERGE.md`](docs/MERGE.md)。

## 和原版不一样的地方

除了合并本身（删掉判断那一路的客户端、配置项和设置页那一节），三棵树跟上游还有这些出入。来龙去脉见 [`docs/MERGE.md`](docs/MERGE.md) 和各变体自己的 README。

**三处都有**

- 选择题的答案不再是一组固定的英文标签：模型用**对话那门语言**写一句短语，三张中文对照表跟着删了，面板显示它写的那句。这一条不是合并逼出来的，是有意选的（[为什么](docs/MERGE.md#判断会不会变差)）。数值完全不受影响。

**Windows**（[详版](docs/MERGE.md#windows)）

- 更新检查关掉了：原版查的是它自己的 Release 页，版本号跟这里的代码不是一回事。设置页那个开关还在，现在是死路。
- `max_tokens` 400 → 1600（思考模式 4000 → 5200）；`core/llm.py` 加了 JSON 模式，默认关着。
- key 的回退改成认来源了——**修原版的毛病**：只设了 DeepSeek key 却选别家，原版会把 key 发给别家。代价是那种情况现在报「未配置」。
- `docs/KICKOFF.md` 被就地改了；`docs/*.png` 截图没重拍，跟现在的面板对不上。

**Android**（[详版](docs/MERGE.md#android)）

- 应用 id 和启动器名字改了（见[上一节](#用之前要知道的)），无障碍服务的组件名跟着改成动态拼的。
- 签名配置不再默认指向一个 Windows 路径——**修原版的 bug**，不修则非 Windows 机器上连未签名 release 都打不出来，也就没有那个 APK。
- `gradlew` 补上了可执行位；版本号退回 `1` / `0.1.0`；`apk/` 目录没有跟着拷过来。
- 顺带改对了原版 README 的一处笔误：危险等级是 0–9，不是 1–9。

**macOS**（[详版](docs/MERGE.md#macos)）

- 本地判断模型删了（见[上一节](#用之前要知道的)），`src/judge.py` / `src/judge_jev.py` 和那份 22 条的中文意图回归测试（「86.4%」的出处）跟着一起删。
- 候选的百分比从「跨话术可比」变成「话术内占比」；面板实际用的排序没变。
- 判断适配器里的兜底挪到了面板（`src/hud.py`），坏数据两边显示不同，5 处记在 `macos/docs/EQUIVALENCE.md`；顺带修掉 `float(value or 0.0)` 碰上非数字会带走 UI 线程的隐患。
- 判断不再抢在候选前面上屏，「一半成功一半失败」也不再可能。
- `MAX_TOKENS` 300 → 1200；版本号退回 `0.1.0`；`torch` / `transformers` / `huggingface-hub` / `laya` 从依赖删了，但 `uv.lock` 还锁着它们——**已知的不一致**。

## jev-chat 与致谢

这是一份派生作品。它只改「判断由谁产出」这一件事，其余全部是它的工作——采集、OCR、悬浮窗、填入、题面本身，一样都不是这里写的。三个原版都以 MIT 开源：

- **[jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis)**（Jev 聊天助手，Android，最早的那一版；判断题面和分类体系出自这里）—— Finderchangchang 与 jev-chat 贡献者，`android/` 的直接来源
- **[jev-chat-windows](https://github.com/jev-chat/jev-chat-windows)**（Jev 聊天助手 Windows 版）—— rezoch340 与 jev-chat 贡献者，`windows/` 的直接来源
- **[jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac)**（macOS 版）—— eatmoreduck，`macos/` 的直接来源

分发或商用时请保留 LICENSE 与 [NOTICE](NOTICE) 并写明来源。本仓库与原作者没有任何关系，也**不要**用「Jev 聊天助手」「jev-chat」这些名字或 chatjevs.com 域名暗示由他们出品或背书。原版的第三方组件条款（包括 Windows 打包件会受约束的 PySide6-Fluent-Widgets GPLv3）原样保留在 `windows/NOTICE`。

## 许可与免责

MIT，继承 jev-chat 版权，见 [LICENSE](LICENSE)。

本项目只处理**你自己设备上、你自己有权查看的**聊天：不注入、不 hook、不解密数据库、不自动发送任何消息。请遵守微信等各软件的许可协议与当地法律法规，作者不对使用后果负责。聊天内容只在触发分析的那一刻发给你自己配置的模型接口，这里没有任何自建服务器。
