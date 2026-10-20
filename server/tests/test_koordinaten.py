"""Einheitenangabe Koordinaten-/Höhensystem (F23), Fälle wie web/tests/koordinaten.test.ts."""
from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

import pytest
from pakete import lege_paket

from scanview_server import export, maengelliste
from scanview_server.katalog import Katalog
from scanview_server.koordinaten import einheit


def crs(wkt: str | None, vd: str | None = None) -> dict[str, str | None]:
    return {"wkt": wkt, "verticalDatum": vd, "declaredBy": "test"}


@pytest.mark.parametrize(("wkt", "vd", "erwartet"), [
    ("EPSG:25833", "dhhn2016", "[m, ETRS89/UTM33, NHN]"),
    ("EPSG:25833", "dhhn92", "[m, ETRS89/UTM33, NHN (DHHN92)]"),
    ("EPSG:25833", "ellipsoidal-etrs89", "[m, ETRS89/UTM33, ellips.]"),
    ("EPSG:25833", None, "[m, ETRS89/UTM33, Höhe ?]"),
    ("EPSG:25833", "Amsterdam", "[m, ETRS89/UTM33, Amsterdam]"),
    ("EPSG:25832", None, "[m, ETRS89/UTM32, Höhe ?]"),
    ('PROJCS["ETRS89 / UTM zone 33N",GEOGCS["ETRS89"]]', None,
     "[m, ETRS89 / UTM zone 33N, Höhe ?]"),
    (f'PROJCRS["{"x" * 50}"]', None, f"[m, {'x' * 32}…, Höhe ?]"),
    # Lokal: kein Höhensystem, auch wenn eins eingetragen ist; nicht auswertbar = lokal.
    (None, "dhhn2016", "[m, lokal]"),
    ('LOCAL_CS["Kirche"]', None, "[m, lokal]"),
    ("irgendwas", None, "[m, lokal]"),
    ("EPSG:4326", "dhhn2016", "[m, lokal]"),
])
def test_einheit(wkt: str | None, vd: str | None, erwartet: str) -> None:
    assert einheit(crs(wkt, vd)) == erwartet


def test_einheit_ohne_crs_angabe() -> None:
    assert einheit(None) == "[m, lokal]"


def test_katalog_traegt_einheit(tmp_path: Path) -> None:
    lege_paket(tmp_path, "utm", crs_wkt="EPSG:25833")
    lege_paket(tmp_path, "lokal")
    katalog = Katalog(tmp_path)
    katalog.laden()
    karten = {k.id: k for k in katalog.alle()}
    assert karten["utm"].einheit == "[m, ETRS89/UTM33, Höhe ?]"
    assert karten["lokal"].einheit == "[m, lokal]"


def test_csv_kopf_und_xlsx_kopfzeile() -> None:
    kopf = export.csv_text([], [], einheit="[m, ETRS89/UTM33, NHN]")[1:].split("\r\n")[0].split(";")
    spalte = export.KOPF.index(export.KOORDINATEN_KOPF)
    assert kopf[spalte] == "Koordinaten X Y Z [m, ETRS89/UTM33, NHN]"
    assert export.csv_text([], [])[1:].split("\r\n")[0].split(";") == export.KOPF
    mappe = maengelliste.xlsx([], "P", 1, 0, einheit="[m, lokal]")
    with zipfile.ZipFile(io.BytesIO(mappe)) as z:
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    erste = ["".join(c.itertext()) for c in sheet.findall("s:sheetData/s:row", ns)[0]]
    assert erste[5:] == ["Koordinaten", "[m, lokal]"]
