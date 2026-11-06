#!/usr/bin/env python3
"""
Value-scale generator for proportional-symbol maps - Python port.

Re-implementation of the web tool by Martin Holub & Radek Barvir
(Palacky University Olomouc). Reference:
  Barvir, R., Holub, M., Vondrakova, A. (2025): Proportional Symbol Maps:
  Value-Scale Types, Online Value-Scale Generator and User Perspectives.
  ISPRS Int. J. Geo-Inf. 14(9), 340. https://doi.org/10.3390/ijgi14090340

Geometry
  x axis  : linear in the phenomenon value; the scalebar length is the distance
            between the centres of the smallest and the largest symbol.
  y axis  : symbol height h(v).
            2D shapes (circle, square, triangle, pentagon, hexagon, custom SVG):
            symbol area proportional to value      h = h_min * sqrt(v / v_min)
            1D shapes (column, apex): height proportional to value
                                                   h = h_min * v / v_min  (straight line)
            With a maximum symbol height (Pro: Symbology > Maximum size checked),
            symbols run from h_min at v_min to h_max at v_max instead, with t = (v - v_min) / (v_max - v_min):
              "area"   (default)  h = sqrt(h_min^2 + t * (h_max^2 - h_min^2))   [1D: linear in h]
              "height"            h = h_min + t * (h_max - h_min)
  No Flannery appearance compensation (same as the original tool).

Usage (CLI)
  python value_scale.py --min 6 --max 224 -o scale.svg -o scale.pdf
  python value_scale.py --min 6 --max 224 --shape hexagon --min-size 4 --unit mm ...
  python value_scale.py --help

Usage (library)
  from value_scale import ScaleConfig, generate_svg
  svg = generate_svg(ScaleConfig(value_min=6, value_max=224))

Standard library only: SVG and PDF are both written without extra packages
(runs as-is in ArcGIS Pro's Python). `cairosvg` or `svglib` + `reportlab` are
used for PDF only if a custom SVG symbol uses features the built-in writer lacks.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
import tempfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")

# conversion of supported units to typographic points (SVG user unit here = 1 pt)
PT = {"pt": 1.0, "mm": 72 / 25.4, "cm": 720 / 25.4, "in": 72.0}

SHAPES = ("circle", "square", "triangle", "pentagon", "hexagon", "column", "apex", "custom")
SHAPES_1D = ("column", "apex")   # constant width, height grows linearly

# regular polygons: (number of vertices, angle of first vertex in degrees)
POLYGONS = {"triangle": (3, 90), "square": (4, 45), "pentagon": (5, 90), "hexagon": (6, 0)}

# default line weights of the scale itself (pt); baseline = 1.2 x and subdivisions = 0.8 x line_width
W_CURVE, W_LINE = 1.0, 0.5
DEFAULT_STROKE = 1.0
LINE_COLOUR = "#000000"
FONT = "Arial"  # single family name: ArcGIS Pro's SVG reader resolves Windows font names only


# --------------------------------------------------------------------------- config
@dataclass
class ScaleConfig:
    value_min: float
    value_max: float
    symbol_shape: str = "circle"
    min_symbol_size: float = 12.0       # height of the smallest symbol, in symbol_unit
    max_symbol_size: float | None = None  # height of the largest symbol (Pro "Maximum size"), None = none
    max_interpolation: str = "area"     # with a maximum: interpolate symbol "area" or "height"
    symbol_unit: str = "pt"             # pt | mm | cm | in
    scalebar_length: float = 120.0      # mm
    font_size: float = 10.0             # pt, 0 = no labels
    environment: str = "cz"             # cz (12 345,6) | en (12,345.6) | no (12345.6)
    divisions: list[float] | None = None  # user-defined divisions, None = automatic
    subdivisions: int | None = None       # gaps between main divisions, None = automatic
    fill_colour: str | None = None        # None = no fill
    stroke: float | None = None           # symbol stroke in pt, None = default, 0 = none
    stroke_colour: str = "#000000"
    svg_file: str | None = None           # custom symbol (shape == "custom")
    # Label range: numbers printed on the scale instead of the data values.
    # label_min sits at value_min, label_max at value_max; in between the link is
    # linear, or logarithmic ("log": data = a + b*log(label), e.g. data stored as log10).
    # Divisions are then given / generated in label units.
    label_min: float | None = None
    label_max: float | None = None
    label_mapping: str = "linear"         # linear | log
    symbols_at_divisions: bool = False    # also draw a symbol at every main division
    column_width: float = 5.0             # column / apex-base width (1D shapes), in symbol_unit
    # Symbol outline drawn offset from the symbol edge, in pt: + inward, - outward
    # (e.g. +stroke/2 keeps the whole outline inside the symbol). The symbol size, and so
    # the curve, refer to the un-offset shape, as an Offset effect on the outline in Pro.
    outline_offset: float = 0.0
    curve_width: float = W_CURVE          # pt, the value curve / line
    line_width: float = W_LINE            # pt, divisions; baseline 1.2x, subdivisions 0.8x

    @property
    def custom_labels(self) -> bool:
        return self.label_min is not None and self.label_max is not None

    def validate(self) -> None:
        if not 0 < self.value_min < self.value_max:
            raise ValueError("Require 0 < minimum value < maximum value.")
        if self.symbol_shape not in SHAPES:
            raise ValueError(f"Unknown symbol shape '{self.symbol_shape}'. Use one of {SHAPES}.")
        if self.symbol_unit not in PT:
            raise ValueError(f"Unknown unit '{self.symbol_unit}'. Use one of {tuple(PT)}.")
        if self.min_symbol_size <= 0:
            raise ValueError("Minimum symbol height must be positive.")
        if self.max_symbol_size is not None and not self.max_symbol_size > self.min_symbol_size:
            raise ValueError("Maximum symbol height must be greater than the minimum symbol height.")
        if self.max_interpolation not in ("area", "height"):
            raise ValueError("Maximum-size interpolation must be 'area' or 'height'.")
        if self.scalebar_length <= 0:
            raise ValueError("Scalebar length must be positive.")
        if self.font_size < 0:
            raise ValueError("Font size cannot be negative.")
        if self.environment not in ("cz", "en", "no"):
            raise ValueError("Typographic style must be 'cz', 'en' or 'no'.")
        if self.subdivisions is not None and self.subdivisions < 0:
            raise ValueError("Number of subdivisions cannot be negative.")
        if self.symbol_shape in SHAPES_1D and not self.column_width > 0:
            raise ValueError("Column / apex-base width must be positive.")
        if self.curve_width <= 0 or self.line_width <= 0:
            raise ValueError("Line widths must be greater than 0.")
        if self.symbol_shape == "custom" and not self.svg_file:
            raise ValueError("Custom shape needs an SVG file.")
        if (self.label_min is None) != (self.label_max is None):
            raise ValueError("Give both label minimum and label maximum (or neither).")
        if self.label_mapping not in ("linear", "log"):
            raise ValueError("Label mapping must be 'linear' or 'log'.")
        if self.custom_labels:
            if not self.label_min < self.label_max:
                raise ValueError("Label minimum must be smaller than label maximum.")
            if self.label_mapping == "log" and self.label_min <= 0:
                raise ValueError("Logarithmic labels need a label minimum greater than 0.")


# --------------------------------------------------------------------------- helpers
def f(x: float) -> str:
    """Compact coordinate formatting."""
    s = f"{x:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def format_number(v: float, env: str) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    int_part, _, dec = s.partition(".")
    if env in ("cz", "en") and len(int_part) > 3:
        sep = "\u00a0" if env == "cz" else ","
        int_part = f"{int(int_part):,}".replace(",", sep)
    dec_sep = "," if env == "cz" else "."
    return int_part + (dec_sep + dec if dec else "")


def nice_step(span: float) -> float:
    """Base step between main divisions, as in the original tool (get_step_iter):
    the range is matched to (1-2], (2-5], (5-10] x 10^k and gets the step
    0.5, 1, 2 x 10^k respectively, e.g. 6-224 (range 218) -> step 100."""
    k = math.floor(math.log10(span))
    m = span / 10 ** k
    if m <= 1 + 1e-9:          # exactly 1 x 10^k belongs to the upper end of the lower decade
        k, m = k - 1, m * 10
    if m <= 2 + 1e-9:
        return 0.5 * 10 ** k
    if m <= 5 + 1e-9:
        return 1.0 * 10 ** k
    return 2.0 * 10 ** k


def auto_subdivisions(step: float, unit_len: float, min_gap: float = 4.0) -> int:
    """Pick 5/4/2 subdivisions from the step's leading digit, if they fit (min_gap in pt)."""
    lead = round(step / 10 ** math.floor(math.log10(step)))
    options = {1: (5, 2), 2: (4, 2), 5: (5,), 10: (5, 2)}.get(lead, (2,))
    for n in options:
        if step / n * unit_len >= min_gap:
            return n
    return 0


