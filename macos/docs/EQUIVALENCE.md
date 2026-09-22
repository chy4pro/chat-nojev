# Equivalence with upstream (macOS)

## The standard

The goal is **not** that our intermediate structures match upstream's byte for byte. It is
that the same downstream code handles them the same way: `src/hud.py` must produce the same
panel, the same candidate order and the same verdict dict from our one call that it produces
from upstream's three.

That is a weaker requirement than an identical wire format, and it is the right one — but on
this app it comes with a catch the Windows variant did not have, and the catch is worth
stating first.

## Where the hardening lives, and why it moved

On the Windows variant, the consumers were already hardened against a judgment model
returning junk, so the adapter could pass values straight through. **Here they were not.**
Upstream macOS hardens in the *judge adapter itself* — `src/judge_jev.py::JevJudge.judge`
(upstream lines 79-101):

- `intent = intent_ans.get("choice") or "闲聊"`, then, if the label is not one of the eight
  in `INTENTS`, a substring rescue and finally `"闲聊"`;
- `confidence = float(intent_ans.get("confidence") or 0.0)`;
- `risk = float(risk) if isinstance(risk, (int, float)) else 0.0`, then `round(risk, 1)`;
- `actions = ACTION_MAP.get(intent, [])` — a static table keyed by the label.

And the panel relies on all of it: upstream `hud.applyJudgment_` writes `v["intent"]`,
formats `v['confidence']:.0%` and computes `int(round(float(v.get("risk", 0))))` with no
guard of its own. A non-numeric risk from a gateway would take the UI thread down.

So "the adapter only translates" cannot be had for free here. Either the adapter keeps
upstream's coercions — and then it is validating, clamping and substituting exactly what
this project says it should not — or the validation moves to the point of use. **It moved.**
`src/generate.py::_verdict` copies the model's values under the keys the panel reads and
touches nothing else; `src/hud.py` grew four small readers that each guard the one thing
they render:

| reader | renders | falls back to |
| --- | --- | --- |
| `_judgment_text(value)` | the intent line | `暂未判断` when missing, empty or not a string |
| `_risk_level(value)` | the risk line, rounded | `None` → `风险待判断` when it is not a finite number |
| `_confidence_text(verdict)` | `意图识别率 NN%` | `""` — the row is left blank rather than claiming 0% |
| `_actions_text(verdict)` | the 具体行动 row | `""`, and non-string entries are skipped |
| `_prob_text(value)` | a candidate's score | `排序中` for `None`, `待定` for anything not a finite number |

Two things these readers deliberately do **not** do:

- **No range check.** Upstream displays `● 危险  11/9` for a risk of 11, because nothing
  upstream clamps it either. We display the same. Clamping to 9 would show `9/9` — that is
  changing behaviour, not protecting anything.
- **No substitution.** A missing confidence leaves the row blank instead of printing the
  `0%` upstream's `float(None or 0.0)` produces. Nothing is invented in place of a value the
  model did not give.

## Where the old design forced a closed set

`intent` was answered by a classifier, which can only pick from a closed set, so upstream had
to map anything else onto `闲聊` and the panel displayed one of eight fixed Chinese labels.
A general model does not need a closed set. The prompt (`src/questions.py::_INTENT_ANSWER`)
now asks it to name the intent as a short phrase **in the language of the conversation**,
using one of the eight if it fits and writing its own if it does not; the panel shows the
string it was given. The eight labels and their descriptions are unchanged and still frame
the question.

`actions` followed intent out of the closed set. Upstream derives it with
`ACTION_MAP.get(intent, [])` — the comment above the table says it out loud: *"V0: actions
are a static derivation, no generation involved"*, because a judgment model cannot write
text. With a free phrase for an intent that lookup can only miss, so the same call now
writes the action hints too, and `ACTION_MAP` is carried over verbatim into
`src/questions.py` as the worked example the prompt shows the model. When the model omits
them the row is empty — exactly what upstream shows when its lookup misses.

## What the merge cost

- **The local judge is gone.** `Mapika/decider-2b` judged with no key and no network —
  generation always needed a remote model, so this never made the app usable offline; what
  it bought was privacy, since only the drafting call left the machine and the judgment
  stayed local. Now the two are one call, so the judgment's content leaves with the draft
  too. It was a second judgment model, so by this project's thesis it goes; but it was
  also a real feature, and removing it is a product loss. `src/questions.py` carries the
  full account at the top of the file, including what it would take to bring it back.
- **Judgment no longer beats the candidates to the screen.** Upstream ran the judge and the
  generation in parallel and painted the verdict about a second earlier. They are now two
  halves of one answer and appear together.
