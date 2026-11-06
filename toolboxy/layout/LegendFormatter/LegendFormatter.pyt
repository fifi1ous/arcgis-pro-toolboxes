# -*- coding: utf-8 -*-
"""
LegendFormatter.pyt  -  ArcGIS Pro Python toolbox

Copies the layout of a legend that was converted to graphics (a group element
made of polygon "Patch" elements and text labels) onto another such legend,
possibly in a different layout.

What is always copied from the template:
  * patch width / height and vertical spacing (pitch) between patches
  * position of the labels relative to the patches (e.g. labels placed on the
    boundaries between patches instead of next to patch centres)
  * position of the title relative to the patch stack
  * text alignment (horizontal / vertical), so positions match the template

What is optional:
  * text font style (font, size, colour, halo, ...) of the title and labels
  * patch outlines (stroke layers) - the fill of the patches is NEVER touched
  * re-formatting of numbers in the labels (round / truncate to N decimals)

The target legend keeps its own top-left corner of the patch stack, its own
fill colours and its own label texts.
"""

import arcpy
import copy
import math
import re
import statistics

UNIT_MM = {
    "INCH": 25.4,
    "CENTIMETER": 10.0,
    "MILLIMETER": 1.0,
    "POINT": 25.4 / 72.0,
}

NUM_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")
TAG_RE = re.compile(r"(<[^>]*>)")


# --------------------------------------------------------------------------
# small CIM helpers (CIM geometries are dicts, other CIM objects are classes)
# --------------------------------------------------------------------------
def _tn(o):
    return type(o).__name__


def _get(o, key, default=None):
    if o is None:
        return default
    if isinstance(o, dict):
        return o.get(key, default)
    return getattr(o, key, default)


def _set(o, key, value):
    if isinstance(o, dict):
        o[key] = value
    else:
        setattr(o, key, value)


def _walk(elements):
    for e in elements or []:
        yield e
        if _tn(e) == "CIMGroupElement":
            yield from _walk(_get(e, "elements"))


def _find_group(cim_layout, name):
    for e in _walk(cim_layout.elements):
        if _tn(e) == "CIMGroupElement" and e.name == name:
            return e
    return None


def _group_names(layout):
    cim = layout.getDefinition("V3")
    return sorted({e.name for e in _walk(cim.elements)
                   if _tn(e) == "CIMGroupElement"})


def _points(obj, out):
    """Collect all [x, y, ...] coordinate pairs from (curve)rings."""
    if isinstance(obj, (list, tuple)):
        if len(obj) >= 2 and all(isinstance(v, (int, float)) for v in obj[:2]) \
                and not any(isinstance(v, (list, tuple, dict)) for v in obj):
            out.append(obj)
        else:
            for v in obj:
                if isinstance(v, (list, tuple, dict)):
                    _points(v, out)
    elif isinstance(obj, dict):
        for v in obj.values():
            if isinstance(v, (list, tuple, dict)):
                _points(v, out)
    return out


def _poly_coords(poly):
    pts = []
    for key in ("rings", "curveRings"):
        r = _get(poly, key)
        if r:
            _points(r, pts)
    return pts


def _env(poly):
    pts = _poly_coords(poly)
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _rect(template_poly, xmin, ymin, xmax, ymax):
    """Rectangle polygon (clockwise ring) keeping e.g. spatialReference."""
    ring = [[xmin, ymax], [xmax, ymax], [xmax, ymin], [xmin, ymin], [xmin, ymax]]
    if isinstance(template_poly, dict):
        new = copy.deepcopy(template_poly)
        for k in ("curveRings", "hasZ", "hasM"):
            new.pop(k, None)
        new["rings"] = [ring]
        return new
    if template_poly is None:
        return {"rings": [ring]}
    new = copy.deepcopy(template_poly)
    setattr(new, "rings", [ring])
    if hasattr(new, "curveRings"):
        setattr(new, "curveRings", None)
    return new


def _shift_poly(poly, dx, dy):
    for key in ("rings", "curveRings"):
        r = _get(poly, key)
        if r:
            for p in _points(r, []):
                p[0] += dx
                p[1] += dy