def _length(value: str | None) -> float:
    if not value:
        raise ValueError("Custom SVG has neither viewBox nor width/height.")
    m = re.match(r"\s*([0-9.eE+-]+)", value)
    if not m:
        raise ValueError(f"Cannot read SVG length '{value}'.")
    return float(m.group(1))


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


# --------------------------------------------------------------------------- shapes
def regular_polygon(n: int, start_deg: float) -> tuple[list[tuple[float, float]], float]:
    """Unit polygon normalised to height 1, bottom at y=0 (SVG y down), centred on x=0.
    Returns (points, width/height ratio)."""
    pts = []
    for k in range(n):
        a = math.radians(start_deg + 360 * k / n)
        pts.append((math.cos(a), -math.sin(a)))
    xs, ys = zip(*pts)
    hgt, x0, ymax = max(ys) - min(ys), (max(xs) + min(xs)) / 2, max(ys)
    norm = [((x - x0) / hgt, (y - ymax) / hgt) for x, y in pts]
    return norm, (max(xs) - min(xs)) / hgt


def _incircle(pts: list[tuple[float, float]]) -> tuple[float, float, float]:
    """Incentre and inradius of a polygon that has an incircle (any triangle, regular polygons)."""
    if len(pts) == 3:  # incentre: vertices weighted by the opposite side lengths
        (ax, ay), (bx, by), (cx, cy) = pts
        a, b, c = math.dist(pts[1], pts[2]), math.dist(pts[0], pts[2]), math.dist(pts[0], pts[1])
        ix, iy = (a * ax + b * bx + c * cx) / (a + b + c), (a * ay + b * by + c * cy) / (a + b + c)
    else:              # regular polygon: centre = mean of the vertices
        ix, iy = sum(x for x, _ in pts) / len(pts), sum(y for _, y in pts) / len(pts)
    r = min(abs((x2 - x1) * (y1 - iy) - (x1 - ix) * (y2 - y1)) / math.dist((x1, y1), (x2, y2))
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))
    return ix, iy, r


def _offset_polygon(pts: list[tuple[float, float]], d: float) -> list[tuple[float, float]]:
    """Parallel offset by d (here + = outward, mitred corners): scaling about the incentre moves
    every edge of a tangential polygon by exactly d."""
    if d == 0:
        return pts
    ix, iy, r = _incircle(pts)
    k = (r + d) / r
    return [(ix + k * (x - ix), iy + k * (y - iy)) for x, y in pts]


