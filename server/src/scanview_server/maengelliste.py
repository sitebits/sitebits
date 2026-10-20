"""Befundliste als minimale Office-Open-XML-Arbeitsmappe.

Spalten zentral in `SPALTEN`/`BREITEN`; bei eingeschalteten Befund-Fäden hängt der Aufrufer
Status, Zuständig und Frist hinten an (`zusatz`), sonst bleibt die Mappe wie bisher."""
from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from xml.etree import ElementTree as ET

from .inhalte import Eintrag

SPALTEN = ("Nr.", "Titel", "Kategorie", "Notiz", "Standpunkt", "X [m]", "Y [m]", "Z [m]",
           "Erstellt am (UTC)", "Erstellt von", "Geändert am (UTC)", "Geändert von")
BREITEN = (7, 28, 20, 55, 18, 14, 14, 14, 23, 24, 23, 24)
ZUSATZ_BREITE = 22
_KOORDINATEN = range(5, 8)            # Spalten X, Y, Z: Zahlformat 0.000
_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
# Excel schreibt SpreadsheetML ohne Präfix; ns0:-Präfixe vertragen nicht alle Leser.
ET.register_namespace("", _NS)
ET.register_namespace("r", _DOC)


def zeit_utc(zeit: int) -> str:
    return datetime.fromtimestamp(zeit, UTC).strftime("%Y-%m-%d %H:%M")


Zusatz = tuple[tuple[str, ...], dict[str, list[str]]]


def zeilen(pois: list[Eintrag], zusatz: Zusatz | None = None) -> list[list[str | int | float]]:
    werte = {} if zusatz is None else zusatz[1]
    leer = [] if zusatz is None else [""] * len(zusatz[0])
    return [[nr, p.daten["title"], p.daten["category"], p.daten["note"],
             p.daten["station"] or "", *p.daten["position"], zeit_utc(p.erstellt_am),
             p.erstellt_von_name or "", zeit_utc(p.geaendert_am), p.geaendert_von_name or "",
             *(werte.get(p.id, leer) if zusatz is not None else [])]
            for nr, p in enumerate(sorted(pois, key=lambda p: (p.erstellt_am, p.id)), 1)]


