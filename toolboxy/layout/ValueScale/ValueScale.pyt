# -*- coding: utf-8 -*-
"""
ArcGIS Pro Python toolbox: Value-scale generator for proportional-symbol maps.

Keep this file next to value_scale.py and value_scale_layout.py (same folder). In ArcGIS Pro:
Catalog pane > Toolboxes > Add Toolbox > ValueScale.pyt

Port of the web tool by M. Holub & R. Barvir (Palacky University Olomouc):
Barvir, R., Holub, M., Vondrakova, A. (2025), ISPRS Int. J. Geo-Inf. 14(9), 340.
"""
import importlib
import os
import re
import sys

import arcpy

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
# Reload on every toolbox load: Pro keeps modules imported by an earlier version of the
# toolbox in memory, and a stale copy makes the toolbox fail to load (red "!").
# If the import fails, the toolbox still loads and the tool shows the real error.
try:
    import value_scale as vs  # noqa: E402
    import value_scale_layout as vsl  # noqa: E402
    vs = importlib.reload(vs)
    vsl = importlib.reload(vsl)
    _IMPORT_ERROR = None
except Exception as _e:  # noqa: BLE001
    vs = vsl = None
    _IMPORT_ERROR = (f"Could not load value_scale.py / value_scale_layout.py from {_HERE}: "
                     f"{type(_e).__name__}: {_e}. Put the current versions of both files next to ValueScale.pyt.")

SHAPES = ["circle", "square", "triangle", "pentagon", "hexagon", "column", "apex", "custom"]
UNITS = {"Points": "pt", "Millimeters": "mm", "Centimeters": "cm", "Inches": "in"}
STYLES = {"European (12 345,6)": "cz", "English (12,345.6)": "en", "Raw numbers (12345.6)": "no"}
HEX = re.compile(r"^#?([0-9A-Fa-f]{3}|[0-9A-Fa-f]{6})$")
OPTIONAL = "Optional settings"
LAYOUT = "Layout"
LABELS = "Legend labels"
SOURCES = ["Layer symbology", "Field", "Expression"]
MAPPINGS = {"Linear": "linear", "Logarithmic (values stored as logs)": "log"}
INTERP = {"Area (symbol area grows linearly)": "area", "Height (symbol height grows linearly)": "height"}

# (layer, field/expression) -> (min, max, skipped) or the exception it raised.
# Filled in updateParameters, read in updateMessages, so the data are scanned once per change.
_RANGES = {}


def _range(layer, source, refresh=False):
    key = (layer, source)
    if refresh or key not in _RANGES:
        try:
            _RANGES[key] = vsl.field_range(layer, source)
        except Exception as e:  # shown on the field / expression parameter
            _RANGES[key] = e
    return _RANGES[key]


def _fmt(v):
    return f"{v:.10g}"


def _hex(text):
    if not text:
        return None
    text = text.strip()
    return text if text.startswith("#") else "#" + text


class Toolbox(object):
    def __init__(self):
        self.label = "Value-Scale Generator"
        self.alias = "valuescale"
        self.tools = [GenerateValueScale]