def _text_xy(text_elm):
    """Reference point of a CIM text: the point itself, or for text placed in
    a polygon/box the left-middle of its extent."""
    shape = _get(_th(text_elm), "shape")
    x, y = _get(shape, "x"), _get(shape, "y")
    if x is not None and y is not None:
        return (x, y)
    env = _env(shape)
    if env is None:
        xmin, ymin = _get(shape, "xmin"), _get(shape, "ymin")
        if xmin is not None:
            return (xmin, (ymin + _get(shape, "ymax")) / 2.0)
        return None
    return (env[0], (env[1] + env[3]) / 2.0)


def _move_text(text_elm, nx, ny):
    shape = _get(_th(text_elm), "shape")
    ox, oy = _text_xy(text_elm)
    dx, dy = nx - ox, ny - oy
    if _get(shape, "x") is not None:
        _set(shape, "x", nx)
        _set(shape, "y", ny)
    elif _get(shape, "xmin") is not None:
        for k, d in (("xmin", dx), ("xmax", dx), ("ymin", dy), ("ymax", dy)):
            _set(shape, k, _get(shape, k) + d)
    else:
        _shift_poly(shape, dx, dy)
    frame = _get(text_elm, "frame")
    if frame is not None:
        _shift_poly(frame, dx, dy)


def _format_numbers(text, mode, decimals):
    if mode == "Keep" or not text:
        return text
    factor = 10 ** decimals

    def rep(m):
        s = m.group(0)
        comma = "," in s
        v = float(s.replace(",", "."))
        if mode == "Truncate":
            v = math.trunc(v * factor + math.copysign(1e-9, v)) / factor
        else:
            v = round(v, decimals)
        out = "{0:.{1}f}".format(v, decimals)
        return out.replace(".", ",") if comma else out

    parts = TAG_RE.split(text)        # don't touch formatting tags <FNT ...>
    return "".join(p if TAG_RE.fullmatch(p) else NUM_RE.sub(rep, p)
                   for p in parts)


def _copy_outline(dst_sym, src_sym):
    """Replace stroke layers of dst with the template's, keep fill layers."""
    src_layers = list(_get(src_sym, "symbolLayers") or [])
    dst_layers = list(_get(dst_sym, "symbolLayers") or [])
    strokes = [copy.deepcopy(l) for l in src_layers if "Stroke" in _tn(l)]
    others = [l for l in dst_layers if "Stroke" not in _tn(l)]
    strokes_on_top = bool(src_layers) and "Stroke" in _tn(src_layers[0])
    dst_sym.symbolLayers = strokes + others if strokes_on_top else others + strokes


# Text style property groups of CIMTextSymbol. Missing attributes are skipped,
# so the tool works across ArcGIS Pro versions.
TEXT_STYLE_GROUPS = {
    "Font (family, style, case, underline)": [
        "fontFamilyName", "fontStyleName", "fontType", "fontEffects",
        "fontEncoding", "textCase", "underline", "strikethrough", "smallCaps"],
    "Size": ["height"],
    "Colour and fill effects": ["symbol"],
    "Halo": ["haloSize", "haloSymbol", "haloBlurSize"],
    "Shadow": ["shadowColor", "shadowOffsetX", "shadowOffsetY", "shadowBlurSize"],
    "Background / callout": ["callout"],
    "Spacing and other effects": [
        "letterSpacing", "letterWidth", "wordSpacing", "kerning", "ligatures",
        "lineGap", "lineGapType", "flipAngle", "glyphRotation",
        "verticalGlyphOrientation", "drawGlyphsAsGeometry", "depth3D",
        "extrapolateBaselines", "compatibilityMode", "textDirection",
        "blockProgression", "indentAfter", "indentFirstLine", "indentLeft"],
    "Inline formatting tags (<CLR>, <FNT>, <BOL> ...)": [],
}
TAGS_GROUP = "Inline formatting tags (<CLR>, <FNT>, <BOL> ...)"
LEAD_TAGS_RE = re.compile(r"^(?:\s*<(?!/)[^>]*>)*")
TRAIL_TAGS_RE = re.compile(r"(?:</[^>]*>\s*)*$")


