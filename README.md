<div align="center">

# chat-nojev

**jev-chat 的派生版。只改了一件事：判断和排序现在由写候选回复的那个模型在同一次调用里给出。其余是原样的原版代码，连同它的 bug。**

[![许可](https://img.shields.io/badge/%E8%AE%B8%E5%8F%AF-MIT-blue?style=flat-square)](LICENSE)
[![Windows](https://img.shields.io/badge/Windows-10%201903%2B%20%2F%2011-0078D4?style=flat-square&logo=windows&logoColor=white)](#windows)
[![Android](https://img.shields.io/badge/Android-11%2B-3DDC84?style=flat-square&logo=android&logoColor=white)](#android)
[![macOS](https://img.shields.io/badge/macOS-13%2B-000000?style=flat-square&logo=apple&logoColor=white)](#macos)
[![真机验证](https://img.shields.io/badge/%E7%9C%9F%E6%9C%BA%E9%AA%8C%E8%AF%81-%E6%97%A0-9e9e9e?style=flat-square)](#没改的是什么)
[![模型调用](https://img.shields.io/badge/%E6%A8%A1%E5%9E%8B%E8%B0%83%E7%94%A8-2%E2%80%933%20%E6%AC%A1%20%E2%86%92%201%20%E6%AC%A1-1f6feb?style=flat-square)](#改的是哪一件事)

[![测试](https://img.shields.io/github/actions/workflow/status/chy4pro/chat-nojev/test.yml?branch=main&style=flat-square&label=%E6%B5%8B%E8%AF%95)](https://github.com/chy4pro/chat-nojev/actions/workflows/test.yml)

[改的是哪一件事](#改的是哪一件事) · [没改的是什么](#没改的是什么) · [破例的地方](#只改一件事不成立的地方) · [快速开始](#快速开始) · [Windows 变体](windows/) · [Android 变体](android/) · [macOS 变体](macos/) · [jev-chat](https://github.com/jev-chat) · [NOTICE](NOTICE)

</div>

这是 [jev-chat](https://github.com/jev-chat) 三个版本的派生：Windows、Android、macOS 各一棵树，每棵都是把原版整棵拷过来，然后动了一个地方。第一节说是哪个地方，第二节说哪些没动，第三节把「只改了一件事」这句话不成立的地方一处不漏地列出来。

## 改的是哪一件事

原版的链路是两段：生成模型起草 3 条候选回复，专门的判断模型（TypeSafe Jev）回答对方的真实意图、紧张度、该不该马上回；macOS 上还要再来一次，让判断模型给候选打分排序。两路接口、两把 key。

这一版只发一次请求。同一个模型写候选、答那几道判断题、给每条候选打分，一个 JSON 回来。题面原文、选项集合、0–9 的紧张度档位都是从原版原样搬过来的，**换的只是产出方**；悬浮窗读到的字段、类型和渲染那段代码一行没动。

<div align="center">

<img src="docs/images/flow.png" width="860" alt="jev-chat 两到三次调用、这一版一次调用，送到面板的是同一批字段" />

</div>

这道接缝有差分测试盯着：同一批场景同时喂给原版那棵树和这一棵，两边各起一个进程跑真实代码，逐字段比对处理结果；判据不是中间数据逐字节一样，而是同一段下游代码处理出来的结果一样。每套都自带负控——故意改坏一处，必须变红。

```bash
python tools/equivalence_check.py         --upstream /path/to/jev-chat-windows
python tools/equivalence_check_macos.py   --upstream /path/to/jev-chat-jarvis-mac
bash   tools/equivalence_check_android.sh --upstream /path/to/jev-chat-jarvis --probe /path/to/toolchain
```

逐场景的结果、判据的由来、macOS 那几处有意为之的分歧，都写在 [`windows/docs/EQUIVALENCE.md`](windows/docs/EQUIVALENCE.md)、[`android/docs/EQUIVALENCE.md`](android/docs/EQUIVALENCE.md)、[`macos/docs/EQUIVALENCE.md`](macos/docs/EQUIVALENCE.md) 里。**这是关于一道接缝的证据，不是对这个 App 的质量判断**——它只管「判断和排序由谁产出」这一处，别的地方它一点都没看。

## 没改的是什么

除了那道接缝，其余全是原版的代码：读屏、OCR、起草候选用的提示词、悬浮窗、把回复填进输入框、以及「发送键永远你自己按」那条边界。**原版的 bug 和粗糙处也一起留着。** 这个仓库没有审过这些代码，也没有修任何东西——除了下一节那几处不修就过不去的。

这些地方在这个仓库里**一次都没被验证过**。没有一个变体在真实设备上跑过：这台机器上没有 Android SDK、没有 Mac、也没有能显示 PySide6 窗口的桌面。三个 `v0.1.0` 是 CI 在 GitHub 的 runner 上编译打包出来的，证明的只是「编译得过、打得出包」，不是「装上能用」。这些地方的行为应该和原版一模一样，因为代码就是同一份——但那是推理，不是观察。

## 「只改一件事」不成立的地方

上面那句话不完全是真的。不把它不成立的地方列出来，它就是假话。下面是全部——每一条都附上为什么躲不掉，其中几条是**修了原版的 bug**，也就是「和原版一模一样」被故意违反的地方。

**三个变体都有的一处，而且是有意的：** 那三道（macOS 是一道）选择题的答案不再是一组固定的英文标签。原版由分类器作答，只能从封闭的 key 集合里挑，面板再拿一张对照表翻成固定的中文；这一版让模型用**对话那门语言**写一句短语，三张对照表（Windows `app/overlay.py` 的 `_CHOICES`、Android `OverlayController` 的 `INTENT`/`NEEDS`/`ACTION`、macOS 的意图查表）跟着删了，面板显示模型写的那句。**这一条不是合并逼出来的**——完全可以要求合并后的模型照旧吐英文 key、对照表原样留着。选它是因为封闭集合本来就会在题面没预料到的说法上逼模型说错话（[为什么这么改](#为什么这么改)）。代价：常见情况下面板的用词跟原版很接近但不逐字相同，英文对话会显示英文。数值（紧张度、分数、百分比）完全不受影响。

### Android 那一份

- **应用 id 从 `com.jev.probe` 改成 `com.jev.probe.nojev`，启动器名字从「Jev助手」改成「对话副驾」。** 不改就只能二选一：安卓按应用 id 认包，同 id 且签名不同的两个包互相装不上去。改了才能**和原版并排装在同一台手机上对着比**——这正是想要的。Kotlin 包名（`namespace`）没动。
- **无障碍服务的组件名从写死的字符串改成动态拼的**（`MainActivity` 里 `"com.jev.probe/…SelectToSpeakService"` → `"$packageName/…"`）。这是上一条的直接后果：那个字符串要拿去跟系统的 `ENABLED_ACCESSIBILITY_SERVICES` 比对，而系统那边的条目是「应用 id/类名」。不跟着改，主页会永远报「无障碍没开」。全项目只有这一处把应用 id 写成了字面量。
- **签名配置不再默认指向一个 Windows 路径——这是修原版的 bug。** 原版写的是 `file(System.getenv("JEV_KEYSTORE_PROPS") ?: "H:/android/keys/jev-release.properties")`。Gradle 的 `file()` 会把字符串按 URI 解析，Linux 上那个盘符前缀在配置阶段就转换失败，构建还没走到「要不要签名」那一步就死了——于是在 Windows 以外的任何机器上**连一个未签名的 release 都打不出来**，尽管它自己的注释写着「没有这个文件就出未签名包」。现在没有默认值：环境变量没设就是不签，正是那句注释一直承诺的行为。不修就没有 CI，也就没有那个 APK。
- **`gradlew` 补上了可执行位。** 原版提交的是 `rw-r--r--`，`./gradlew` 跑不起来（它们大概只用 `gradlew.bat`）。
- **版本号从 `versionCode 4` / `1.3` 退回 `1` / `0.1.0`。** 这是另一份代码，不该冒用它的版本号。
- **`apk/` 目录没有跟着拷过来。** 那里面是原版签好名的成品，跟这里的改动无关；留着只会让人装错东西。
- 顺带改对了原版 README 里的一处笔误：危险等级是 **0–9**，不是它写的 1–9（题面里就是 10 档）。

### Windows 那一份

- **更新检查关掉了。** 原版启动时（可关）向 `api.github.com/repos/jev-chat/jev-chat-windows/releases/latest` 查一次版本号。那是**原版自己的** Release 页，版本号跟这里的代码根本不是一回事，查到「有新版」只会把用户导去装另一个程序。现在 `app/update.py` 的 `_REPO` 留空，`check_latest()` 直接返回 `None`，一个请求都不发。设置页那个开关和标题栏的提示条还在代码里，但现在是死路——**还没清理**。
- **`max_tokens` 从 400 提到 1600（开思考模式 4000 → 5200）。** 400 是「只写三句话」的量，判断和概率表跟着回来就会被截断，整个 JSON 作废。方向上躲不掉，具体数字是拍的。
- **`core/llm.py` 加了 JSON 模式**（OpenAI 的 `response_format`、Gemini 的 `response_mime_type`），端点不认就脱掉重发一次。严格说不是非加不可——原版的起草调用要的是纯文本，这一版要的是一个结构化对象，加上它更稳。默认关着，老的调用路径一字未变。
- **key 的回退改成认来源了。** jev-chat-windows 里 `LLM_API_KEY` 空着就退到 `DEEPSEEK_API_KEY`，不看当前选的是哪家；只设了 DeepSeek key 却选了 OpenRouter 的人，会把 DeepSeek 的 key 发给 OpenRouter。这一版把惯用变量名写在每家来源自己那一行，只有选中这家时才当回退用，没有公认惯例的几家留空；脱敏名单反过来是更宽的，六个名字全遮，不管这次会不会读它。**这条是修原版的毛病，不是合并带出来的**——它那边 `JEV_API_KEY` 退到 `OPENROUTER_API_KEY` 也不看判断渠道选的是谁。代价是明的：只设了 `DEEPSEEK_API_KEY` 又选了别家的人，从「悄悄能用、但 key 发错了家」变成「未配置」。
- **`docs/KICKOFF.md` 被就地改了。** 那是原版立项时写的说明，按理该原样留着；这里把「两把 key」那几处改成了一把并加了说明头。
- **截图没有重拍。** `docs/*.png` 还是原版的图，上面是它那套固定中文标签和两节模型卡片，跟现在的面板对不上。

### macOS 那一份

- **本地判断模型删掉了，判断从此必须出网。** 原版的判断层可以完全在本机跑（`Mapika/decider-2b`，约 7 GB，不要 key、不联网），起草那次调用才出网。合并之后判断和候选是同一次调用，这条路没有了——`src/judge.py`、`src/judge_jev.py` 跟着删掉的还有内存不足时的保护、预热状态行、云端 TypeSafe 判断层，以及那份 22 条的中文意图回归测试（`judge_zh_test.py`，那个「86.4%」的出处）。躲不掉：判断和候选出自同一次请求，就不可能只让其中一半留在本机。**丢的主要是隐私，不是离线能力**——生成端点指向本地 Ollama 时整条链路照样不出网，没有了的是「用云端端点写候选、同时让判断留在本机」那一种组合，而那是默认配置。题面和那些表原样搬进了 `src/questions.py`，顶上记着要恢复得做什么。
- **候选的分从「跨话术可比」变成「话术内占比」。** 原版是所有话术生成完之后再单独调一次排序，那一次看得见全部候选；这一版每种话术一次调用，每次只看得见自己写的那两条。面板实际用的排序没变（它本来就在组内排），变的是左边那个百分比的含义。躲不掉：分是跟候选一起回来的，而候选是分组生成的。
- **原本藏在判断适配器里的那层「兜底」挪到了面板上。** 原版的 `JevJudge.judge` 在交出答案的路上会悄悄改它：意图查不到就落回「闲聊」、风险不是数字就记 `0.0`、缺 `confidence` 就当 0%。适配层只翻译不做主，这些兜底就得换个地方——`src/hud.py` 长出几个只管自己那一行的读取函数。后果是同一份坏数据两边显示不同（原版「● 安全 0/9」，这一版「风险待判断」），那 5 处逐条记在 [`macos/docs/EQUIVALENCE.md`](macos/docs/EQUIVALENCE.md)。顺带修掉一个真实隐患：原版 `float(value or 0.0)` 碰上非数字的模型分会抛异常，把 UI 线程带走。
- **`MAX_TOKENS` 从 300 提到 1200。** 跟 Windows 同一个原因：两张概率表塞不进 300，截断就整个对象作废。
- **版本号从 `0.4.0` 退回 `0.1.0`；`torch` / `transformers` / `huggingface-hub` / `laya` 从依赖里删了**（本地模型没了，它们没人用）。`uv.lock` 还是原版那一份、还锁着这四个——没在 macOS 上重跑过 `uv lock`，**这是个已知的不一致**。

除此之外，三棵树里其余的改动都是那一次合并的直接后果（删掉判断那一路的客户端、配置项和设置页那一节，把两次调用的返回拼成一次的形状）。`git diff` 对着上游一比就能看全。

## 为什么这么改

- **少一把 key。** 原版要判断和起草两路接口各配一把；这里只有一把，配置页也就少了一整节。
- **少一次把整段对话发出去。** 判断调用要把同一段对话再发一遍。现在发一次。
- **判断题一个字没改。** 7 道题（macOS 是它自己的 2 道）、选项 key 和描述、0–9 的档位都照搬原版，仍然框着这道题该怎么答。
- **代价是明写的。** `confidence` / `probabilities` 现在是模型的自评而不是校准过的概率；macOS 那个不用联网、只在本地跑的判断模型也被删掉了——丢的主要是隐私：判断的内容原来可以不出这台机器，用云端端点写候选时，它现在跟着一起出网了。都在[合并掉了什么](#合并掉了什么)。

**判断会不会变差，这里给个论证，不是量出来的结论。** 那几道题问的是开放的社会判断——对方到底想说什么、该不该马上接、这句话有多紧张。通用模型读这类东西，比只能从一张固定标签表里选一个的小分类器更合适，而且现在不再被关在那张表里：它可以直接用对话原本的语言作答。题面没有改——原文、选项集合、0–9 的档位都照原样保留，牵着模型走的还是那套为旧模型调出来的措辞。判断和候选也不再是互相看不见的两个意见：原来起草是单独一次调用，盲写三条，看不到判断会给出什么结论（见 `windows/core/draft.py` 里的说明）；现在一次调用两样都出，回复和判断出自对同一段对话的同一次读法。

分类器只能从一个封闭集合里选，答案不在里面也得硬凑一个最接近的。Windows / Android 那三道 choice 题的选项分别是 6、7、5 个，macOS 的意图题是 8 个——遇到题面没预料到的说法，macOS 原版的判断层会直接退回默认标签「闲聊」，没有「都不是」这个选项。通用模型不受这个限制：它用一句自己的话作答，用的是对话原本的语言，题面之外的说法不用被抹成最接近的那个标签。选项表还留在提示词里，所以常见情况回来的还是那几个熟悉的说法——这份自由只在选项表原来会逼它说错话的地方才用得上。也是因为这样，面板不再需要那张把英文 key 翻成中文的对照表：原来的判断模型只能吐一个英文 key，翻译表才有存在的理由；现在模型直接写短语，翻译表没活可干。

速度上，省的不只是「两三次变一次」。排序本来就没法在候选写完之前开始——没有候选可排。这个串行等待三处都有：Windows 先盲写候选，再把候选连同对话交给判断模型去判并排序；Android 的候选那条路先起草，写完才把候选连同对话再发一次给判断模型排序；macOS 判断和起草并行，但排序是第三次调用，要等每种话术都生成完才发得出去。合并把这段等待去掉了。

代价也在这儿：`confidence` 和候选的排序分原来是训练出来干这件事的分类器给的校准输出，现在是模型的自评。Android 和 macOS 的面板确实把它显示成百分比（「把握」「识别率」），但代码里没有地方拿这两个数做阈值判断，只当参考顺序展示；真要有谁拿它卡逻辑，才会踩到「不是校准过的概率」这一点。

以上是两种设计的差异推出来的判断，不是拿真实模型跑出来的结果——这个仓库没有量过判断准确率。

## 三个变体

三份代码不是同一个东西的三次移植：Windows 和 Android 问的是同一套 7 道判断题，macOS 问的是它自己的两道（8 类意图 + 0–9 风险），候选按话术分组生成。所以「合并」这件事在三处的做法和代价都不一样。

| 变体 | 来自 | 采集方式 | 原版的调用 | 这一版 | 配置 |
|---|---|---|---|---|---|
| [`windows/`](windows/) | [jev-chat-windows](https://github.com/jev-chat/jev-chat-windows) | 微信 Windows 4.x 窗口截图 + 本地离线 OCR | 起草 1 + 判断 1 | **1 次** | 一把 key |
| [`android/`](android/) | [jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis)（最早的那一版） | 无障碍读气泡节点，飞书等走 ML Kit 离线 OCR | 判断 1 + 起草 1 + 排序 1（分属两路接口） | **1 次** | 「模型接口」一把 key |
| [`macos/`](macos/) | [jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac) | 抓微信窗口 + Vision OCR | 判断 1 + 排序 1 + 每个话术一次生成 | **每个话术一次生成** | 一把 key |

## 快速开始

**先说清楚现在是什么状态：三个变体各发了 `v0.1.0`，由仓库自己的 workflow 在 GitHub 的 runner 上编译、打包、发布——但没有一个在真实设备上跑过。** 谁装谁就是第一个试的人。

### Windows

**[下载 windows-v0.1.0](https://github.com/chy4pro/chat-nojev/releases/tag/windows-v0.1.0)** —— `jev-chat-windows-v0.1.0.zip`（165.5 MB）。解压到一个固定目录，双击里面的 `jev-chat-windows.exe`。这是 PyInstaller 的 onedir 打包，体积大是因为整个文件夹里带着离线 OCR 模型和 Qt；exe 没签名，SmartScreen 会拦一下，「更多信息」→「仍要运行」。

要求 Windows 10 1903+ / 11、微信 Windows 4.x。首次启动弹设置页，「模型」卡片只有一节（jev-chat-windows 是判断、起草两节）：填一把 OpenAI 兼容端点的 key，选你们的关系，保存。key 进 Windows 用户环境变量 `LLM_API_KEY`（注册表 `HKCU\Environment`），不落文件；其余设置写 `config.json`。

**从源码跑**

```bash
git clone https://github.com/chy4pro/chat-nojev.git
cd chat-nojev/windows
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

自己打包是 `build.bat` / `pyinstaller --noconfirm --clean jev.spec`；上面那个 zip 就是推 `windows-v0.1.0` 这个 tag 后 `.github/workflows/release-windows.yml` 在 `windows-latest` 上跑出来、原样挂到 Release 的，本仓库自己没有在真机上装过跑过。

### Android

**[下载 android-v0.1.0](https://github.com/chy4pro/chat-nojev/releases/tag/android-v0.1.0)** —— `jev-chat-android-v0.1.0-unsigned.apk`（24.5 MB，另附 `.sha256`）。**这个 APK 没有签名**：仓库没配签名密钥，CI 就只能出未签名包。没签名意味着**它装不上**：安卓要求每个 APK 都带一个有效签名，这跟「允许未知来源」是两回事——后者管的是允不允许从商店以外的地方装，签名是任何来源都绕不过的一道。要装得先自己签一次（`apksigner sign --ks 你的.jks --out signed.apk jev-chat-android-v0.1.0-unsigned.apk`），Release 页上有同样的说明。安卓的签名是自签的，不需要任何机构审核，`keytool` 生成一把就行。这个包用了自己的 application id 和启动器名字（见[前面那节](#只改一件事不成立的地方)），所以是**装在 `jev-chat-jarvis` 旁边，不是替换它**。

装上之后：设置 →「接口」只有两张卡（模型 / 视觉），填「模型接口」一把 key 就能用，视觉留空自动继承它。权限还是它那三项——无障碍、悬浮窗、自启动 + 省电无限制。

**从源码打包**

```bash
cd android
./gradlew assembleDebug     # app/build/outputs/apk/debug/app-debug.apk
```

JDK 17 + Android SDK（platform 35 / build-tools 35）。上面 Release 里那个未签名 APK 就是推 `android-v0.1.0` 这个 tag 后 `.github/workflows/release-android.yml` 在 GitHub 自带 SDK 的 Linux runner 上用 `assembleRelease` 打出来的；`apk/` 目录没有从 jev-chat-jarvis 复制过来，它自己 Releases 里签好名的包是原版那一份，不含这里的改动。

### macOS

**[下载 macos-v0.1.0](https://github.com/chy4pro/chat-nojev/releases/tag/macos-v0.1.0)** —— `jev-jarvis-macos-v0.1.0.zip`（0.1 MB，同一个 Release 下还有别名 `jev-jarvis-macos-latest.zip` 和一份 `SHA256SUMS`）。zip 这么小不是漏打包：它只是个小启动器，首次运行时再联网拉自己的 Python 依赖，这是原版自己的设计，这一版没有改动它。没有代码签名、也没有公证，Gatekeeper 会在第一次打开时拦下来：右键（或按住 Control 点）→ 打开 → 再点一次「打开」；如果提示「已损坏，无法打开」，在终端跑：

```bash
xattr -d com.apple.quarantine /Applications/jev-jarvis.app
```

构建 runner 是 Apple Silicon（arm64）。

要求 macOS 13+，微信在运行，终端已授予「屏幕录制」；「填入」另需「辅助功能」。配置是一个 env 文件，只有一把 key：

```bash
mkdir -p ~/.config/jev-jarvis
cat > ~/.config/jev-jarvis/env <<'ENV'
export OPENAI_API_KEY="sk-你的key"
export OPENAI_BASE_URL="https://api.deepseek.com"
export OPENAI_MODEL="deepseek-chat"
ENV
chmod 600 ~/.config/jev-jarvis/env
```

jev-chat-jarvis-mac 那条「判断层不填 key 就走本地 decider-2b」的路在这一版被删掉了：判断跟候选是同一次调用，配哪个端点它就走哪个端点。（打包版内置共享 key 那条原版设计没动，这里也没验证过它还通不通。）

**从源码跑**

```bash
cd macos
./start.command
```

打包是 `packaging/build_app.sh`（发布走 `packaging/release.sh`）；上面那个 zip 就是推 `macos-v0.1.0` 这个 tag 后 `.github/workflows/release-macos.yml` 在 `macos-latest` 上跑出来的，本仓库自己没有 Mac 去装它、跑它。

## 合并掉了什么

- **macOS 那个不用联网、只在本地跑的判断模型没了。** `Mapika/decider-2b` 不要 key、不联网也能判意图和风险。它省下的主要是隐私：用云端端点起草时，判断的内容原来可以不出这台机器，现在两者合成一次，判断跟着一起出网了。（生成端点本来就指向本地 Ollama 的话，整条链路照样不出网，那条路没变。）它同时也是一个真实功能，删掉它是产品上的损失，不只是少了一个模型。`macos/src/questions.py` 顶上记着完整的账，包括要恢复得做什么。
- **macOS 候选的百分比变成了「组内占比」。** jev-chat-jarvis-mac 一次排序看得见所有话术的所有候选，百分比跨话术可比；现在每次调用只看得见自己写的那两条。面板实际用的排序没变（它本来就在组内排，`#1`/`#2` 一直都是「这两条里更好的那条」），变的是那个数字的含义。
- **判断不再抢在候选前面上屏。** 原版判断约 1 秒先把面板画出来，候选后到；现在它们是一个答案的两半，一起到。Android 那边同理：jev-chat-jarvis 那张「判断已到、回复还在生成中」的半张面板不存在了。
- **一半成功一半失败不再可能。** 原版可以判断成了、起草挂了，面板显示判断加一行「回复接口出错」；现在一次调用要么全成要么全败，面板显示那一个错误。
- **`confidence` / `probabilities` 是模型的自评。** 同一个模型写了候选、又给自己打了分、还评了自己的把握度。它们不是分类器校准过的输出，下游任何地方都不该当成那种东西读。

## 常见问题

<details>
<summary><b>判断是不是变差了？</b></summary>

没有拿真机数据量过，这一版也不拿 jev-chat 的数字充数——jev-chat-jarvis-mac 那个「8 类意图 86.4%」是 `decider-2b` 在 22 条消息上测出来的（`judge_zh_test.py`，跟着模型一起删了），它说不了一个通用模型答同一道题会怎么样。[为什么这么改](#为什么这么改) 里有为什么在道理上站得住的论证，但那是推论，不是测量；差分测试保证的是「同样的值送到面板上是同一个结果」，不是「模型判得一样准」——后者要拿你自己的真实对话去标一批才知道。

</details>

<details>
<summary><b>它会替我发消息吗？</b></summary>

不会。程序只把选中的回复填进输入框，发送键永远你自己按，转账红包一律不碰。这是 jev-chat 的设计，这一版没有碰它——也没有验证过它，这段代码原样留着，没人在真机上试过。

</details>

<details>
<summary><b>还能离线用吗？</b></summary>

Windows 和 Android 没变：起草候选那次调用一直要联网，这两个变体从来没能离线用。macOS 也没变的是那条彻底离线的路——生成端点指向本地 Ollama，整条链路不出网，原版能这么用，这一版也能。变的是中间那种：原版判断层不配 key 就走本地 decider-2b，于是「云端写候选 + 本地判断」是可能的，判断的内容不出这台机器；合成一次之后这种组合没有了。介意的话用 jev-chat-jarvis-mac 那一版。

</details>

<details>
<summary><b>那为什么不干脆把判断模型留着？</b></summary>

留着就是两把 key、两路配置、两次把整段对话发出去，而它答的那几道题通用模型也答得了——这个仓库就是去验证这句话成不成立，验证的方式是那三套差分测试，不是这段说明。不成立的地方都记在各自的 `docs/EQUIVALENCE.md` 里。

</details>

<details>
<summary><b>显示的字变了吗？</b></summary>

变了一处，是有意的，而且是「只改一件事」不成立的地方之一：[那一节](#只改一件事不成立的地方)开头讲了是哪一处、为什么这么选、代价是什么。英文 key 和描述没动，仍然框着题；数值类的（紧张度、分数）完全不受影响。

</details>

## 目录

```
windows/   Windows 变体（PySide6 + 本地 OCR）
android/   Android 变体（Kotlin，无障碍采集）
macos/     macOS 变体（Python + PyObjC + Vision OCR）
tools/     三套差分测试和它们的桩
docs/      这份说明用到的图
NOTICE     jev-chat 署名，以及从它继承下来的条款
```

## jev-chat 与致谢

这是一份派生作品。它只改「判断由谁产出」这一件事，其余全部是它的工作——采集、OCR、悬浮窗、填入、题面本身，一样都不是这里写的。三个原版都以 MIT 开源：

- **[jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis)**（Jev 聊天助手，Android，最早的那一版；判断题面和分类体系出自这里）—— Finderchangchang 与 jev-chat 贡献者，`android/` 的直接来源
- **[jev-chat-windows](https://github.com/jev-chat/jev-chat-windows)**（Jev 聊天助手 Windows 版）—— rezoch340 与 jev-chat 贡献者，`windows/` 的直接来源
- **[jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac)**（macOS 版）—— eatmoreduck，`macos/` 的直接来源

分发或商用时请保留 LICENSE 与 [NOTICE](NOTICE) 并写明来源。本仓库与原作者没有任何关系，也**不要**用「Jev 聊天助手」「jev-chat」这些名字或 chatjevs.com 域名暗示由他们出品或背书。原版的第三方组件条款（包括 Windows 打包件会受约束的 PySide6-Fluent-Widgets GPLv3）原样保留在 `windows/NOTICE`。

## 许可与免责

MIT，继承 jev-chat 版权，见 [LICENSE](LICENSE)。

本项目只处理**你自己设备上、你自己有权查看的**聊天：不注入、不 hook、不解密数据库、不自动发送任何消息。请遵守微信等各软件的许可协议与当地法律法规，作者不对使用后果负责。聊天内容只在触发分析的那一刻发给你自己配置的模型接口，这里没有任何自建服务器。