class CustomSymbol:
    """User SVG symbol, scaled so that its viewBox height equals the symbol height."""

    def __init__(self, path: str):
        data = Path(path).read_bytes()
        if len(data) > 1_000_000:
            raise ValueError("Custom SVG exceeds the 1 MB size limit.")
        root = ET.fromstring(data)
        if _local(root.tag) != "svg":
            raise ValueError("Custom file is not an SVG.")
        vb = root.get("viewBox")
        if vb:
            self.x, self.y, self.w, self.h = map(float, re.split(r"[\s,]+", vb.strip()))
        else:
            self.x = self.y = 0.0
            self.w, self.h = _length(root.get("width")), _length(root.get("height"))
        if self.w <= 0 or self.h <= 0:
            raise ValueError("Custom SVG has zero size.")
        skip = {"title", "desc", "metadata"}
        self.children = [c for c in root if _local(c.tag) not in skip]
        drawable = {"path", "circle", "ellipse", "rect", "polygon", "polyline", "line"}
        self.styled = any(_local(el.tag) == "style" for el in root.iter()) or any(
            {"style", "fill", "stroke", "class"} & set(el.attrib)
            for el in root.iter() if _local(el.tag) in drawable
        )
        self.ratio = self.w / self.h

    def place(self, cx: float, base: float, h: float, fill: str, stroke: float, stroke_colour: str) -> str:
        s = h / self.h
        tx = cx - s * (self.x + self.w / 2)
        ty = base - s * (self.y + self.h)
        style = ""
        if not self.styled:  # original styles are kept, otherwise apply universal style
            if stroke > 0:
                style = f' fill="{fill}" stroke="{stroke_colour}" stroke-width="{f(stroke / s)}"'
            else:
                style = f' fill="{fill}" stroke="none"'
        inner = "".join(ET.tostring(c, encoding="unicode") for c in self.children)
        return f'<g transform="translate({f(tx)} {f(ty)}) scale({s:.6g})"{style}>{inner}</g>'


# --------------------------------------------------------------------------- generator
def symbol_heights(cfg: ScaleConfig) -> tuple[float, float, float, float]:
    """(min height, max height, a mid value, its height) in cfg.symbol_unit, to compare with the map."""
    v0, v1, h0 = cfg.value_min, cfg.value_max, cfg.min_symbol_size
    one_d = cfg.symbol_shape in SHAPES_1D
    vm = (v0 + v1) / 2

    def h(v):
        if cfg.max_symbol_size is None:
            return h0 * (v / v0 if one_d else math.sqrt(v / v0))
        t, h1 = (v - v0) / (v1 - v0), cfg.max_symbol_size
        if one_d or cfg.max_interpolation == "height":
            return h0 + t * (h1 - h0)
        return math.sqrt(h0 ** 2 + t * (h1 ** 2 - h0 ** 2))
    return h(v0), h(v1), vm, h(vm)