- **The candidate scores are per-tone.** Upstream asked its judge one ranking question
  containing every candidate from every tone, so the percentages were comparable across
  tones. Each call now only sees the two candidates it wrote. The ordering the panel
  actually uses is unchanged — it sorts *within* a group, and `#1`/`#2` always meant "the
  better of these two" — but the displayed percentage is now a within-tone share.
- **One repaint fewer.** Upstream pushed the candidates unranked (`排序中`), then again after
  the ranking call returned. The scores arrive with the candidates, so there is one push.
  The streamed early look is unchanged.

## The check

`tools/equivalence_check_macos.py` at the repository root runs both trees on the same
neutral scenario data, rendered into the two wire formats: a Jev `answers` document plus
plain candidate lines for upstream, and one merged JSON object per tone for this version.
Both run in separate processes, because both trees ship modules named `generate`, `styles`
and `userconfig`, and the results are compared key by key.

Both sides are stubbed at the same seam — `generate.http_post_json` (and
`judge_jev.http_post_json` upstream, which imported the name by value). Upstream's real
`JevJudge.judge`, `JevJudge.rank_candidates` and `Generator._parse` run; our real
`Generator.generate` runs end to end, concurrency and all. The downstream halves
(`_payload_from_gen`, `_rank_payload`, `_finish_generate`, `applyJudgment_`) are not
reimplemented: each side's functions are lifted out of its own `src/hud.py` by AST and run
against a fake `self`, because `hud.py` cannot be imported without PyObjC.

    python tools/equivalence_check_macos.py --upstream /path/to/jev-chat-jarvis-mac

Sixteen scenarios: eleven identical on every compared key, five with the divergences listed
below, none failing. Negative controls confirm the harness bites: making the adapter clamp
`risk` into 0..9 turns `risk_outside_its_range` red, and inverting the within-group sort key
turns fifteen of the sixteen red.

Upstream is driven at its **cloud** judge. The local decider-2b produces the same dict
through the same `ACTION_MAP` lookup, but it needs torch and a 7 GB download; it is also the
thing being removed.

### The five documented divergences

| scenario | upstream | this version |
| --- | --- | --- |
| `intent_outside_the_taxonomy` | falls back to `闲聊`, and `actions` follows it through `ACTION_MAP` | shows the phrase the model wrote, with the actions it wrote |
| `intent_is_an_empty_string` | `"" or "闲聊"` → `闲聊` | `暂未判断`; nothing is invented |
| `risk_not_a_number` | `0.0` → the panel reads `● 安全  0/9` | passed through → `风险待判断`. Printing "safe" for a value the model did not give is worse than saying so |
| `confidence_omitted` | `float(None or 0.0)` → `意图识别率 0%` | the key is absent, the row is blank |
| `judgment_fails_entirely_generation_succeeds` | the ranking dies with the judgment (same model), so every candidate keeps `prob = None` and the rows read `排序中` for ever | the scores came back with the candidates, so the ordering survives a failed judgment |

The first four are the same decision seen four times: upstream's judgment layer edited the
model's answer on the way out, and this version does not. The fifth is a consequence of the
merge that happens to favour this version.

### Excluded from the comparison

- `verdict.backend` — upstream reports `jev/<model>`, we report the one model's name. It is
  the producer's name; it must differ.
- the panel's status line, which displays exactly that string.
- the push sequence, reported separately (two `applyCandidates:` upstream, one here).
- API call counts, reported separately: over the sixteen scenarios upstream makes 64 calls
  (16 judgments, 15 rankings, 33 generations) and this version makes 33.

`int` versus `float` differences are reported but not counted: upstream's `float(risk)` makes
`4` into `4.0`, and the adapter does not coerce, so an integer stays an integer.

`confidence` and `intent_probs` are now the model's own self-report rather than a calibrated
distribution — the same model wrote the candidates and rated them. That is a property of the
values, noted in `src/generate.py`, not something the adapter acts on.

## What the check does not cover

- **The displayed language.** Nothing in the prompt or the code pins the intent phrase to
  Chinese any more; an English conversation gets an English phrase. The taxonomy and the
  risk criteria are unaffected.
- **The 86.4% intent accuracy.** That number was measured on decider-2b over 22 messages
  (`judge_zh_test.py`, deleted with the model it tested). It says nothing about a general
  model answering the same question, and the README no longer quotes it as current.
- **Anything needing PyObjC or a real Mac**: the panel's layout, the OCR, the fill path.
  `tools/offline_check.py` covers what the panel would *render* by running the real
  expressions out of `hud.py`; it cannot cover how they look.
