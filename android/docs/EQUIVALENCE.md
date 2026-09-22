# Equivalence with upstream

## The standard

The goal is **not** that our intermediate JSON matches upstream's byte for byte. It is
that the same downstream code handles it the same way: `overlay/OverlayController.kt` must
render from our one call what it renders from upstream's three.

That is a weaker requirement than an identical structure, and it is the right one, because
the downstream code is already hardened. Upstream had to survive its own judgment model
returning junk, so it copes at the point of use:

- `JudgeClient.parseChoice` took `o.optString("choice")` and `o.optDouble("confidence", 0.0)` —
  any phrase at all is carried, and a missing confidence is already 0.0.
- `OverlayController.render()` drew the intent headline as `INTENT[it.choice] ?: it.choice`:
  a label outside the taxonomy already rendered as the label itself.
- The danger badge is `"危险 ${it.score.roundToInt()}/${it.maxLevel}"` with no range check,
  so upstream shows "危险 11/9" for a score of 11.
- `parseRanked` turns a score it cannot read into `0.0` and sorts what it has.

So `jev/MergedAnswer.kt` is a **parsing and translation layer only**. It digs the JSON
object out of the model's answer, moves the values into the three answer shapes the
judgment API answered in (`noul` / `choice` / `score`) plus the `best_reply` block, and
then runs upstream's own `parseChoice` / `parseScore` / `parseRanked` on them — that code
is copied into `MergedAnswer` unchanged. It deliberately does not re-validate what the
consumers already handle; re-validating would *change* behaviour rather than preserve it.
Upstream shows "危险 11/9" for a score of 11; clamping it to 9 would show "危险 9/9".

If a value is missing entirely the field is left out, the way upstream's `answers` came
back without a field its model did not answer, and the corresponding `Analysis` field
stays null. Nothing is invented in its place.

What the layer still does, because it is structurally necessary:

- extracting the JSON object from the response text, including a fenced block, and falling
  back to upstream's own line-by-line candidate reader for an answer that is not JSON at
  all — that is parsing, not validation;
- padding the candidates to three with `（稍等，我看下）`, which is upstream's
  `ReplyClient.parseThree` behaviour and is what makes "the model wrote two replies"
  land the same way on both sides;
- tolerating the spellings a model uses for a noul — a bare number, `{"noul": x}`, or
  `true`/`false` for 1 / 0 — and the same bare-value spelling for a choice or a score;
- accepting the judgment fields whether the model nested them under `judgment` or put them
  at the top level;
- keying the per-candidate scores by position (`reply_a/b/c` against `replies[0..2]`),
  which is what the model was told they mean.

## The two decisions this port had to make

### The typed object

`Choice(choice, confidence, probabilities)` and `Score(score, confidence, maxLevel)` are
what `render()` reads field by field, so the types stay exactly as they are. How they are
filled:

- **`Choice.choice`** is the phrase the model wrote (see "the displayed wording" below).
- **`Choice.confidence` and `Score.confidence`** are `Double`, not `Double?`, and upstream
  itself wrote `0.0` into them when its model omitted the field (`optDouble(..., 0.0)`).
  We keep that exact parse, so a missing confidence renders as "把握 0%" here and there.
  What we do **not** do is what the Windows adapter used to: substitute the probability
  the model gave the option it chose. That would be inventing a number.
  **These values are now the model's self-report** — the same model wrote the candidates,
  rated them, and rated its own certainty. They are not a classifier's calibrated
  outputs, and nothing downstream should read them as if they were. This is stated in
  `MergedAnswer`'s own documentation too.
- **`Choice.probabilities`** is whatever table the model gave, unchanged: not normalized,
  not filtered against the option keys. An empty map when it gave none, which is what
  upstream produced for an answer without a `probabilities` object.
- **`Score.maxLevel`** was never a model value: upstream read it from the `legend` the
  judgment API sent with a score answer, falling back to 9. There is no legend to send
  here, so the fallback is the level count of the question we actually asked
  (`JevQuestions.maxLevel`) — the same 9, and it follows the question set if that ever
  changes. The `score_legend_from_api` scenario feeds upstream a real legend and checks
  both sides still say 9.
- **The nullable fields stay null.** A question the model did not answer produces no
  `Choice`/`Score` at all (`trueIntent`, `dangerLevel`, `sheNeeds`, `bestAction` are
  `Choice?`/`Score?`), and `shouldReplyNow` / `tensionResolved` / `literalQuestion` are
  `Double?` and stay null — exactly what upstream produced when its `answers` lacked the
  field. `render()` already skips a null field.

### The two parallel tasks

`ChatCaptureService.runAnalysis()` used to submit two independent tasks: `judge(...)`,
which was fast (~1s) and drew the panel on its own, and `draftAndRank(...)`, which was
slower and filled the reply cards in afterwards. Merging collapses them into one task.
What that changes, observably:

- **The half-drawn panel is gone.** Upstream showed the danger badge, the intent headline
  and the secondary line while the reply cards still read "生成中…". There is no state now
  in which one half has arrived and the other has not, so `showJudgment` / `showReplies`
  became one `showAnalysis`, and `render`'s `generating` branch went with them.
- **Everything appears later, at once** — at the pace of the generative call, not the
  judgment call. The panel shows "分析中…" for that whole time, as it already did before
  the judgment landed.