class GenerateValueScale(object):
    def __init__(self):
        self.label = "Generate Value Scale"
        self.description = (
            "Creates a value scale for the legend of a proportional-symbol map as SVG and/or PDF. "
            "Symbol area grows in proportion to the value."
        )
        self.canRunInBackground = True

    # ------------------------------------------------------------------ parameters
    def getParameterInfo(self):
        def P(name, label, dtype, ptype="Optional", direction="Input", category=None):
            return arcpy.Parameter(name=name, displayName=label, datatype=dtype,
                                   parameterType=ptype, direction=direction, category=category)

        in_layer = P("in_layer", "Input Features (to read min/max)", "GPFeatureLayer")
        source = P("value_source", "Value Source", "GPString", "Required")
        source.filter.type = "ValueList"
        source.filter.list = SOURCES
        source.value = "Field"

        field = P("value_field", "Value Field", "Field")
        field.parameterDependencies = [in_layer.name]
        field.filter.list = ["Short", "Long", "BigInteger", "Float", "Double"]
        # ArcGIS's expression builder, e.g. math.log10(!POP!) or !POP! / !AREA!;
        # simple Arcade ($feature.POP, Log($feature.POP)) works too.
        # Min / max below are filled in from it as soon as it is set.
        expr = P("value_expression", "Value Expression", "GPCalculatorExpression")
        expr.parameterDependencies = [in_layer.name]

        vmin = P("value_min", "Minimum Phenomenon Value (filled from the source)", "GPDouble")
        vmax = P("value_max", "Maximum Phenomenon Value (filled from the source)", "GPDouble")
        vmin.value, vmax.value = 6, 224

        shape = P("symbol_shape", "Symbol Shape", "GPString", "Required")
        shape.filter.type = "ValueList"
        shape.filter.list = SHAPES
        shape.value = "circle"

        svg_file = P("svg_file", "Custom Symbol (SVG, max 1 MB)", "DEFile")
        svg_file.filter.list = ["svg"]

        min_size = P("min_symbol_size", "Minimum Symbol Height", "GPDouble", "Required")
        min_size.value = 12

        max_size = P("max_symbol_size", "Maximum Symbol Height (optional, as Maximum size in Symbology)",
                     "GPDouble")
        interp = P("max_interpolation", "Sizes Between Minimum and Maximum", "GPString")
        interp.filter.type = "ValueList"
        interp.filter.list = list(INTERP)
        interp.value = list(INTERP)[0]

        col_w = P("column_width", "Column | Apex-Base Width (symbol-size unit)", "GPDouble")
        col_w.value = 5

        unit = P("symbol_unit", "Symbol-Size Unit", "GPString", "Required")
        unit.filter.type = "ValueList"
        unit.filter.list = list(UNITS)
        unit.value = "Points"

        length = P("scalebar_length", "Scalebar Length (mm)", "GPDouble", "Required")
        length.value = 120
        font = P("font_size", "Font Size (pt, 0 = no labels)", "GPDouble", "Required")
        font.value = 10

        style = P("number_style", "Typographic Style", "GPString", "Required")
        style.filter.type = "ValueList"
        style.filter.list = list(STYLES)
        style.value = list(STYLES)[0]

        label_min = P("label_min", "Label Minimum (printed at the minimum value)", "GPDouble", category=LABELS)
        label_max = P("label_max", "Label Maximum (printed at the maximum value)", "GPDouble", category=LABELS)
        mapping = P("label_mapping", "Label Mapping", "GPString", category=LABELS)
        mapping.filter.type = "ValueList"
        mapping.filter.list = list(MAPPINGS)
        mapping.value = "Linear"

        divisions = P("divisions", "User-Defined Divisions (e.g. 50; 100; 128.6; in label units)",
                      "GPString", category=OPTIONAL)
        subdivisions = P("subdivisions", "Number of Subdivisions", "GPLong", category=OPTIONAL)
        sym_divs = P("symbols_at_divisions", "Draw a Symbol at Every Division", "GPBoolean", category=OPTIONAL)
        sym_divs.value = False
        fill = P("fill_colour", "Fill Colour (hex, e.g. #9ecae1)", "GPString", category=OPTIONAL)
        stroke = P("stroke", "Symbol Stroke (pt, 0 = none)", "GPDouble", category=OPTIONAL)
        stroke_col = P("stroke_colour", "Symbol Stroke Colour (hex)", "GPString", category=OPTIONAL)
        stroke_col.value = "#000000"
        offset = P("outline_offset", "Symbol Outline Offset (pt, + inward, - outward)", "GPDouble",
                   category=OPTIONAL)
        offset.value = 0
        curve_w = P("curve_width", "Curve Line Width (pt)", "GPDouble", category=OPTIONAL)
        curve_w.value = 1.0
        line_w = P("line_width", "Division Line Width (pt; baseline 1.2x, subdivisions 0.8x)", "GPDouble",
                   category=OPTIONAL)
        line_w.value = 0.5

        out_svg = P("out_svg", "Output SVG", "DEFile", "Optional", "Output")
        out_svg.filter.list = ["svg"]
        out_pdf = P("out_pdf", "Output PDF", "DEFile", "Optional", "Output")
        out_pdf.filter.list = ["pdf"]

        layout = P("layout", "Insert into Layout", "GPString", category=LAYOUT)
        layout.filter.type = "ValueList"
        layout.filter.list = self._layout_names()
        pos_x = P("pos_x", "Position X (page units, upper-left corner)", "GPDouble", category=LAYOUT)
        pos_y = P("pos_y", "Position Y (page units, upper-left corner)", "GPDouble", category=LAYOUT)
        el_name = P("element_name", "Picture Element Name", "GPString", category=LAYOUT)
        el_name.value = "Value scale"
        replace = P("replace", "Replace Existing Element With the Same Name", "GPBoolean", category=LAYOUT)
        replace.value = True

        return [in_layer, source, field, expr, vmin, vmax, shape, svg_file, min_size, max_size, interp, col_w, unit, length,
                font, style, label_min, label_max, mapping, divisions, subdivisions, sym_divs,
                fill, stroke, stroke_col, offset, curve_w, line_w, out_svg, out_pdf, layout, pos_x, pos_y, el_name, replace]

    @staticmethod
    def _layout_names():
        try:
            return [l.name for l in arcpy.mp.ArcGISProject("CURRENT").listLayouts()]
        except Exception:  # not running inside ArcGIS Pro (e.g. standalone script)
            return []

    def isLicensed(self):
        return True

    @staticmethod
    def _p(parameters):
        return {p.name: p for p in parameters}

    @staticmethod
    def _source(p):
        """Field name or Python expression to compute min/max from ('' for symbology)."""
        kind = p["value_source"].valueAsText or "Field"
        if kind == "Expression":
            return (p["value_expression"].valueAsText or "").strip()
        if kind == "Field":
            return p["value_field"].valueAsText or ""
        return ""

    # ------------------------------------------------------------------ validation
    def updateParameters(self, parameters):
        if _IMPORT_ERROR:
            return
        p = self._p(parameters)
        shape = p["symbol_shape"].valueAsText or "circle"
        p["svg_file"].enabled = shape == "custom"

        names = self._layout_names()
        if names:
            p["layout"].filter.list = names
        to_layout = bool(p["layout"].valueAsText)
        for name in ("pos_x", "pos_y", "element_name", "replace"):
            p[name].enabled = to_layout

        p["column_width"].enabled = shape in ("column", "apex")
        p["max_interpolation"].enabled = (p["max_symbol_size"].value is not None
                                          and shape not in ("column", "apex"))

        lyr, fld, expr, src = p["in_layer"], p["value_field"], p["value_expression"], p["value_source"]
        kind = src.valueAsText or "Field"
        fld.enabled = kind == "Field"
        expr.enabled = kind == "Expression"
        target = expr if kind == "Expression" else fld
        # Only the parameters that define the range count (a disabled one may never be
        # marked validated, which would refill min/max on every edit and undo typed values).
        relevant = [lyr, src] + ([target] if kind in ("Field", "Expression") else [])
        changed = any(not q.hasBeenValidated for q in relevant)
        if not (lyr.value and changed):
            return
        if kind == "Layer symbology":
            try:  # same field / Arcade expression and data range as the Symbology pane
                info = vsl.symbology_info(lyr.valueAsText)
                p["value_min"].value, p["value_max"].value = info["value_min"], info["value_max"]
                if info["min_symbol_size_pt"]:
                    p["min_symbol_size"].value = info["min_symbol_size_pt"]
                    p["symbol_unit"].value = "Points"
            except Exception:
                pass  # reported in updateMessages
        elif target.valueAsText:
            r = _range(lyr.valueAsText, self._source(p), refresh=True)
            if not isinstance(r, Exception):
                p["value_min"].value, p["value_max"].value = r[0], r[1]
        return

    def updateMessages(self, parameters):
        if _IMPORT_ERROR:
            parameters[0].setErrorMessage(_IMPORT_ERROR)
            return
        p = self._p(parameters)
        vmin, vmax = p["value_min"].value, p["value_max"].value
        if vmin is None:
            p["value_min"].setErrorMessage("Enter a minimum value, or choose input features and a value field.")
        elif vmin <= 0:
            p["value_min"].setErrorMessage("Minimum value must be greater than 0 (symbol size is relative to it).")
        if vmax is None:
            p["value_max"].setErrorMessage("Enter a maximum value, or choose input features and a value field.")
        elif vmin is not None and vmax <= vmin:
            p["value_max"].setErrorMessage("Maximum value must be greater than the minimum value.")

        shape = p["symbol_shape"].valueAsText
        if shape == "custom" and not p["svg_file"].value:
            p["svg_file"].setErrorMessage("Select an SVG file for the custom shape.")
        if (p["min_symbol_size"].value or 0) <= 0:
            p["min_symbol_size"].setErrorMessage("Height must be greater than 0.")
        mx = p["max_symbol_size"].value
        if mx is not None and mx <= (p["min_symbol_size"].value or 0):
            p["max_symbol_size"].setErrorMessage("Maximum height must be greater than the minimum height.")
        if (p["scalebar_length"].value or 0) <= 0:
            p["scalebar_length"].setErrorMessage("Length must be greater than 0.")
        if (p["font_size"].value or 0) < 0:
            p["font_size"].setErrorMessage("Font size cannot be negative.")
        for name in ("curve_width", "line_width"):
            if p[name].value is not None and p[name].value <= 0:
                p[name].setErrorMessage("Line width must be greater than 0.")
        if p["outline_offset"].value and shape == "custom":
            p["outline_offset"].setWarningMessage("Not supported for custom SVG symbols; ignored.")
        elif p["outline_offset"].value and p["stroke"].value == 0:
            p["outline_offset"].setWarningMessage("Symbol stroke is 0, so there is no outline to offset.")
        if p["subdivisions"].value is not None and p["subdivisions"].value < 0:
            p["subdivisions"].setErrorMessage("Cannot be negative.")

        if (p["value_source"].valueAsText == "Layer symbology"):
            if not p["in_layer"].value:
                p["in_layer"].setErrorMessage("Choose the layer whose symbology should be used.")
            else:
                try:
                    info = vsl.symbology_info(p["in_layer"].valueAsText)
                    notes = list(info["notes"])
                    if info["flannery"]:
                        notes.insert(0, "The layer uses Flannery compensation; this scale does not, "
                                        "so the symbol sizes will differ.")
                    if notes:
                        p["value_source"].setWarningMessage(" ".join(notes))
                except Exception as e:
                    p["value_source"].setErrorMessage(str(e))
        kind = p["value_source"].valueAsText or "Field"
        target = p["value_expression"] if kind == "Expression" else p["value_field"]
        if kind in ("Field", "Expression") and target.valueAsText:
            if not p["in_layer"].value:
                p["in_layer"].setErrorMessage("Choose the input features the field / expression is computed on.")
                if kind == "Expression":
                    try:
                        vsl.compile_expression(target.valueAsText)
                    except Exception as e:
                        target.setErrorMessage(str(e))
            else:
                r = _range(p["in_layer"].valueAsText, self._source(p))
                if isinstance(r, Exception):
                    target.setErrorMessage(str(r))
                else:
                    lo, hi, skipped = r
                    notes = []
                    if skipped:
                        notes.append(f"{skipped} feature(s) ignored (value <= 0 or not computable, e.g. log of 0).")
                    if vmin is not None and vmax is not None and (
                            abs(vmin - lo) > 1e-9 * abs(lo) or abs(vmax - hi) > 1e-9 * abs(hi)):
                        notes.append(f"Min/Max differ from the data range {_fmt(lo)} - {_fmt(hi)} "
                                     f"(re-select the field / expression to refill them).")
                    if notes:
                        target.setWarningMessage(" ".join(notes))

        if (p["column_width"].value or 0) <= 0 and shape in ("column", "apex"):
            p["column_width"].setErrorMessage("Width must be greater than 0.")

        lmin, lmax = p["label_min"].value, p["label_max"].value
        if (lmin is None) != (lmax is None):
            (p["label_max"] if lmax is None else p["label_min"]).setErrorMessage(
                "Give both label minimum and maximum, or leave both empty.")
        elif lmin is not None:
            if lmin >= lmax:
                p["label_max"].setErrorMessage("Label maximum must be greater than label minimum.")
            elif MAPPINGS.get(p["label_mapping"].valueAsText) == "log" and lmin <= 0:
                p["label_min"].setErrorMessage("Logarithmic mapping needs a label minimum greater than 0.")

        if p["divisions"].valueAsText:
            try:
                vs._divisions(p["divisions"].valueAsText)
            except ValueError:
                p["divisions"].setErrorMessage("Use numbers separated by semicolons, e.g. 50; 100; 128.6")
        for name in ("fill_colour", "stroke_colour"):
            text = p[name].valueAsText
            if text and not HEX.match(text.strip()):
                p[name].setErrorMessage("Use a hex colour such as #9ecae1.")

        if not (p["out_svg"].valueAsText or p["out_pdf"].valueAsText or p["layout"].valueAsText):
            p["out_svg"].setErrorMessage("Choose a layout to insert into, or set an SVG and/or PDF output.")
        if p["layout"].valueAsText and not (p["element_name"].valueAsText or "").strip():
            p["element_name"].setErrorMessage("Give the picture element a name.")
        return

    # ------------------------------------------------------------------ run
    def execute(self, parameters, messages):
        global vs, vsl
        if _IMPORT_ERROR:
            arcpy.AddError(_IMPORT_ERROR)
            raise arcpy.ExecuteError
        vs = importlib.reload(vs)  # pick up edits to the modules without restarting Pro
        vsl = importlib.reload(vsl)
        p = self._p(parameters)

        if p["in_layer"].value and p["value_source"].valueAsText == "Layer symbology":
            info = vsl.symbology_info(p["in_layer"].valueAsText)
            arcpy.AddMessage(f"From symbology ({info['source']}): "
                             f"{info['value_min']} - {info['value_max']}")
            for note in info["notes"] + (["Flannery compensation is on; this scale does not use it."]
                                         if info["flannery"] else []):
                arcpy.AddWarning(note)
        elif p["in_layer"].value and self._source(p):
            try:
                lo, hi, skipped = vsl.field_range(p["in_layer"].valueAsText, self._source(p))
            except ValueError as e:
                arcpy.AddError(str(e))
                raise arcpy.ExecuteError
            arcpy.AddMessage(f"Range of positive values: {_fmt(lo)} - {_fmt(hi)}")
            vmin, vmax = p["value_min"].value, p["value_max"].value
            if (vmin is not None and abs(vmin - lo) > 1e-9 * abs(lo)) or \
                    (vmax is not None and abs(vmax - hi) > 1e-9 * abs(hi)):
                arcpy.AddWarning(f"Using the typed Minimum/Maximum ({vmin} / {vmax}) instead of the data range.")
            if skipped:
                arcpy.AddWarning(f"{skipped} feature(s) ignored for the range "
                                 f"(value <= 0 or not computable, e.g. log of 0).")

        cfg = vs.ScaleConfig(
            value_min=p["value_min"].value,
            value_max=p["value_max"].value,
            symbol_shape=p["symbol_shape"].valueAsText,
            min_symbol_size=p["min_symbol_size"].value,
            max_symbol_size=p["max_symbol_size"].value,
            max_interpolation=INTERP.get(p["max_interpolation"].valueAsText or "", "area"),
            symbol_unit=UNITS[p["symbol_unit"].valueAsText],
            scalebar_length=p["scalebar_length"].value,
            font_size=p["font_size"].value,
            environment=STYLES[p["number_style"].valueAsText],
            divisions=vs._divisions(p["divisions"].valueAsText) if p["divisions"].valueAsText else None,
            subdivisions=p["subdivisions"].value,
            fill_colour=_hex(p["fill_colour"].valueAsText),
            stroke=p["stroke"].value,
            stroke_colour=_hex(p["stroke_colour"].valueAsText) or "#000000",
            svg_file=p["svg_file"].valueAsText,
            label_min=p["label_min"].value,
            label_max=p["label_max"].value,
            label_mapping=MAPPINGS.get(p["label_mapping"].valueAsText or "Linear", "linear"),
            symbols_at_divisions=bool(p["symbols_at_divisions"].value),
            column_width=p["column_width"].value or 5,
            outline_offset=p["outline_offset"].value or 0.0,
            curve_width=p["curve_width"].value or 1.0,
            line_width=p["line_width"].value or 0.5,
        )
        notes = []
        try:
            svg = vs.generate_svg(cfg, notes)
        except ValueError as e:
            arcpy.AddError(str(e))
            raise arcpy.ExecuteError
        for n in notes:
            arcpy.AddWarning(n)
        unit_txt = p["symbol_unit"].valueAsText.lower()
        lo_h, hi_h, mid_v, mid_h = vs.symbol_heights(cfg)
        arcpy.AddMessage(f"Symbol heights in the scale: {_fmt(lo_h)} - {_fmt(hi_h)} {unit_txt}; "
                         f"value {_fmt(mid_v)} -> {_fmt(mid_h)} {unit_txt}. Compare with a feature of that "
                         f"value in the map to check the sizes match.")

        out_svg, out_pdf = p["out_svg"].valueAsText, p["out_pdf"].valueAsText
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

        layout = p["layout"].valueAsText
        if layout:
            name = p["element_name"].valueAsText.strip()
            svg_path = out_svg
            if not svg_path:  # picture is embedded in the project, a scratch copy is enough
                svg_path = os.path.join(arcpy.env.scratchFolder or os.environ.get("TEMP", "."),
                                        re.sub(r"\W+", "_", name).strip("_") + ".svg")
                with open(svg_path, "w", encoding="utf-8") as fh:
                    fh.write(svg)
            try:
                el = vsl.place_svg(svg_path, layout, p["pos_x"].value, p["pos_y"].value,
                                   name, bool(p["replace"].value))
            except Exception as e:
                arcpy.AddError(f"Could not insert into layout '{layout}': {e}")
                raise arcpy.ExecuteError
            arcpy.AddMessage(f"Inserted '{el.name}' into layout '{layout}' "
                             f"({el.elementWidth:.2f} x {el.elementHeight:.2f} page units).")
        return

    def postExecute(self, parameters):
        return
