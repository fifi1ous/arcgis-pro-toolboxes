# -*- coding: utf-8 -*-
"""
Segmented Legend - ArcGIS Pro Python toolbox (ArcGIS Pro 3.0+)

Creates a "pie / donut / polygon" legend graphic such as:

        VĚKOVÉ KATEGORIE
          (  donut  )
      65+  |  0–14  | 15–64

Shapes
  * Circle (pie)   - full circle divided into sectors
  * Donut          - ring divided into sectors
  * Polygon        - 2 parts = rectangle split in two, 3 = triangle,
                     4 = square, 5 = pentagon, ... (one part per side)
  * Polygon ring   - the same polygon with a hole (donut-like)
Styles
  * Full           - filled segments, thin separator line
  * Outline        - only the segment outlines, in the segment colour

Output
  * Layout  - polygons + EDITABLE text elements, grouped, optionally
              overwriting a previous legend of the same name
              (+ optionally "Also save as image file" = a copy of the
              same legend saved as SVG / PNG / JPG / PDF in one run)
  * SVG / PNG / JPG / PDF file only (SVG keeps the text as real text)
"""

import math
import os
import re

import arcpy

# --------------------------------------------------------------------------
# constants
# --------------------------------------------------------------------------
HEX_RE = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")

PALETTE = ["#86C97F", "#3E66B0", "#FFC48A", "#E8706A", "#9B7BC4",
           "#F2D15C", "#5BBFC4", "#B5B5B5", "#C98B5B", "#E58FC0"]

DEFAULT_ROWS = [["0–14", "#86C97F", 1.0],
                ["15–64", "#3E66B0", 1.0],
                ["65+", "#FFC48A", 1.0]]

TARGETS = ["Layout", "SVG", "PNG", "JPG", "PDF"]
SHAPES = ["Circle (pie)", "Donut", "Polygon", "Polygon ring"]
STYLES = ["Full", "Outline"]
LABEL_POS = ["Inside", "Outside", "None"]
TITLE_POS = ["Above", "Below", "None"]
DIRECTIONS = ["Clockwise", "Counter-clockwise"]

PT_PER_UNIT = {"MILLIMETER": 72.0 / 25.4, "CENTIMETER": 72.0 / 2.54,
               "INCH": 72.0, "POINT": 1.0}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def norm_hex(value, default=None):
    """'#abc', 'AABBCC', '#aabbcc' -> '#AABBCC'.  Empty -> default."""
    if value is None:
        return default
    s = str(value).strip()
    if not s:
        return default
    m = HEX_RE.match(s)
    if not m:
        raise ValueError("Invalid hex colour: '%s' (use e.g. #3E66B0)" % s)
    h = m.group(1)
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.upper()


def hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def pick_colour(initial_hex=None):
    """Open the Windows colour dialog (full spectrum). Returns '#RRGGBB' or None."""
    try:
        import ctypes
        from ctypes import wintypes

        class CHOOSECOLOR(ctypes.Structure):
            _fields_ = [("lStructSize", wintypes.DWORD),
                        ("hwndOwner", wintypes.HWND),
                        ("hInstance", wintypes.HWND),
                        ("rgbResult", wintypes.DWORD),
                        ("lpCustColors", ctypes.POINTER(wintypes.DWORD)),
                        ("Flags", wintypes.DWORD),
                        ("lCustData", wintypes.LPARAM),
                        ("lpfnHook", ctypes.c_void_p),
                        ("lpTemplateName", wintypes.LPCWSTR)]

        CC_RGBINIT, CC_FULLOPEN, CC_ANYCOLOR = 0x1, 0x2, 0x100
        custom = (wintypes.DWORD * 16)()
        for i, c in enumerate(PALETTE[:16]):
            r, g, b = hex_to_rgb(c)
            custom[i] = r | (g << 8) | (b << 16)

        cc = CHOOSECOLOR()
        cc.lStructSize = ctypes.sizeof(CHOOSECOLOR)
        cc.hwndOwner = ctypes.windll.user32.GetForegroundWindow()
        r, g, b = hex_to_rgb(norm_hex(initial_hex, "#FFFFFF"))
        cc.rgbResult = r | (g << 8) | (b << 16)
        cc.lpCustColors = custom
        cc.Flags = CC_RGBINIT | CC_FULLOPEN | CC_ANYCOLOR
        if ctypes.windll.comdlg32.ChooseColorW(ctypes.byref(cc)):
            v = cc.rgbResult
            return "#%02X%02X%02X" % (v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF)
    except Exception:
        pass
    return None


