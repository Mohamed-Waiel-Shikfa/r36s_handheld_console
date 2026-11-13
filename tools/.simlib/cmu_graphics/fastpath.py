"""Speed patches for cmu_graphics on slow handhelds (R36S, RK3326).

Installed from cmu_graphics/__init__.py unless CMU_FAST=0. Every patch keeps the
original behaviour; anything the fast paths don't handle falls back to the
original code, so error messages for bad arguments stay the same.

1. redrawAll runs once per rendered frame instead of after every event handler
   (stock code reruns it after onStep, onKeyHold, onKeyPress... and throws all but
   the last result away). Frames where nothing changed are skipped.
2. The cairo frame is blitted as an opaque RGBX surface instead of RGBA, so SDL
   copies pixels instead of alpha-blending them onto an opaque screen.
3. drawRect/Oval/Circle/Line/Polygon/RegularPolygon/Star/Arc/Label/Image with
   ordinary arguments (including rotateAngle, dashes, opacity, align) skip the
   validated shape-object machinery and draw straight to cairo.
4. Translation lookups, colours, rgb() objects, text measurements, rendered text,
   resized and rotated images are cached.
5. Shapes are drawn the moment drawX() is called (no shape list), and an unchanged
   start of the frame (background, tile map...) is pasted from a cached bitmap
   instead of being redrawn (StaticPrefix; CMU_STATIC_LAYER=0 disables it).
6. Sound loads on first play (stock decodes every file to raw audio up front:
   a 26-minute mp3 is ~280 MB). Long files stream from disk instead.

Opt-in via environment (the R36S launcher sets these):
  CMU_FIT=1        scale the app to fill the screen (800x600 arcade games -> 640x480),
                   rendered at screen resolution, not resized afterwards. Mouse
                   positions are mapped back to app coordinates and a cursor is drawn
                   (gptokeyb can drive the mouse from a stick).
  CMU_R36S_JOY=1   translate the R36S gamepad to the CMU arcade box's joystick numbering
                   (see r36s/README.md); Select+Start quits.
  CMU_ANTIALIAS=fast|none   cheaper shape edges for very heavy games.
"""

import gc
import math
import os
import time

import cairo
import pygame

from . import shape_logic as sl
from . import cmu_graphics as cg

TWO_PI = 2 * math.pi
DEG = math.pi / 180
FAIL = object()  # "can't fast-path this call, use the original"


# ---------------------------------------------------------------- caches

_origToEnglish = sl.toEnglish
_teCache = {}


def toEnglish(key, context, returnLanguage=False):
    # type(key) keeps False/0 and True/1 apart (equal hashes)
    k = (type(key), key, context, returnLanguage, sl.cmuGraphicsLanguage)
    try:
        return _teCache[k]
    except KeyError:
        pass
    except TypeError:  # unhashable key
        return _origToEnglish(key, context, returnLanguage)
    if len(_teCache) > 20000:
        _teCache.clear()
    r = _teCache[k] = _origToEnglish(key, context, returnLanguage)
    return r


_origT = sl.t
_tCache = {}


def t(key, variables=None, language=None):
    if variables is not None or language is not None:
        return _origT(key, variables, language)
    k = (type(key), key, sl.cmuGraphicsLanguage)
    try:
        return _tCache[k]
    except KeyError:
        pass
    except TypeError:
        return _origT(key)
    r = _tCache[k] = _origT(key)
    return r


_rgbCache = {}


def rgb(r, g, b):
    """rgb() returning shared objects (RGB is immutable and compares by value)."""
    k = (type(r), r, type(g), g, type(b), b)
    try:
        c = _rgbCache.get(k)
    except TypeError:
        return sl.RGB(r, g, b)
    if c is None:
        if len(_rgbCache) > 50000:
            _rgbCache.clear()
        c = _rgbCache[k] = sl.RGB(r, g, b)
    return c


_colorCache = {}


def color(c, alpha):
    """cairo source tuple for a colour, None for no colour, FAIL to fall back.
    Channels are BGRA-swapped like shape_logic does (the frame is read back as RGBA)."""
    if c is None:
        return None
    tc = type(c)
    if tc is str:
        bgr = _colorCache.get(c)
        if bgr is None:
            v = sl.CSS3_COLORS_TO_RGB.get(toEnglish(c, 'color').lower())
            if v is None:
                return FAIL
            d = v.__dict__
            bgr = _colorCache[c] = (d['_blue'] / 255, d['_green'] / 255, d['_red'] / 255)
        return bgr + (alpha,)
    if tc is sl.RGB:
        d = c.__dict__
        r, g, b = d['_red'], d['_green'], d['_blue']
        if not (_num(r) and _num(g) and _num(b)):
            return FAIL
        return (b / 255, g / 255, r / 255, alpha)
    return FAIL  # gradients etc.


_extentsCache = {}


def textExtents(face, size, text):
    k = (face, size, text)
    e = _extentsCache.get(k)
    if e is None:
        if len(_extentsCache) > 5000:
            _extentsCache.clear()
        fc = sl.fontCtx
        fc.save()
        fc.select_font_face(*face)
        fc.set_font_size(size)
        xb, yb, w, h, xa, ya = fc.text_extents(text)
        fc.restore()
        e = _extentsCache[k] = (xb, yb, w, xa)
    return e


_fontCache = {}


def fontFace(font, bold, italic):
    k = (font, bold, italic)
    f = _fontCache.get(k)
    if f is None:
        f = _fontCache[k] = sl.getFont(font, bold, italic)
    return f


# ---------------------------------------------------------------- argument checks


def _num(v):
    tv = type(v)
    return tv is int or tv is float


_ALIGNS = None


def alignAttrs(align):
    global _ALIGNS
    if _ALIGNS is None:
        _ALIGNS = set(sl.TRANSLATED_ALIGNS['keys'])
    if type(align) is not str:
        return FAIL
    a = toEnglish(align, 'align')
    if a not in _ALIGNS:
        return FAIL
    return sl.getAlignAttrs(a)