- **A partial failure is no longer possible.** Upstream could show the judgment plus
  "回复接口出错：…" when only the draft failed, and `OverlayController.replyError` existed
  for that; the reverse (judgment failed, candidates fine) already ended as a bare error
  panel, because `showReplies` returns early when there is no judgment to copy. Now a
  failure of the one call is `Analysis.error` and the panel shows the error, with the same
  error text `HttpJson` produces. `replyError` is deleted.
- **Two tokens' worth of calls become one**, and the judgment no longer pays for a second
  send of the whole conversation. The differential test prints the call counts per
  scenario: upstream `[判断接口, 回复接口, 判断接口]`, ours `[回复接口]`.
- The worker pool, the debounce, the `analyzing` flag and the knowledge-context step in
  front of the call are untouched.

## What the check does not cover: the displayed wording

The check compares the `Analysis` object, not what the panel renders. One rendering change
is deliberate and is not equivalent to upstream:

Upstream's three choice questions were answered by a classifier, which can only pick from
a closed set of English keys, so `OverlayController` carried three tables — `INTENT`,
`NEEDS`, `ACTION` — translating each key into Chinese display text. A general model does
not need a closed set. The prompt (`JevQuestions.CHOICE_ANSWER`) now asks it to name the
option it picked as a short phrase **in the language of the conversation**, and to write
its own phrase if none of the options fits; the three tables are deleted and `render()`
shows `it.choice` as written.

The English keys and descriptions in `JevQuestions.judge()` are unchanged — they still
frame the questions. Only the answer vocabulary changed. The consequence is that the
displayed wording in the ordinary case is no longer word-for-word identical to upstream's
fixed strings: a Chinese conversation shows something close to them, an English
conversation shows English. Nothing in the prompt or the code pins the display language
any more. `danger_level` and everything else numeric are unaffected.

## The ranking, and a thing worth knowing about this app

`parseRanked` sorts the candidates by their score and **ignores `best_reply.choice`
entirely** — that is upstream's code, and it is kept. So on Android the model's named
winner has never decided anything; only the per-candidate scores do. (The Windows app is
the other way round: its engine reads `choice` and the panel pins that candidate first.)
The name is still asked for, still translated into the ranking block, and still ignored by
the same function that ignored it upstream. `winner_not_top_scorer` is the scenario that
holds this in place: the named winner is not the top scorer, and both sides put the top
scorer first.

## The check

`tools/equivalence_check_android.kt` at the repository root runs both trees on the same
neutral scenario data, rendered into the two wire formats: the judgment API's `answers`
object (plus a bare JSON array of candidates for the reply route) for upstream, and the
one merged JSON object for this version. Both sides then call the same entry point,
`JevClient.analyze(snapshot, relationship)`, and the resulting `Analysis` objects are
compared field by field.

Only `jev/HttpJson.post` is stubbed, by `tools/equivalence_stub/HttpJson.kt`. Everything
else on both sides is the real code: the real prompt, the real request body, the real
parser, the real translation. Our side is handed the raw model text, not a pre-parsed
structure.

Both trees declare the same package names, so each side is compiled into its own output
directory and run as its own JVM, and the driver compares the two JSON files — the same
isolation the Python suite gets from separate processes. No Gradle and no Android SDK:
the files under test are pure Kotlin plus `org.json`, and the three Android classes they
touch (`Rect`, `Log`, `Context`/`SharedPreferences`) come from about fifty lines of
hand-written stubs.

    tools/equivalence_check_android.sh --upstream /path/to/jev-chat-jarvis \
                                       --probe /path/to/kotlinc-probe

Sixteen scenarios, **all sixteen identical on every compared field**, none failing.
`latencyMs` is excluded: it is wall-clock time, not a value either side computed.

Scenarios that exist specifically to hold the standard in place:

- `score_out_of_range` feeds both sides a `danger_level` of 11. Both carry it through, and
  the panel would show "危险 11/9" on both.
- `intent_outside_taxonomy` feeds both sides a `true_intent` the taxonomy does not contain.
  Both carry it through.
- `probabilities_not_normalized` feeds both sides per-candidate scores that sum to 10.
  Neither side normalizes them.
- `confidence_omitted` drops `confidence` from two answers; both sides render 0%.
- `two_candidates_only` gives two replies where three were asked for; both sides pad.

Negative controls (`--negative-control keys|clamp`, which patch a *copy* of our tree):

- `keys` reverses the reply keys, so the scores land on the wrong candidates: **15 of the
  16 turn red**. The one that stays green is `ranking_block_missing`, where every score is
  0.0 and the order cannot be wrong.
- `clamp` makes the adapter clamp the tension score into 0..9 — the exact re-validation
  this document argues against: `score_out_of_range` turns red, upstream 11.0 against our
  9.0, and nothing else moves.

## What has not been verified here

No APK is built or signed in this container, and nothing has run on a device or an
emulator: there is no Android SDK, and installing one was out of scope. Everything above
about the panel is read from the code, not seen on a screen. The files that only the SDK
can compile — `OverlayController.kt`, `ChatCaptureService.kt`, `SettingsActivity.kt`,
`MainActivity.kt` — are changed but **not compiled**; the ones in the differential test's
compile set are.
