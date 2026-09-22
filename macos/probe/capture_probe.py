#!/usr/bin/env python3
"""Capture probe: find WeChat's main window, capture it, OCR it with Vision.

Validates the OCR route end to end and measures real Chinese recognition quality
on a live WeChat window.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import Quartz


def all_wechat_windows() -> list[dict]:
    opts = Quartz.kCGWindowListOptionAll | Quartz.kCGWindowListExcludeDesktopElements
    wins = Quartz.CGWindowListCopyWindowInfo(opts, Quartz.kCGNullWindowID)
    rows = []
    for w in wins:
        owner = w.get("kCGWindowOwnerName") or ""
        if "WeChat" in owner or "微信" in owner:
            b = dict(w.get("kCGWindowBounds") or {})
            rows.append({
                "owner": owner,
                "pid": w.get("kCGWindowOwnerPID"),
                "wid": w.get("kCGWindowNumber"),
                "title": w.get("kCGWindowName") or "",
                "w": b.get("Width", 0), "h": b.get("Height", 0),
                "x": b.get("X", 0), "y": b.get("Y", 0),
                "layer": w.get("kCGWindowLayer"),
                "onscreen": bool(w.get("kCGWindowIsOnscreen", False)),
            })
    return sorted(rows, key=lambda r: r["w"] * r["h"], reverse=True)


def capture_window(wid: int, out: Path) -> bool:
    """screencapture CLI captures a window by ID, unaffected by partial occlusion."""
    p = subprocess.run(["screencapture", "-x", "-o", "-l", str(wid), str(out)],
                       capture_output=True, text=True)
    return p.returncode == 0 and out.exists() and out.stat().st_size > 0


def ocr_image(path: Path) -> list[dict]:
    """Vision framework OCR with zh-Hans, returning text + bbox + confidence."""
    import Vision
    from Foundation import NSURL
    import objc

    url = NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, None)

    results: list[dict] = []

    def completion(request, error):
        if error:
            results.append({"error": str(error)})
            return
        for obs in request.results() or []:
            candidates = obs.topCandidates_(1)
            if not candidates:
                continue
            c = candidates[0]
            bb = obs.boundingBox()
            results.append({
                "text": c.string(),
                "conf": round(float(c.confidence()), 3),
                # Vision bbox: origin bottom-left, normalized
                "x": round(float(bb.origin.x), 4),
                "y": round(float(bb.origin.y), 4),
                "w": round(float(bb.size.width), 4),
                "h": round(float(bb.size.height), 4),
            })

    req = Vision.VNRecognizeTextRequest.alloc().initWithCompletionHandler_(completion)
    req.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    req.setRecognitionLanguages_(["zh-Hans", "en-US"])
    req.setUsesLanguageCorrection_(True)
    handler.performRequests_error_([req], None)
    return results


def main() -> None:
    wins = all_wechat_windows()
    print("=== all WeChat windows ===")
    for w in wins:
        print(f"  wid={w['wid']} {w['w']:.0f}x{w['h']:.0f} at ({w['x']:.0f},{w['y']:.0f}) "
              f"layer={w['layer']} onscreen={w['onscreen']} title={w['title']!r}")

    if not wins:
        print("no WeChat windows found")
        return

    target = wins[0]
    print(f"\n=== capture wid={target['wid']} ({target['w']:.0f}x{target['h']:.0f}) ===")
    with tempfile.TemporaryDirectory() as td:
        png = Path(td) / "cap.png"
        ok = capture_window(int(target["wid"]), png)
        print("capture:", "OK" if ok else "FAILED", png.stat().st_size if ok else "")
        if not ok:
            return
        keep = Path("/tmp/jevvid") / "wechat_capture.png"
        keep.write_bytes(png.read_bytes())
        print("saved to", keep)

        print("\n=== Vision OCR (zh-Hans) ===")
        items = ocr_image(png)
        if items and "error" in items[0]:
            print("OCR error:", items[0]["error"])
            return
        items_sorted = sorted(items, key=lambda r: -r["y"])
        print(f"blocks: {len(items_sorted)}")
        for it in items_sorted[:40]:
            print(f"  y={it['y']:.3f} x={it['x']:.3f} conf={it['conf']:.2f}  {it['text']}")
        json.dump(items_sorted, open("/tmp/jevvid/ocr_result.json", "w"),
                  ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