def _copy_text_style(dst_elm, src_elm, groups):
    """Copy chosen style groups from the template text element to the target."""
    if src_elm is None or not groups:
        return
    src = _tsym(src_elm)
    dst = _tsym(dst_elm)
    for g in groups:
        for attr in TEXT_STYLE_GROUPS.get(g, []):
            if hasattr(src, attr):
                try:
                    setattr(dst, attr, copy.deepcopy(getattr(src, attr)))
                except Exception:
                    pass
    if TAGS_GROUP in groups:
        st = _ttext(src_elm)
        lead = LEAD_TAGS_RE.match(st).group(0)
        trail = TRAIL_TAGS_RE.search(st).group(0)
        plain = TAG_RE.sub("", _ttext(dst_elm))
        _set_ttext(dst_elm, lead.strip() + plain + trail.strip())


def _apply_text_symbol(text_elm, src_sym, copy_style):
    if src_sym is None:
        return
    if copy_style:
        _set_tsym(text_elm, copy.deepcopy(src_sym))
    else:
        dst = _tsym(text_elm)
        for attr in ("horizontalAlignment", "verticalAlignment"):
            if hasattr(src_sym, attr):
                setattr(dst, attr, getattr(src_sym, attr))


def _th(e):
    """Object holding text/shape/symbol: the graphic if present, else the element."""
    g = _get(e, "graphic")
    return g if g is not None else e


def _tsym(e):
    ref = _get(_th(e), "symbol")
    inner = _get(ref, "symbol")
    return inner if inner is not None else ref


def _set_tsym(e, sym):
    ref = _get(_th(e), "symbol")
    if ref is not None and _get(ref, "symbol") is not None:
        _set(ref, "symbol", sym)
    else:
        _set(_th(e), "symbol", sym)


def _ttext(e):
    return _get(_th(e), "text") or ""


def _set_ttext(e, text):
    _set(_th(e), "text", text)


def _is_text(e):
    """Any CIM element carrying text (CIMTextGraphic, paragraph text, ...)."""
    if _tn(e) == "CIMGroupElement":
        return False
    g = _get(e, "graphic")
    if g is not None and (isinstance(_get(g, "text"), str) or "Text" in _tn(g)):
        return True
    return "Text" in _tn(e) and isinstance(_get(e, "text", _get(g, "text")), str)


def _is_patch(e):
    if _tn(e) == "CIMGroupElement" or _is_text(e):
        return False
    g = _get(e, "graphic")
    return g is not None and _get(g, "polygon") is not None


def _type_report(group):
    counts = {}
    for e in _walk(group.elements):
        if _tn(e) == "CIMGroupElement":
            continue
        k = "{}/{}".format(_tn(e), _tn(_get(e, "graphic")))
        counts[k] = counts.get(k, 0) + 1
    return ", ".join("{} x{}".format(k, v) for k, v in sorted(counts.items()))


# --------------------------------------------------------------------------
# legend analysis
# --------------------------------------------------------------------------
def _analyse(group):
    patches, texts = [], []
    for e in _walk(group.elements):
        if _is_text(e):
            texts.append(e)
        elif _is_patch(e):
            patches.append(e)

    pe = []
    for p in patches:
        env = _env(p.graphic.polygon)
        if env:
            pe.append((env, p))
    if not pe:
        raise ValueError("Group '{}' contains no patch (polygon) elements."
                         .format(group.name))
    pe.sort(key=lambda t: (-t[0][3], t[0][0]))      # top to bottom

    top0, left0 = pe[0][0][3], pe[0][0][0]
    W = statistics.median(e[2] - e[0] for e, _ in pe)
    H = statistics.median(e[3] - e[1] for e, _ in pe)
    pitch = (pe[0][0][3] - pe[-1][0][3]) / (len(pe) - 1) if len(pe) > 1 else H
    if pitch <= 0:
        pitch = H

    tp, skipped = [], 0
    for t in texts:
        xy = _text_xy(t)
        if xy is None:
            skipped += 1
            continue
        tp.append((xy, t))
    tp.sort(key=lambda t: -t[0][1])                 # top to bottom

    title = None
    if tp and tp[0][0][1] > top0 + 0.05 * H:        # text clearly above patches
        title = tp.pop(0)

    return {"patches": pe, "top0": top0, "left0": left0, "W": W, "H": H,
            "pitch": pitch, "title": title, "labels": tp, "skipped": skipped}



