# Equivalence with upstream

`tools/equivalence_check.py` at the repository root proves that the processing
between "what the API returned" and "what `analyze()` returns" behaves the same
here as it does upstream. Each scenario is written once as neutral data, then
rendered into the two wire formats: a Jev `answers` dict for upstream, and the
merged JSON object for this version. Both are run in separate processes, because
both trees ship a package named `core`, and the returned dicts are compared key
by key.

Upstream is stubbed at `draft_candidates` and `ask`. This version is stubbed only
at the HTTP call inside `core/llm.py`, so its real parser and adapter run on a raw
model string.

Run it with:

    python tools/equivalence_check.py --upstream /path/to/jev-chat-windows

Fifteen scenarios: twelve identical on every key except `usage`, three with a
known divergence, none failing. A negative control confirms the harness bites:
inverting `_REPLY_IDX` turns thirteen of the fifteen red.

## The three known divergences

**A dead candidate label.** When the model names a candidate that does not exist,
upstream passes the label through into `answers` and the engine clamps the index;
this version drops the label in the adapter and the engine falls back by its other
branch. `best_index`, `best_reply`, `candidates` and `scores` are identical. Only
a label the UI cannot resolve differs, and this version is the stricter of the two.

**Unnormalised scores.** This version normalises the per-candidate scores to sum
to one, because a drafting model's self-reported numbers are not a distribution.
The Jev API returns one by contract, so upstream has nothing to normalise. The
sibling scenario proves the point: rendered as the distribution Jev would actually
return, the two sides match exactly. The raw variant can only come from an input
the old API could not produce.

**A missing confidence.** When the model omits `confidence`, this version falls
back to the probability it gave the option it chose, which is what that number
means for a choice answer. Jev always returns `confidence`, so upstream never sees
this input either.

All three arise only from inputs the judgment API could not have produced. None of
the three values is read by `app/overlay.py`.

## Representation

`answers.danger_level.score` is an `int` upstream in this test and a `float` here,
because the adapter's numeric helper returns `float`. The values are equal, and the
only consumer formats it with `:.0f`. Note that the real Jev API returns an
interpolated float for a score question, so the `int` in the test is an artefact of
the fixture rather than a property of upstream.

## usage

Upstream reports the judgment endpoint's token counts. This version reports an
empty dict: `core/llm.chat()` returns text only and does not surface token counts.
