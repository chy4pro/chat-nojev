"""Shared WeChat text-bubble classification inside automatic or calibrated bounds.

No OCR height/length deletion. Uniform bubble surfaces are located from padding next
to text. Nested neutral panels and detached quote rails supply quote boundaries.
Unresolved ownership stays unknown; unverified media layouts are not interpreted.
"""
from collections import deque
from math import ceil, floor
import re
import numpy as np
import Quartz
from perception import Message, TextBlock, MIN_CONF

Box = tuple[int, int, int, int]


def pixels(image):
    width, height = Quartz.CGImageGetWidth(image), Quartz.CGImageGetHeight(image)
    data = np.zeros((height, width, 4), dtype=np.uint8)
    ctx = Quartz.CGBitmapContextCreate(data, width, height, 8, width * 4,
        Quartz.CGColorSpaceCreateDeviceRGB(), Quartz.kCGImageAlphaPremultipliedLast)
    Quartz.CGContextDrawImage(ctx, Quartz.CGRectMake(0, 0, width, height), image)
    return data[:, :, :3]


def component(mask, sx, sy, limit):
    """Scanline flood fill: bound work for background and avoid per-pixel Python loops.

    limit 是面积上限，超过则返回 None（削 587ms 那种尖峰）。同时硬限 50 万像素
    （即使 limit 更大），避免极端画面下遍历百万级像素。
    """
    limit = min(limit, 500000)
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
    crop_full = rgb[y0:y1, x0:x1].astype(np.int16)
    h_full, w_full = crop_full.shape[:2]
    # Retina captures (≈1468×1198) make component() traverse ~1M pixels per call.
    # Stride-2 downsampling cuts area to 1/4; colour differences between bubble and
    # background survive at half resolution, so classification is unchanged.
    # Synthetic test canvases (≤600px) stay at scale=1 to keep pixel-exact assertions.
    scale = 2 if min(h_full, w_full) > 600 else 1
    crop = crop_full[::scale, ::scale] if scale > 1 else crop_full
    h, w = crop.shape[:2]
    # A tall bubble may occupy most of the pane. Its exterior margins, rather
    # than the whole crop, provide the background colour in that case.
    margin = max(1, round(w*.02))
    background = np.median(np.concatenate((crop[:, :margin].reshape(-1,3),
                                           crop[:, -margin:].reshape(-1,3))), axis=0)
    # Fixed header strips span the pane, unlike bubbles with an avatar margin.
    # Include faint full-width separators when the strip shares the pane colour.
    # ponytail: top-of-pane horizontal strips only; other layouts need real samples.
    header_bottom = 0
    for row_y in range(min(h, round(h*.20))):
        row = crop[row_y]
        color = np.median(row, axis=0)
        if (np.max(np.abs(color-background)) >= 2
                and np.mean(np.max(np.abs(row-color), axis=1) <= 1) >= .94):
            header_bottom = row_y+1
    groups: dict[Box, tuple[list[TextBlock], np.ndarray]] = {}
    unresolved: list[TextBlock] = []
    surfaces: list[tuple[Box, np.ndarray]] = []

    def surface_at(sx, sy):
        if not (0 <= sx < w and 0 <= sy < h): return None
        color = crop[sy, sx]
        if np.max(np.abs(color-background)) < 7: return None
        for box, known_color in surfaces:
            l,t,r,bot = box
            if l <= sx < r and t <= sy < bot and np.max(np.abs(color-known_color)) <= 6:
                return box, known_color
        mask = np.max(np.abs(crop-color), axis=2) <= 6
        found = component(mask, sx, sy, w*h*.85)
        if found is None: return None
        l, t, r, bot, area = found
        # A nested quote can occupy most of a bubble. The outer padding still
        # forms a rectangle; check its border rather than requiring a solid fill.
        border = np.r_[mask[t, l:r], mask[bot-1, l:r], mask[t:bot, l], mask[t:bot, r-1]]
        density = area/((r-l)*(bot-t))
        if (l > 0 and r < w and t > 0 and bot < h
                and (density >= .60 or (density >= .25 and np.mean(border) >= .75))):
            surface = ((l, t, r, bot), color)
            surfaces.append(surface)
            return surface
        return None
    for b in sorted(blocks, key=lambda b: 1-b.y-b.h):
        # UI words such as “发送” may be genuine bubble/quote content. Region and
        # surface ownership decide admission; never delete a body by substring.
        if b.conf < MIN_CONF or not b.text.strip(): continue
        bx, by = (b.x*W-x0)/scale, ((1-b.y-b.h)*H-y0)/scale
        bw, bh = b.w*W/scale, b.h*H/scale
        if bx < 0 or by < 0 or bx+bw > w+1 or by+bh > h+1: continue
        if by+bh <= header_bottom: continue
        found = None
        for sx in (round(bx-3), round(bx+bw+3)):
            candidate = surface_at(sx, round(by+bh/2))
            if candidate is None: continue
            (l,t,r,bot), color = candidate
            if (l <= bx and t <= by and r >= bx+bw-1 and bot >= by+bh-1
                    and bot-t >= bh*1.25):
                found = candidate
                break
        if found is None:
            unresolved.append(b)
        else:
            box, color = found
            groups.setdefault(box, ([], color))[0].append(b)

    # Some WeChat quotes have no filled panel, just a neutral vertical rail on
    # the pane background. Locate that rail independently of bubble surfaces.
    rails: dict[Box, list[TextBlock]] = {}
    for b in unresolved[:]:
        bx, by, bh = (b.x*W-x0)/scale, ((1-b.y-b.h)*H-y0)/scale, b.h*H/scale
        sy = round(by+bh/2)
        if not 0 <= sy < h: continue
        for sx in range(max(0, round(bx-bh*1.8)), max(0, round(bx-3))):
            color = crop[sy,sx]
            contrast = np.max(np.abs(color-background))
            if np.ptp(color) > 16 or not 8 <= contrast <= 120: continue
            mask = np.max(np.abs(crop-color),axis=2) <= 3
            found = component(mask,sx,sy,w*h*.02)
            if found is None: continue
            l,t,r,bot,area = found
            if (r <= bx-3 and r-l <= max(3,bh*.25)
                    and bh*.8 <= bot-t <= bh*6 and t <= by+bh*.2 and bot >= by+bh*.8):
                rails.setdefault((l,t,r,bot),[]).append(b)
                unresolved.remove(b)
                break
    bare_quotes = set()
    for (l,t,r,bot), members in rails.items():
        # The rail proves ownership; OCR's box may extend beyond it slightly.
        top = min(t, min(floor(((1-b.y-b.h)*H-y0)/scale) for b in members))
        bottom = max(bot, max(ceil(((1-b.y)*H-y0)/scale) for b in members))
        right = max(ceil((b.x_right*W-x0)/scale) for b in members)
        box = (l,top,right,bottom)
        groups[box] = (members, background)
        bare_quotes.add(box)

    # A quote-only OCR result may still sit inside a larger empty outer bubble.
    for (l,t,r,bot) in list(groups):
        for sx,sy in ((l-3, (t+bot)//2), (r+3, (t+bot)//2)):
            candidate = surface_at(sx, sy)
            if candidate is None: continue
            box, color = candidate
            if box[0] < l and box[1] < t and box[2] > r and box[3] > bot:
                groups.setdefault(box, ([], color))
    # Find nested surfaces BEFORE assigning any OCR block to body/quote. This also
    # preserves a mixed OCR block as unresolved content instead of inventing a split.
    panels: dict[Box, list[Box]] = {box: [] for box in groups}
    for outer, (members, color) in list(groups.items()):
        l,t,r,bot = outer
        inset = max(4, min(round(min((b.h*H/scale for b in members), default=16)*.7), round(w*.04)))
        for sy in range(t+inset, bot-inset, max(1, inset//2)):
            candidate = surface_at(l+inset, sy)
            if candidate is None: continue
            box, inner_color = candidate
            il,it,ir,ib = box
            if (l < il < ir < r and t < it < ib < bot
                    and ir-il > (r-l)*.45 and ib-it >= inset*1.5
                    and np.ptp(inner_color) <= 16 and box not in panels[outer]):
                panels[outer].append(box)
        for inner in groups:
            il,it,ir,ib = inner
            if l < il < ir < r and t < it < ib < bot and inner not in panels[outer]:
                panels[outer].append(inner)
    nested = {inner for items in panels.values() for inner in items}
    for outer in groups:
        if outer in nested: continue
        for inner in panels[outer]:
            if inner in groups:
                groups[outer][0].extend(groups[inner][0])
    groups = {box: value for box, value in groups.items() if box not in nested}

    # A detached quote needs a structural quote rail. Small grey text alone can
    # also be a normal message, so it is insufficient to steal another bubble.
    detached = set(bare_quotes)
    for box, (members, color) in groups.items():
        l,t,r,bot = box
        band = crop[t+3:bot-3, l+2:min(r, l+14)]
        dark = np.mean(band, axis=2) < np.mean(color)-25
        if dark.size and np.any(np.mean(dark, axis=0) > .65):
            detached.add(box)
    for quote_box in sorted(detached, key=lambda box: box[1]):
        ql,qt,qr,qb = quote_box
        quote_members = groups[quote_box][0]
        candidates = [box for box in groups if box not in detached
                      and 0 <= qt-box[3] <= max(b.h*H/scale for b in quote_members)*1.5
                      and (abs(ql-box[0]) < max(b.h*H/scale for b in quote_members)
                           or abs(qr-box[2]) < max(b.h*H/scale for b in quote_members))]
        if len(candidates) == 1:
            parent = candidates[0]
            panels[parent].append(quote_box)
            groups[parent][0].extend(quote_members)
    # Short bubbles establish side anchors; long wrapping bubbles can share those
    # anchors without being classified by their (potentially central) text centre.
    anchors: dict[str, list[int]] = {'them': [], 'me': []}
    for (l,t,r,bot), (members, color) in groups.items():
        if (l,t,r,bot) in detached: continue
        near = min(max(b.h*H/scale for b in members)*5, w*.30)
        if l < near and l*1.8 < w-r: anchors['them'].append(l)
        if w-r < near and (w-r)*1.8 < l: anchors['me'].append(r)
    messages = []
    for (l,t,r,bot), (members, color) in groups.items():
        if (l,t,r,bot) in detached: continue
        left_gap, right_gap = l, w-r
        # Bubble padding and avatar column are measured within the selected pane.
        near = min(max(b.h*H/scale for b in members)*5, w*.30)
        side = ('them' if left_gap < near and left_gap*1.8 < right_gap else
                'me' if right_gap < near and right_gap*1.8 < left_gap else 'unknown')
        # Compare all blocks in the same coordinate system. Per-block font
        # heights cannot serve as a sorting unit for a variable-height line.
        ordered = sorted(members, key=lambda b: 1-b.y-b.h)
        rows: list[list[TextBlock]] = []
        for b in ordered:
            if rows and abs((1-b.y-b.h)-(1-rows[-1][0].y-rows[-1][0].h)) < min(b.h, rows[-1][0].h)*.5:
                rows[-1].append(b)
            else:
                rows.append([b])
        members = [b for row in rows for b in sorted(row, key=lambda b: b.x)]
        tolerance = max(b.h*H/scale for b in members)*.6
        left_match = any(abs(l-a)<tolerance for a in anchors['them'])
        right_match = any(abs(r-a)<tolerance for a in anchors['me'])
        if left_match != right_match:
            side = 'them' if left_match else 'me'
        quote_blocks, body_blocks = [], []
        mixed = False
        for b in members:
            bx, by = (b.x*W-x0)/scale, ((1-b.y-b.h)*H-y0)/scale
            if any(il <= bx and it <= by and ir >= bx+b.w*W/scale-1 and ib >= by+b.h*H/scale-1
                   for il,it,ir,ib in panels[(l,t,r,bot)]):
                quote_blocks.append(b)
            else:
                body_blocks.append(b)
                if any(bx < ir and bx+b.w*W/scale > il and by < ib and by+b.h*H/scale > it
                       for il,it,ir,ib in panels[(l,t,r,bot)]):
                    mixed = True
        if mixed:
            # OCR merged roles into one block; retain original reading order.
            body_blocks, quote_blocks = members, []
        text = '\n'.join(b.text for b in body_blocks)
        quote = '\n'.join(b.text for b in quote_blocks)
        quote_sender = ''
        author = re.match(r'^([^：:\n]{1,40})[：:]\s*', quote)
        if author:
            quote_sender, quote = author[1], quote[author.end():]
        if not text: continue  # An explicit quote alone is not a new utterance.
        sender = None
        if side == 'them':
            first = body_blocks[0]
            names = [b for b in unresolved
                     if abs(b.x-first.x)*W < first.h*H*1.5
                     and 0 <= (y0+t*scale)/H-(1-b.y) < first.h*2]
            if names:
                # A nickname is outside a confirmed bubble, on the nearest row.
                # Font size and text length cannot distinguish names from bodies.
                nearest = max(names, key=lambda b: 1-b.y)
                row = sorted((b for b in unresolved
                              if abs(b.y-nearest.y)*H < first.h*H*.5
                              and b.x >= nearest.x), key=lambda b: b.x)
                names = [nearest]
                for b in row:
                    if b is nearest: continue
                    if (b.x-names[-1].x_right)*W > first.h*H*1.5: break
                    names.append(b)
                sender = ' '.join(b.text for b in names)
                for b in names: unresolved.remove(b)
        left = min([l] + [p[0] for p in panels[(l,t,r,bot)]])
        right = max([r] + [p[2] for p in panels[(l,t,r,bot)]])
        bottom = max([bot] + [p[3] for p in panels[(l,t,r,bot)]])
        messages.append(Message(text,side,(y0+t*scale)/H,min(b.conf for b in members),
            h=(bottom-t)*scale/H,sender=sender,lines=[b.text for b in body_blocks],
            x=(x0+left*scale)/W,w=(right-left)*scale/W,last_y=(y0+t*scale)/H,
            quote=quote,quote_sender=quote_sender,content_state='mixed' if mixed else ''))
    for b in unresolved:
        messages.append(Message(b.text,'unknown',1-b.y-b.h,b.conf,
            h=b.h,lines=[b.text],x=b.x,w=b.w,last_y=1-b.y-b.h))
    return sorted(messages,key=lambda m:m.y)[-max_messages:]


def recover_numeric_bubbles(image, blocks, rect):
    """One local accurate OCR pass for empty, compact, uniform bubbles only.

    Vision can omit isolated digits in a full chat image. Never infer sequences or
    convert lookalike letters to numbers; retain only explicit digits at Vision confidence >= 0.5.
    """
    # 快速前置判断:只在「已识别 blocks 全是长文本且可能有紧凑数字气泡」时才扫描
    # (省 ~130ms/次)。如果已识别到短 block(0-2 字符)且包含数字,说明 Vision 没漏。
    if any(len(b.text.strip()) <= 2 and any(c.isdigit() for c in b.text) for b in blocks):
        return blocks
    from perception import ocr_image
    rgb = pixels(image)
    H, W = rgb.shape[:2]
    rx, ry, rw, rh = rect
    x0, y0 = round(rx*W), round(ry*H)
    crop = rgb[y0:round((ry+rh)*H), x0:round((rx+rw)*W)].astype(np.int16)
    h, w = crop.shape[:2]
    background = np.median(crop.reshape(-1, 3), axis=0)
    colors, counts = np.unique(crop[::4, ::4].reshape(-1, 3), axis=0, return_counts=True)
    # 第二层:候选颜色里如果没有符合数字气泡特征的(中性灰、对比度 7-65),提前退
    candidates = [c for c in colors[np.argsort(counts)[-6:]]
                  if np.ptp(c) <= 12 and 7 <= np.max(np.abs(c-background)) <= 65]
    if not candidates:
        return blocks
    recovered: list[TextBlock] = []
    for color in candidates:
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