def dashPattern(d):
    if d is False:
        return None
    if d is True:
        return (5, 5)
    if type(d) in (list, tuple) and d and all(_num(v) and v >= 0 for v in d) and any(d):
        return tuple(d)
    return FAIL


_SHAPE_KW = frozenset(['fill', 'border', 'borderWidth', 'opacity', 'rotateAngle', 'dashes', 'align', 'visible', 'db'])
_POLY_KW = _SHAPE_KW - {'align'}
_STAR_KW = _SHAPE_KW | {'roundness'}
_LINE_KW = frozenset(['fill', 'opacity', 'rotateAngle', 'dashes', 'visible', 'db', 'lineWidth', 'arrowStart', 'arrowEnd'])
_LABEL_KW = frozenset(['fill', 'border', 'borderWidth', 'opacity', 'rotateAngle', 'align', 'visible', 'db', 'bold', 'italic', 'size', 'font'])
_IMAGE_KW = _SHAPE_KW | {'width', 'height'}
_NO_STYLE = (None, None, 2, None, None, 0)


_styleCache = {}


def style(kw, allowed, defaultAlign):
    """Common keyword handling, memoized on the exact keywords, value types and values.
    Returns (fill, border, borderWidth, alignAttrs, dash, rotateAngle) or FAIL."""
    try:
        k = (allowed, defaultAlign, tuple(kw.items()), tuple(map(type, kw.values())))
        s = _styleCache.get(k)
    except TypeError:  # unhashable value, e.g. a dashes list
        return parseStyle(kw, allowed, defaultAlign)
    if s is None:
        if len(_styleCache) > 20000:
            _styleCache.clear()
        s = _styleCache[k] = parseStyle(kw, allowed, defaultAlign)
    return s


def parseStyle(kw, allowed, defaultAlign):
    if not kw.keys() <= allowed:
        return FAIL
    get = kw.get
    if get('visible', True) is not True or get('db', '') != '':
        return FAIL
    if get('arrowStart', False) is not False or get('arrowEnd', False) is not False:
        return FAIL
    rot = get('rotateAngle', 0)
    if not _num(rot):
        return FAIL
    dash = dashPattern(get('dashes', False))
    if dash is FAIL:
        return FAIL
    opacity = get('opacity', 100)
    if not _num(opacity) or not 0 <= opacity <= 100:
        return FAIL
    alpha = opacity / 100
    fill = color(get('fill', 'black'), alpha)
    border = color(get('border', None), alpha)
    if fill is FAIL or border is FAIL:
        return FAIL
    bw = get('borderWidth', 2)
    if not _num(bw) or bw < 0:
        return FAIL
    if defaultAlign:
        aa = alignAttrs(get('align', defaultAlign))
        if aa is FAIL:
            return FAIL
    else:
        aa = None
    return fill, border, bw, aa, dash, rot


def alignLeftTop(aa, x, y, w, h):
    xa, ya = aa
    left = x if xa == 'left' else (x - w if xa == 'right' else x - w / 2)
    top = y if ya == 'top' else (y - h if ya == 'bottom' else y - h / 2)
    return left, top


# ---------------------------------------------------------------- drawing


class FastShape(object):
    """Stand-in for a shape in the top-level group; only knows how to draw itself."""

    __slots__ = ('fn', 'args')
    isGroup = False

    def __init__(self, fn, args):
        self.fn = fn
        self.args = args

    def draw(self, ctx):
        self.fn(ctx, *self.args)

    def hits(self, x, y):
        return False

    @property
    def studentShape(self):
        return self


def rotateAbout(ctx, deg, ax, ay):
    ctx.translate(ax, ay)
    ctx.rotate(deg * DEG)
    ctx.translate(-ax, -ay)


def paintPath(ctx, fill, border, bw, dash, roundJoin=False):
    """Fill the current path, then draw an inner border (2x width clipped to the
    shape), the way shape_logic does for polygons and ovals."""
    if fill is not None:
        ctx.set_source_rgba(*fill)
        ctx.fill_preserve()
    if border is not None and bw:
        ctx.clip_preserve()
        ctx.set_source_rgba(*border)
        if dash:
            ctx.set_dash(dash)
        if roundJoin:
            ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        ctx.set_line_width(bw * 2)
        ctx.stroke()
    ctx.new_path()


def strokeInset(ctx, border, bw, dash):
    """Stroke the current (already inset by bw/2) path at bw: the same pixels as
    shape_logic's 2*bw stroke clipped to the shape, without a costly clip mask."""
    ctx.set_source_rgba(*border)
    if dash:
        ctx.set_dash(dash)
    ctx.set_line_width(bw)
    ctx.stroke()


def drawRectFast(ctx, x, y, w, h, fill, border, bw, dash, rot):
    if not rot:
        if border is None or not bw:
            if fill is not None:
                ctx.new_path()
                ctx.rectangle(x, y, w, h)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            return
        if w > bw and h > bw and not dash:  # dashed: clip path keeps the dash phase
            ctx.save()
            ctx.new_path()
            if fill is not None:
                ctx.rectangle(x, y, w, h)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            ctx.rectangle(x + bw / 2, y + bw / 2, w - bw, h - bw)
            strokeInset(ctx, border, bw, dash)
            ctx.restore()
            return
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, x + w / 2, y + h / 2)
    ctx.new_path()
    ctx.rectangle(x, y, w, h)
    paintPath(ctx, fill, border, bw, dash)
    ctx.restore()


def drawOvalFast(ctx, cx, cy, rx, ry, fill, border, bw, dash, rot):
    if rx == ry:
        if border is None or not bw:
            if fill is not None:
                ctx.new_path()
                ctx.arc(cx, cy, rx, 0, TWO_PI)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            return
        if rx > bw / 2 and not dash:
            ctx.save()
            ctx.new_path()
            if fill is not None:
                ctx.arc(cx, cy, rx, 0, TWO_PI)
                ctx.set_source_rgba(*fill)
                ctx.fill()
            ctx.arc(cx, cy, rx - bw / 2, 0, TWO_PI)
            ctx.close_path()
            strokeInset(ctx, border, bw, dash)
            ctx.restore()
            return
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, cx, cy)
    ctx.new_path()
    if rx == ry:
        ctx.arc(cx, cy, rx, 0, TWO_PI)
    else:
        ctx.save()
        ctx.translate(cx, cy)
        ctx.scale(rx, ry)
        ctx.arc(0, 0, 1, 0, TWO_PI)
        ctx.restore()
    ctx.close_path()
    paintPath(ctx, fill, border, bw, dash)
    ctx.restore()


