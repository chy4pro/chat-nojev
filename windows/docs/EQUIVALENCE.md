# Equivalence with upstream

## The standard

The goal is **not** that our `answers` dict matches upstream's byte for byte. It is
that the same downstream code handles it the same way: `core/engine.py` and
`app/overlay.py` must produce the same result from our merged call that they produce
from upstream's two calls.

That is a weaker requirement than an identical dict, and it is the right one, because
the downstream code is already hardened. Upstream had to survive its own judgment model
returning junk, so it validates at the point of use:

- `app/overlay.py::_choice` (upstream line 51) is `_CHOICES[name].get(<label>, "暂未判断")` —
  a label outside the taxonomy already renders as "not judged".
- `app/overlay.py` line 1054: `valid_score = isinstance(score, (int, float)) and isfinite(score)
  and 0 <= score <= 9`, and both the text and the colour fall back when that is false. A
  non-numeric, infinite or out-of-range score is already handled.
- `core/engine.py` maps an unknown `best_reply.choice` through `_REPLY_IDX.get(key, 0)`
  (upstream line 51), guards `best_index >= len(candidates)`, and wraps the score
  extraction in `except (TypeError, ValueError)` with a `0.0` fallback.

So the judgment and ranking adapter in `core/draft.py` is a **translation layer only**. It
moves the model's values into the shapes those consumers read and passes the values
through as they are. It deliberately does not re-validate what the consumers already
validate — re-validating would *change* behaviour rather than preserve it. Upstream shows
"紧张度待判断" for a score of 11; clamping it to 9 would show "紧张度 9/9".

If a value is missing entirely the key is left out, the way upstream does when its
judgment model omits an answer. Nothing is invented in its place.

What the adapter still does, because it is structurally necessary:

- putting values under the right keys, in the `{"type": ..., "choice"/"score"/"noul": ...}`
  shapes the consumers read;
- tolerating the spellings a model uses for a noul — a bare number, `{"noul": x}`, or
  `true`/`false` for 1 / 0 — and the same bare-value spelling for a choice or a score;
- accepting the judgment fields whether the model nested them under `judgment` or put them
  at the top level;
- extracting the JSON object from the response text, including a fenced block, and the
  line-by-line fallback for a truncated answer — that is parsing, not validation;
- the top-up call when fewer than three candidates come back, and its failure handling;
- the candidate sanitisation upstream already does in `draft_candidates` (injection
  suspects, echoing the other party), which is unrelated to the judgment;
- mapping the per-candidate scores **by candidate text rather than by position**. The model
  scores the candidates it wrote; sanitisation may drop some of them, so the surviving
  texts have to be relocated to their new positions. That is correctness, not validation.
  When the named candidate does not survive, the label the model wrote is passed through
  unchanged and `_REPLY_IDX.get(key, 0)` in the engine does what it does upstream.

## The check

`tools/equivalence_check.py` at the repository root runs both trees on the same neutral
scenario data, rendered into the two wire formats: a Jev `answers` dict for upstream, and
the merged JSON object for this version. Both run in separate processes, because both
trees ship a package named `core`, and the returned dicts are compared key by key.

Upstream is stubbed at `draft_candidates` and `ask`. This version is stubbed only at the
HTTP call inside `core/llm.py`, so its real parser and adapter run on a raw model string.

    python tools/equivalence_check.py --upstream /path/to/jev-chat-windows

Fifteen scenarios, **all fifteen identical on every key except `usage`**, none failing. A
negative control confirms the harness bites: inverting `_REPLY_IDX` turns fourteen of the
fifteen red.

Two scenarios exist specifically to hold the standard in place:

- `values_outside_the_taxonomy` feeds both sides a `true_intent` the taxonomy does not
  contain and a `danger_level` of 11. Both sides carry them through identically.
- `probabilities_not_normalized` feeds both sides per-candidate scores that sum to 10.
  Neither side normalises them.

## Divergences that used to be documented here

All three are gone, because each was the adapter re-validating:

- **A dead candidate label.** The adapter used to drop `best_reply.choice` when it named a
  candidate that no longer existed. It now passes the label through and the engine clamps,
  exactly as upstream does.
- **Unnormalised scores.** The adapter used to rescale the per-candidate scores to sum to
  one. It no longer touches them. The two scenarios that covered this are now one.
- **A missing confidence.** The adapter used to substitute the probability the model gave
  the option it chose. It now leaves the key out, as upstream does.

The `int` versus `float` note is also gone: the adapter no longer coerces
`danger_level.score`, so an integer stays an integer.

`confidence` and `probabilities` are still the model's own self-report rather than a
calibrated distribution — the same model wrote the candidates and rated them. That is a
property of the values, noted in `core/draft.py`, not something the adapter acts on.

## usage

Upstream reports the judgment endpoint's token counts. This version reports an empty dict:
`core/llm.chat()` returns text only and does not surface token counts. `usage` is excluded
from the comparison.

## What the check does not cover: the displayed wording

The check compares `analyze()` output, not what the panel renders. One rendering change is
deliberate and is not equivalent to upstream:

Upstream's three choice questions are answered by a classifier, which can only pick from a
closed set of English keys, so `app/overlay.py` carried a `_CHOICES` table translating each
key into Chinese display text. A general model does not need a closed set. The prompt
(`core/questions.py::_CHOICE_ANSWER`) now asks it to name the option it picked as a short
phrase **in the language of the conversation**, and to write its own phrase if none of the
options fits; `_CHOICES` is deleted and `_choice` shows the string it was given, falling
back to "暂未判断" only when the value is missing, empty, or not a string.

The English keys and descriptions in `core/questions.py` are unchanged — they still frame
the questions. Only the answer vocabulary changed. The consequence is that the displayed
wording in the ordinary case is no longer word-for-word identical to upstream's fixed
strings: a Chinese conversation shows something close to them, an English conversation
shows English. Nothing in the prompt or the code pins the display language any more.
`danger_level` and everything else numeric are unaffected.