def _spalte(nr: int) -> str:
    """Spaltenbuchstabe (0 → A, 25 → Z, 26 → AA)."""
    return (_spalte(nr // 26 - 1) if nr >= 26 else "") + chr(65 + nr % 26)


def _text(text: str) -> str:
    return "".join(c for c in text if c in "\t\n\r" or 0x20 <= ord(c) <= 0xD7FF
                   or 0xE000 <= ord(c) <= 0xFFFD or 0x10000 <= ord(c) <= 0x10FFFF)


def _el(parent: ET.Element, tag: str, **attrs: str) -> ET.Element:
    return ET.SubElement(parent, f"{{{_NS}}}{tag}", attrs)


def _sheet(pois: list[Eintrag], titel: str, revision: int | None, stand: int,
           zusatz: Zusatz | None, einheit: str, genauigkeit: str) -> ET.Element:
    spalten: list[str | int | float] = [*SPALTEN, *(zusatz[0] if zusatz else ())]
    breiten = [*BREITEN, *([ZUSATZ_BREITE] * (len(spalten) - len(SPALTEN)))]
    sheet = ET.Element(f"{{{_NS}}}worksheet")
    view = _el(_el(sheet, "sheetViews"), "sheetView", workbookViewId="0")
    _el(view, "pane", ySplit="4", topLeftCell="A5", activePane="bottomLeft", state="frozen")
    cols = _el(sheet, "cols")
    for nr, width in enumerate(breiten, 1):
        _el(cols, "col", min=str(nr), max=str(nr), width=str(width), customWidth="1")
    data = _el(sheet, "sheetData")
    rows: list[list[str | int | float]] = [
        # Koordinaten- und Höhensystem der Spalten X/Y/Z (F23) in der Kopfzeile;
        # Zeile 4 bleibt die Spaltenzeile.
        ["Projekt", titel, "", "", "", "Koordinaten", einheit,
         *(["Genauigkeit", genauigkeit] if genauigkeit else [])],
        ["Paket-Revision", revision if revision is not None else ""],
        ["Stand (UTC)", zeit_utc(stand)],
        spalten, *zeilen(pois, zusatz),
    ]
    for nr, values in enumerate(rows, 1):
        row = _el(data, "row", r=str(nr))
        for col, value in enumerate(values):
            cell = _el(row, "c", r=f"{_spalte(col)}{nr}",
                       t="inlineStr" if isinstance(value, str) else "n",
                       s="1" if nr == 4 else "2" if nr > 4 and col in _KOORDINATEN else "0")
            if isinstance(value, str):
                text = _el(_el(cell, "is"), "t")
                text.set("{http://www.w3.org/XML/1998/namespace}space", "preserve")
                text.text = _text(value)
            else:
                number = f"{value:.3f}" if nr > 4 and col in _KOORDINATEN else str(value)
                _el(cell, "v").text = "0.000" if number == "-0.000" else number
    _el(sheet, "autoFilter", ref=f"A4:{_spalte(len(spalten) - 1)}{len(rows)}")
    return sheet


def _styles() -> ET.Element:
    styles = ET.Element(f"{{{_NS}}}styleSheet")
    _el(_el(styles, "numFmts", count="1"), "numFmt", numFmtId="164", formatCode="0.000")
    fonts = _el(styles, "fonts", count="2")
    _el(fonts, "font")
    _el(_el(fonts, "font"), "b")
    fills = _el(styles, "fills", count="2")
    for pattern in ("none", "gray125"):
        _el(_el(fills, "fill"), "patternFill", patternType=pattern)
    _el(_el(styles, "borders", count="1"), "border")
    _el(_el(styles, "cellStyleXfs", count="1"), "xf", numFmtId="0", fontId="0", fillId="0",
        borderId="0")
    xfs = _el(styles, "cellXfs", count="3")
    for font, number in (("0", "0"), ("1", "0"), ("0", "164")):
        _el(xfs, "xf", numFmtId=number, fontId=font, fillId="0", borderId="0", xfId="0")
    return styles


def xlsx(pois: list[Eintrag], titel: str, revision: int | None, stand: int,
         zusatz: Zusatz | None = None, einheit: str = "[m]", genauigkeit: str = "") -> bytes:
    types = ET.Element("Types", xmlns="http://schemas.openxmlformats.org/package/2006/content-types")
    for ext, mime in (("rels", "application/vnd.openxmlformats-package.relationships+xml"),
                      ("xml", "application/xml")):
        ET.SubElement(types, "Default", Extension=ext, ContentType=mime)
    mime = "application/vnd.openxmlformats-officedocument.spreadsheetml"
    for part, kind in (("workbook.xml", "sheet.main"), ("worksheets/sheet1.xml", "worksheet"),
                       ("styles.xml", "styles")):
        ET.SubElement(types, "Override", PartName=f"/xl/{part}", ContentType=f"{mime}.{kind}+xml")
    root_rels = ET.Element("Relationships", xmlns=_REL)
    ET.SubElement(root_rels, "Relationship", Id="rId1", Type=f"{_DOC}/officeDocument",
                  Target="xl/workbook.xml")
    workbook = ET.Element(f"{{{_NS}}}workbook")
    _el(_el(workbook, "sheets"), "sheet", name="Befundliste", sheetId="1",
        **{f"{{{_DOC}}}id": "rId1"})
    rels = ET.Element("Relationships", xmlns=_REL)
    for nr, kind, target in ((1, "worksheet", "worksheets/sheet1.xml"),
                             (2, "styles", "styles.xml")):
        ET.SubElement(rels, "Relationship", Id=f"rId{nr}", Type=f"{_DOC}/{kind}", Target=target)
    parts = {"[Content_Types].xml": types, "_rels/.rels": root_rels, "xl/workbook.xml": workbook,
             "xl/_rels/workbook.xml.rels": rels,
             "xl/worksheets/sheet1.xml": _sheet(pois, titel, revision, stand, zusatz, einheit,
                                                genauigkeit),
             "xl/styles.xml": _styles()}
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, element in parts.items():
            archive.writestr(name, ET.tostring(element, encoding="utf-8", xml_declaration=True))
    return output.getvalue()