def drawPolyFast(ctx, pts, fill, border, bw, dash):
    # Same path as utils.makePolygonPath: start at the last point.
    ctx.save()
    ctx.new_path()
    ctx.move_to(pts[-2], pts[-1])
    for i in range(0, len(pts), 2):
        ctx.line_to(pts[i], pts[i + 1])
    ctx.close_path()
    paintPath(ctx, fill if len(pts) > 4 else None, border, bw, dash)
    ctx.restore()


def _polyPath(ctx, pts):
    ctx.move_to(pts[-2], pts[-1])
    for i in range(0, len(pts), 2):
        ctx.line_to(pts[i], pts[i + 1])
    ctx.close_path()


def drawInsetPolyFast(ctx, pts, inner, fill, border, bw, dash):
    ctx.save()
    ctx.new_path()
    if fill is not None:
        _polyPath(ctx, pts)
        ctx.set_source_rgba(*fill)
        ctx.fill()
    _polyPath(ctx, inner)
    strokeInset(ctx, border, bw, dash)
    ctx.restore()


def drawArcFast(ctx, cx, cy, rx, ry, start, sweep, fill, border, bw, dash, rot):
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, cx, cy)
    ctx.new_path()
    ctx.move_to(cx, cy)
    ctx.save()
    ctx.translate(cx, cy)
    ctx.scale(rx, ry)
    # CPCS arcs: angle 0 = east, counter-clockwise on screen
    ctx.arc_negative(0, 0, 1, -start * DEG, -(start + sweep) * DEG)
    ctx.restore()
    ctx.close_path()
    paintPath(ctx, fill, border, bw, dash, roundJoin=True)
    ctx.restore()


def drawLineFast(ctx, x1, y1, x2, y2, fill, lw, dash, rot):
    ctx.save()
    if rot:
        rotateAbout(ctx, rot, (x1 + x2) / 2, (y1 + y2) / 2)
    ctx.new_path()
    ctx.set_source_rgba(*fill)
    if dash:
        ctx.set_dash(dash)
    ctx.set_line_width(lw)
    ctx.move_to(x1, y1)
    ctx.line_to(x2, y2)
    ctx.stroke()
    ctx.restore()


def _quarter(v):
    i = math.floor(v)
    f = round((v - i) * 4) / 4
    if f == 1:
        return i + 1, 0.0
    return i, f


_maskCache = {}


def textMask(face, size, text, fx, fy):
    """Alpha mask of the text, rendered once at this size and sub-pixel offset."""
    k = (face, size, text, fx, fy)
    m = _maskCache.get(k)
    if m is None:
        if len(_maskCache) > 3000:
            _maskCache.clear()
        fc = sl.fontCtx
        fc.save()
        fc.select_font_face(*face)
        fc.set_font_size(size)
        xb, yb, w, h, xa, ya = fc.text_extents(text)
        fc.restore()
        pad = 2
        ox = pad - math.floor(xb)
        oy = pad - math.floor(yb)
        surf = cairo.ImageSurface(
            cairo.FORMAT_A8, int(math.ceil(w)) + 2 * pad + 2, int(math.ceil(h)) + 2 * pad + 2
        )
        c = cairo.Context(surf)
        c.select_font_face(*face)
        c.set_font_size(size)
        c.move_to(ox + fx, oy + fy)
        c.show_text(text)
        surf.flush()
        m = _maskCache[k] = (surf, ox, oy)
    return m


def drawLabelFast(ctx, text, x, y, face, size, fill, border, bw, rot, cx, cy):
    ctx.save()
    ctx.new_path()
    ctx.set_source_rgba(*fill)
    m = ctx.get_matrix()
    if border is None or not bw:
        if not rot and m.xy == 0 and m.yx == 0 and m.xx == m.yy:
            # Stamp a cached mask in device pixels (no resampling when the app is scaled).
            s = m.xx
            ix, fx = _quarter(x * s + m.x0)
            iy, fy = _quarter(y * s + m.y0)
            mask, ox, oy = textMask(face, size * s, text, fx, fy)
            ctx.identity_matrix()
            ctx.mask_surface(mask, ix - ox, iy - oy)
            ctx.restore()
            return
    if rot:
        rotateAbout(ctx, rot, cx, cy)
    ctx.select_font_face(*face)
    ctx.set_font_size(size)
    ctx.move_to(x, y)
    if border is not None and bw:
        ctx.text_path(text)
        ctx.fill_preserve()
        ctx.set_source_rgba(*border)
        ctx.set_line_width(bw)
        ctx.stroke()
    else:
        ctx.show_text(text)
    ctx.new_path()
    ctx.restore()


_scaledCache = {}
_scaledPixels = [0]
SCALED_PIXEL_BUDGET = 12_000_000  # ~48 MB of resized images


def scaledImage(key, surf, w, h):
    """The image resampled once to w x h pixels, so each frame is a plain copy."""
    w = max(1, int(round(w)))
    h = max(1, int(round(h)))
    if w == surf.get_width() and h == surf.get_height():
        return surf
    k = (key, w, h)
    s = _scaledCache.get(k)
    if s is None:
        if _scaledPixels[0] + w * h > SCALED_PIXEL_BUDGET:
            _scaledCache.clear()
            _scaledPixels[0] = 0
        _scaledPixels[0] += w * h
        s = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
        c = cairo.Context(s)
        c.scale(w / surf.get_width(), h / surf.get_height())
        c.set_source_surface(surf, 0, 0)
        c.paint()
        s.flush()
        _scaledCache[k] = s
    return s


_rotatedCache = {}
_rotatedPixels = [0]
ROTATED_PIXEL_BUDGET = 12_000_000  # ~48 MB of cached rotated sprites