def generate_svg(cfg: ScaleConfig, warnings: list | None = None) -> str:
    """SVG text of the value scale. Pass a list as `warnings` to receive notes such as
    division labels left out because they would collide."""
    cfg.validate()
    v0, v1 = cfg.value_min, cfg.value_max
    shape = cfg.symbol_shape
    u = PT[cfg.symbol_unit]
    h_min = cfg.min_symbol_size * u
    length = cfg.scalebar_length * PT["mm"]
    unit_len = length / (v1 - v0)              # pt per one unit of data value
    stroke = DEFAULT_STROKE if cfg.stroke is None else cfg.stroke
    fill = cfg.fill_colour or "none"
    fs = cfg.font_size
    custom = CustomSymbol(cfg.svg_file) if shape == "custom" else None

    def X(v: float) -> float:
        return (v - v0) * unit_len

    one_d = shape in SHAPES_1D
    col_w = cfg.column_width * u

    h_cap = None if cfg.max_symbol_size is None else cfg.max_symbol_size * u

    def H(v: float) -> float:
        if h_cap is None:                    # true proportional, as without Pro's Maximum size
            return h_min * (v / v0 if one_d else math.sqrt(v / v0))
        t = (v - v0) / (v1 - v0)             # sized between the minimum and maximum symbol
        if one_d or cfg.max_interpolation == "height":
            return h_min + t * (h_cap - h_min)
        return math.sqrt(h_min ** 2 + t * (h_cap ** 2 - h_min ** 2))

    if shape in POLYGONS:
        poly, ratio = regular_polygon(*POLYGONS[shape])
    else:
        poly, ratio = None, {"circle": 1.0, "custom": custom.ratio if custom else 1.0}.get(shape, 0)

    def width(h: float) -> float:
        return col_w if one_d else h * ratio

    offset = -cfg.outline_offset if stroke > 0 else 0.0  # internally + = outward
    if offset and shape == "custom":
        offset = 0.0
        if warnings is not None:
            warnings.append("Outline offset is not supported for custom SVG symbols; ignored.")

    def geom(cx: float, h: float, d: float, attrs: str) -> str:
        """Symbol shape of height h on the baseline, its edge moved by d (+ outward)."""
        if shape == "circle":
            return f'<circle cx="{f(cx)}" cy="{f(-h / 2)}" r="{f(h / 2 + d)}"{attrs}/>'
        if shape == "column":
            return (f'<rect x="{f(cx - col_w / 2 - d)}" y="{f(-h - d)}" width="{f(col_w + 2 * d)}" '
                    f'height="{f(h + 2 * d)}"{attrs}/>')
        if shape == "apex":
            pts = [(cx - col_w / 2, 0), (cx, -h), (cx + col_w / 2, 0)]
        else:
            pts = [(cx + x * h, y * h) for x, y in poly]
        pts = _offset_polygon(pts, d)
        return f'<polygon points="{" ".join(f"{f(x)},{f(y)}" for x, y in pts)}"{attrs}/>'

    def symbol(cx: float, h: float) -> str:
        line = f' stroke="{cfg.stroke_colour}" stroke-width="{f(stroke)}" stroke-linejoin="miter"'
        if shape == "custom":
            return custom.place(cx, 0, h, fill, stroke, cfg.stroke_colour)
        if stroke <= 0:
            return geom(cx, h, 0, f' fill="{fill}" stroke="none"')
        if not offset:
            return geom(cx, h, 0, f' fill="{fill}"{line}')
        parts = [] if fill == "none" else [geom(cx, h, 0, f' fill="{fill}" stroke="none"')]
        parts.append(geom(cx, h, offset, f' fill="none"{line}'))   # outline moved off the edge
        return "<g>" + "".join(parts) + "</g>"

    if offset < 0:  # an inward outline must still fit inside the smallest symbol
        if shape == "circle":
            limit = h_min / 2
        elif shape == "column":
            limit = min(col_w, h_min) / 2
        elif shape == "apex":
            limit = _incircle([(-col_w / 2, 0), (0, -h_min), (col_w / 2, 0)])[2]
        else:
            limit = _incircle([(x * h_min, y * h_min) for x, y in poly])[2]
        if -offset >= limit:
            raise ValueError(f"Inward outline offset {-offset:g} pt is too large for the smallest symbol "
                             f"(must be under {limit:.2f} pt).")

    # ---- label space: L = printed numbers, D(L) = data value it stands for
    L0, L1 = (cfg.label_min, cfg.label_max) if cfg.custom_labels else (v0, v1)
    log_map = cfg.custom_labels and cfg.label_mapping == "log"

    def D(lv: float) -> float:
        if log_map:
            t = (math.log10(lv) - math.log10(L0)) / (math.log10(L1) - math.log10(L0))
        else:
            t = (lv - L0) / (L1 - L0)
        return v0 + t * (v1 - v0)

    def XL(lv: float) -> float:
        return X(D(lv))

    def close(a: float, b: float) -> bool:
        return abs(a - b) <= 1e-9 * max(abs(a), abs(b), 1e-12)

    def inside(lv: float) -> bool:
        return (L0 < lv or close(lv, L0)) and (lv < L1 or close(lv, L1))

    def unique(vals):
        out = []
        for v in sorted(vals):
            if not out or not close(v, out[-1]):
                out.append(v)
        return out

    # ---- divisions (in label units)
    step = mults = None
    if cfg.divisions:
        major = unique(d for d in cfg.divisions if inside(d))
        if not major and warnings is not None:
            warnings.append("No user-defined division lies inside the value range; only min and max are drawn.")
        labelled = [(L0, 0), (L1, 0)] + [(d, 1) for d in major if not (close(d, L0) or close(d, L1))]
    elif log_map:                                             # decades, or 1-2-5 if few decades
        k0, k1 = math.floor(math.log10(L0)), math.ceil(math.log10(L1))
        for mults in ((1,), (1, 2, 5), tuple(range(1, 10))):
            major = unique(round(m * 10.0 ** k, 12) for k in range(k0, k1 + 1) for m in mults
                           if inside(round(m * 10.0 ** k, 12)))
            if len(major) >= 3:
                break
        labelled = [(L0, 0), (L1, 0)] + [(d, 1) for d in major if not (close(d, L0) or close(d, L1))]
    else:
        step = nice_step(L1 - L0)
        k0, k1 = math.ceil(L0 / step - 1e-9), math.floor(L1 / step + 1e-9)
        major = [round(k * step, 10) for k in range(k0, k1 + 1)]
        labelled = [(L0, 0), (L1, 0)] + [(d, 1) for d in major if not (close(d, L0) or close(d, L1))]
    divs = unique([L0, L1, *major])                           # endpoint lines always drawn

    # ---- subdivisions (in label units)
    subs: list[float] = []
    if step is not None:
        n = cfg.subdivisions if cfg.subdivisions is not None else auto_subdivisions(step, length / (L1 - L0))
        if n > 0:
            sub = step / n
            subs = [k * sub for k in range(math.ceil(L0 / sub - 1e-9), math.floor(L1 / sub + 1e-9) + 1)]
    elif cfg.subdivisions:                                    # equal parts between divisions
        n = cfg.subdivisions
        for a, b in zip(divs, divs[1:]):
            subs += [a + (b - a) * i / n for i in range(1, n)]
    elif log_map and mults == (1,):                           # log ruler ticks 2..9 per decade
        decade = length / (math.log10(L1) - math.log10(L0))
        ms = range(2, 10) if decade >= 60 else (2, 5) if decade >= 20 else ()
        k0, k1 = math.floor(math.log10(L0)), math.ceil(math.log10(L1))
        subs = [m * 10.0 ** k for k in range(k0, k1 + 1) for m in ms]
    subs = [v for v in subs if inside(v) and not any(close(v, d) for d in divs)]

    # ---- symbols: min and max, optionally one at every main division
    sym_vals = unique([L0, L1, *(major if cfg.symbols_at_divisions else [])])
    sym_vals.sort(key=lambda lv: -H(D(lv)))                   # big first, small ones on top

    # ---- labels (greedy placement, endpoints / user values first, no overlaps)
    tick = max(fs * 0.35, 2.5)
    labels: list[tuple[float, str]] = []
    if fs > 0:
        taken: list[tuple[float, float]] = []
        dropped = []
        for lv, prio in sorted(labelled, key=lambda t: t[1]):
            text = format_number(lv, cfg.environment)
            half = 0.55 * fs * len(text) / 2
            lo, hi = XL(lv) - half, XL(lv) + half
            # min and max are always labelled; other labels only where they fit
            if prio == 0 or all(hi + fs * 0.5 < a or lo - fs * 0.5 > b for a, b in taken):
                taken.append((lo, hi))
                labels.append((lv, text))
            else:
                dropped.append(text)
        if dropped and warnings is not None:
            warnings.append("Labels left out to avoid collisions: " + ", ".join(dropped))

    # ---- bounding box (baseline at y = 0, min symbol centre at x = 0)
    h_max = H(v1)
    min_x, max_x = 0.0, length
    for lv in sym_vals:
        half_w = width(H(D(lv))) / 2
        min_x, max_x = min(min_x, XL(lv) - half_w), max(max_x, XL(lv) + half_w)
    for lv, text in labels:
        half = 0.55 * fs * len(text) / 2
        min_x, max_x = min(min_x, XL(lv) - half), max(max_x, XL(lv) + half)
    min_y = -h_max
    max_y = tick + (fs * 1.25 if labels else 0)
    pad = 2 + stroke * 2 + max(offset, 0) * 3  # room for miter tips of sharp shapes
    min_x, min_y, max_x, max_y = min_x - pad, min_y - pad, max_x + pad, max_y + pad
    W, Hh = max_x - min_x, max_y - min_y

    # ---- assemble
    out = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="{SVG_NS}" xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{f(W)}pt" height="{f(Hh)}pt" viewBox="{f(min_x)} {f(min_y)} {f(W)} {f(Hh)}">',
        '<g id="symbols">', *[symbol(XL(lv), H(D(lv))) for lv in sym_vals], "</g>",
        f'<g id="subdivisions" stroke="{LINE_COLOUR}" stroke-width="{f(cfg.line_width * 0.8)}">',
        *[f'<line x1="{f(XL(v))}" y1="0" x2="{f(XL(v))}" y2="{f(tick * 0.55)}"/>' for v in subs],
        "</g>",
        f'<g id="divisions" stroke="{LINE_COLOUR}" stroke-width="{f(cfg.line_width)}">',
        *[f'<line x1="{f(XL(d))}" y1="{f(tick)}" x2="{f(XL(d))}" y2="{f(-H(D(d)))}"/>' for d in divs],
        "</g>",
    ]
    n_pts = 2 if one_d else 240  # 1D shapes: straight line
    pts = [v0 + (v1 - v0) * i / (n_pts - 1) for i in range(n_pts)]
    d = "M" + " L".join(f"{f(X(v))},{f(-H(v))}" for v in pts)
    out.append(f'<path id="curve" d="{d}" fill="none" stroke="{LINE_COLOUR}" '
               f'stroke-width="{f(cfg.curve_width)}" stroke-linejoin="round"/>')
    out.append(f'<line id="baseline" x1="0" y1="0" x2="{f(length)}" y2="0" '
               f'stroke="{LINE_COLOUR}" stroke-width="{f(cfg.line_width * 1.2)}"/>')
    if labels:
        out.append(f'<g id="labels" font-family="{FONT}" font-size="{f(fs)}" '
                   f'text-anchor="middle" fill="{LINE_COLOUR}">')
        y = tick + fs * 0.95
        out += [f'<text x="{f(XL(v))}" y="{f(y)}">{escape(t)}</text>' for v, t in labels]
        out.append("</g>")
    out.append("</svg>")
    return "\n".join(out)


