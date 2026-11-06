# -*- coding: utf-8 -*-
"""
Script for an ArcGIS Pro *script tool* (New Script dialog) - Value-scale generator.
Keep next to value_scale.py and value_scale_layout.py.

Parameters are read by their NAME (the "Name" column), so row order does not matter
and missing optional rows are simply treated as empty. Names used:

 Input_Features   Value_Source (Layer symbology | Field | Expression)
 Value_Field (Field)   Value_Expression (Calculator Expression)
 Minimum_Value    Maximum_Value
 Symbol_Shape     Custom_Symbol     Minimum_Symbol_Height     Maximum_Symbol_Height
 Max_Interpolation (Area | Height)   Column_Width   Symbol_Unit
 Scalebar_Length  Font_Size         Typographic_Style
 Label_Minimum    Label_Maximum     Label_Mapping
 Divisions        Subdivisions      Symbols_At_Divisions
 Fill_Colour      Stroke            Stroke_Colour     Outline_Offset
 Curve_Width      Line_Width
 Output_SVG       Output_PDF
 Layout           Position_X        Position_Y        Element_Name    Replace_Existing

Value_Source picks where min/max come from. Without a Value_Source row:
expression if filled, otherwise the field.
"""
import os
import re
import sys

import arcpy

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import value_scale as vs  # noqa: E402
import value_scale_layout as vsl  # noqa: E402

UNITS = {"points": "pt", "millimeters": "mm", "centimeters": "cm", "inches": "in",
         "pt": "pt", "mm": "mm", "cm": "cm", "in": "in"}
HEX = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")


_INDEX = None


def _idx(name):
    global _INDEX
    if _INDEX is None:
        _INDEX = {p.name.lower(): i for i, p in enumerate(arcpy.GetParameterInfo())}
    return _INDEX.get(name.lower())


def text(name):
    i = _idx(name)
    return "" if i is None else (arcpy.GetParameterAsText(i) or "").strip()


def number(name):
    i = _idx(name)
    v = None if i is None else arcpy.GetParameter(i)  # locale-safe (avoids "6,5" vs "6.5")
    return None if v in (None, "") else v


def flag(name, default=False):
    i = _idx(name)
    v = None if i is None else arcpy.GetParameter(i)
    return default if v in (None, "") else bool(v)


def colour(name):
    t = text(name)
    if not t:
        return None
    if not HEX.match(t):
        arcpy.AddError(f"{name.replace('_', ' ')}: use a hex colour such as #9ecae1.")
        raise arcpy.ExecuteError
    return t if t.startswith("#") else "#" + t


def style_code(t):
    t = t.lower()
    return "en" if t.startswith("eng") else "no" if t.startswith("raw") else "cz"