def _arc(r, a0, a1, step=2.0):
    n = max(2, int(math.ceil(abs(a1 - a0) / step)) + 1)
    pts = []
    for i in range(n):
        a = math.radians(a0 + (a1 - a0) * i / (n - 1))
        pts.append((r * math.cos(a), r * math.sin(a)))
    return pts


def _centroid(pts):
    area = cx = cy = 0.0
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        c = x0 * y1 - x1 * y0
        area += c
        cx += (x0 + x1) * c
        cy += (y0 + y1) * c
    if abs(area) < 1e-12:
        return (sum(p[0] for p in pts) / len(pts), sum(p[1] for p in pts) / len(pts))
    return cx / (3.0 * area), cy / (3.0 * area)


def _rotate(p, deg):
    a = math.radians(deg)
    c, s = math.cos(a), math.sin(a)
    return (p[0] * c - p[1] * s, p[0] * s + p[1] * c)


def _ray_extent(pts, d):
    """Farthest intersection of ray (0,0)+t*d (t>=0) with polygon edges."""
    best = 0.0
    dx, dy = d
    for (x0, y0), (x1, y1) in zip(pts, pts[1:] + pts[:1]):
        ex, ey = x1 - x0, y1 - y0
        den = dx * ey - dy * ex
        if abs(den) < 1e-12:
            continue
        t = (x0 * ey - y0 * ex) / den
        u = (x0 * dy - y0 * dx) / den
        if t >= 0 and -1e-9 <= u <= 1 + 1e-9:
            best = max(best, t)
    return best


