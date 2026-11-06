# -*- coding: utf-8 -*-
"""
Place a generated value scale into an ArcGIS Pro layout (arcpy.mp).

The scale is inserted as an SVG picture element at its TRUE size, so the
symbol heights in the legend match the symbols in the map. The picture is
embedded in the project (the SVG file itself is no longer needed).

Python window example (ArcGIS Pro, this file next to value_scale.py on sys.path):

    import sys; sys.path.append(r"C:\\path\\to\\folder")
    import value_scale_layout as vsl
    vsl.add_value_scale(layer="Cities", field="POP",       # min/max read from the field
                        symbol_shape="circle", min_symbol_size=4, symbol_unit="mm",
                        scalebar_length=80, layout="Layout")

    vsl.add_value_scale(value_min=6, value_max=224)          # active layout, default position

Run it again with the same element name and the existing picture is replaced in place.
"""
import math
import os
import re

import arcpy

import value_scale as vs

# page units -> size of one typographic point in those units
_PER_PT = {"INCH": 1 / 72, "CENTIMETER": 2.54 / 72, "MILLIMETER": 25.4 / 72, "POINT": 1.0}


_SAFE = {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
_SAFE.update({"math": math, "abs": abs, "min": min, "max": max, "round": round,
              "float": float, "int": int, "pow": pow})


def _clean(expression):
    expression = (expression or "").strip()
    if len(expression) > 1 and expression[0] == expression[-1] and expression[0] in "\"'":
        expression = expression[1:-1].strip()  # builder may wrap the text in quotes
    return expression


def is_arcade(expression):
    return "$feature" in _clean(expression)


def compile_expression(expression):
    """Field-calculator style Python expression, e.g. "math.log10(!POP! / !AREA!)".
    A bare field name ("POP") is accepted too. Returns (field names, code object).
    Arcade expressions ($feature.POP) are accepted by field_range, not here."""
    expression = _clean(expression)
    if not expression:
        raise ValueError("Empty value expression.")
    if is_arcade(expression):
        return None, None
    fields = list(dict.fromkeys(re.findall(r"!([^!]+)!", expression)))
    if not fields:  # bare field name
        if not re.fullmatch(r"[A-Za-z_][\w.]*", expression):
            raise ValueError("Write fields as !FieldName! (Python), e.g. math.log10(!Suma!), "
                             "or use Arcade with $feature.FieldName.")
        fields, expression = [expression], f"!{expression}!"
    code = expression
    for i, name in enumerate(fields):
        code = code.replace(f"!{name}!", f"_f[{i}]")
    try:
        return fields, compile(code, "<value expression>", "eval")
    except SyntaxError as e:
        raise ValueError(f"Invalid value expression: {e.msg}")


_ARCADE_FUNCS = {"Log": "math.log", "Sqrt": "math.sqrt", "Exp": "math.exp", "Pow": "pow",
                 "Abs": "abs", "Round": "round", "Floor": "math.floor", "Ceil": "math.ceil",
                 "Min": "min", "Max": "max", "Number": "float"}


def arcade_to_python(expression):
    """Translate a one-line Arcade expression ($feature.POP / $feature["AREA"], Log, Sqrt,
    Pow ...) into a field-calculator Python expression, or None if it is more complex
    (var / return / IIf ...). Lets simple Arcade be evaluated with a plain cursor, which is
    fast and safe to run while the tool dialog validates."""
    e = _clean(expression)
    if any(t in e for t in ("\n", ";", "return", "var ", "IIf", "When", "Decode", "?")):
        return None
    e = re.sub(r"\$feature\[\s*[\"']([^\"']+)[\"']\s*\]", r"!\1!", e)
    e = re.sub(r"\$feature\.([A-Za-z_]\w*)", r"!\1!", e)
    if "$" in e:
        return None
    e = re.sub(r"\b(" + "|".join(_ARCADE_FUNCS) + r")\s*\(", lambda m: _ARCADE_FUNCS[m.group(1)] + "(", e)
    try:
        compile_expression(e)
    except ValueError:
        return None
    return e


def _arcade_values(layer, expression):
    """Evaluate an Arcade expression with Calculate Field on a temporary in-memory copy
    (the source data are never modified)."""
    tmp = "memory\\vs_value_tmp"
    try:
        arcpy.management.CopyRows(layer, tmp)  # respects selection and definition query
        arcpy.management.AddField(tmp, "VS_VALUE", "DOUBLE")
        arcpy.management.CalculateField(tmp, "VS_VALUE", expression, "ARCADE")
        with arcpy.da.SearchCursor(tmp, ["VS_VALUE"]) as cursor:
            return [row[0] for row in cursor]
    except arcpy.ExecuteError:
        raise ValueError("Arcade expression failed: " + arcpy.GetMessages(2).strip())
    finally:
        try:
            arcpy.management.Delete(tmp)
        except Exception:
            pass


def _python_values(layer, expression):
    fields, code = compile_expression(expression)
    values = []
    try:
        cursor = arcpy.da.SearchCursor(layer, fields)
    except RuntimeError as e:
        raise ValueError(f"Field not found in the layer ({', '.join(fields)}): {e}")
    with cursor:
        for row in cursor:
            if any(v is None for v in row):
                values.append(None)
                continue
            try:
                values.append(float(eval(code, {"__builtins__": {}, **_SAFE}, {"_f": row})))
            except (ValueError, ZeroDivisionError, OverflowError, TypeError):
                values.append(float("nan"))  # e.g. log of 0 -> skipped, counted
            except NameError as e:
                raise ValueError(f"Unknown name in expression: {e}. Use e.g. math.log10(!Field!).")
    return values


def field_range(layer, expression):
    """(min, max, skipped) of a field, Python expression (!Field!) or Arcade expression
    ($feature.Field) over the features (respects selection and definition query).
    Empty inputs are ignored; values <= 0 or not computable (log of 0) are skipped and counted."""
    expression = _clean(expression)
    if is_arcade(expression):
        py = arcade_to_python(expression)
        values = _python_values(layer, py) if py else _arcade_values(layer, expression)
    else:
        values = _python_values(layer, expression)
    lo = hi = None
    skipped = 0
    for v in values:
        if v is None:
            continue
        if not v > 0 or math.isinf(v):  # also catches NaN
            skipped += 1
            continue
        lo = v if lo is None else min(lo, v)
        hi = v if hi is None else max(hi, v)
    if lo is None:
        raise ValueError(f"'{expression}' gives no positive values.")
    return lo, hi, skipped


def find_layer(layer, aprx=None):
    """arcpy.mp Layer from a Layer object or a layer name (searched in all maps,
    the active map first)."""
    if hasattr(layer, "getDefinition"):
        return layer
    aprx = aprx or arcpy.mp.ArcGISProject("CURRENT")
    maps = aprx.listMaps()
    try:
        active = aprx.activeMap
        if active is not None:
            maps = [active] + [m for m in maps if m.name != active.name]
    except Exception:
        pass
    name = str(layer)
    for m in maps:
        for lyr in m.listLayers():
            if lyr.name == name or getattr(lyr, "longName", None) == name:
                return lyr
    raise ValueError(f"Layer '{name}' not found in the project's maps.")


def _attr(obj, *names):
    for n in names:
        v = getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return None


def _symbol_size(sym_ref):
    """Largest marker size (pt) of a CIM point symbol reference, or None."""
    symbol = _attr(sym_ref, "symbol", "Symbol")
    layers = _attr(symbol, "symbolLayers", "SymbolLayers") or []
    sizes = [getattr(sl, "size", None) for sl in layers]
    sizes = [s for s in sizes if isinstance(s, (int, float)) and s > 0]
    return max(sizes) if sizes else None


def symbology_info(layer, aprx=None):
    """Read a proportional-symbols layer's renderer (as set in the Symbology pane).

    Returns dict: value_min, value_max (the data range Pro computed for the field or
    Arcade expression), source (expression or field text), min_symbol_size_pt (or None),
    flannery (bool).
    """
    lyr = find_layer(layer, aprx)
    renderer = lyr.getDefinition("V3").renderer
    if type(renderer).__name__ != "CIMProportionalRenderer":
        raise ValueError(f"Layer '{lyr.name}' is not symbolized with Proportional Symbols "
                         f"(found {type(renderer).__name__}).")
    lo = _attr(renderer, "minDataValue", "MinDataValue")
    hi = _attr(renderer, "maxDataValue", "MaxDataValue")
    expr_info = _attr(renderer, "valueExpressionInfo", "ValueExpressionInfo")
    expression = _attr(expr_info, "expression", "Expression") if expr_info else None
    field = _attr(renderer, "field", "Field")
    if lo is None or hi is None:
        raise ValueError("The symbology stores no data range; open the layer's Symbology pane once and retry.")
    notes = []
    unit = _attr(renderer, "unitSymbolization", "UnitSymbolization")
    if unit is not None and not any(t in str(unit).lower() for t in ("unknown", "none", "point")):
        notes.append(f"Symbols seem to be sized in real-world units ({unit}); their page size then depends "
                     f"on the map scale. Check that Unit is 'Unknown' in the Symbology pane.")
    if _attr(renderer, "visualVariables", "VisualVariables"):
        notes.append("The renderer has visual variables (e.g. a size or rotation variable); "
                     "a size variable changes symbol sizes and the scale will not match.")
    if _attr(renderer, "normalizationField", "NormalizationField"):
        notes.append("Values are normalized; the range from the symbology is used, but a range from a "
                     "field / expression would not be normalized.")
    return {
        "notes": notes,
        "value_min": float(lo),
        "value_max": float(hi),
        "source": f"Arcade: {expression}" if expression else (field or "?"),
        "min_symbol_size_pt": _symbol_size(_attr(renderer, "minSymbol", "MinSymbol")),
        "flannery": bool(_attr(renderer, "flanneryCompensation", "FlanneryCompensation")),
    }


def svg_size_pt(svg_text):
    """Width and height (pt) of an SVG written by value_scale."""
    head = re.search(r"<svg\b[^>]*>", svg_text).group(0)
    w = re.search(r'\swidth="([\d.]+)pt"', head)
    h = re.search(r'\sheight="([\d.]+)pt"', head)
    if not (w and h):
        raise ValueError("SVG size in pt not found.")
    return float(w.group(1)), float(h.group(1))


def get_layout(layout=None, aprx=None):
    """Return (project, layout). `layout` may be a Layout object, a name, or None
    (= the active layout view, otherwise the project's first layout)."""
    aprx = aprx or arcpy.mp.ArcGISProject("CURRENT")
    if layout is not None and not isinstance(layout, str):
        return aprx, layout
    if layout:
        found = [l for l in aprx.listLayouts() if l.name == layout]
        if not found:
            raise ValueError(f"Layout '{layout}' not found in the project.")
        return aprx, found[0]
    try:
        view = aprx.activeView
        if view is not None and hasattr(view, "pageWidth"):  # a Layout, not a MapView
            return aprx, view
    except Exception:
        pass
    layouts = aprx.listLayouts()
    if not layouts:
        raise ValueError("The project has no layout.")
    return aprx, layouts[0]


def reference_scale_notes(lyt):
    """Warnings for map frames whose map has a reference scale different from the frame scale.
    Pro then draws symbols ref/frame times their nominal size, so a legend drawn at the
    nominal (point) size no longer matches the map."""
    notes = []
    for mf in lyt.listElements("MAPFRAME_ELEMENT"):
        try:
            ref = mf.map.referenceScale or 0
            scale = mf.camera.scale
        except Exception:
            continue
        if ref > 0 and scale > 0 and abs(scale - ref) / ref > 0.005:
            k = ref / scale
            notes.append(f"Map frame '{mf.name}' shows map '{mf.map.name}' at 1:{scale:,.0f}, but its reference "
                         f"scale is 1:{ref:,.0f}: map symbols are drawn {k:.3f}x their Symbology size, so the "
                         f"value scale will not match. Set the frame scale to 1:{ref:,.0f}, or multiply the "
                         f"Minimum Symbol Height by {k:.3f}.")
    return notes


def place_svg(svg_path, layout=None, x=None, y=None, name="Value scale", replace=True, aprx=None):
    """Insert (or replace) an SVG picture element at its true size.

    x, y : position of the picture's anchor (upper-left for new pictures), in the
           layout's page units. Default: lower-left corner of the page, 5 % margin.
    """
    aprx, lyt = get_layout(layout, aprx)
    with open(svg_path, encoding="utf-8") as fh:
        w_pt, h_pt = svg_size_pt(fh.read())
    k = _PER_PT.get(str(lyt.pageUnits).upper(), 1 / 72)
    width, height = w_pt * k, h_pt * k

    existing = []
    if replace:
        existing = [e for e in lyt.listElements("PICTURE_ELEMENT") if e.name == name]
    if existing:
        el = existing[0]
        el.sourceImage = svg_path
    else:
        if x is None:
            x = lyt.pageWidth * 0.05
        if y is None:
            y = lyt.pageHeight * 0.05 + height  # anchor is the upper-left corner
        el = aprx.createPictureElement(lyt, arcpy.Point(x, y), svg_path, name)
    el.elementWidth = width
    el.elementHeight = height
    for note in reference_scale_notes(lyt):
        arcpy.AddWarning(note)
    if existing:
        if x is not None:
            el.elementPositionX = x
        if y is not None:
            el.elementPositionY = y
    return el


def add_value_scale(layout=None, *, layer=None, field=None, x=None, y=None,
                    name="Value scale", replace=True, svg_path=None, aprx=None, **scale_kwargs):
    """Generate a value scale and put it into a layout.

    scale_kwargs are ScaleConfig fields: value_min, value_max, symbol_shape,
    min_symbol_size, symbol_unit, scalebar_length, font_size,
    environment, divisions, subdivisions, fill_colour, stroke, stroke_colour, svg_file.
    If layer and field are given, value_min / value_max default to the field range.
    Returns the picture element.
    """
    if layer is not None and field:
        lo, hi, skipped = field_range(layer, field)
        scale_kwargs.setdefault("value_min", lo)
        scale_kwargs.setdefault("value_max", hi)
        if skipped:
            arcpy.AddWarning(f"{skipped} feature(s) with value <= 0 were ignored for the range.")
    svg = vs.generate_svg(vs.ScaleConfig(**scale_kwargs))
    if not svg_path:
        folder = arcpy.env.scratchFolder or os.environ.get("TEMP", ".")
        svg_path = os.path.join(folder, re.sub(r"\W+", "_", name).strip("_") + ".svg")
    with open(svg_path, "w", encoding="utf-8") as fh:
        fh.write(svg)
    return place_svg(svg_path, layout, x, y, name, replace, aprx)
