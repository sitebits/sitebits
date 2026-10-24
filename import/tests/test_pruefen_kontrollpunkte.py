"""Katalogprüfer P3: Kontrollpunkte als CSV gegen bekannte Wahrheit, Negativkontrollen je Falle."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from test_pruefen import PROFIL, _lieferung, _summen
from test_pruefen_flaeche import X0, Y0, Z0, _dichte_laz

from scanview_import.pruefen import cli, pruefarten
from scanview_import.pruefen.kontrollpunkte import KontrollpunktFehler, lese

CRS = "EPSG:25833+7837"  # wie die Testwolke: ETRS89/UTM 33 + DHHN2016


def _punkte(
    n: int = 36,
    rolle: str = "check",
    dh: float = 0.03,
    sigma_h: float | None = 0.02,
    lage: tuple[float, float] | None = (0.01, 0.0),
    sigma_lage: float | None = 0.01,
    massstab: float = 1.0,
) -> list[dict[str, Any]]:
    """n Punkte im 1-m-Raster außerhalb der Testlücke; Soll-Höhe = Wolke minus dh."""
    out = []
    for k in range(n):
        e, nn = X0 + 0.5 + (k % 6), Y0 + 0.5 + (k // 6) % 6
        p: dict[str, Any] = {"id": f"K{k:02d}", "rolle": rolle, "e": e, "n": nn, "h": Z0 - dh}
        p |= {"sigma_hoehe": sigma_h, "sigma_lage": sigma_lage}
        if lage is not None:
            p |= {"ist_e": X0 + (e - X0) * massstab + lage[0], "ist_n": nn + lage[1]}
        out.append(p)
    return out


def _csv(punkte: list[dict[str, Any]], crs: str | None = CRS, trenner: str = ";") -> str:
    spalten = ["id", "rolle", "e", "n", "h", "sigma_lage", "sigma_hoehe", "ist_e", "ist_n"]
    spalten = [s for s in spalten if any(s in p for p in punkte)]

    def feld(v: Any) -> str:
        if v is None:
            return ""
        t = f"{v:.4f}" if isinstance(v, float) else str(v)
        return t.replace(".", ",") if trenner == ";" else t

    zeilen = [f"# crs = {crs}"] if crs else []
    zeilen.append(trenner.join(spalten))
    zeilen += [trenner.join(feld(p.get(s)) for s in spalten) for p in punkte]
    return "\n".join(zeilen) + "\n"


def _lf(tmp_path: Path, csv: str | None) -> Path:
    o = _lieferung(tmp_path / "lieferung", summen=False)
    _dichte_laz(o / "wolke" / "dicht.laz")
    if csv is not None:
        (o / "kontrollpunkte.csv").write_text(csv)
    _summen(o)
    return o


def _z(e: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {z["id"]: z for z in e["zeilen"]}


def test_hoehe_lage_massstab_mit_bekannter_wahrheit(tmp_path: Path) -> None:
    fern = [
        {"id": "S1", "rolle": "check", "e": X0 + 30, "n": Y0, "h": Z0, "sigma_hoehe": 0.02,
         "sigma_lage": 0.01, "ist_e": X0 + 30.01, "ist_n": Y0},
        {"id": "S2", "rolle": "check", "e": X0 - 30, "n": Y0, "h": Z0, "sigma_hoehe": 0.02,
         "sigma_lage": 0.01, "ist_e": X0 - 29.99, "ist_n": Y0},
    ]  # fmt: skip
    lf = _lf(tmp_path, _csv(_punkte() + fern))
    z = _z(cli.pruefen(PROFIL, lf))
    # Höhe: alle 36 Punkte um +0,03 m, sigma 0,02 m → √(0,03² + 0,02²) = 0,036 m ≤ 0,05 m
    assert z["L03"]["urteil"] == pruefarten.ERFUELLT, z["L03"]["grund"]
    assert z["L03"]["grund"] == "RMSE_V 0,036 m ≤ 0,050 m an 36 Kontrollpunkten"
    nicht = [t["id"] for t in z["L03"]["tabelle"] if t["status"] != "gemessen"]
    assert nicht == ["S1", "S2"]  # außerhalb der Wolke: einzeln ausgewiesen, nicht gemittelt
    # Lage: Versatz 0,01 m, sigma 0,01 m → √2·0,01 = 0,014 m ≤ 0,025 m
    assert z["L02"]["urteil"] == pruefarten.ERFUELLT, z["L02"]["grund"]
    assert z["L02"]["grund"].startswith("RMSE_H 0,014 m")
    assert "nicht nachgemessen" in z["L02"]["belege"][1]
    # Maßstab: reiner Versatz, Strecke S1 bis S2 (60 m) unverändert
    assert z["L05"]["urteil"] == pruefarten.ERFUELLT, z["L05"]["grund"]


def test_passpunkte_zaehlen_nie(tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(rolle="passpunkt")))))
    assert z["L03"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L03"]["grund"].startswith("nur 0 unabhängige")
    assert "36 Passpunkte (zählen nicht" in z["L03"]["belege"][0]


def test_zu_wenige_kontrollpunkte(tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(n=29)))))
    assert z["L03"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L03"]["grund"].startswith("nur 29 unabhängige")


def test_punkt_ausserhalb_drueckt_unter_das_minimum(tmp_path: Path) -> None:
    punkte = _punkte(n=30)
    punkte[0] |= {"e": X0 + 50}
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(punkte))))
    assert z["L03"]["grund"].startswith("nur 29 unabhängige, messbare")


def test_ausreisser_bleibt_im_rmse(tmp_path: Path) -> None:
    punkte = _punkte()
    punkte[5]["h"] = Z0 - 0.5
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(punkte))))
    assert z["L03"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "größte Abweichung 0,500 m an K05" in z["L03"]["belege"][-1]


def test_fehlendes_sigma(tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(sigma_h=None)))))
    assert z["L03"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "Genauigkeit (\u03c3) der Kontrollpunkte fehlt bei 36" in z["L03"]["grund"]


def test_anderes_hoehensystem_wird_nicht_umgerechnet(tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(), crs="EPSG:25833+5783"))))
    assert z["L03"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "dhhn2016 ≠ Kontrollpunkte dhhn92" in z["L03"]["grund"]


def test_ohne_deklaration_aus_der_lieferung_nicht_erfuellt(tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(), crs=None))))
    assert z["L03"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert "Bezugssystem nicht deklariert" in z["L03"]["grund"]
    (tmp_path / "b").mkdir()
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path / "b", _csv(_punkte(), crs="EPSG:25833"))))
    assert "kein bekanntes Höhensystem" in z["L03"]["grund"]


def test_liste_des_auftraggebers_per_cli(tmp_path: Path) -> None:
    lf = _lf(tmp_path, None)
    kp = tmp_path / "ag.csv"
    kp.write_text(_csv(_punkte(), crs=None))
    e = cli.pruefen(PROFIL, lf, kontrollpunkte=kp, kontrollpunkte_crs=CRS)
    assert _z(e)["L03"]["urteil"] == pruefarten.ERFUELLT
    assert "Kontrollpunkte ag.csv" in e["eingaenge_extern_sha256"]
    # ohne Bezugssystem: Eingabefehler, Exit 2
    args = [str(lf), "--profil", "bestandserfassung", "--aus", str(tmp_path / "a")]
    assert cli.main([*args, "--kontrollpunkte", str(kp)]) == 2
    aus = tmp_path / "b"
    args[-1] = str(aus)
    assert cli.main([*args, "--kontrollpunkte", str(kp), "--kontrollpunkte-crs", CRS]) == 1
    assert (aus / "L03-messwerte.csv").read_text().startswith("id;e;n;h;sigma_hoehe;punkte")


def test_dezimalkomma_bei_komma_trenner_bricht_ab(tmp_path: Path) -> None:
    text = f"# crs = {CRS}\nid,rolle,e,n,h\nK1,check,365800,5,5806700,5,39,97\n"
    with pytest.raises(KontrollpunktFehler, match="Felder"):
        lese(text, None, "x.csv", "Test")
    ok = lese(text.replace("365800,5,5806700,5,39,97", "365800.5,5806700.5,39.97"), None, "", "")
    assert ok.punkte[0].soll == (365800.5, 5806700.5, 39.97)


def test_ohne_ist_lage_ist_lage_nicht_pruefbar(tmp_path: Path) -> None:
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(lage=None, sigma_lage=None)))))
    assert z["L02"]["urteil"] == pruefarten.NICHT_PRUEFBAR
    assert z["L02"]["grund"].startswith("Grundlage fehlt: Ist-Lage der Kontrollpunkte")
    assert z["L05"]["urteil"] == pruefarten.NICHT_PRUEFBAR
    assert z["L03"]["urteil"] == pruefarten.ERFUELLT


def test_massstabsfehler(tmp_path: Path) -> None:
    # Ist-Lage aller Punkte um 0,2 % gestreckt (um X0): jede Strecke ≥ 10 m ist 0,2 % zu lang
    fern = _punkte(n=2, massstab=1.002)
    fern[0] |= {"id": "S1", "e": X0 + 30, "ist_e": X0 + 30 * 1.002 + 0.01}
    fern[1] |= {"id": "S2", "e": X0 - 30, "ist_e": X0 - 30 * 1.002 + 0.01}
    z = _z(cli.pruefen(PROFIL, _lf(tmp_path, _csv(_punkte(massstab=1.002) + fern))))
    assert z["L05"]["urteil"] == pruefarten.NICHT_ERFUELLT
    assert z["L05"]["grund"].startswith("größte Abweichung 0,200 %")
    assert len(z["L05"]["tabelle"]) == 73  # 36 Punkte je zu S1 und S2, dazu S1 bis S2


def _dach(pfad: Path, klasse: int = 6) -> None:
    """Dachpunkte 5 m über dem Boden über der Testlücke (dort gibt es keinen Boden)."""
    import laspy
    import numpy as np
    from test_pruefen_flaeche import LUECKE, UTM

    a = np.arange(0.05, 2, 0.1)
    x, y = np.meshgrid(LUECKE[0] + a, LUECKE[1] + a)
    h = laspy.LasHeader(point_format=6, version="1.4")
    h.offsets, h.scales = [X0, Y0, Z0], [0.001, 0.001, 0.001]
    h.add_crs(UTM)
    las = laspy.LasData(h)
    las.x, las.y, las.z = x.ravel(), y.ravel(), np.full(x.size, Z0 + 5)
    las.classification = np.full(x.size, klasse, np.uint8)
    las.write(pfad)


def test_punkt_unter_dach_misst_nicht_das_dach(tmp_path: Path) -> None:
    punkte = _punkte()
    punkte[0] |= {"e": X0 + 7, "n": Y0 + 7}  # Mitte der Lücke, darüber nur Dach
    o = _lf(tmp_path, _csv(punkte))
    _dach(o / "wolke" / "dach.laz")
    _summen(o)
    z = _z(cli.pruefen(PROFIL, o))["L03"]
    k00 = next(t for t in z["tabelle"] if t["id"] == "K00")
    assert k00["status"].startswith("nicht messbar: 0 Punkte (Klasse 2)")
    assert z["grund"] == "RMSE_V 0,036 m ≤ 0,050 m an 35 Kontrollpunkten"


def _viewer_export(punkte: list[dict[str, Any]], crs: str = "epsg:25833+7837") -> str:
    """Wie der Export des Viewer-Panels „Kontrollpunkte“ (web/src/kontrollpunkte.ts): Quelle und
    Ebene nur als Kommentar, Dezimalpunkt, leere Ist-Zellen für ungepickte Punkte."""
    kopf = [
        f"# crs = {crs}",
        "# quelle = siteBits-Viewer 0.10.3+e066038, am Modell gepickt",
        "# ebene K00 = Punktwolke: cloud (grobe Stufe)",
        "id;rolle;e;n;h;sigma_lage;sigma_hoehe;ist_e;ist_n;ist_h",
    ]
    spalten = ("id", "rolle", "e", "n", "h", "sigma_lage", "sigma_hoehe", "ist_e", "ist_n", "ist_h")

    def feld(v: Any) -> str:
        return "" if v is None else f"{v:.3f}" if isinstance(v, float) else str(v)

    zeilen = [";".join(feld(p.get(s)) for s in spalten) for p in punkte]
    return "\n".join(kopf + zeilen) + "\n"


def test_am_modell_gepickt_wird_als_solches_ausgewiesen(tmp_path: Path) -> None:
    lf = _lf(tmp_path, None)
    kp = tmp_path / "kontrollpunkte-ist-test.csv"
    kp.write_text(_viewer_export(_punkte()))
    # Schreibweise der Deklaration egal: Datei „epsg:…“, Vorgabe „EPSG:…“
    e = cli.pruefen(PROFIL, lf, kontrollpunkte=kp, kontrollpunkte_crs=CRS)
    z = _z(e)
    assert z["L02"]["urteil"] == pruefarten.ERFUELLT, z["L02"]["grund"]
    b = " ".join(z["L02"]["belege"])
    assert "am Modell gepickt" in b and "nicht nachgemessen" not in b
    assert "Einordnung zur Orientierung (kein Urteil): BFR-Verm OGL3" in b  # 0,014 m
    assert "nicht nachgemessen" not in " ".join(z["L05"]["belege"])


def test_ohne_quellenangabe_bleibt_lieferantenwert(tmp_path: Path) -> None:
    text = _viewer_export(_punkte()).replace("# quelle = siteBits-Viewer", "# quelle = Tachymeter")
    liste = lese(text, None, "x.csv", "Test")
    assert not liste.am_modell_gepickt and liste.ist_quelle is not None


def test_bom_und_widerspruechliche_deklarationen(tmp_path: Path) -> None:
    text = _viewer_export(_punkte(n=2))
    assert len(lese("﻿" + text, None, "x.csv", "Test").punkte) == 2
    with pytest.raises(KontrollpunktFehler, match="widersprüchlich"):
        lese(text, "EPSG:25833+5783", "x.csv", "Test")
    with pytest.raises(KontrollpunktFehler, match="widersprüchlich"):
        lese("# crs = EPSG:25832+7837\n" + text, None, "x.csv", "Test")


@pytest.mark.parametrize(
    ("rmse", "lage", "erwartet"),
    [
        (0.20, True, "gröber als OGL1"),
        (0.05, True, "OGL2"),  # 5 cm erfüllt OGL2 (1,5 bis 5 cm) noch
        (0.06, True, "OGL1"),
        (0.02, True, "OGL2"),
        (0.0004, True, "feiner als OGL4"),
        (0.03, False, "OGH1"),
        (0.01, False, "OGH2"),
    ],
)
def test_einordnung_bfr_verm(rmse: float, lage: bool, erwartet: str) -> None:
    from scanview_import.pruefen.genauigkeitsklassen import einordnung

    assert f"BFR-Verm {erwartet} " in einordnung(rmse, lage)


def test_einordnung_loa_und_eckstein() -> None:
    from scanview_import.pruefen.genauigkeitsklassen import einordnung

    t = einordnung(0.02, lage=True)  # 2 · RMSE = 40 mm
    assert "USIBD LOA20" in t and "Eckstein Stufe IV" in t
    t = einordnung(0.024, lage=True)
    assert "Eckstein Stufe III" in t
    assert "Eckstein nur Stufe I" in einordnung(0.2, lage=True)
    assert "LOA50" in einordnung(0.0004, lage=False)