# --------------------------------------------------------------------------
# geometry engine (pure python, shared by layout and file output)
# --------------------------------------------------------------------------
def build_spec(parts, shape, style, size, ring_ratio=0.55, rotation=0.0,
               clockwise=True, label_pos="Inside", title="", title_pos="Above",
               title_pt=12.0, label_pt=8.0, pt_unit=25.4 / 72.0,
               label_colour=None, title_colour="#000000"):
    """
    parts : list of dicts {label, colour, weight}
    size  : diameter (circle) / circumscribed diameter (polygon) / width (2-part rectangle)
    pt_unit : size of 1 typographic point in the output units
    Returns a dict with segments (polygon point lists), labels and title, centred
    so that the bounding box of the shape is at (0, 0).
    """
    n = len(parts)
    R = size / 2.0
    ring = shape in ("Donut", "Polygon ring")
    ring_ratio = min(max(ring_ratio, 0.05), 0.95)
    sdir = -1.0 if clockwise else 1.0
    segs = []  # list of [pts, part]

    if shape in ("Circle (pie)", "Donut"):
        w = [max(0.0, float(p["weight"] or 0)) for p in parts]
        if sum(w) <= 0:
            w = [1.0] * n
        total = sum(w)
        start = 90.0 - rotation
        cum = 0.0
        ri = R * ring_ratio
        for p, wi in zip(parts, w):
            span = 360.0 * wi / total
            if span <= 0:
                continue
            a0 = start + sdir * cum
            a1 = a0 + sdir * span
            cum += span
            full = span >= 359.999
            if not ring:
                pts = _arc(R, a0, a1)[:-1] if full else [(0.0, 0.0)] + _arc(R, a0, a1)
                lab_r = 0.0 if full else R * 0.62
            else:
                pts = _arc(R, a0, a1) + _arc(ri, a1, a0)
                lab_r = (R + ri) / 2.0
            am = math.radians(a0 + sdir * span / 2.0)
            d = (math.cos(am), math.sin(am))
            segs.append({"pts": pts, "part": p, "dir": d,
                         "inside": (lab_r * d[0], lab_r * d[1]), "extent": R})
    else:
        if n < 2:
            raise ValueError("Polygon shapes need at least 2 divisions.")
        raw = []
        if n == 2:
            W, H = size, size / 2.0
            t = (1.0 - ring_ratio) * H / 2.0
            w0 = max(0.0, float(parts[0]["weight"] or 0))
            w1 = max(0.0, float(parts[1]["weight"] or 0))
            frac = 0.5 if (w0 + w1) <= 0 else w0 / (w0 + w1)
            xs = -W / 2.0 + W * frac
            lo, hi = (-W / 2 + t + 1e-6, W / 2 - t - 1e-6) if ring else (-W / 2, W / 2)
            xs = min(max(xs, lo), hi)
            l, r, b, tp = -W / 2, W / 2, -H / 2, H / 2
            if not ring:
                left = [(xs, tp), (l, tp), (l, b), (xs, b)]
                right = [(xs, tp), (xs, b), (r, b), (r, tp)]
            else:
                left = [(xs, tp), (l, tp), (l, b), (xs, b), (xs, b + t),
                        (l + t, b + t), (l + t, tp - t), (xs, tp - t)]
                right = [(xs, tp), (xs, tp - t), (r - t, tp - t), (r - t, b + t),
                         (xs, b + t), (xs, b), (r, b), (r, tp)]
            raw = [(left, parts[0]), (right, parts[1])]
        else:
            base = -90.0 + 180.0 / n          # flat bottom edge
            verts = [(R * math.cos(math.radians(base + k * 360.0 / n)),
                      R * math.sin(math.radians(base + k * 360.0 / n))) for k in range(n)]
            # start at top-most vertex (leftmost on a tie)
            first = max(range(n), key=lambda k: (round(verts[k][1], 9), -verts[k][0]))
            step = -1 if clockwise else 1
            seq = [(first + step * k) % n for k in range(n + 1)]
            for k in range(n):
                vi, vj = verts[seq[k]], verts[seq[k + 1]]
                if ring:
                    pts = [vi, vj, (vj[0] * ring_ratio, vj[1] * ring_ratio),
                           (vi[0] * ring_ratio, vi[1] * ring_ratio)]
                else:
                    pts = [(0.0, 0.0), vi, vj]
                raw.append((pts, parts[k]))
        for pts, p in raw:
            pts = [_rotate(q, -rotation) for q in pts]
            c = _centroid(pts)
            ln = math.hypot(*c) or 1.0
            d = (c[0] / ln, c[1] / ln)
            segs.append({"pts": pts, "part": p, "dir": d, "inside": c,
                         "extent": _ray_extent(pts, d)})

    # ---- centre the bounding box of the shape on (0,0)
    allp = [q for s in segs for q in s["pts"]]
    minx, maxx = min(q[0] for q in allp), max(q[0] for q in allp)
    miny, maxy = min(q[1] for q in allp), max(q[1] for q in allp)
    ox, oy = (minx + maxx) / 2.0, (miny + maxy) / 2.0

    gap = 0.45 * label_pt * pt_unit + 0.03 * size
    out = []
    for s in segs:
        p = s["part"]
        pts = [(x - ox, y - oy) for x, y in s["pts"]]
        lab = None
        if label_pos != "None" and str(p["label"] or "").strip():
            if label_pos == "Inside":
                lx, ly = s["inside"]
                align = "center"
            else:
                dx, dy = s["dir"]
                dist = s["extent"] + gap
                if abs(dx) < 0.25:                       # top / bottom -> centred
                    dist += 0.5 * label_pt * pt_unit
                    align = "center"
                else:
                    align = "left" if dx > 0 else "right"
                lx, ly = dx * dist, dy * dist
            if label_colour:
                col = label_colour
            elif label_pos == "Inside" and style == "Full":
                col = "#FFFFFF"
            elif style == "Outline":
                col = p["colour"]
            else:
                col = "#333333"
            lab = {"text": str(p["label"]), "pos": (lx - ox, ly - oy),
                   "align": align, "colour": col}
        out.append({"pts": pts, "colour": p["colour"], "label": lab})

    # ---- title
    t = None
    if title and title_pos != "None":
        top = (maxy - miny) / 2.0
        bot = -top
        for s in out:
            if s["label"]:
                y = s["label"]["pos"][1]
                top = max(top, y + 0.6 * label_pt * pt_unit)
                bot = min(bot, y - 0.6 * label_pt * pt_unit)
        g = 0.5 * title_pt * pt_unit
        if title_pos == "Above":
            t = {"text": title, "pos": (0.0, top + g), "valign": "bottom"}
        else:
            t = {"text": title, "pos": (0.0, bot - g), "valign": "top"}
        t["colour"] = title_colour
    return {"segments": out, "title": t, "style": style}