# --------------------------------------------------------------------------
# arcpy.mp text helpers (positions with real text bounds)
# --------------------------------------------------------------------------
ANCHOR_F = {
    "BOTTOM_LEFT_CORNER": (0.0, 0.0), "BOTTOM_MID_POINT": (0.5, 0.0),
    "BOTTOM_RIGHT_CORNER": (1.0, 0.0), "LEFT_MID_POINT": (0.0, 0.5),
    "CENTER_POINT": (0.5, 0.5), "RIGHT_MID_POINT": (1.0, 0.5),
    "TOP_LEFT_CORNER": (0.0, 1.0), "TOP_MID_POINT": (0.5, 1.0),
    "TOP_RIGHT_CORNER": (1.0, 1.0),
}


def _mp_center(e):
    fx, fy = ANCHOR_F.get(e.anchor, (0.0, 0.0))
    return (e.elementPositionX + (0.5 - fx) * e.elementWidth,
            e.elementPositionY + (0.5 - fy) * e.elementHeight)


def _anchor_point(e, anchor):
    """Page position the element's given anchor point would have."""
    cx, cy = _mp_center(e)
    fx, fy = ANCHOR_F.get(anchor, (0.0, 0.0))
    return (cx + (fx - 0.5) * e.elementWidth, cy + (fy - 0.5) * e.elementHeight)


def _mp_walk(elms):
    for e in elms or []:
        yield e
        if getattr(e, "type", "") == "GROUP_ELEMENT" and hasattr(e, "listElements"):
            try:
                yield from _mp_walk(e.listElements())
            except Exception:
                pass


def _mp_texts(layout, group_name, names):
    """Elements of the layout whose names are the group's text names.

    layout.listElements() also returns elements nested in groups; converted
    legend texts may be reported as TEXT_ELEMENT or GRAPHIC_ELEMENT, so any
    non-group element with a matching name is accepted.
    """
    found, seen = [], set()
    try:
        elms = layout.listElements()
    except Exception as ex:
        arcpy.AddWarning("listElements failed in '{}': {}".format(layout.name, ex))
        return found
    for e in elms:
        try:
            if getattr(e, "type", "") == "GROUP_ELEMENT" or e.name not in names:
                continue
            if e.name in seen:
                continue
            e.elementPositionX       # must be positionable
            seen.add(e.name)
            found.append(e)
        except Exception:
            continue
    return found


def _set_anchor(e, anchor):
    try:
        if hasattr(e, "setAnchor"):
            e.setAnchor(anchor)
        else:
            e.anchor = anchor
    except Exception:
        pass


def _cim_texts_by_name(group):
    return {e.name: e for e in _walk(group.elements) if _is_text(e)}


def _sym(cim_text):
    return _tsym(cim_text) if cim_text is not None else None


def _mp_text_info(layout, cim_group, info):
    names = set(_cim_texts_by_name(cim_group))
    elms = _mp_texts(layout, cim_group.name, names)
    arcpy.AddMessage("Layout '{}': found {} of {} legend texts via arcpy.mp ({})."
                     .format(layout.name, len(elms), len(names),
                             ", ".join(sorted({getattr(e, "type", "?") for e in elms}))
                             or "none"))
    if not elms or len(elms) < len(names):
        missing = sorted(names - {e.name for e in elms})
        if missing:
            arcpy.AddWarning("Not found in '{}': {}".format(layout.name,
                                                            "; ".join(missing)))
        if not elms:
            return None
    items = sorted(((_mp_center(e), e) for e in elms), key=lambda t: -t[0][1])
    title = None
    if items and items[0][0][1] > info["top0"] + 0.05 * info["H"]:
        c, e = items.pop(0)
        title = (e.name, e)
    return {"title": title, "labels": [(e.name, e) for _, e in items]}

# --------------------------------------------------------------------------
# toolbox
# --------------------------------------------------------------------------
class Toolbox(object):
    def __init__(self):
        self.label = "Legend Formatter"
        self.alias = "legendformatter"
        self.tools = [FormatLegendFromTemplate]


