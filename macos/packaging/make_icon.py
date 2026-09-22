#!/usr/bin/env python3
"""Render the app icon into an .iconset directory (then iconutil turns it into .icns).

Drawn offscreen with AppKit — no Pillow, no ImageMagick. Design: WeChat-green rounded
square, a white "J" mark, and a small badge dot in the corner.

Usage: python3 packaging/make_icon.py <out.iconset>
"""

from __future__ import annotations

import sys
from pathlib import Path

import AppKit
from AppKit import (
    NSBitmapImageRep,
    NSColor,
    NSFont,
    NSGraphicsContext,
    NSMutableParagraphStyle,
    NSString,
    NSPNGFileType,
)
from Foundation import NSMakeRect

GREEN = (0.027, 0.757, 0.376)   # WeChat #07C160
SIZES = [16, 32, 64, 128, 256, 512, 1024]


def render(px: int) -> bytes:
    rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, px, px, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.setCurrentContext_(ctx)

    inset = px * 0.09
    radius = px * 0.225
    body = NSMakeRect(inset, inset, px - 2 * inset, px - 2 * inset)

    # rounded square, WeChat green
    path = AppKit.NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(body, radius, radius)
    NSColor.colorWithCalibratedRed_green_blue_alpha_(*GREEN, 1.0).set()
    path.fill()

    # the "J" mark
    text = NSString.stringWithString_("J")
    font = NSFont.boldSystemFontOfSize_(px * 0.56)
    style = NSMutableParagraphStyle.alloc().init()
    style.setAlignment_(AppKit.NSTextAlignmentCenter)
    attrs = {
        AppKit.NSFontAttributeName: font,
        AppKit.NSForegroundColorAttributeName: NSColor.whiteColor(),
        AppKit.NSParagraphStyleAttributeName: style,
    }
    text.drawInRect_withAttributes_(
        NSMakeRect(body.origin.x, body.origin.y + px * 0.14, body.size.width, px * 0.62),
        attrs)

    # speak-bubble tail: a small white notch on the lower-left of the mark, so the icon
    # reads as "conversation" rather than a plain letter tile
    tail = AppKit.NSBezierPath.bezierPath()
    tx, ty = body.origin.x + body.size.width * 0.30, body.origin.y + body.size.height * 0.20
    tail.moveToPoint_((tx, ty + px * 0.10))
    tail.lineToPoint_((tx + px * 0.14, ty + px * 0.10))
    tail.lineToPoint_((tx, ty - px * 0.04))
    tail.closePath()
    NSColor.whiteColor().set()
    tail.fill()

    NSGraphicsContext.restoreGraphicsState()
    data = rep.representationUsingType_properties_(NSPNGFileType, {})
    return bytes(data)


def main() -> None:
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "AppIcon.iconset")
    out.mkdir(parents=True, exist_ok=True)

    # AppKit drawing wants an application object present, even headless
    AppKit.NSApplication.sharedApplication()

    written = 0
    for size in SIZES:
        png = render(size)
        for scale, suffix in ((1, ""), (2, "@2x")):
            logical = size // scale
            if logical < 16 or size % scale:
                continue
            name = f"icon_{logical}x{logical}{suffix}.png"
            (out / name).write_bytes(png if scale == 1 else render(size))
            written += 1
    print(f"wrote {written} pngs to {out}")


if __name__ == "__main__":
    main()