# --------------------------------------------------------------------------
# file renderer (matplotlib - ships with ArcGIS Pro)
# --------------------------------------------------------------------------
def render_file(spec, path, fmt, dpi=300, transparent=True, font="Arial",
                label_pt=8.0, title_pt=12.0, label_bold=True, title_bold=True,
                line_w=1.5, separator="#FFFFFF"):
    """Units of spec must be millimetres."""
    import matplotlib
    from matplotlib.figure import Figure
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.patches import Polygon as MplPolygon

    pt = 25.4 / 72.0
    xs, ys = [], []
    for s in spec["segments"]:
        xs += [q[0] for q in s["pts"]]
        ys += [q[1] for q in s["pts"]]
    texts = [(s["label"], label_pt) for s in spec["segments"] if s["label"]]
    if spec["title"]:
        texts.append((spec["title"], title_pt))
    for tx, size in texts:  # generous text extent estimate; final crop is tight
        hw = len(tx["text"]) * 0.65 * size * pt
        xs += [tx["pos"][0] - hw, tx["pos"][0] + hw]
        ys += [tx["pos"][1] - 1.5 * size * pt, tx["pos"][1] + 1.5 * size * pt]
    m = 5.0
    minx, maxx, miny, maxy = min(xs) - m, max(xs) + m, min(ys) - m, max(ys) + m
    W, H = maxx - minx, maxy - miny

    rc = {"svg.fonttype": "none", "pdf.fonttype": 42, "font.family": "sans-serif",
          "font.sans-serif": [font, "Arial", "DejaVu Sans"]}
    with matplotlib.rc_context(rc):
        fig = Figure(figsize=(W / 25.4, H / 25.4))
        FigureCanvasAgg(fig)
        ax = fig.add_axes([0, 0, 1, 1])
        ax.set_xlim(minx, maxx)
        ax.set_ylim(miny, maxy)
        ax.set_axis_off()
        full = spec["style"] == "Full"
        for s in spec["segments"]:
            if full:
                patch = MplPolygon(s["pts"], closed=True, facecolor=s["colour"],
                                   edgecolor=separator if line_w > 0 else "none",
                                   linewidth=line_w, joinstyle="round")
            else:
                patch = MplPolygon(s["pts"], closed=True, facecolor="none",
                                   edgecolor=s["colour"], linewidth=line_w,
                                   joinstyle="round", capstyle="round")
            ax.add_patch(patch)
        for s in spec["segments"]:
            lb = s["label"]
            if lb:
                ax.text(lb["pos"][0], lb["pos"][1], lb["text"], ha=lb["align"],
                        va="center", fontsize=label_pt, color=lb["colour"],
                        fontweight="bold" if label_bold else "normal")
        if spec["title"]:
            t = spec["title"]
            ax.text(t["pos"][0], t["pos"][1], t["text"], ha="center", va=t["valign"],
                    fontsize=title_pt, color=t["colour"],
                    fontweight="bold" if title_bold else "normal")
        opaque = fmt == "jpg" or not transparent
        fig.savefig(path, format="jpeg" if fmt == "jpg" else fmt, dpi=dpi,
                    transparent=not opaque, facecolor="white" if opaque else "none",
                    bbox_inches="tight", pad_inches=0.04)


# --------------------------------------------------------------------------
# layout renderer (editable graphic + text elements)
# --------------------------------------------------------------------------
def _cim(name):
    return arcpy.cim.CreateCIMObjectFromClassName(name, "V3")


def _cim_colour(h):
    c = _cim("CIMRGBColor")
    r, g, b = hex_to_rgb(h)
    c.values = [r, g, b, 100]
    return c


def _poly_symbol(fill_hex, stroke_hex, stroke_w):
    layers = []
    if stroke_hex and stroke_w > 0:
        s = _cim("CIMSolidStroke")
        s.enable = True
        s.color = _cim_colour(stroke_hex)
        s.width = stroke_w
        s.capStyle = "Round"
        s.joinStyle = "Round"
        s.miterLimit = 10
        layers.append(s)
    if fill_hex:
        f = _cim("CIMSolidFill")
        f.enable = True
        f.color = _cim_colour(fill_hex)
        layers.append(f)
    sym = _cim("CIMPolygonSymbol")
    sym.symbolLayers = layers
    return sym


def _anchor_frac(anchor):
    a = str(anchor or "BottomLeftCorner")
    fx = 0.0 if "Left" in a else 1.0 if "Right" in a else 0.5
    fy = 0.0 if "Bottom" in a else 1.0 if "Top" in a else 0.5
    return fx, fy


def _elm_bbox(elm):
    try:
        anchor = elm.getDefinition("V3").anchor
    except Exception:
        anchor = None
    fx, fy = _anchor_frac(anchor)
    w, h = elm.elementWidth, elm.elementHeight
    x0 = elm.elementPositionX - fx * w
    y0 = elm.elementPositionY - fy * h
    return x0, y0, x0 + w, y0 + h


def _place(elm, x, y, ax, ay):
    """Move element so that its bbox point (ax, ay in 0..1) lands on (x, y)."""
    try:
        anchor = elm.getDefinition("V3").anchor
    except Exception:
        anchor = None
    fx, fy = _anchor_frac(anchor)
    w, h = elm.elementWidth, elm.elementHeight
    elm.elementPositionX = x + (fx - ax) * w
    elm.elementPositionY = y + (fy - ay) * h