# --------------------------------------------------------------------------- built-in PDF writer
# Dependency-free SVG -> PDF for the elements this generator produces, including most
# custom symbols (path commands incl. arcs, basic shapes, style attributes, transforms).
# Anything it cannot handle (CSS classes, <use>, gradients ...) falls back to cairosvg/svglib.

class _Unsupported(Exception):
    pass


_NUM = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_HELV = {c: 556 for c in "0123456789"}  # Helvetica advance widths (1/1000 em)
_HELV.update({" ": 278, " ": 278, ",": 278, ".": 278, "-": 333})
_INHERITED = ("fill", "stroke", "stroke-width", "stroke-linejoin", "fill-rule", "font-size", "text-anchor")
_NAMED = {"black": (0, 0, 0), "white": (1, 1, 1), "red": (1, 0, 0), "green": (0, 0.5, 0),
          "blue": (0, 0, 1), "gray": (0.5, 0.5, 0.5), "grey": (0.5, 0.5, 0.5)}


def _nums(s: str) -> list[float]:
    return [float(t) for t in re.findall(_NUM, s or "")]


def _pf(v: float) -> str:
    s = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _rgb(c: str):
    c = c.strip().lower()
    if c in ("none", "transparent"):
        return None
    if c in _NAMED:
        return _NAMED[c]
    if re.fullmatch(r"#[0-9a-f]{3}", c):
        c = "#" + "".join(ch * 2 for ch in c[1:])
    if re.fullmatch(r"#[0-9a-f]{6}", c):
        return tuple(int(c[i:i + 2], 16) / 255 for i in (1, 3, 5))
    m = re.fullmatch(r"rgb\(([^)]*)\)", c)
    if m:
        return tuple(v / 255 for v in _nums(m.group(1))[:3])
    raise _Unsupported(f"colour {c}")