def rotatedImage(surf, deg):
    """surf rotated by a whole number of degrees, rendered once.
    Returns (surface, half width, half height) of its bounding box."""
    k = (surf, deg)
    r = _rotatedCache.get(k)
    if r is None:
        w, h = surf.get_width(), surf.get_height()
        c, s = abs(math.cos(deg * DEG)), abs(math.sin(deg * DEG))
        bw, bh = int(math.ceil(w * c + h * s)) + 2, int(math.ceil(w * s + h * c)) + 2
        if _rotatedPixels[0] + bw * bh > ROTATED_PIXEL_BUDGET:
            _rotatedCache.clear()
            _rotatedPixels[0] = 0
        out = cairo.ImageSurface(cairo.FORMAT_ARGB32, bw, bh)
        ctx = cairo.Context(out)
        ctx.translate(bw / 2, bh / 2)
        ctx.rotate(deg * DEG)
        ctx.set_source_surface(surf, -w / 2, -h / 2)
        ctx.rectangle(-w / 2, -h / 2, w, h)
        ctx.fill()
        out.flush()
        _rotatedPixels[0] += bw * bh
        r = _rotatedCache[k] = (out, bw / 2, bh / 2)
    return r


def drawImageFast(ctx, key, src, left, top, w, h, alpha, rot):
    ctx.save()
    ctx.new_path()
    m = ctx.get_matrix()
    if m.xy == 0 and m.yx == 0:
        # Work in device pixels with an image pre-scaled to its on-screen size.
        surf = scaledImage(key, src, w * m.xx, h * m.yy)
        ctx.identity_matrix()
        deg = round(rot) % 360
        if deg:
            # Sprite pre-rotated to the nearest degree: drawing is a plain copy.
            surf, hw, hh = rotatedImage(surf, deg)
            x = round((left + w / 2) * m.xx + m.x0 - hw)
            y = round((top + h / 2) * m.yy + m.y0 - hh)
        else:
            # Whole-pixel placement keeps pixman on its unfiltered copy path.
            x, y = round(left * m.xx + m.x0), round(top * m.yy + m.y0)
        sw, sh = surf.get_width(), surf.get_height()
    else:
        if rot:
            rotateAbout(ctx, rot, left + w / 2, top + h / 2)
        ctx.translate(left, top)
        ctx.scale(w / src.get_width(), h / src.get_height())
        surf, x, y = src, 0, 0
        sw, sh = src.get_width(), src.get_height()
    ctx.set_source_surface(surf, x, y)
    ctx.rectangle(x, y, sw, sh)
    if alpha >= 1:
        ctx.fill()
    else:
        ctx.clip()
        ctx.paint_with_alpha(alpha)
    ctx.restore()


# ---------------------------------------------------------------- builders


def buildRect(args, kw):
    if len(args) != 4:
        return FAIL
    x, y, w, h = args
    if not (_num(x) and _num(y) and _num(w) and _num(h)) or w <= 0 or h <= 0:
        return FAIL
    s = style(kw, _SHAPE_KW, 'left-top') if kw else _DEFAULT_RECT
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    left, top = alignLeftTop(aa, x, y, w, h)
    return FastShape(drawRectFast, (left, top, w, h, fill, border, bw, dash, rot))


def buildOval(args, kw):
    if len(args) != 4:
        return FAIL
    x, y, w, h = args
    if not (_num(x) and _num(y) and _num(w) and _num(h)) or w <= 0 or h <= 0:
        return FAIL
    s = style(kw, _SHAPE_KW, 'center')
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    left, top = alignLeftTop(aa, x, y, w, h)
    return FastShape(drawOvalFast, (left + w / 2, top + h / 2, w / 2, h / 2, fill, border, bw, dash, rot))


def buildCircle(args, kw):
    if len(args) != 3:
        return FAIL
    x, y, r = args
    if not (_num(x) and _num(y) and _num(r)) or r <= 0:
        return FAIL
    s = style(kw, _SHAPE_KW, 'center')
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    left, top = alignLeftTop(aa, x, y, 2 * r, 2 * r)
    return FastShape(drawOvalFast, (left + r, top + r, r, r, fill, border, bw, dash, rot))


def _rotatePts(pts, deg, ax, ay):
    c, s = math.cos(deg * DEG), math.sin(deg * DEG)
    out = []
    for i in range(0, len(pts), 2):
        dx, dy = pts[i] - ax, pts[i + 1] - ay
        out.append(ax + dx * c - dy * s)
        out.append(ay + dx * s + dy * c)
    return out