def _own_elements(layout, name):
    out = []
    for e in layout.listElements():
        if e.name == name or e.name.startswith(name + "_"):
            out.append(e)
    return out


def render_layout(spec, aprx, layout, name, cx, cy, overwrite, font="Arial",
                  label_pt=8.0, title_pt=12.0, label_bold=True, title_bold=True,
                  line_w=1.5, separator="#FFFFFF", messages=None):
    existing = _own_elements(layout, name)
    if existing:
        if not overwrite:
            raise ValueError("Layout already contains an element named '%s'. "
                             "Tick 'Overwrite existing' or use another name." % name)
        if cx is None or cy is None:   # keep the old position
            boxes = [_elm_bbox(e) for e in existing if e.name.startswith(name + "_part")]
            if boxes:
                bx = ((min(b[0] for b in boxes) + max(b[2] for b in boxes)) / 2.0,
                      (min(b[1] for b in boxes) + max(b[3] for b in boxes)) / 2.0)
                cx = bx[0] if cx is None else cx
                cy = bx[1] if cy is None else cy
        for e in sorted(existing, key=lambda e: 0 if getattr(e, "isGroup", False) else 1):
            try:
                layout.deleteElement(e)
            except Exception:
                pass
        for e in _own_elements(layout, name):   # leftovers outside the group
            try:
                layout.deleteElement(e)
            except Exception:
                pass
    if cx is None:
        cx = layout.pageWidth / 2.0
    if cy is None:
        cy = layout.pageHeight / 2.0

    elems = []
    full = spec["style"] == "Full"
    for i, s in enumerate(spec["segments"]):
        arr = arcpy.Array([arcpy.Point(cx + x, cy + y) for x, y in s["pts"]])
        arr.append(arr[0])
        geom = arcpy.Polygon(arr)
        elm = aprx.createGraphicElement(layout, geom, name="%s_part%d" % (name, i + 1))
        cim = elm.getDefinition("V3")
        if full:
            cim.graphic.symbol.symbol = _poly_symbol(s["colour"], separator, line_w)
        else:
            cim.graphic.symbol.symbol = _poly_symbol(None, s["colour"], line_w)
        elm.setDefinition(cim)
        elems.append(elm)

    def add_text(text, x, y, size, bold, colour, halign, ax, ay, ename):
        te = aprx.createTextElement(layout, arcpy.Point(cx + x, cy + y), "POINT", text,
                                    size, font, "Bold" if bold else "Regular",
                                    name=ename)
        cim = te.getDefinition("V3")
        ts = cim.graphic.symbol.symbol
        ts.horizontalAlignment = halign
        ts.verticalAlignment = "Center"
        try:
            for lyr in ts.symbol.symbolLayers:
                if type(lyr).__name__ == "CIMSolidFill":
                    lyr.color = _cim_colour(colour)
        except Exception:
            pass
        te.setDefinition(cim)
        _place(te, cx + x, cy + y, ax, ay)
        elems.append(te)

    for i, s in enumerate(spec["segments"]):
        lb = s["label"]
        if lb:
            ax = {"left": 0.0, "right": 1.0}.get(lb["align"], 0.5)
            add_text(lb["text"], lb["pos"][0], lb["pos"][1], label_pt, label_bold,
                     lb["colour"], lb["align"].capitalize(), ax, 0.5,
                     "%s_label%d" % (name, i + 1))
    if spec["title"]:
        t = spec["title"]
        add_text(t["text"], t["pos"][0], t["pos"][1], title_pt, title_bold, t["colour"],
                 "Center", 0.5, 0.0 if t["valign"] == "bottom" else 1.0, name + "_title")
    try:
        aprx.createGroupElement(layout, elems, name)
    except Exception as ex:
        if messages:
            messages.addWarningMessage("Elements created but could not be grouped: %s" % ex)
    return elems


# --------------------------------------------------------------------------
# toolbox
# --------------------------------------------------------------------------
FILE_EXTS = {"svg": "svg", "png": "png", "jpg": "jpg", "jpeg": "jpg", "pdf": "pdf"}


def _ext_of(path):
    return os.path.splitext(str(path or ""))[1].lower().lstrip(".")


def _file_format(target, path):
    """Format for file output: explicit target, else from the extension (default svg)."""
    if target and target != "Layout":
        return {"SVG": "svg", "PNG": "png", "JPG": "jpg", "PDF": "pdf"}[target]
    return FILE_EXTS.get(_ext_of(path), "svg")


def _fix_ext(path, fmt):
    if FILE_EXTS.get(_ext_of(path)) != fmt:
        path += "." + fmt
    return path


