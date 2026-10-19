# 上游同步记录

这个仓库是三个上游应用各自的派生，每个变体都是「某一个上游 commit 的整棵树 + 我们那一处合并」。
**同步的前提是知道每棵树当前对齐到上游的哪个 commit**，所以每次同步都往下面这张表添一行。
2026-09-22 建仓时没记，第一次同步得靠逐个 commit 比对整棵树才找回来，别再重来一次。

| 变体 | 上游仓库 | 当前对齐到 | 上游当时的 tag | 同步日期 |
|---|---|---|---|---|
| `windows/` | [jev-chat-windows](https://github.com/jev-chat/jev-chat-windows) | `c6d3a10` | `v0.1.12` | 2026-10-19 |
| `android/` | [jev-chat-jarvis](https://github.com/jev-chat/jev-chat-jarvis) 的 `cn/` | `bd3fe42` | `v1.7` + 18 | 2026-10-19 |
| `macos/` | [jev-chat-jarvis-mac](https://github.com/jev-chat/jev-chat-jarvis-mac) | `cbf6fe4` | `v0.6.2` + 16 | 2026-10-19 |

上一次（2026-09-26）对齐到：`windows/` = `946d3d1`（`v0.1.11`）、`android/` = `71ee797`（`v1.4` + 23）、
`macos/` = `2387d71`（`v0.6.0` + 19）。

建仓时的 fork 点（表里第一行的上一行）：`windows/` = `016eac1`、`android/` = `7c07d9d`、
`macos/` = `a28cb9e`，都在 2026-09-22。

## 怎么同步

三方合并，让 git 干机械的那部分——不要手抄 diff。每个变体一次：

```bash
git clone https://github.com/jev-chat/<repo>.git /tmp/up          # 完整历史，不要 --depth 1
git -C /tmp/up branch nojev <上表的「当前对齐到」>                  # 合并基
git -C /tmp/up worktree add /tmp/m nojev
# 把我们这棵树整个铺到 worktree 上（保留 .git，上游自己的 .github 先留着，最后再删）
# 然后提交成一个「ours」commit，再 merge 上游 HEAD
git -C /tmp/m commit -am 'chat-nojev <变体>'
git -C /tmp/m merge <上游 HEAD>
```

**android 要多一步。** 2026-10-06 起 jev-chat-jarvis 把国内版挪进了 `cn/`，根目录另有一个海外版
`global/`（Jev for WhatsApp，英文，跟这个变体无关，不搬）。直接 merge 会变成整棵树挪目录。做法是
先造一个「上游」commit：树是上游 HEAD 的 `cn/` 子树挪到根、再加上根目录的 `LICENSE` 和 `NOTICE`，
父提交是合并基。这样 `git diff <合并基> <这个 commit>` 就是上游对应用本身的真实改动：

```bash
GIT_INDEX_FILE=/tmp/cn.index git -C /tmp/up read-tree <上游 HEAD>:cn
for f in LICENSE NOTICE; do
  GIT_INDEX_FILE=/tmp/cn.index git -C /tmp/up update-index --add --cacheinfo 100644,$(git -C /tmp/up rev-parse <上游 HEAD>:$f),$f
done
T=$(GIT_INDEX_FILE=/tmp/cn.index git -C /tmp/up write-tree)
C=$(git -C /tmp/up commit-tree $T -p <合并基> -m "upstream cn/ re-rooted")
git -C /tmp/m merge $C
```

差分测试的 `--upstream` 给仓库根或 `cn/` 都行，脚本自己认。

于是 `git diff <合并基> HEAD` 就是**我们的偏离**，`git diff <合并基> <上游 HEAD>` 就是
**上游这段时间干的事**，每个冲突 hunk 都能对着这两份判断该要哪边。解完冲突再铺回
`<变体>/`。`.github/` 的规矩：我们的 CI 在仓库根，变体目录里的 `.github/` GitHub 不会读；
`windows/`、`android/` 不带，`macos/` 只带 `ISSUE_TEMPLATE/` 和 `PULL_REQUEST_TEMPLATE.md`
（它的 `ci/` 脚本和测试引用它们），不带 workflows。

## 解冲突的规矩

1. **默认要上游的。** 先问「这个 hunk 跟『判断由谁产出』有关系吗」——无关就原样取上游，
   不顺手优化、不加注释。
2. **我们只保留 [`MERGE.md`](MERGE.md) 里记着的那些偏离**，一条不多。
3. **上游的新行为，只要不是「判断由谁产出」，都要落到合并后那条路上。** 最容易漏的一条：
   上游修了 bug、加了来源、改了提示词，哪怕它落在我们改过的函数里，也要跟上。
4. 同步完跑那五套（两个 `offline_check.py` + 三套差分 + Android 的两个负控），
   然后把这份表和 `MERGE.md` 一起更新。上游改了题面、选项集合或档位的话，根 README 里
   「题面一个字没改」那句得跟着改。

## 2026-09-26 那次同步踩过的坑

下次同步照着查一遍，这几类都不会在冲突里自己冒出来。

- **同名不同义的符号。** 上游 `033d6ef` 在 `windows/core/questions.py` 加了 `CHOICE_LABELS`
  （英文 key → 中文显示），我们那里原本也有一个 `CHOICE_LABELS`（每道题的选项 key 元组）。
  git 把两个都合进来了，后一个悄悄盖掉前一个，不报冲突。我们的已经改名 `CHOICE_OPTIONS`；
  以后自己加模块级名字前，先在上游那棵树里 grep 一下。
- **原版的兜底只见过候选。** 三处原版都有「JSON 解析不出来就逐行抠」的兜底。合并之后同一段回答
  里还有判断，`"replies": []` 会让兜底把 JSON 自己的行当候选抠出来。三套差分各有一个场景盯着
  （`all_candidates_filtered_out` / `one_tone_writes_nothing_usable`），同步完它们必须是绿的，或者是
  写明了原因的刻意不同（2026-10-19 起 Android 那个就是：见下面最后一条）。
- **上游吞异常，桩坏了看起来像差分。** macOS 的 `_rank_payload` 把任何异常都吞成「没分」，上游
  v0.6.0 开始从 `self` 上读 `_reply_worker` / `_active_context`，桩里没有，于是 16 个场景全部
  报「我们这边分数不对」。`tools/equivalence_check_macos.py` 现在先检查上游那侧真的排出了分，
  没排出来就直接报「桩坏了」。上游再往 `self` 上加读的东西，报的是这句，按它补桩。
- **上游的新功能会改我们偏离的前提。** 这次两处：Windows 改成三段式，于是「候选盲写」这条论证
  过期、JSON 字段顺序得跟着改；macOS 每话术候选数变成可配 1–5，于是固定的输出预算不够了。
  读上游日志时，凡是碰到判断、起草、排序、候选数、提示词的提交，都要回头看 `MERGE.md` 里哪句话
  靠它成立。
- **App 里指向原版的出口。** 上游会往 App 里加链接（这次是 Android 的隐私政策和开源仓库按钮），
  默认指着它自己的站点。同步完 `grep -rnE 'chatjevs\.com|jev-jarvis\.com|github\.com/jev-chat'`
  一遍变体的源码目录，App 里能点到的都要指本仓库。

## 2026-10-19 那次同步多出来的两条

- **原版修好了我们记着的 bug，那条偏离就删。** Android 签名配置那条偏离的理由是「Gradle 的 `file()` 在
  Linux 上转不了 Windows 盘符路径，离开 Windows 打不出包」。jev-chat-jarvis 在 `b2665c6` 自己改成了
  `File()`，在 Linux 上只是找不到那个文件、照样出未签名包，于是这条偏离删了，取原版的。每次同步都要
  对着 `MERGE.md` 里写着「修原版的 bug」的那几条，查一遍原版是不是自己修了。
- **原版的新 bug 落在我们替换掉的那段里，不照抄，记成刻意不同。** jev-chat-jarvis v1.7 不再把候选补足
  三条，排序题却还要求恰好三条，于是两条候选时排序抛异常、面板一条候选都没有；`[]` 被逐行兜底解析成
  一条字面的「[]」。这两段都在我们换成一次调用的那条路上，我们这边没有这两个问题。Android 差分里
  `two_candidates_only`、`choice_names_missing_candidate`、`all_candidates_filtered_out` 三个场景因此
  记为刻意不同（只差在 `rankedReplies`，判断那几项两边一致）。原版修了，就把这几条刻意不同撤掉。