def main():
    layer = text("Input_Features")
    kind = text("Value_Source").lower()
    if kind.startswith("field"):
        field = text("Value_Field")
    elif kind.startswith("expr"):
        field = text("Value_Expression")
    else:
        field = text("Value_Expression") or text("Value_Field")
    vmin, vmax = number("Minimum_Value"), number("Maximum_Value")
    min_size = number("Minimum_Symbol_Height")
    unit = UNITS.get((text("Symbol_Unit") or "points").lower(), "pt")
    if layer and kind.startswith("layer"):  # same data range as the Symbology pane
        try:
            info = vsl.symbology_info(layer)
        except Exception as e:
            arcpy.AddError(str(e))
            raise arcpy.ExecuteError
        vmin = info["value_min"] if vmin is None else vmin
        vmax = info["value_max"] if vmax is None else vmax
        arcpy.AddMessage(f"From symbology ({info['source']}): {info['value_min']} - {info['value_max']}")
        if min_size is None and info["min_symbol_size_pt"]:
            min_size, unit = info["min_symbol_size_pt"], "pt"
            arcpy.AddMessage(f"Minimum symbol size from symbology: {min_size} pt")
        if info["flannery"]:
            arcpy.AddWarning("The layer uses Flannery compensation; this scale does not.")
        for note in info["notes"]:
            arcpy.AddWarning(note)
    elif layer and field:
        try:
            lo, hi, skipped = vsl.field_range(layer, field)
        except ValueError as e:
            arcpy.AddError(str(e))
            raise arcpy.ExecuteError
        arcpy.AddMessage(f"Range of positive values from '{field}': {lo} - {hi}")
        if skipped:
            arcpy.AddWarning(f"{skipped} feature(s) ignored for the range "
                             f"(value <= 0 or not computable, e.g. log of 0).")
        if (vmin is not None and vmin != lo) or (vmax is not None and vmax != hi):
            arcpy.AddWarning(f"Minimum/Maximum Value typed in the tool ({vmin} / {vmax}) are used instead "
                             f"of this range. Leave them empty to use {lo} - {hi}.")
        vmin = lo if vmin is None else vmin
        vmax = hi if vmax is None else vmax
    if vmin is None or vmax is None:
        arcpy.AddError("Enter minimum and maximum values, or choose input features and a value field.")
        raise arcpy.ExecuteError

    out_svg, out_pdf, layout = text("Output_SVG"), text("Output_PDF"), text("Layout")
    if not (out_svg or out_pdf or layout):
        arcpy.AddError("Choose a layout, or set an SVG and/or PDF output.")
        raise arcpy.ExecuteError

    divisions = text("Divisions")
    cfg = vs.ScaleConfig(
        value_min=float(vmin), value_max=float(vmax),
        symbol_shape=(text("Symbol_Shape") or "circle").lower(),
        svg_file=text("Custom_Symbol") or None,
        min_symbol_size=float(min_size or 12),
        max_symbol_size=None if number("Maximum_Symbol_Height") is None else float(number("Maximum_Symbol_Height")),
        max_interpolation="height" if text("Max_Interpolation").lower().startswith("h") else "area",
        symbol_unit=unit,
        scalebar_length=float(number("Scalebar_Length") or 120),
        font_size=float(10 if number("Font_Size") is None else number("Font_Size")),
        environment=style_code(text("Typographic_Style") or "European"),
        divisions=vs._divisions(divisions) if divisions else None,
        subdivisions=None if number("Subdivisions") is None else int(number("Subdivisions")),
        fill_colour=colour("Fill_Colour"),
        stroke=None if number("Stroke") is None else float(number("Stroke")),
        stroke_colour=colour("Stroke_Colour") or "#000000",
        label_min=None if number("Label_Minimum") is None else float(number("Label_Minimum")),
        label_max=None if number("Label_Maximum") is None else float(number("Label_Maximum")),
        label_mapping="log" if text("Label_Mapping").lower().startswith("log") else "linear",
        symbols_at_divisions=flag("Symbols_At_Divisions"),
        column_width=float(number("Column_Width") or 5),
        outline_offset=float(number("Outline_Offset") or 0),
        curve_width=float(number("Curve_Width") or vs.W_CURVE),
        line_width=float(number("Line_Width") or vs.W_LINE),
    )
    notes = []
    try:
        svg = vs.generate_svg(cfg, notes)
    except ValueError as e:
        arcpy.AddError(str(e))
        raise arcpy.ExecuteError
    for n in notes:
        arcpy.AddWarning(n)

    if out_svg:
        with open(out_svg, "w", encoding="utf-8") as fh:
            fh.write(svg)
        arcpy.AddMessage(f"SVG written: {out_svg}")
    if out_pdf:
        try:
            vs.svg_to_pdf(svg, out_pdf)
            arcpy.AddMessage(f"PDF written: {out_pdf}")
        except RuntimeError as e:
            arcpy.AddWarning(str(e))

    if layout:
        name = text("Element_Name") or "Value scale"
        replace = flag("Replace_Existing", default=True)
        svg_path = out_svg
        if not svg_path:  # the picture gets embedded in the project; a scratch copy is enough
            svg_path = os.path.join(arcpy.env.scratchFolder or os.environ.get("TEMP", "."),
                                    re.sub(r"\W+", "_", name).strip("_") + ".svg")
            with open(svg_path, "w", encoding="utf-8") as fh:
                fh.write(svg)
        try:
            el = vsl.place_svg(svg_path, layout, number("Position_X"), number("Position_Y"), name, replace)
        except Exception as e:
            arcpy.AddError(f"Could not insert into layout '{layout}': {e}")
            raise arcpy.ExecuteError
        arcpy.AddMessage(f"Inserted '{el.name}' into layout '{layout}' "
                         f"({el.elementWidth:.2f} x {el.elementHeight:.2f} page units).")


if __name__ == "__main__":
    main()