def _rows(param):
    rows = []
    for r in (param.values or []):
        r = list(r) + ["", "", 1.0]
        try:
            w = float(str(r[2]).replace(",", ".")) if str(r[2]).strip() else 1.0
        except ValueError:
            w = 1.0
        rows.append([str(r[0] or ""), str(r[1] or "").strip(), w])
    return rows


class Toolbox(object):
    def __init__(self):
        self.label = "Segmented Legend"
        self.alias = "seglegend"
        self.tools = [SegmentedLegend]


class SegmentedLegend(object):
    def __init__(self):
        self.label = "Create Segmented Legend"
        self.description = ("Creates a pie / donut / polygon legend graphic with a "
                            "title and part labels, into a layout or to SVG/PNG/JPG/PDF.")
        self.canRunInBackground = False   # needs the CURRENT project

    # ---------------------------------------------------------------
    def getParameterInfo(self):
        def P(name, label, dtype, req="Required", direction="Input", cat=None,
              default=None, values=None):
            p = arcpy.Parameter(displayName=label, name=name, datatype=dtype,
                                parameterType=req, direction=direction, category=cat)
            if values:
                p.filter.type = "ValueList"
                p.filter.list = values
            if default is not None:
                p.value = default
            return p

        target = P("target", "Output to", "GPString", default="Layout", values=TARGETS)
        also_file = P("also_file", "Also save as image file (SVG / PNG / JPG / PDF)",
                      "GPBoolean", "Optional", default=False)
        out_file = P("out_file", "Output file (format = file extension)", "DEFile",
                     "Optional", "Output")
        out_file.filter.list = ["svg", "png", "jpg", "jpeg", "pdf"]
        layout = P("layout", "Layout", "GPString", "Optional", cat="Layout")
        ename = P("element_name", "Element (group) name", "GPString", "Optional",
                  cat="Layout", default="SegmentedLegend")
        overwrite = P("overwrite", "Overwrite existing element with this name",
                      "GPBoolean", "Optional", cat="Layout", default=True)
        pos_x = P("pos_x", "Centre X (page units, empty = keep old / page centre)",
                  "GPDouble", "Optional", cat="Layout")
        pos_y = P("pos_y", "Centre Y (page units, empty = keep old / page centre)",
                  "GPDouble", "Optional", cat="Layout")

        title = P("title", "Main label", "GPString", "Optional", default="VĚKOVÉ KATEGORIE")
        count = P("count", "Number of divisions", "GPLong", default=3)
        count.filter.type = "Range"
        count.filter.list = [1, 24]
        div = arcpy.Parameter(displayName="Divisions (label, hex colour, weight)",
                              name="divisions", datatype="GPValueTable",
                              parameterType="Required", direction="Input")
        div.columns = [["GPString", "Label"], ["GPString", "Colour (hex)"],
                       ["GPDouble", "Weight"]]
        div.values = DEFAULT_ROWS
        pick = P("pick", "Pick colour from colour picker for division…", "GPString",
                 "Optional")
        pick.filter.type = "ValueList"
        pick.filter.list = ["%d: %s" % (i + 1, r[0]) for i, r in enumerate(DEFAULT_ROWS)]

        shape = P("shape", "Shape", "GPString", cat="Shape", default="Donut", values=SHAPES)
        style = P("style", "Style", "GPString", cat="Shape", default="Full", values=STYLES)
        size = P("size", "Size – diameter / width (page units; mm for files)", "GPDouble",
                 cat="Shape", default=40.0)
        ring = P("ring_ratio", "Hole size (0–1 of outer size)", "GPDouble", "Optional",
                 cat="Shape", default=0.55)
        ring.filter.type = "Range"
        ring.filter.list = [0.05, 0.95]
        rot = P("rotation", "Rotation (degrees, clockwise)", "GPDouble", "Optional",
                cat="Shape", default=0.0)
        direction = P("direction", "Order of divisions", "GPString", "Optional",
                      cat="Shape", default="Clockwise", values=DIRECTIONS)
        line_w = P("line_width", "Line width (pt) – separator (Full) / outline (Outline)",
                   "GPDouble", "Optional", cat="Shape", default=1.5)
        sep = P("separator", "Separator colour (Full style, hex)", "GPString", "Optional",
                cat="Shape", default="#FFFFFF")

        lpos = P("label_pos", "Part label position", "GPString", "Optional", cat="Text",
                 default="Inside", values=LABEL_POS)
        tpos = P("title_pos", "Main label position", "GPString", "Optional", cat="Text",
                 default="Above", values=TITLE_POS)
        font = P("font", "Font family", "GPString", "Optional", cat="Text", default="Arial")
        tsize = P("title_size", "Main label size (pt)", "GPDouble", "Optional",
                  cat="Text", default=12.0)
        lsize = P("label_size", "Part label size (pt)", "GPDouble", "Optional",
                  cat="Text", default=8.0)
        tbold = P("title_bold", "Main label bold", "GPBoolean", "Optional", cat="Text",
                  default=True)
        lbold = P("label_bold", "Part labels bold", "GPBoolean", "Optional", cat="Text",
                  default=True)
        tcol = P("title_colour", "Main label colour (hex)", "GPString", "Optional",
                 cat="Text", default="#000000")
        lcol = P("label_colour", "Part label colour (hex, empty = automatic)", "GPString",
                 "Optional", cat="Text")

        dpi = P("dpi", "Resolution (DPI)", "GPLong", "Optional", cat="File export",
                default=300)
        transp = P("transparent", "Transparent background", "GPBoolean", "Optional",
                   cat="File export", default=True)

        return [target, also_file, out_file, layout, ename, overwrite, pos_x, pos_y,
                title, count, div, pick,
                shape, style, size, ring, rot, direction, line_w, sep,
                lpos, tpos, font, tsize, lsize, tbold, lbold, tcol, lcol,
                dpi, transp]

    def isLicensed(self):
        return True

    # ---------------------------------------------------------------
    def updateParameters(self, parameters):
        P = {p.name: p for p in parameters}
        tgt = P["target"].valueAsText or "Layout"
        is_layout = tgt == "Layout"
        also = is_layout and bool(P["also_file"].value)
        P["also_file"].enabled = is_layout
        P["out_file"].enabled = (not is_layout) or also
        for n in ("layout", "element_name", "overwrite", "pos_x", "pos_y"):
            P[n].enabled = is_layout
        fmt = _file_format(tgt, P["out_file"].valueAsText) if (not is_layout or also) else None
        P["dpi"].enabled = fmt in ("png", "jpg")
        P["transparent"].enabled = fmt in ("png", "svg", "pdf")
        P["ring_ratio"].enabled = P["shape"].valueAsText in ("Donut", "Polygon ring")
        P["separator"].enabled = P["style"].valueAsText != "Outline"

        if is_layout:
            try:
                names = [l.name for l in arcpy.mp.ArcGISProject("CURRENT").listLayouts()]
                if names:
                    P["layout"].filter.list = names
                    if not P["layout"].value:
                        P["layout"].value = names[0]
            except Exception:
                pass

        # number of divisions <-> table
        rows = _rows(P["divisions"])
        cnt, div = P["count"], P["divisions"]
        if cnt.altered and not cnt.hasBeenValidated and cnt.value:
            n = max(1, int(cnt.value))
            if n != len(rows):
                rows = rows[:n] + [["Part %d" % (i + 1), PALETTE[i % len(PALETTE)], 1.0]
                                   for i in range(len(rows), n)]
                div.values = rows
        elif div.altered and not div.hasBeenValidated:
            if len(rows) != cnt.value:
                cnt.value = len(rows)

        # colour picker
        pick = P["pick"]
        choice = pick.valueAsText
        if pick.altered and not pick.hasBeenValidated and choice:
            try:
                idx = int(choice.split(":")[0]) - 1
                if 0 <= idx < len(rows):
                    cur = rows[idx][1] if HEX_RE.match(rows[idx][1] or "") else None
                    new = pick_colour(cur)
                    if new:
                        rows[idx][1] = new
                        div.values = rows
            except Exception:
                pass
            pick.value = None
        labels = ["%d: %s" % (i + 1, r[0]) for i, r in enumerate(rows)]
        if labels:
            if pick.value and pick.valueAsText not in labels:
                pick.value = None
            pick.filter.list = labels
        return

    # ---------------------------------------------------------------
    def updateMessages(self, parameters):
        P = {p.name: p for p in parameters}
        rows = _rows(P["divisions"])
        bad = [r[1] for r in rows if not HEX_RE.match(r[1] or "")]
        if bad:
            P["divisions"].setErrorMessage("Invalid / missing hex colour(s): %s. "
                                           "Use e.g. #3E66B0, or pick one with the "
                                           "colour picker." % ", ".join(repr(b) for b in bad))
        elif any(r[2] < 0 for r in rows):
            P["divisions"].setErrorMessage("Weights must be ≥ 0.")
        if P["shape"].valueAsText in ("Polygon", "Polygon ring") and len(rows) < 2:
            P["shape"].setErrorMessage("Polygon shapes need at least 2 divisions.")
        for n in ("separator", "title_colour", "label_colour"):
            v = P[n].valueAsText
            if v and not HEX_RE.match(v.strip()):
                P[n].setErrorMessage("Invalid hex colour (e.g. #FFFFFF).")
        tgt = P["target"].valueAsText
        if tgt != "Layout" and not P["out_file"].valueAsText:
            P["out_file"].setErrorMessage("Choose an output file for %s export." % tgt)
        if tgt == "Layout" and P["also_file"].value:
            f = P["out_file"].valueAsText
            if not f:
                P["out_file"].setErrorMessage("Choose an output file (.svg, .png, "
                                              ".jpg or .pdf) for the image copy.")
            elif _ext_of(f) not in FILE_EXTS:
                P["out_file"].setWarningMessage("No / unknown extension – the image "
                                                "will be saved as SVG.")
        if tgt == "Layout" and not P["layout"].valueAsText:
            P["layout"].setErrorMessage("Choose a layout (open a project with a layout).")
        if P["shape"].valueAsText in ("Polygon", "Polygon ring") and \
                len(rows) > 2 and len(set(r[2] for r in rows)) > 1:
            P["divisions"].setWarningMessage("Weights are ignored for polygons with 3+ "
                                             "parts (each side is one part).")
        return

    # ---------------------------------------------------------------
    def execute(self, parameters, messages):
        P = {p.name: p for p in parameters}

        def val(n, default=None):
            v = P[n].value
            return default if v is None or v == "" else v

        rows = _rows(P["divisions"])
        parts = [{"label": r[0], "colour": norm_hex(r[1]), "weight": r[2]} for r in rows]
        tgt = P["target"].valueAsText
        shape = P["shape"].valueAsText
        style = P["style"].valueAsText
        common = dict(font=val("font", "Arial"),
                      label_pt=float(val("label_size", 8.0)),
                      title_pt=float(val("title_size", 12.0)),
                      label_bold=bool(val("label_bold", True)),
                      title_bold=bool(val("title_bold", True)),
                      line_w=float(val("line_width", 1.5)),
                      separator=norm_hex(val("separator"), "#FFFFFF"))

        def spec_for(pt_unit, size_scale=1.0):
            return build_spec(parts, shape, style, float(val("size", 40.0)) * size_scale,
                              ring_ratio=float(val("ring_ratio", 0.55)),
                              rotation=float(val("rotation", 0.0)),
                              clockwise=val("direction", "Clockwise") == "Clockwise",
                              label_pos=val("label_pos", "Inside"),
                              title=val("title", ""),
                              title_pos=val("title_pos", "Above"),
                              title_pt=common["title_pt"], label_pt=common["label_pt"],
                              pt_unit=pt_unit,
                              label_colour=norm_hex(val("label_colour")),
                              title_colour=norm_hex(val("title_colour"), "#000000"))

        if tgt == "Layout":
            aprx = arcpy.mp.ArcGISProject("CURRENT")
            lyts = aprx.listLayouts(P["layout"].valueAsText)
            if not lyts:
                raise ValueError("Layout '%s' not found." % P["layout"].valueAsText)
            layout = lyts[0]
            pt_unit = 1.0 / PT_PER_UNIT.get(str(layout.pageUnits).upper(), 72.0 / 25.4)
            spec = spec_for(pt_unit)
            name = (val("element_name", "SegmentedLegend") or "SegmentedLegend").strip()
            render_layout(spec, aprx, layout, name, val("pos_x"), val("pos_y"),
                          bool(val("overwrite", True)), messages=messages, **common)
            messages.addMessage("Legend '%s' created in layout '%s'." % (name, layout.name))

            if val("also_file", False):
                path = P["out_file"].valueAsText
                if not path:
                    raise ValueError("Choose an output file for the image copy.")
                fmt = _file_format("Layout", path)
                path = _fix_ext(path, fmt)
                # same physical size as in the layout: page units -> millimetres
                mm_per_unit = PT_PER_UNIT.get(str(layout.pageUnits).upper(),
                                              72.0 / 25.4) * 25.4 / 72.0
                spec_mm = spec_for(25.4 / 72.0, size_scale=mm_per_unit)
                render_file(spec_mm, path, fmt, dpi=int(val("dpi", 300)),
                            transparent=bool(val("transparent", True)), **common)
                messages.addMessage("Image copy written to %s" % path)
        else:
            fmt = _file_format(tgt, None)
            path = _fix_ext(P["out_file"].valueAsText, fmt)
            spec = spec_for(25.4 / 72.0)   # millimetres
            render_file(spec, path, fmt, dpi=int(val("dpi", 300)),
                        transparent=bool(val("transparent", True)), **common)
            messages.addMessage("Legend written to %s" % path)
        return
