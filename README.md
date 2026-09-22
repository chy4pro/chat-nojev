# chat-nojev

A build of the Jev 聊天助手 conversation copilot in which one ordinary LLM endpoint does both jobs: it reads the conversation and returns the other party's intent, a risk level, whether a reply is due, and the candidate replies, in a single call. You configure one API key instead of two.

Everything else is the upstream app: it reads only what is on your own screen, never hooks or modifies the chat client, and never sends a message for you.

Three variants are in the tree, each derived from its own upstream:

- `windows/` — from jev-chat-windows. One LLM call, one API key.
- `android/` — from jev-chat-jarvis (the Android original). Same change: the
  seven judgment questions, the candidate replies and the ranking come back
  from one call to the reply model, as the typed `Analysis` object the overlay
  already read. `android/docs/EQUIVALENCE.md` states the standard and the
  differential test against upstream that holds it.
- `macos/` — from jev-chat-jarvis-mac. Not a port of the other two: it asks two
  questions of its own (an intent over eight labels, a risk over ten levels)
  and it had *two* judgment models, a local one and a cloud one, plus a third
  call for ranking. All of that now rides along with the per-tone generation
  calls. The local judge is gone, so offline judgment is gone with it — a real
  loss, recorded in `macos/src/questions.py` and
  `macos/docs/EQUIVALENCE.md`.

## How the change is held to the original

The claim is not that the intermediate data matches the original byte for byte.
It is that **the same downstream code handles it the same way**, and each variant
carries a differential test that holds it to that.

Each test writes a scenario once as neutral data, renders it into both wire
formats — the judgment API's answers for the original, the merged object for this
version — runs both sides in separate processes, and compares the results field
by field. The original is stubbed at its drafting and judgment calls; this version
is stubbed only at the HTTP call, so its real parser and adapter run on a raw model
string. Every suite also ships a negative control: a deliberate break that must
turn it red, so a green run means something.

| Variant | Scenarios | Identical | Documented divergences |
|---|---|---|---|
| `windows/` | 15 | 15 | none |
| `android/` | 16 | 16 | none |
| `macos/` | 16 | 11 | five |

The five in `macos/` are places where the original's judgment layer quietly
substituted a value and this version does not: an unparseable risk shows as
"risk not judged" rather than a green "safe 0/9", a missing confidence leaves the
row blank rather than printing 0%, and an intent the eight labels do not cover is
shown as the model wrote it rather than replaced with "small talk". Each is listed
in `macos/docs/EQUIVALENCE.md`.

One rule falls out of all three: the adapter translates and stops there. The
consumers already handle a judgment model returning junk, and re-validating on the
way in does not protect them — it changes what they show. Clamping a risk of 11
into 9 turns "11/9", which the original displays, into "9/9", which it does not.

## Running the tests

    python tools/equivalence_check.py --upstream /path/to/jev-chat-windows
    python tools/equivalence_check_macos.py --upstream /path/to/jev-chat-jarvis-mac
    bash   tools/equivalence_check_android.sh --upstream /path/to/jev-chat-jarvis --probe /path/to/toolchain

The Android one needs a JDK and `kotlinc`, not the Android SDK: the files it
compiles are framework-free apart from three types that the runner stubs.

## Status

Work in progress. Nothing to install yet: the Windows variant has no release
build and the Android one has no APK — this container has no Android SDK, so
nothing here has been packaged or run on a device. The macOS variant has not
been run either: it needs PyObjC, a Mac and WeChat, none of which exist here;
only its offline suites were run.

## Upstream

Derived from [jev-chat](https://github.com/jev-chat) by Finderchangchang, rezoch340 and the jev-chat contributors, MIT licensed. Their original keeps a separate judgment model (TypeSafe Jev) ahead of the reply model; this variant merges the two calls. See NOTICE for the attribution their licence requires.

## Licence

MIT, inheriting the upstream copyright. See LICENSE.