def _transform(s: str) -> str:
    out = []
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        a = _nums(args)
        if name == "translate":
            ms = [(1, 0, 0, 1, a[0], a[1] if len(a) > 1 else 0)]
        elif name == "scale":
            ms = [(a[0], 0, 0, a[1] if len(a) > 1 else a[0], 0, 0)]
        elif name == "matrix":
            ms = [tuple(a[:6])]
        elif name == "rotate":
            t = math.radians(a[0])
            rot = (math.cos(t), math.sin(t), -math.sin(t), math.cos(t), 0, 0)
            ms = [rot] if len(a) < 3 else [(1, 0, 0, 1, a[1], a[2]), rot, (1, 0, 0, 1, -a[1], -a[2])]
        else:
            raise _Unsupported(f"transform {name}")
        out += [" ".join(_pf(v) for v in m) + " cm" for m in ms]
    return " ".join(out)


def _arc(x1, y1, rx, ry, phi, fa, fs, x2, y2):
    """SVG elliptical arc -> list of cubic Bezier segments (SVG spec, appendix F.6)."""
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [(x1, y1, x2, y2, x2, y2)]
    p = math.radians(phi)
    cp, sp = math.cos(p), math.sin(p)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p, y1p = cp * dx + sp * dy, -sp * dx + cp * dy
    lam = x1p ** 2 / rx ** 2 + y1p ** 2 / ry ** 2
    if lam > 1:
        rx, ry = rx * math.sqrt(lam), ry * math.sqrt(lam)
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) * (-1 if fa == fs else 1)
    cxp, cyp = co * rx * y1p / ry, -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x1 + x2) / 2
    cy = sp * cxp + cp * cyp + (y1 + y2) / 2

    def ang(ux, uy, vx, vy):
        return math.atan2(ux * vy - uy * vx, ux * vx + uy * vy)

    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    t1 = ang(1, 0, ux, uy)
    dt = ang(ux, uy, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not fs and dt > 0:
        dt -= 2 * math.pi
    elif fs and dt < 0:
        dt += 2 * math.pi
    n = max(1, math.ceil(abs(dt) / (math.pi / 2)))
    d = dt / n
    k = 4 / 3 * math.tan(d / 4)

    def pt(a):
        return (cx + rx * math.cos(a) * cp - ry * math.sin(a) * sp,
                cy + rx * math.cos(a) * sp + ry * math.sin(a) * cp)

    def der(a):
        return (-rx * math.sin(a) * cp - ry * math.cos(a) * sp,
                -rx * math.sin(a) * sp + ry * math.cos(a) * cp)

    segs = []
    for i in range(n):
        a1, a2 = t1 + i * d, t1 + (i + 1) * d
        (ax, ay), (bx, by), (dax, day), (dbx, dby) = pt(a1), pt(a2), der(a1), der(a2)
        segs.append((ax + k * dax, ay + k * day, bx - k * dbx, by - k * dby, bx, by))
    return segs


def _path(d: str) -> str:
    toks = re.findall(r"[MmLlHhVvCcSsQqTtZzAa]|" + _NUM, d)
    out, i, cmd = [], 0, None
    cx = cy = sx = sy = 0.0
    lc = lq = None  # last cubic / quadratic control point (for S / T)

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    def cubic(x1, y1, x2, y2, x, y):
        out.append(f"{_pf(x1)} {_pf(y1)} {_pf(x2)} {_pf(y2)} {_pf(x)} {_pf(y)} c")

    while i < len(toks):
        if toks[i].isalpha():
            cmd = toks[i]
            i += 1
            if cmd in "Zz":
                out.append("h")
                cx, cy, lc, lq = sx, sy, None, None
                continue
        elif cmd is None or cmd in "Zz":
            raise _Unsupported("path syntax")
        rel, C = cmd.islower(), cmd.upper()
        ox, oy = (cx, cy) if rel else (0.0, 0.0)
        nlc = nlq = None
        if C == "M":
            cx, cy = num() + ox, num() + oy
            sx, sy = cx, cy
            out.append(f"{_pf(cx)} {_pf(cy)} m")
            cmd = "l" if rel else "L"
        elif C == "L":
            cx, cy = num() + ox, num() + oy
            out.append(f"{_pf(cx)} {_pf(cy)} l")
        elif C == "H":
            cx = num() + ox
            out.append(f"{_pf(cx)} {_pf(cy)} l")
        elif C == "V":
            cy = num() + oy
            out.append(f"{_pf(cx)} {_pf(cy)} l")
        elif C in "CS":
            if C == "C":
                x1, y1 = num() + ox, num() + oy
            else:
                x1, y1 = (2 * cx - lc[0], 2 * cy - lc[1]) if lc else (cx, cy)
            x2, y2, x, y = num() + ox, num() + oy, num() + ox, num() + oy
            cubic(x1, y1, x2, y2, x, y)
            cx, cy, nlc = x, y, (x2, y2)
        elif C in "QT":
            if C == "Q":
                qx, qy = num() + ox, num() + oy
            else:
                qx, qy = (2 * cx - lq[0], 2 * cy - lq[1]) if lq else (cx, cy)
            x, y = num() + ox, num() + oy
            cubic(cx + 2 / 3 * (qx - cx), cy + 2 / 3 * (qy - cy),
                  x + 2 / 3 * (qx - x), y + 2 / 3 * (qy - y), x, y)
            cx, cy, nlq = x, y, (qx, qy)
        elif C == "A":
            rx, ry, phi, fa, fs = num(), num(), num(), num(), num()
            x, y = num() + ox, num() + oy
            for seg in _arc(cx, cy, rx, ry, phi, int(fa), int(fs), x, y):
                cubic(*seg)
            cx, cy = x, y
        lc, lq = nlc, nlq
    return " ".join(out)


def _ellipse(cx, cy, rx, ry) -> str:
    k = 0.5522847498
    return (f"{_pf(cx + rx)} {_pf(cy)} m "
            f"{_pf(cx + rx)} {_pf(cy + k * ry)} {_pf(cx + k * rx)} {_pf(cy + ry)} {_pf(cx)} {_pf(cy + ry)} c "
            f"{_pf(cx - k * rx)} {_pf(cy + ry)} {_pf(cx - rx)} {_pf(cy + k * ry)} {_pf(cx - rx)} {_pf(cy)} c "
            f"{_pf(cx - rx)} {_pf(cy - k * ry)} {_pf(cx - k * rx)} {_pf(cy - ry)} {_pf(cx)} {_pf(cy - ry)} c "
            f"{_pf(cx + k * rx)} {_pf(cy - ry)} {_pf(cx + rx)} {_pf(cy - k * ry)} {_pf(cx + rx)} {_pf(cy)} c h")


def _pdf_text(s: str) -> str:
    b = s.encode("cp1252", errors="replace").decode("latin-1")
    return b.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def svg_to_pdf_builtin(svg: str) -> bytes:
    root = ET.fromstring(svg.encode("utf-8"))
    mx, my, W, H = _nums(root.get("viewBox"))
    ops = [f"1 0 0 -1 {_pf(-mx)} {_pf(H + my)} cm"]  # SVG coords (y down) -> PDF (y up)

    def walk(el, st):
        tag = _local(el.tag)
        if tag in ("title", "desc", "metadata", "defs"):
            return
        if el.get("class") is not None:
            raise _Unsupported("CSS class")
        st = dict(st)
        for k in _INHERITED:
            if el.get(k) is not None:
                st[k] = el.get(k)
        for decl in (el.get("style") or "").split(";"):
            if ":" in decl:
                k, v = (t.strip() for t in decl.split(":", 1))
                if k in _INHERITED:
                    st[k] = v
        tf = _transform(el.get("transform")) if el.get("transform") else ""
        g = float
        if tag in ("svg", "g"):
            ops.append("q " + tf)
            for c in el:
                walk(c, st)
            ops.append("Q")
            return
        if tag == "text":
            fs = float(_nums(st.get("font-size", "12"))[0])
            txt = "".join(el.itertext())
            x, y = g(el.get("x", 0)), g(el.get("y", 0))
            width = sum(_HELV.get(ch, 556) for ch in txt) * fs / 1000
            anchor = st.get("text-anchor", "start")
            x -= width / 2 if anchor == "middle" else width if anchor == "end" else 0
            col = _rgb(st.get("fill", "black")) or (0, 0, 0)
            ops.append(f"q {tf} BT {' '.join(_pf(v) for v in col)} rg /F1 {_pf(fs)} Tf "
                       f"1 0 0 -1 {_pf(x)} {_pf(y)} Tm ({_pdf_text(txt)}) Tj ET Q")
            return
        if tag == "path":
            geom = _path(el.get("d", ""))
        elif tag == "line":
            geom = (f"{_pf(g(el.get('x1', 0)))} {_pf(g(el.get('y1', 0)))} m "
                    f"{_pf(g(el.get('x2', 0)))} {_pf(g(el.get('y2', 0)))} l")
        elif tag == "rect":
            geom = (f"{_pf(g(el.get('x', 0)))} {_pf(g(el.get('y', 0)))} "
                    f"{_pf(g(el.get('width', 0)))} {_pf(g(el.get('height', 0)))} re")
        elif tag in ("polygon", "polyline"):
            v = _nums(el.get("points"))
            pts = list(zip(v[::2], v[1::2]))
            geom = f"{_pf(pts[0][0])} {_pf(pts[0][1])} m " + " ".join(
                f"{_pf(x)} {_pf(y)} l" for x, y in pts[1:]) + (" h" if tag == "polygon" else "")
        elif tag == "circle":
            r = g(el.get("r", 0))
            geom = _ellipse(g(el.get("cx", 0)), g(el.get("cy", 0)), r, r)
        elif tag == "ellipse":
            geom = _ellipse(g(el.get("cx", 0)), g(el.get("cy", 0)), g(el.get("rx", 0)), g(el.get("ry", 0)))
        else:
            raise _Unsupported(tag)
        fill = _rgb(st.get("fill", "black")) if tag not in ("line", "polyline") or "fill" in st else None
        stroke = _rgb(st.get("stroke", "none"))
        sw = _nums(st.get("stroke-width", "1"))[0]
        paint = [f"q {tf}"]
        if fill:
            paint.append(" ".join(_pf(v) for v in fill) + " rg")
        if stroke and sw > 0:
            join = {"miter": 0, "round": 1, "bevel": 2}.get(st.get("stroke-linejoin", "miter"), 0)
            paint.append(" ".join(_pf(v) for v in stroke) + f" RG {_pf(sw)} w {join} j")
        else:
            stroke = None
        even = "*" if st.get("fill-rule") == "evenodd" else ""
        op = ("B" + even) if fill and stroke else ("f" + even) if fill else "S" if stroke else "n"
        ops.append(" ".join(paint) + f" {geom} {op} Q")

    walk(root, {})
    content = "\n".join(ops).encode("latin-1")
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_pf(W)} {_pf(H)}] "
         f"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>").encode(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    pdf = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for n, obj in enumerate(objs, 1):
        offsets.append(len(pdf))
        pdf += b"%d 0 obj\n" % n + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    pdf += b"".join(b"%010d 00000 n \n" % o for o in offsets)
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)
    return bytes(pdf)