def buildPolygon(args, kw):
    n = len(args)
    if n < 6 or n % 2:
        return FAIL
    for v in args:
        if not _num(v):
            return FAIL
    s = style(kw, _POLY_KW, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    pts = args
    if rot:
        cx, cy = sl.utils.getPolygonCentroid([[args[i], args[i + 1]] for i in range(0, n, 2)])
        pts = _rotatePts(args, rot, cx, cy)
    return FastShape(drawPolyFast, (pts, fill, border, bw, dash))


_unitCache = {}


def unitPoints(kind, points, roundness):
    """Regular polygon / star vertices for radius 1 as (sin, -cos) of the angle
    from north, clockwise (same as utils.getRegularPolygonPoints/getStarPoints)."""
    k = (kind, points, roundness)
    u = _unitCache.get(k)
    if u is None:
        dt = 360 / points
        angles = []
        for i in range(points):
            angles.append((i * dt, 1))
            if kind == 'star':
                angles.append((i * dt + dt / 2, roundness / 100))
        u = _unitCache[k] = [(a * DEG, r) for a, r in angles]
    return u


def _inCircle(kind, args, kw, allowed):
    if len(args) != 4:
        return FAIL
    cx, cy, r, points = args
    if not (_num(cx) and _num(cy) and _num(r)) or r <= 0:
        return FAIL
    if type(points) is not int or points < 3:
        return FAIL
    if kw.get('align', 'center') != 'center':
        return FAIL
    roundness = None
    if kind == 'star':
        roundness = kw.get('roundness', 'default')
        if roundness == 'default':
            roundness = sl.utils.getDefaultRoundness(points)
        elif not _num(roundness) or not 0 <= roundness <= 100:
            return FAIL
        roundness = max(roundness, 5)
    s = style(kw, allowed, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    unit = unitPoints(kind, points, roundness)
    rr = rot * DEG
    pts = []
    for a, k in unit:
        pts.append(cx + r * k * math.sin(a + rr))
        pts.append(cy - r * k * math.cos(a + rr))
    if kind == 'poly' and border is not None and bw and not dash:
        # Convex: insetting by bw/2 keeps it regular (apothem - bw/2).
        ri = r - (bw / 2) / math.cos(math.pi / points)
        if ri > 0:
            inner = []
            for a, k in unit:
                inner.append(cx + ri * math.sin(a + rr))
                inner.append(cy - ri * math.cos(a + rr))
            return FastShape(drawInsetPolyFast, (pts, inner, fill, border, bw, dash))
    return FastShape(drawPolyFast, (pts, fill, border, bw, dash))


def buildRegularPolygon(args, kw):
    return _inCircle('poly', args, kw, _SHAPE_KW)


def buildStar(args, kw):
    return _inCircle('star', args, kw, _STAR_KW)


def buildArc(args, kw):
    if len(args) != 6:
        return FAIL
    cx, cy, w, h, start, sweep = args
    if not (_num(cx) and _num(cy) and _num(w) and _num(h) and _num(start) and _num(sweep)):
        return FAIL
    if w <= 0 or h <= 0 or not 0 < sweep < 360:
        return FAIL
    if kw.get('align', 'center') != 'center':
        return FAIL
    s = style(kw, _SHAPE_KW, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    return FastShape(drawArcFast, (cx, cy, w / 2, h / 2, start, sweep, fill, border, bw, dash, rot))


def buildLine(args, kw):
    if len(args) != 4:
        return FAIL
    x1, y1, x2, y2 = args
    if not (_num(x1) and _num(y1) and _num(x2) and _num(y2)):
        return FAIL
    s = style(kw, _LINE_KW, None)
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    lw = kw.get('lineWidth', 2)
    if not _num(lw) or lw <= 0:
        return FAIL
    if fill is None:
        return FastShape(lambda ctx: None, ())
    return FastShape(drawLineFast, (x1, y1, x2, y2, fill, lw, dash, rot))


def buildLabel(args, kw):
    if len(args) != 3:
        return FAIL
    value, x, y = args
    if not (_num(x) and _num(y)) or callable(value):
        return FAIL
    s = style(kw, _LABEL_KW, 'center')
    if s is FAIL:
        return FAIL
    fill, border, bw, aa, dash, rot = s
    get = kw.get
    size, font = get('size', 12), get('font', 'arial')
    bold, italic = get('bold', False), get('italic', False)
    if not _num(size) or size < 0 or type(font) is not str:
        return FAIL
    if type(bold) is not bool or type(italic) is not bool:
        return FAIL
    if 'font' in kw:
        sl.maybe_show_font_warning(font)
    if fill is None:
        fill = (0, 0, 0, 1)  # shape_logic draws fill=None labels black
    text = str(value)
    face = fontFace(font, bold, italic)
    xb, yb, w, xa = textExtents(face, size, text)
    h = -yb
    outerSpaces = len(text) > 0 and (text[0] == ' ' or text[-1] == ' ')
    if outerSpaces:
        w = max(w, xa)
    left, top = alignLeftTop(aa, x, y, w, h)
    xAdjust = 0 if outerSpaces else xb
    return FastShape(
        drawLabelFast,
        (text, left - xAdjust, top + h, face, size, fill, border, bw, rot, left + w / 2, top + h / 2),
    )


def buildImage(args, kw):
    if len(args) != 3:
        return FAIL
    url, x, y = args
    if not (_num(x) and _num(y)):
        return FAIL
    if not (type(url) is str or isinstance(url, sl.PILWrapper)):
        return FAIL
    if kw.get('fill') is not None or kw.get('border') is not None or kw.get('dashes', False):
        return FAIL
    rest = {k: v for k, v in kw.items() if k != 'width' and k != 'height' and k != 'fill'}
    if not kw.keys() <= _IMAGE_KW:
        return FAIL
    s = style(rest, _SHAPE_KW, 'left-top')
    if s is FAIL:
        return FAIL
    try:
        sl.loadImage(url)  # cached by shape_logic after the first call
    except Exception:
        return FAIL  # the original call raises the proper error
    key = sl.hashReference(url)
    src = sl.activeDrawing.images[key]
    w, h = kw.get('width', src.get_width()), kw.get('height', src.get_height())
    if not (_num(w) and _num(h)) or w <= 0 or h <= 0:
        return FAIL
    left, top = alignLeftTop(s[3], x, y, w, h)
    alpha = kw.get('opacity', 100) / 100
    return FastShape(drawImageFast, (key, src, left, top, w, h, alpha, s[5]))


_DEFAULT_RECT = None


def flushSlowShapes(ctx):
    """Draw (in order) and drop shapes the original code added to the group."""
    tlg = sl.activeDrawing.tlg
    g = getattr(tlg, '_shape', tlg)
    if g._shapes:
        shapes = g._shapes
        g._shapes = []
        ctx.save()
        try:
            for shape in shapes:
                shape.draw(ctx)
        finally:
            ctx.restore()


def drawNow(ctx, builder, slowFn, args, kwargs):
    item = builder(args, kwargs)
    if item is not FAIL:
        item.fn(ctx, *item.args)  # immediate mode: straight into the frame
        return None
    result = slowFn(*args, **kwargs)
    flushSlowShapes(ctx)
    return result


class StaticPrefix(object):
    """Skips re-drawing the unchanged start of a frame.

    Each draw call gets a signature (function, args, kwargs). While this frame's
    calls equal last frame's, they aren't drawn; once the matching run reaches the
    cached length, a bitmap of that prefix is pasted instead. A mismatch inside the
    run replays the skipped calls first, so output is always exact. The bitmap is
    re-captured whenever the static run gets shorter or longer."""

    SKIP, EXTEND, NORMAL = 0, 1, 2
    MIN_CALLS = 16  # shorter static runs aren't worth a full-frame copy

    def __init__(self):
        self.on = os.environ.get('CMU_STATIC_LAYER', '1') != '0'
        self.reset()

    def reset(self):
        self.prev = []
        self.length = 0
        self.snap = None
        self.key = None

    def begin(self, ctx, key, paintBg):
        """Returns True if the caller must paint the background now."""
        if key != self.key:
            self.reset()
            self.key = key
        self.ctx = ctx
        self.cur = []
        self.pending = []
        self.paintBg = paintBg
        self.mode = self.SKIP if self.snap is not None else self.EXTEND
        return self.mode != self.SKIP  # the pasted bitmap already holds the background

    def capture(self, n):
        if n < self.MIN_CALLS:
            self.snap, self.length = None, 0
            return
        src = self.ctx.get_target()
        src.flush()
        if self.snap is None:
            self.snap = cairo.ImageSurface(cairo.FORMAT_ARGB32, src.get_width(), src.get_height())
        c = cairo.Context(self.snap)
        c.set_operator(cairo.OPERATOR_SOURCE)
        c.set_source_surface(src, 0, 0)
        c.paint()
        self.length = n

    def paste(self):
        ctx = self.ctx
        ctx.save()
        ctx.identity_matrix()
        ctx.reset_clip()
        ctx.set_operator(cairo.OPERATOR_SOURCE)
        ctx.set_source_surface(self.snap, 0, 0)
        ctx.paint()
        ctx.restore()

    def replayPending(self):
        self.paintBg()
        pending, self.pending = self.pending, []
        for call in pending:
            drawNow(self.ctx, *call)

    def step(self, sig, builder, slowFn, args, kwargs):
        """True if the call was absorbed (skipped), False if the caller must draw it."""
        i = len(self.cur)
        prev = self.prev
        # prev[i] was hash-checked last frame, so an equal sig holds no mutable values.
        same = i < len(prev) and prev[i] is not None and sig == prev[i]
        if not same:
            try:
                hash(sig)  # unhashable (e.g. a list argument) = can't prove unchanged
            except TypeError:
                sig = None
        self.cur.append(sig)
        if self.mode == self.SKIP:
            if same and i < self.length:
                self.pending.append((builder, slowFn, args, kwargs))
                if i + 1 == self.length:
                    self.pending = []
                    self.paste()
                    self.mode = self.EXTEND
                return True
            self.replayPending()  # mismatch inside the cached run
            self.capture(i)
            self.mode = self.NORMAL
            return False
        if self.mode == self.EXTEND:
            if same:
                return False
            if i != self.length:
                self.capture(i)  # the frame so far is exactly calls 0..i-1
            self.mode = self.NORMAL
        return False

    def end(self):
        n = len(self.cur)
        if self.mode == self.SKIP:  # frame ended inside the cached run
            self.replayPending()
            self.capture(n)
        elif self.mode == self.EXTEND and n != self.length:  # whole frame unchanged
            self.capture(n)
        self.prev = self.cur
        self.ctx = None


def makeFastDrawFn(builder, slowFn):
    def drawFn(*args, **kwargs):
        ctx = S.ctx  # set only while the user's redrawAll runs in CPCS mode
        if ctx is None:
            return slowFn(*args, **kwargs)
        layer = S.layer
        if layer.on:
            sig = (builder, args, tuple(kwargs.items()))
            if layer.step(sig, builder, slowFn, args, kwargs):
                return None
        return drawNow(ctx, builder, slowFn, args, kwargs)

    drawFn.__name__ = slowFn.__name__
    drawFn.__doc__ = slowFn.__doc__
    return drawFn


# ---------------------------------------------------------------- frame loop


class State(object):
    dirty = True
    lastRender = 0.0
    cairoSurface = None
    pgSurface = None
    fit = False
    joy = False
    offset = (0, 0)
    frameSize = None
    antialias = None
    ctx = None  # cairo context while the user's redrawAll is drawing
    layer = None
    scale = 1.0
    cursorOn = False  # a mouse moved: draw our own cursor (KMSDRM shows none)
    cursorPos = (0, 0)  # screen pixels, from the latest mouse event
    lastCursor = None


S = State()
_origRedrawAllWrapper = cg.App.redrawAllWrapper
_origRedrawAll = cg.App.redrawAll
_origUpdateScreen = cg.App.updateScreen


def redrawAllWrapper(self):
    # Defer: the user's redrawAll runs once, right before the frame is rendered.
    if self._isMvc:
        S.dirty = True
    else:
        _origRedrawAllWrapper(self)


def paintBackground(self, ctx):
    bg = self.background or 'white'
    c = color(bg, 1.0)
    if c is FAIL or c is None:
        shape = sl.Rect(
            {'noGroup': True, 'top': 0, 'left': 0, 'width': self.width, 'height': self.height, 'fill': bg}
        )
        shape.draw(ctx)
    else:
        ctx.set_source_rgba(*c)
        ctx.paint()


def rebuild(self, ctx):
    """Run the user's redrawAll, drawing each shape into ctx as it is called."""
    S.dirty = False
    layer = S.layer
    if layer.on:
        bg = self.background
        if layer.begin(ctx, (id(self._cairo_surface), bg if bg is None or type(bg) is str else id(bg)),
                       lambda: paintBackground(self, ctx)):
            paintBackground(self, ctx)
    else:
        paintBackground(self, ctx)
    ctx.save()
    S.ctx = ctx
    try:
        _origRedrawAllWrapper(self)
        flushSlowShapes(ctx)
        if layer.on:
            layer.end()
    except Exception:
        self.inRedrawAll = False
        tlg = self._tlg._shape
        tlg._shapes = []
        layer.reset()
        raise
    finally:
        S.ctx = None
        ctx.restore()
        ctx.new_path()


def updateScreen(self, newScreen):
    if not S.fit:
        _origUpdateScreen(self, newScreen)
        if S.antialias is not None:
            self._ctx.set_antialias(S.antialias)
        return
    # Fill the display, keep the aspect ratio, and render straight at that size.
    dw, dh = pygame.display.get_desktop_sizes()[0]
    s = min(dw / self.width, dh / self.height)
    fw, fh = max(1, int(round(self.width * s))), max(1, int(round(self.height * s)))
    if newScreen or self._screen is None:
        self._screen = pygame.display.set_mode((dw, dh))
        self._screen.fill((0, 0, 0))
    S.offset = ((dw - fw) // 2, (dh - fh) // 2)
    S.frameSize = (fw, fh)
    S.scale = s
    pygame.mouse.set_visible(False)
    self._cairo_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, fw, fh)
    self._ctx = cairo.Context(self._cairo_surface)
    self._ctx.scale(s, s)
    if S.antialias is not None:
        self._ctx.set_antialias(S.antialias)


CURSOR = ((0, 0), (0, 17), (4, 13), (7, 20), (10, 19), (7, 12), (12, 12))


def present(screen):
    """Put the current frame (and the cursor, if a mouse is in use) on screen."""
    screen.blit(S.pgSurface, S.offset if S.fit else (0, 0))
    if S.cursorOn:
        x, y = S.cursorPos
        pts = [(x + px, y + py) for px, py in CURSOR]
        pygame.draw.polygon(screen, (255, 255, 255), pts)
        pygame.draw.polygon(screen, (0, 0, 0), pts, 1)
        S.lastCursor = (x, y)
    pygame.display.flip()


def cursorMoved():
    return S.cursorOn and S.cursorPos != S.lastCursor


def redrawAll(self, screen, cairo_surface, ctx):
    inspector = self.shouldDrawInspector()
    ownSurface = cairo_surface is self._cairo_surface
    if self._isMvc and ownSurface:
        if inspector or S.cairoSurface is not cairo_surface:
            S.dirty = True  # no display list to replay: rebuild the frame
        elif not S.dirty:
            if cursorMoved():
                present(screen)  # only the cursor moved
            return  # nothing changed since the last frame
        now = time.perf_counter()
        sps = self.stepsPerSecond
        if (
            S.cairoSurface is cairo_surface
            and not (self.paused or self.stopped)
            and sps >= 10
            and now - S.lastRender < 0.5 / sps
        ):
            if cursorMoved():
                present(screen)
            return  # event-only redraw right after a frame: the next step draws it
        S.lastRender = now
        if self.stopped:
            return  # keep the last frame; the error screen draws itself
        rebuild(self, ctx)
    else:
        # Shape-object mode, or drawErrorScreen's own app-sized surface.
        if S.fit and not ownSurface:
            sc = S.frameSize[0] / self.width
            ctx.scale(sc, sc)
        paintBackground(self, ctx)
        ctx.save()
        try:
            self._tlg._shape.draw(ctx)
        finally:
            ctx.restore()

    if inspector:
        ctx.save()
        try:
            self.inspector.draw(ctx)
        finally:
            ctx.restore()

    if S.cairoSurface is not cairo_surface:
        # Opaque view of the cairo buffer; shares memory, so build it once per surface.
        S.pgSurface = pygame.image.frombuffer(
            cairo_surface.get_data(), (cairo_surface.get_width(), cairo_surface.get_height()), 'RGBX'
        )
        S.cairoSurface = cairo_surface
    cairo_surface.flush()
    present(screen)
    self.frameworkRedrew = True


# ---------------------------------------------------------------- R36S gamepad

# R36S "GO-Super Gamepad" raw button -> CMU arcade box button number.
# a/b/x/y, L1 (coin) and R1 (P1) already match; Select/Start move to 8/9 like the
# arcade box; console-only buttons take the numbers left free.
R36S_BUTTONS = {0: 0, 1: 1, 2: 2, 3: 3, 4: 4, 5: 5, 6: 6, 7: 7, 12: 8, 13: 9, 14: 10, 15: 11}
R36S_DPAD = {8: (1, -1), 9: (1, 1), 10: (0, -1), 11: (0, 1)}  # raw button -> (axis, direction)
R36S_SELECT, R36S_START = 12, 13


class Pad(object):
    held = set()


def translateEvents(events):
    """R36S gamepad events -> arcade box events. The D-pad becomes the arcade
    joystick (digital axes 0/1, like the left stick); Select+Start quits."""
    out = []
    for e in events:
        et = e.type
        if et == pygame.JOYBUTTONDOWN or et == pygame.JOYBUTTONUP:
            b = e.button
            down = et == pygame.JOYBUTTONDOWN
            if down:
                Pad.held.add(b)
            else:
                Pad.held.discard(b)
            if R36S_SELECT in Pad.held and R36S_START in Pad.held:
                out.append(pygame.event.Event(pygame.QUIT))
                continue
            if b in R36S_DPAD:
                axis = R36S_DPAD[b][0]
                neg = 10 if axis == 0 else 8
                pos = 11 if axis == 0 else 9
                value = float((pos in Pad.held) - (neg in Pad.held))
                out.append(pygame.event.Event(
                    pygame.JOYAXISMOTION, joy=getattr(e, 'joy', 0), instance_id=e.instance_id,
                    axis=axis, value=value))
                continue
            if b in R36S_BUTTONS:
                out.append(pygame.event.Event(
                    et, joy=getattr(e, 'joy', 0), instance_id=e.instance_id, button=R36S_BUTTONS[b]))
                continue
        out.append(e)
    return out


_MOUSE_EVENTS = (pygame.MOUSEMOTION, pygame.MOUSEBUTTONDOWN, pygame.MOUSEBUTTONUP)


def mapMouse(events):
    """Screen pixels -> app coordinates when the app is scaled to fit."""
    out = []
    app = object.__getattribute__(cg.app, '_app')
    for e in events:
        if e.type in _MOUSE_EVENTS:
            S.cursorOn = True
            x, y = e.pos
            S.cursorPos = (int(x), int(y))
            ax = min(max((x - S.offset[0]) / S.scale, 0), app.width - 1)
            ay = min(max((y - S.offset[1]) / S.scale, 0), app.height - 1)
            d = dict(e.dict, pos=(int(ax), int(ay)))
            if 'rel' in d:
                d['rel'] = (int(d['rel'][0] / S.scale), int(d['rel'][1] / S.scale))
            e = pygame.event.Event(e.type, d)
        out.append(e)
    return out


_origEventGet = pygame.event.get


def eventGet(*args, **kwargs):
    events = _origEventGet(*args, **kwargs)
    if S.joy:
        events = translateEvents(events)
    if S.fit:
        events = mapMouse(events)
    return events


# ---------------------------------------------------------------- sound

STREAM_BYTES = 1_000_000  # bigger files (music) stream through pygame.mixer.music
_decoded = {}
_music = {'owner': None, 'paused': False}


def soundInit(self, url):
    """cmu_graphics.Sound, minus decoding everything up front."""
    if not pygame.mixer.get_init():
        pygame.mixer.init()
        pygame.mixer.set_num_channels(1)
    if not isinstance(url, str):
        raise Exception(f'TypeError: Sound.url should be string (but {url!r} is of type {type(url).__name__})')
    cg.Sound.number_of_sounds += 1
    if pygame.mixer.get_num_channels() == cg.Sound.number_of_sounds:
        pygame.mixer.set_num_channels(cg.Sound.number_of_sounds * 2)
    if url.startswith('file://'):
        url = url.split('//')[-1]
    if url.startswith('http'):
        self._src = url
        self._stream = False
    else:
        main = __import__('__main__')
        base = os.path.join(main.__file__, '..') if hasattr(main, '__file__') else os.getcwd()
        self._src = os.path.abspath(os.path.join(base, url))
        self._stream = os.path.getsize(self._src) > STREAM_BYTES
    self._sound = None
    self._volume = 1.0
    self.channel = None


def soundData(self):
    if self._sound is None:
        k = self._src
        if k not in _decoded:
            if k.startswith('http'):
                import io
                _decoded[k] = pygame.mixer.Sound(io.BytesIO(cg.webrequest.get(k).read()))
            else:
                _decoded[k] = pygame.mixer.Sound(k)
        self._sound = _decoded[k]
    return self._sound


def soundPlay(self, loop=False, restart=False, **kwargs):
    for k in kwargs:
        en = toEnglish(k, 'shape-attr')
        if en == 'loop':
            loop = kwargs[k]
        elif en == 'restart':
            restart = kwargs[k]
        else:
            raise Exception(f"TypeError: Sound.play() got an unexpected keyword argument '{k}'")
    if not isinstance(loop, bool) or not isinstance(restart, bool):
        raise Exception('The loop and restart arguments to Sound.play must be True or False')
    loops = -1 if loop else 0
    if self._stream:
        music = pygame.mixer.music
        if _music['owner'] is self and _music['paused'] and not restart:
            music.unpause()
        elif _music['owner'] is not self or restart or not music.get_busy():
            music.load(self._src)
            music.set_volume(self._volume)
            music.play(loops=loops)
            _music['owner'] = self
        _music['paused'] = False
        return
    snd = soundData(self)
    if not self.channel or not self.channel.get_busy():
        self.channel = snd.play(loops=loops)
    elif restart:
        self.channel.stop()
        self.channel = snd.play(loops=loops)
    else:
        self.channel.unpause()


def soundPause(self):
    if self._stream:
        if _music['owner'] is self:
            pygame.mixer.music.pause()
            _music['paused'] = True
    elif self.channel:
        self.channel.pause()


def soundSetVolume(self, volume):
    self._volume = volume
    if self._stream:
        if _music['owner'] is self:
            pygame.mixer.music.set_volume(volume)
    else:
        soundData(self).set_volume(volume)


def soundGetVolume(self):
    return self._volume if self._stream else soundData(self).get_volume()


def installSound():
    cg.Sound.__init__ = soundInit
    cg.Sound.sound = property(soundData)
    cg.Sound.play = soundPlay
    cg.Sound.pause = soundPause
    cg.Sound.setVolume = soundSetVolume
    cg.Sound.getVolume = soundGetVolume


# ---------------------------------------------------------------- install

_BUILDERS = {
    'drawRect': buildRect,
    'drawOval': buildOval,
    'drawCircle': buildCircle,
    'drawLine': buildLine,
    'drawPolygon': buildPolygon,
    'drawRegularPolygon': buildRegularPolygon,
    'drawStar': buildStar,
    'drawArc': buildArc,
    'drawLabel': buildLabel,
    'drawImage': buildImage,
}


def install(pkgGlobals):
    global _DEFAULT_RECT
    # Fewer, cheaper GC passes: every frame allocates many short-lived objects,
    # and the big module-level tables never need rescanning.
    gc.freeze()
    gc.set_threshold(20000, 20, 20)

    sl.toEnglish = toEnglish
    sl.t = t
    for g in (cg.__dict__, pkgGlobals):
        if g.get('toEnglish') is not None:
            g['toEnglish'] = toEnglish
        if g.get('t') is not None:
            g['t'] = t
        if g.get('rgb') is not None:
            g['rgb'] = rgb
    _DEFAULT_RECT = style({}, _SHAPE_KW, 'left-top')
    # Same hash as RGB.__hash__, without three trips through the translating __getattr__.
    sl.RGB.__hash__ = lambda c: hash((c.__dict__['_red'], c.__dict__['_green'], c.__dict__['_blue']))

    cg.App.redrawAllWrapper = redrawAllWrapper
    cg.App.redrawAll = redrawAll
    cg.App.updateScreen = updateScreen
    S.layer = StaticPrefix()
    S.fit = os.environ.get('CMU_FIT') == '1'
    S.antialias = {'fast': cairo.ANTIALIAS_FAST, 'none': cairo.ANTIALIAS_NONE}.get(
        os.environ.get('CMU_ANTIALIAS', ''))
    S.joy = os.environ.get('CMU_R36S_JOY') == '1'
    if S.joy or S.fit:
        pygame.event.get = eventGet
    installSound()

    for name, builder in _BUILDERS.items():
        fast = makeFastDrawFn(builder, cg.__dict__[name])
        cg.__dict__[name] = fast
        if name in pkgGlobals:
            pkgGlobals[name] = fast
