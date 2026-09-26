"""Experimental text-bubble extraction inside a user-confirmed rectangle.

No OCR height/length deletion. Uniform bubble surfaces are located from padding next
 to text. Unresolved blocks stay unknown; image/quote support is deliberately limited.
"""
from collections import deque
import numpy as np
import Quartz
from perception import Message, _is_noise


def pixels(image):
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    data = np.zeros((height, width, 4), dtype=np.uint8)
    ctx = Quartz.CGBitmapContextCreate(data, width, height, 8, width * 4,
        Quartz.CGColorSpaceCreateDeviceRGB(), Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, width, height), image)
    return data[:, :, :3]


def component(mask, sx, sy, limit):
    """Scanline flood fill: bound work for background and avoid per-pixel Python loops."""
    h, w = mask.shape
    seen = np.zeros_like(mask)
    queue = deque([(sx, sy)])
    left, top, right, bottom, area = sx, sy, sx, sy, 0
    while queue:
        x, y = queue.popleft()
        if seen[y, x] or not mask[y, x]:
            continue
        row = mask[y] & ~seen[y]
        a, b = x, x
        while a > 0 and row[a-1]: a -= 1
        while b + 1 < w and row[b+1]: b += 1
        seen[y, a:b+1] = True
        area += b-a+1
        if area > limit:
            return None
        left, right = min(left, a), max(right, b)
        top, bottom = min(top, y), max(bottom, y)
        for ny in (y-1, y+1):
            if not 0 <= ny < h: continue
            runs = mask[ny, a:b+1] & ~seen[ny, a:b+1]
            starts = np.flatnonzero(runs & ~np.r_[False, runs[:-1]])
            queue.extend((a + int(dx), ny) for dx in starts)
    return left, top, right+1, bottom+1, area


def extract(image, blocks, rect, max_messages=12):
    rgb = pixels(image)
    H, W = rgb.shape[:2]
    rx, ry, rw, rh = rect
    x0, y0 = round(rx*W), round(ry*H)
    x1, y1 = round((rx+rw)*W), round((ry+rh)*H)
    crop = rgb[y0:y1, x0:x1].astype(np.int16)
    h, w = crop.shape[:2]
    # Background occupies the empty space between bubbles, usually the pane centre.
    background = np.median(crop.reshape(-1, 3), axis=0)
    groups, unresolved = [], []
    for b in sorted(blocks, key=lambda b: 1-b.y-b.h):
        if _is_noise(b): continue
        bx, by = b.x*W-x0, (1-b.y-b.h)*H-y0
        bw, bh = b.w*W, b.h*H
        if bx < 0 or by < 0 or bx+bw > w+1 or by+bh > h+1: continue
        group = next((g for g in groups if g[0][0] <= bx and g[0][1] <= by
                      and g[0][2] >= bx+bw-1 and g[0][3] >= by+bh-1), None)
        if group is not None:
            group[1].append(b)
            continue
        box = None
        for sx in (round(bx-3), round(bx+bw+3)):
            sy = round(by+bh/2)
            if not (0 <= sx < w and 0 <= sy < h): continue
            color = crop[sy, sx]
            if np.max(np.abs(color-background)) < 7: continue
            mask = np.max(np.abs(crop-color), axis=2) <= 6
            found = component(mask, sx, sy, w*h*.35)
            if found is None: continue
            l,t,r,bot,area = found
            if (l <= bx and t <= by and r >= bx+bw-1 and bot >= by+bh-1
                    and bot-t >= bh*1.25 and area/((r-l)*(bot-t)) >= .60
                    and l > 0 and r < w and t > 0 and bot < h):
                box = (l,t,r,bot)
                break
        if box is None:
            unresolved.append(b)
        else:
            groups.append((box,[b]))
    # Short bubbles establish side anchors; long wrapping bubbles can share those
    # anchors without being classified by their (potentially central) text centre.
    anchors = {'them': [], 'me': []}
    for (l,t,r,bot), members in groups:
        near = min(max(b.h*H for b in members)*5, w*.30)
        if l < near and l*1.8 < w-r: anchors['them'].append(l)
        if w-r < near and (w-r)*1.8 < l: anchors['me'].append(r)
    messages = []
    for (l,t,r,bot), members in groups:
        left_gap, right_gap = l, w-r
        # Bubble padding and avatar column are measured within the selected pane.
        near = min(max(b.h*H for b in members)*5, w*.30)
        side = ('them' if left_gap < near and left_gap*1.8 < right_gap else
                'me' if right_gap < near and right_gap*1.8 < left_gap else 'unknown')
        members.sort(key=lambda b: (round((1-b.y-b.h)*H / max(1,b.h*H*.5)), b.x))
        tolerance = max(b.h*H for b in members)*.6
        left_match = any(abs(l-a)<tolerance for a in anchors['them'])
        right_match = any(abs(r-a)<tolerance for a in anchors['me'])
        if left_match != right_match:
            side = 'them' if left_match else 'me'
        text = '\n'.join(b.text for b in members)
        sender = None
        if side == 'them':
            first = members[0]
            names = [b for b in unresolved
                     if b.h < first.h*.9 and abs(b.x-first.x)*W < first.h*H*1.5
                     and 0 < (1-first.y-first.h)-(1-b.y) < first.h*2]
            if len(names) == 1:
                sender = names[0].text
                unresolved.remove(names[0])
        messages.append(Message(text,side,(y0+t)/H,min(b.conf for b in members),
            h=(bot-t)/H,sender=sender,lines=[b.text for b in members],
            x=(x0+l)/W,w=(r-l)/W,last_y=(y0+t)/H))
    for b in unresolved:
        messages.append(Message(b.text,'unknown',1-b.y-b.h,b.conf,
            h=b.h,lines=[b.text],x=b.x,w=b.w,last_y=1-b.y-b.h))
    return sorted(messages,key=lambda m:m.y)[-max_messages:]