# --------------------------------------------------------------------------- output
def svg_to_pdf(svg: str, path: str | Path) -> None:
    """Built-in writer first (no dependencies); cairosvg / svglib only for exotic custom SVGs."""
    try:
        Path(path).write_bytes(svg_to_pdf_builtin(svg))
        return
    except _Unsupported as e:
        reason = str(e)
    try:
        import cairosvg
        cairosvg.svg2pdf(bytestring=svg.encode("utf-8"), write_to=str(path))
        return
    except ImportError:
        pass
    try:
        from reportlab.graphics import renderPDF
        from svglib.svglib import svg2rlg
    except ImportError:
        raise RuntimeError(f"The custom SVG uses a feature the built-in PDF writer does not support "
                           f"({reason}). Install cairosvg or svglib + reportlab, or use the SVG output.")
    with tempfile.NamedTemporaryFile("w", suffix=".svg", delete=False, encoding="utf-8") as tmp:
        tmp.write(svg)
    try:
        renderPDF.drawToFile(svg2rlg(tmp.name), str(path))
    finally:
        os.unlink(tmp.name)


def save(cfg: ScaleConfig, *paths: str | Path) -> str:
    notes: list[str] = []
    svg = generate_svg(cfg, notes)
    for n in notes:
        print("Note:", n, file=sys.stderr)
    for p in paths:
        p = Path(p)
        if p.suffix.lower() == ".pdf":
            svg_to_pdf(svg, p)
        else:
            p.write_text(svg, encoding="utf-8")
    return svg


