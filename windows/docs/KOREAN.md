# Korean / KakaoTalk support

한국어 문서: [KOREAN.ko.md](KOREAN.ko.md)

## Why the app could not read Korean

The reader was built around WeChat on Windows, and two of its assumptions are Chinese-specific.

1. **OCR.** RapidOCR ships zh/en detection and recognition models only. On a live KakaoTalk window
   (760×1280, 9 messages on screen) it returned Chinese characters for Korean text — `列是`, `吴`,
   `是` — and took 2.0 s per frame. Not one bubble was read correctly.
2. **Who said it.** `ocr.who_said` calls a bubble mine when its background is green
   (`G > R + 40 and G > B + 40`). KakaoTalk's own bubble is `#FEE500`, so all nine bubbles were
   attributed to the other person. With no `me` line, the copilot cannot tell whose turn it is.

Measured on the same window:

| | RapidOCR (before) | Windows.Media.Ocr (after) |
|---|---|---|
| Bubbles read correctly | 0 / 9 | 9 / 9 |
| Speaker split | 9 her, 0 me | 5 her, 4 me (correct) |
| Frame time | 2.0 s | 0.20 s |

## What changed

- **`app/chatapps.py`** — one `ChatApp` row per chat app: process names, which window is a
  conversation, which OCR backend, how to glue fragments inside a bubble, and the "this bubble is
  mine" colour test. WeChat's old behaviour is the `WECHAT` row, unchanged.
- **`app/ocr_windows.py`** — a `Windows.Media.Ocr` backend with the same call shape as RapidOCR, so
  `app/ocr.py` swaps one for the other. It is offline like RapidOCR: the recognizer comes from the
  OS language pack, nothing leaves the machine. Words are grouped into runs and split where a wide
  horizontal gap appears, which is what separates a right-aligned own message from the bubble
  beside it. Frames are upscaled 2× before recognition; that removes the stray one-character
  fragments for +30 ms (3× brings them back).
- **`app/ocr.py`** — engine chosen per app, with a silent fallback to RapidOCR when the OS has no
  recognizer for the language. Text printed directly on the pane background is treated as a label
  (the sender's name, a timestamp), not a message: WeChat draws that name in grey, KakaoTalk draws
  it as dark as the message text, so the old grey-only rule leaked the name into every turn.
- **`app/capture.py`** — `find_chat_hwnd()` recognizes any known app and returns the window plus its
  profile. KakaoTalk opens one window per conversation, so the roster window is skipped and the
  largest remaining window wins.

## Requirements

Windows must have a Korean recognizer installed — it comes with the Korean language pack
(Settings → Time & language → Language & region). To check:

```powershell
[void][Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
[Windows.Media.Ocr.OcrEngine]::AvailableRecognizerLanguages | % { $_.LanguageTag }
```

Without one, the app falls back to RapidOCR and Korean stays unreadable.

## Adding another app

Add a row to `app/chatapps.py`: process names, the window title to prefer or skip, the OCR backend,
the separator between fragments (`""` for Chinese, `" "` for Korean), and a predicate that answers
"is this bubble background mine". Sample the colours from a real window first — the KakaoTalk row
came from a frame whose ground was `#BACEE0`, other side `#FFFFFF`, own bubble `#FEE500`.

## Known limits

- Korean OCR still makes typos on small text (`헷갈리던데` → `혯갈리던데`). The judging model reads
  through them, but the debug view will show them.
- Only 1:1 KakaoTalk chats were measured. Group chats name every speaker above the bubble; the name
  rule should catch it, but it is untested.
- Emoji and stickers sometimes leave a stray character at the start of a bubble.