class FormatLegendFromTemplate(object):
    def __init__(self):
        self.label = "Format Legend From Template"
        self.description = ("Copies patch size, spacing, label and title "
                            "placement from a template legend (group element) "
                            "to a target legend. Patch fill colours are kept.")
        self.canRunInBackground = False

    def getParameterInfo(self):
        def p(name, label, dtype="GPString", ptype="Required", cat=None):
            return arcpy.Parameter(name=name, displayName=label, datatype=dtype,
                                   parameterType=ptype, direction="Input",
                                   category=cat)

        tpl_layout = p("template_layout", "Template layout")
        tpl_group = p("template_group", "Template legend (group element)")
        tgt_layout = p("target_layout", "Target layout")
        tgt_group = p("target_group", "Target legend (group element)")
        for prm in (tpl_layout, tpl_group, tgt_layout, tgt_group):
            prm.filter.type = "ValueList"

        copy_text = p("copy_text_style", "Copy text style (title + labels)",
                      "GPBoolean", "Optional", "Optional styling")
        copy_text.value = False
        copy_outline = p("copy_outline", "Copy patch outlines",
                         "GPBoolean", "Optional", "Optional styling")
        copy_outline.value = False

        num_mode = p("number_format", "Label numbers", "GPString", "Optional",
                     "Label text")
        num_mode.filter.type = "ValueList"
        num_mode.filter.list = ["Keep", "Round", "Truncate"]
        num_mode.value = "Keep"
        decimals = p("decimals", "Decimal places", "GPLong", "Optional",
                     "Label text")
        decimals.value = 0
        decimals.enabled = False

        text_props = p("text_style_parts", "Text style parts to copy", "GPString",
                       "Optional", "Optional styling")
        text_props.multiValue = True
        text_props.filter.type = "ValueList"
        text_props.filter.list = list(TEXT_STYLE_GROUPS)
        text_props.values = list(TEXT_STYLE_GROUPS)
        text_props.enabled = False

        return [tpl_layout, tpl_group, tgt_layout, tgt_group,
                copy_text, copy_outline, num_mode, decimals, text_props]

    def isLicensed(self):
        return True

    def updateParameters(self, params):
        try:
            aprx = arcpy.mp.ArcGISProject("CURRENT")
        except Exception:
            return
        layouts = {l.name: l for l in aprx.listLayouts()}
        names = sorted(layouts)
        params[0].filter.list = names
        params[2].filter.list = names

        for lp, gp in ((params[0], params[1]), (params[2], params[3])):
            name = lp.valueAsText
            if name and name in layouts:
                if (lp.altered and not lp.hasBeenValidated) or not gp.filter.list:
                    try:
                        gp.filter.list = _group_names(layouts[name])
                    except Exception:
                        gp.filter.list = []
            elif not name:
                gp.filter.list = []

        params[7].enabled = params[6].valueAsText in ("Round", "Truncate")
        params[8].enabled = bool(params[4].value)

    def updateMessages(self, params):
        if (params[0].valueAsText and params[0].valueAsText == params[2].valueAsText
                and params[1].valueAsText
                and params[1].valueAsText == params[3].valueAsText):
            params[3].setErrorMessage("Target legend must differ from the template.")
        if params[7].value is not None and params[7].value < 0:
            params[7].setErrorMessage("Decimal places must be 0 or more.")

    def execute(self, params, messages):
        tpl_layout_name = params[0].valueAsText
        tpl_group_name = params[1].valueAsText
        tgt_layout_name = params[2].valueAsText
        tgt_group_name = params[3].valueAsText
        copy_text = bool(params[4].value)
        copy_outline = bool(params[5].value)
        num_mode = params[6].valueAsText or "Keep"
        decimals = int(params[7].value or 0)
        style_groups = []
        if copy_text:
            style_groups = list(params[8].values or []) or list(TEXT_STYLE_GROUPS)

        aprx = arcpy.mp.ArcGISProject("CURRENT")
        layouts = {l.name: l for l in aprx.listLayouts()}
        tpl_layout = layouts[tpl_layout_name]
        tgt_layout = layouts[tgt_layout_name]

        tgt_cim = tgt_layout.getDefinition("V3")
        tpl_cim = tgt_cim if tpl_layout_name == tgt_layout_name \
            else tpl_layout.getDefinition("V3")

        tpl_group = _find_group(tpl_cim, tpl_group_name)
        tgt_group = _find_group(tgt_cim, tgt_group_name)
        if tpl_group is None or tgt_group is None:
            raise arcpy.ExecuteError("Group element not found.")

        arcpy.AddMessage("Template group contains: " + _type_report(tpl_group))
        arcpy.AddMessage("Target group contains: " + _type_report(tgt_group))
        T = _analyse(tpl_group)
        G = _analyse(tgt_group)

        # page-unit conversion between the two layouts
        f = UNIT_MM.get(str(tpl_layout.pageUnits).upper(), 1.0) / \
            UNIT_MM.get(str(tgt_layout.pageUnits).upper(), 1.0)

        W, H, pitch = T["W"] * f, T["H"] * f, T["pitch"] * f
        top0, left0 = G["top0"], G["left0"]          # target keeps its position

        # ---- text positions read through arcpy.mp (real element bounds) ---
        tpl_mp = _mp_text_info(tpl_layout, tpl_group, T)
        tgt_mp = _mp_text_info(tgt_layout, tgt_group, G)
        use_mp = tpl_mp is not None and tgt_mp is not None
        if not use_mp:
            arcpy.AddWarning("Text elements of the legend group could not be "
                             "accessed through arcpy.mp - falling back to CIM "
                             "text positioning.")

        tpl_cim_texts = _cim_texts_by_name(tpl_group)
        tgt_cim_texts = _cim_texts_by_name(tgt_group)

        # template measurements (numbers only, taken before any change)
        lab_plan = None
        title_plan = None
        if use_mp:
            if tpl_mp["labels"]:
                A = tpl_mp["labels"][0][1].anchor
                pts = [_anchor_point(e, A) for _, e in tpl_mp["labels"]]
                lab_plan = {
                    "anchor": A,
                    "slots": [(T["top0"] - y) / T["pitch"] for _, y in pts],
                    "dx": statistics.median(x - T["left0"] for x, _ in pts) * f,
                }
            if tpl_mp["title"]:
                e = tpl_mp["title"][1]
                title_plan = {"anchor": e.anchor,
                              "dx": (e.elementPositionX - T["left0"]) * f,
                              "dy": (e.elementPositionY - T["top0"]) * f}
            tpl_title_name = tpl_mp["title"][0] if tpl_mp["title"] else None
            tpl_label_names = [n for n, _ in tpl_mp["labels"]]
            tgt_title_name = tgt_mp["title"][0] if tgt_mp["title"] else None
            tgt_label_names = [n for n, _ in tgt_mp["labels"]]
        else:
            tpl_title_name = T["title"][1].name if T["title"] else None
            tpl_label_names = [t.name for _, t in T["labels"]]
            tgt_title_name = G["title"][1].name if G["title"] else None
            tgt_label_names = [t.name for _, t in G["labels"]]

        tpl_title_elm = tpl_cim_texts.get(tpl_title_name)
        tpl_label_elm = tpl_cim_texts.get(tpl_label_names[0]) \
            if tpl_label_names else None

        # ---- patches (CIM) --------------------------------------------------
        tpl_patch_sym = T["patches"][0][1].graphic.symbol.symbol
        for i, (_, p) in enumerate(G["patches"]):
            ytop = top0 - i * pitch
            box = (left0, ytop - H, left0 + W, ytop)
            p.graphic.polygon = _rect(p.graphic.polygon, *box)
            p.frame = _rect(_get(p, "frame") or p.graphic.polygon, *box)
            if copy_outline:
                _copy_outline(p.graphic.symbol.symbol, tpl_patch_sym)
        arcpy.AddMessage("Patches formatted: {}".format(len(G["patches"])))

        # ---- text style + label numbers (CIM) -----------------------------
        for name, t in tgt_cim_texts.items():
            if name == tgt_title_name:
                _copy_text_style(t, tpl_title_elm, style_groups)
            elif name in tgt_label_names:
                _copy_text_style(t, tpl_label_elm, style_groups)
                if num_mode != "Keep":
                    _set_ttext(t, _format_numbers(_ttext(t),
                                                     num_mode, decimals))

        if style_groups:
            arcpy.AddMessage("Text style copied: " + ", ".join(style_groups))

        # ---- fallback: move texts in CIM ----------------------------------
        if not use_mp:
            self._cim_move_texts(T, G, f, top0, left0, pitch, copy_text)

        tgt_layout.setDefinition(tgt_cim)

        # ---- move texts through arcpy.mp ----------------------------------
        if use_mp:
            lay = {l.name: l for l in aprx.listLayouts()}[tgt_layout_name]
            elms = {e.name: e for e in
                    _mp_texts(lay, tgt_group_name, set(tgt_cim_texts))}

            if lab_plan and tgt_label_names:
                slots, step = lab_plan["slots"], 1.0
                if len(slots) > 1:
                    step = slots[-1] - slots[-2]
                moved = 0
                for k, name in enumerate(tgt_label_names):
                    e = elms.get(name)
                    if e is None:
                        continue
                    s = slots[k] if k < len(slots) else \
                        slots[-1] + (k - len(slots) + 1) * step
                    _set_anchor(e, lab_plan["anchor"])
                    e.elementPositionX = left0 + lab_plan["dx"]
                    e.elementPositionY = top0 - s * pitch
                    moved += 1
                arcpy.AddMessage("Labels moved: {}".format(moved))
                if len(tgt_label_names) != len(slots):
                    arcpy.AddWarning("Template has {} labels, target {}; extra "
                                     "label positions were extrapolated."
                                     .format(len(slots), len(tgt_label_names)))
            elif tgt_label_names:
                arcpy.AddWarning("Template has no labels - target labels not moved.")

            if title_plan and tgt_title_name and tgt_title_name in elms:
                e = elms[tgt_title_name]
                _set_anchor(e, title_plan["anchor"])
                e.elementPositionX = left0 + title_plan["dx"]
                e.elementPositionY = top0 + title_plan["dy"]
                arcpy.AddMessage("Title moved.")
            elif title_plan or tgt_title_name:
                arcpy.AddWarning("Title found only in one of the legends - not moved.")

        arcpy.AddMessage("Legend '{}' in layout '{}' updated."
                         .format(tgt_group_name, tgt_layout_name))

    # fallback when arcpy.mp cannot see the grouped text elements
    def _cim_move_texts(self, T, G, f, top0, left0, pitch, copy_text):
        for info, nm in ((T, "template"), (G, "target")):
            if info["skipped"]:
                arcpy.AddWarning("{} {} text(s) have an unsupported shape and "
                                 "were ignored.".format(info["skipped"], nm))
        arcpy.AddMessage("CIM fallback: template {} labels / title {}, target "
                         "{} labels / title {}.".format(
                             len(T["labels"]), bool(T["title"]),
                             len(G["labels"]), bool(G["title"])))
        if T["labels"] and G["labels"]:
            slots = [(T["top0"] - xy[1]) / T["pitch"] for xy, _ in T["labels"]]
            step = slots[-1] - slots[-2] if len(slots) > 1 else 1.0
            dx = statistics.median(xy[0] - T["left0"] for xy, _ in T["labels"]) * f
            src = _tsym(T["labels"][0][1])
            for k, (_, t) in enumerate(G["labels"]):
                s = slots[k] if k < len(slots) else \
                    slots[-1] + (k - len(slots) + 1) * step
                _move_text(t, left0 + dx, top0 - s * pitch)
                if not copy_text:
                    _apply_text_symbol(t, src, False)
            arcpy.AddMessage("Labels moved (CIM): {}".format(len(G["labels"])))
        if T["title"] and G["title"]:
            (tx, ty), tt = T["title"]
            _move_text(G["title"][1], left0 + (tx - T["left0"]) * f,
                       top0 + (ty - T["top0"]) * f)
            if not copy_text:
                _apply_text_symbol(G["title"][1], _tsym(tt), False)
            arcpy.AddMessage("Title moved (CIM).")