# --------------------------------------------------------------------------- CLI
def _divisions(text: str) -> list[float]:
    vals = []
    for part in text.split(";"):
        part = part.strip().replace(" ", "")
        if part:
            vals.append(float(part.replace(",", ".")))
    return vals


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Value-scale generator for legends of proportional-symbol maps (SVG / PDF).")
    p.add_argument("--min", dest="value_min", type=float, required=True, help="minimum phenomenon value")
    p.add_argument("--max", dest="value_max", type=float, required=True, help="maximum phenomenon value")
    p.add_argument("--shape", default="circle", choices=SHAPES, help="symbol shape (default circle)")
    p.add_argument("--svg-file", help="SVG file for --shape custom (max 1 MB)")
    p.add_argument("--min-size", type=float, default=12, help="height of the smallest symbol (default 12)")
    p.add_argument("--max-size", type=float,
                   help="height of the largest symbol, as Pro's optional Maximum size (default none)")
    p.add_argument("--max-interpolation", default="area", choices=("area", "height"),
                   help="with --max-size: symbols between min and max interpolated by area or height")
    p.add_argument("--column-width", type=float, default=5,
                   help="column / apex-base width for --shape column|apex, in --unit (default 5)")
    p.add_argument("--unit", default="pt", choices=tuple(PT), help="unit of symbol size and width (default pt)")
    p.add_argument("--length", type=float, default=120, help="scalebar length in mm (default 120)")
    p.add_argument("--font-size", type=float, default=10, help="label font size in pt, 0 = no labels (default 10)")
    p.add_argument("--style", default="european", choices=("european", "english", "raw"),
                   help="number format: european 12 345,6 | english 12,345.6 | raw 12345.6")
    p.add_argument("--divisions", type=_divisions, help='user-defined divisions, e.g. "50; 100; 128.6; 150"')
    p.add_argument("--subdivisions", type=int, help="gaps between main divisions (default automatic)")
    p.add_argument("--fill", help="symbol fill colour, e.g. '#e34a33' (default no fill)")
    p.add_argument("--stroke", type=float, help=f"symbol stroke in pt, 0 = none (default {DEFAULT_STROKE})")
    p.add_argument("--outline-offset", type=float, default=0,
                   help="move the symbol outline off the symbol edge, pt: + inward, - outward (default 0)")
    p.add_argument("--curve-width", type=float, default=W_CURVE, help=f"curve line width, pt (default {W_CURVE})")
    p.add_argument("--line-width", type=float, default=W_LINE,
                   help=f"division line width, pt; baseline 1.2x, subdivisions 0.8x (default {W_LINE})")
    p.add_argument("--stroke-colour", default="#000000", help="symbol stroke colour (default black)")
    p.add_argument("--label-min", type=float, help="number printed at the minimum (own label range)")
    p.add_argument("--label-max", type=float, help="number printed at the maximum (own label range)")
    p.add_argument("--label-mapping", default="linear", choices=("linear", "log"),
                   help="link between label range and data: linear | log (data stored as logarithms)")
    p.add_argument("--symbols-at-divisions", action="store_true",
                   help="draw a symbol at every main division as well")
    p.add_argument("-o", "--output", action="append",
                   help="output file(s), .svg or .pdf; repeatable (default value_scale.svg)")
    a = p.parse_args(argv)

    cfg = ScaleConfig(
        value_min=a.value_min, value_max=a.value_max, symbol_shape=a.shape,
        min_symbol_size=a.min_size, symbol_unit=a.unit,
        max_symbol_size=a.max_size, max_interpolation=a.max_interpolation,
        scalebar_length=a.length, font_size=a.font_size,
        environment={"european": "cz", "english": "en", "raw": "no"}[a.style],
        divisions=a.divisions, subdivisions=a.subdivisions, fill_colour=a.fill,
        stroke=a.stroke, stroke_colour=a.stroke_colour, svg_file=a.svg_file,
        label_min=a.label_min, label_max=a.label_max, label_mapping=a.label_mapping,
        symbols_at_divisions=a.symbols_at_divisions, column_width=a.column_width,
        outline_offset=a.outline_offset, curve_width=a.curve_width, line_width=a.line_width,
    )
    outputs = a.output or ["value_scale.svg"]
    try:
        save(cfg, *outputs)
    except (ValueError, OSError, RuntimeError, ET.ParseError) as e:
        sys.exit(f"Error: {e}")
    print("Written:", ", ".join(outputs))


if __name__ == "__main__":
    main()