def recover_numeric_bubbles(image, blocks, rect):
    """One local accurate OCR pass for empty, compact, uniform bubbles only.

    Vision can omit isolated digits in a full chat image. Never infer sequences or
    convert lookalike letters to numbers; retain only explicit digits at Vision confidence >= 0.5.
    """
    from perception import ocr_image, TextBlock
    rgb = pixels(image)
    H, W = rgb.shape[:2]
    rx, ry, rw, rh = rect
    x0, y0 = round(rx*W), round(ry*H)
    crop = rgb[y0:round((ry+rh)*H), x0:round((rx+rw)*W)].astype(np.int16)
    h, w = crop.shape[:2]
    background = np.median(crop.reshape(-1, 3), axis=0)
    colors, counts = np.unique(crop[::4, ::4].reshape(-1, 3), axis=0, return_counts=True)
    recovered = []
    for color in colors[np.argsort(counts)[-6:]]:
        # Neutral received bubbles; do not search avatars, green overlays or stickers.
        if np.ptp(color) > 12 or not 7 <= np.max(np.abs(color-background)) <= 65:
            continue
        mask = np.max(np.abs(crop-color), axis=2) <= 4
        for sy, sx in np.argwhere(mask[::4, ::4])*4:
            if not mask[sy, sx]: continue
            found = component(mask, int(sx), int(sy), w*h*.35)
            if found is None: break
            l,t,r,b,area = found
            mask[t:b, l:r] = False
            bw, bh = r-l, b-t
            if (not 16 <= bh <= h*.15 or not .5 <= bw/bh <= 2.5
                    or area/(bw*bh) < .78 or l <= 0 or t <= 0 or r >= w or b >= h
                    or min(l,w-r) > w*.25):
                continue
            full = (x0+l, y0+t, bw, bh)
            if any(full[0] <= bb.x_center*W <= full[0]+bw
                   and full[1] <= (1-bb.y-bb.h/2)*H <= full[1]+bh
                   for bb in blocks+recovered):
                continue
            bubble = Quartz.CGImageCreateWithImageInRect(image, Quartz.CGRectMake(*full))
            # Fixed local magnification, not a chain of guessed OCR fallbacks.
            factor = max(1, int(np.ceil(192 / bh)))
            ctx = Quartz.CGBitmapContextCreate(None,bw*factor,bh*factor,8,bw*factor*4,
                Quartz.CGColorSpaceCreateDeviceRGB(),Quartz.kCGImageAlphaPremultipliedLast)
            Quartz.CGContextDrawImage(ctx,Quartz.CGRectMake(0,0,bw*factor,bh*factor),bubble)
            local = ocr_image(Quartz.CGBitmapContextCreateImage(ctx),languages=('en-US',),chat_only=False)
            if len(local) != 1: continue
            bb = local[0]
            if not bb.text.isascii() or not bb.text.isdigit() or bb.conf < .5: continue
            recovered.append(TextBlock(bb.text,bb.conf,(full[0]+bb.x*bw)/W,
                1-(full[1]+(1-bb.y)*bh)/H,bb.w*bw/W,bb.h*bh/H))
    return blocks+recovered
