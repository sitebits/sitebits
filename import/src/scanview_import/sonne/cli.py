"""`scanview-sonne`: Verschattungs- und Ertragsbericht für eine Fläche, PVcalc-Gegenprobe."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import numpy as np
from pyproj import CRS, Transformer

from ..berichtsatz import pdf_drucken
from ..manifest_io import read_manifest
from . import bericht, ertrag, garten, garten_bericht, pvgis
from .rechnung import AUFNAHME_FELDER, auswerten, ergebnis_hash
from .richtungskarte import Raster

CACHE = Path.home() / ".cache" / "scanview-sonne" / "pvgis"


def _anlage(a: argparse.Namespace) -> ertrag.Anlage:
    return ertrag.Anlage(
        modul_wp=a.modul_wp,
        anzahl=a.anzahl,
        kappung_w=a.kappung_w or None,
        verluste_prozent=a.verluste,
        modulmodell=a.modulmodell,
    )


def _bericht(a: argparse.Namespace) -> int:
    aus: Path = a.aus
    aus.mkdir(parents=True, exist_ok=True)
    rr = Raster(a.richtung_az, a.richtung_h)
    w = auswerten(
        a.paket,
        a.flaeche,
        _anlage(a),
        a.cache,
        raster_m=a.raster,
        richtungsraster=rr,
        ebene=a.ebene,
        offline=a.offline,
        neigung=a.neigung,
        azimut_geo=a.azimut,
        aufnahme_angaben={k: getattr(a, k) for k in AUFNAHME_FELDER},
    )
    e = w.ergebnis
    sha = ergebnis_hash(e)
    (aus / "ergebnis.json").write_text(
        json.dumps(e, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    )
    with (aus / "stichpunkte.csv").open("w") as f:
        f.write("x;y;z;einstrahlung_kwh_m2;anteil_unverschattet\n")
        welt = (
            w.flaeche.punkte
            + np.asarray(e["eingaben"]["ecken_welt"]).mean(axis=0)
            - w.flaeche.ecken.mean(axis=0)
        )
        for p, k, r in zip(welt, w.punkt_kwh_m2, w.punkt_anteil, strict=True):
            f.write(f"{p[0]:.3f};{p[1]:.3f};{p[2]:.3f};{k:.1f};{r:.4f}\n")
    _geojson(
        aus / "stichpunkte.geojson",
        welt,
        [
            {"einstrahlung_kwh_m2": round(float(k), 1), "anteil": round(float(r), 4)}
            for k, r in zip(w.punkt_kwh_m2, w.punkt_anteil, strict=True)
        ],
        e["ort"]["crs"],
    )
    bereich = (
        float(np.floor(w.punkt_kwh_m2.min() / 50) * 50),
        float(np.ceil(w.punkt_kwh_m2.max() / 50) * 50),
    )
    png = bericht.waermekarte(w.flaeche, w.punkt_kwh_m2, a.raster, bereich)
    (aus / "waermekarte.png").write_bytes(png)
    lauf = w.lauf.als_dict()
    lage = bericht.lagebild(
        a.paket,
        read_manifest(a.paket),
        np.asarray(e["eingaben"]["ecken_welt"]),
        e["ort"]["konvergenz_grad"],
    )
    erstellt = dt.datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
    html_datei = aus / "bericht.html"
    html_datei.write_text(bericht.html_bericht(e, png, lage, bereich, lauf, sha, erstellt))
    if not a.ohne_pdf:
        pdf_drucken(html_datei, aus / "bericht.pdf")
    lauf["ergebnis_sha256"] = sha
    (aus / "lauf.json").write_text(json.dumps(lauf, indent=1) + "\n")
    j = e["ertrag"]["jahr"]
    print(
        f"Ertrag {j['mittel']:.0f} kWh/a (P50 {j['p50']:.0f}, P90 {j['p90']:.0f}), unverschattet "
        f"{j['unverschattet']:.0f}; {e['flaeche']['stichpunkte']} Stichpunkte; "
        f"{lauf['gesamt_s']} s, "
        f"{lauf['ram_spitze_mb']} MB; Ergebnis {sha[:16]}"
    )
    return 0


def _geojson(
    pfad: Path, welt: np.ndarray, eigenschaften: list[dict[str, object]], crs: str
) -> None:
    """Stichpunkte als GeoJSON (WGS84, Länge/Breite, Höhe im Paket-Höhensystem)."""
    t = Transformer.from_crs(CRS.from_user_input(crs), CRS.from_epsg(4326), always_xy=True)
    lon, lat = t.transform(welt[:, 0], welt[:, 1])
    merkmale = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(x, 8), round(y, 8), round(z, 3)]},
            "properties": e,
        }
        for x, y, z, e in zip(lon, lat, welt[:, 2], eigenschaften, strict=True)
    ]
    pfad.write_text(json.dumps({"type": "FeatureCollection", "features": merkmale}) + "\n")


def _garten(a: argparse.Namespace) -> int:
    aus: Path = a.aus
    aus.mkdir(parents=True, exist_ok=True)
    w = garten.auswerten_garten(
        a.paket,
        a.flaeche,
        a.cache,
        raster_m=a.raster,
        richtungsraster=Raster(a.richtung_az, a.richtung_h),
        ebene=a.ebene,
        offline=a.offline,
        fenster_m=a.fenster,
        aufnahme_angaben={k: getattr(a, k) for k in AUFNAHME_FELDER},
    )
    e = w.ergebnis
    sha = ergebnis_hash(e)
    (aus / "ergebnis.json").write_text(
        json.dumps(e, ensure_ascii=False, indent=1, sort_keys=True) + "\n"
    )
    welt = (
        w.flaeche.punkte
        + np.asarray(e["eingaben"]["ecken_welt"]).mean(axis=0)
        - w.flaeche.ecken.mean(axis=0)
    )
    namen = [garten.KATEGORIEN[i] for i in w.kategorie]
    namen_zu = [garten.KATEGORIEN[i] for i in w.kategorie_zu]
    unsicher = garten.unsicher(w.kategorie, w.grenzbereich, w.kategorie_zu, w.grenzbereich_zu)
    spalten = list(
        zip(
            welt,
            w.stunden_monat,
            w.mittsommer,
            namen,
            w.mittsommer_zu,
            namen_zu,
            unsicher,
            w.himmelssicht,
            strict=True,
        )
    )
    with (aus / "stichpunkte.csv").open("w") as f:
        monate = ";".join(f"h_tag_{m:02d}" for m in range(1, 13))
        f.write(
            f"x;y;z;{monate};mittsommer_h;lichtlage;mittsommer_h_kronen_zu;lichtlage_kronen_zu;"
            "unsicher;himmelssicht\n"
        )
        for p, h, ms, k, ms_zu, k_zu, u, him in spalten:
            werte = ";".join(f"{x:.2f}" for x in h)
            f.write(
                f"{p[0]:.3f};{p[1]:.3f};{p[2]:.3f};{werte};{ms:.2f};{k};{ms_zu:.2f};{k_zu};"
                f"{int(u)};{him:.4f}\n"
            )
    _geojson(
        aus / "stichpunkte.geojson",
        welt,
        [
            {
                "mittsommer_h": round(float(ms), 2),
                "lichtlage": k,
                "mittsommer_h_kronen_zu": round(float(ms_zu), 2),
                "lichtlage_kronen_zu": k_zu,
                "unsicher": bool(u),
                "himmelssicht": round(float(him), 4),
            }
            for _, _, ms, k, ms_zu, k_zu, u, him in spalten
        ],
        e["ort"]["crs"],
    )
    bereich = (0.0, float(max(np.ceil(w.mittsommer.max()), 1.0)))
    bilder = {
        "stunden": bericht.waermekarte(w.flaeche, w.mittsommer, a.raster, bereich),
        "stunden_kronen_zu": bericht.waermekarte(w.flaeche, w.mittsommer_zu, a.raster, bereich),
        "kategorien": bericht.waermekarte(
            w.flaeche,
            w.mittsommer_zu,
            a.raster,
            bereich,
            rgb=garten_bericht.kategorie_farben(w.kategorie_zu, unsicher),
        ),
    }
    for name, png in bilder.items():
        (aus / f"{name}.png").write_bytes(png)
    lauf = w.lauf.als_dict()
    lage = bericht.lagebild(
        a.paket,
        read_manifest(a.paket),
        np.asarray(e["eingaben"]["ecken_welt"]),
        e["ort"]["konvergenz_grad"],
    )
    erstellt = dt.datetime.now().astimezone().strftime("%d.%m.%Y %H:%M")
    html_datei = aus / "bericht.html"
    html_datei.write_text(
        garten_bericht.html_bericht(e, bilder, lage, bereich, lauf, sha, erstellt)
    )
    if not a.ohne_pdf:
        pdf_drucken(html_datei, aus / "bericht.pdf")
    lauf["ergebnis_sha256"] = sha
    (aus / "lauf.json").write_text(json.dumps(lauf, indent=1) + "\n")
    for titel, li in (("Kronen geschlossen", e["licht_kronen_zu"]), ("Modell", e["licht"])):
        anteile = ", ".join(f"{k['name']} {100 * k['anteil']:.1f} %" for k in li["kategorien"])
        print(f"{titel}: Mittsommer {li['mittsommer_h']['mittel']:.1f} h/Tag; {anteile}")
    print(
        f"unsicher {100 * e['unsicher_anteil']:.1f} %; {e['flaeche']['stichpunkte']} Punkte; "
        f"{lauf['gesamt_s']} s, {lauf['ram_spitze_mb']} MB; Ergebnis {sha[:16]}"
    )
    return 0


def _gegenprobe(a: argparse.Namespace) -> int:
    p = pvgis.Pvgis(a.cache, offline=a.offline)
    reihe, abruf = pvgis.jahresreihe(p, a.breite, a.laenge)
    tmy, _ = pvgis.typjahr(p, a.breite, a.laenge)
    hoehe = float(abruf.antwort["inputs"]["location"]["elevation"])
    anlage = ertrag.Anlage(1000.0, verluste_prozent=a.verluste, modulmodell=a.modulmodell)
    print("Neigung/Azimut(pvlib)  PVcalc E_y  Reihe      TMY        H(i) PVGIS  H(i) pvlib")
    for lage in a.lagen:
        neig, az = (float(x) for x in lage.split("/"))
        pc = pvgis.pvcalc(p, a.breite, a.laenge, neig, az, 1.0, a.verluste).antwort["outputs"][
            "totals"
        ]["fixed"]
        r = ertrag.rechnen(reihe, a.breite, a.laenge, hoehe, neig, az, anlage)
        t = ertrag.rechnen(tmy, a.breite, a.laenge, hoehe, neig, az, anlage)
        e_r = float(r["p_ac"].groupby(r.index.year).sum().mean() / 1000)
        e_t = float(t["p_ac"].sum() / 1000)
        h_r = float(r["poa"].groupby(r.index.year).sum().mean() / 1000)
        ey, hy = pc["E_y"], pc["H(i)_y"]
        print(
            f"{neig:>5.0f}/{az:<5.0f}          {ey:8.1f}  {e_r:7.1f} {e_r / ey - 1:+6.1%}"
            f"  {e_t:7.1f} {e_t / ey - 1:+6.1%}  {hy:8.1f}  {h_r:7.1f} {h_r / hy - 1:+6.1%}"
        )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="scanview-sonne", description=__doc__)
    sub = ap.add_subparsers(dest="befehl", required=True)
    gemeinsam = argparse.ArgumentParser(add_help=False)
    gemeinsam.add_argument(
        "--cache", type=Path, default=CACHE, help="PVGIS-Cache (Antwort, URL, Abrufdatum)"
    )
    gemeinsam.add_argument("--offline", action="store_true", help="nur Cache, kein Abruf")
    anlage = argparse.ArgumentParser(add_help=False)
    anlage.add_argument(
        "--verluste", type=float, default=14.0, help="Systemverluste %% inkl. Wechselrichter"
    )
    anlage.add_argument("--modulmodell", choices=("huld", "pvwatts"), default="huld")
    aufnahme_felder = argparse.ArgumentParser(add_help=False)
    for feld, hilfe in (
        ("datum", "Aufnahmedatum und -uhrzeit"),
        ("geraet", "Kamera/Drohne"),
        ("flughoehe", "Flughöhe und Bodenauflösung"),
        ("wetter", "Wetter bei der Aufnahme"),
        ("belaubung", "Belaubung (z. B. voll, teilweise, kahl)"),
        ("pilot", "Pilot"),
        ("flugrecht", "Flugrechtsgrundlage (z. B. A1, Zustimmungen)"),
    ):
        aufnahme_felder.add_argument(f"--{feld}", help=f"Aufnahme: {hilfe} (sonst „fehlt noch“)")
    paket_flaeche = argparse.ArgumentParser(add_help=False)
    paket_flaeche.add_argument("--paket", type=Path, required=True)
    paket_flaeche.add_argument(
        "--flaeche",
        type=Path,
        required=True,
        help="Flächenmessung (JSON, Ecken in Weltkoordinaten)",
    )
    paket_flaeche.add_argument("--aus", type=Path, required=True)
    paket_flaeche.add_argument(
        "--richtung-az", type=float, default=2.0, help="Richtungsraster Azimut in Grad"
    )
    paket_flaeche.add_argument(
        "--richtung-h", type=float, default=1.0, help="Richtungsraster Höhe in Grad"
    )
    paket_flaeche.add_argument("--ebene", help="Mesh-Ebene (id), sonst die erste")
    paket_flaeche.add_argument("--ohne-pdf", action="store_true")
    b = sub.add_parser(
        "bericht",
        parents=[gemeinsam, anlage, paket_flaeche, aufnahme_felder],
        help="Bericht für eine Fläche",
    )
    b.add_argument("--modul-wp", type=float, required=True)
    b.add_argument("--anzahl", type=int, default=1)
    b.add_argument(
        "--kappung-w", type=float, default=None, help="AC-Kappung in W (Steckersolar 800)"
    )
    b.add_argument("--raster", type=float, default=0.1, help="Stichpunktabstand in m")
    b.add_argument("--neigung", type=float, help="Neigung überschreiben (Grad)")
    b.add_argument("--azimut", type=float, help="Azimut geografisch überschreiben (Nord 0, Ost 90)")
    b.set_defaults(f=_bericht)
    gt = sub.add_parser(
        "garten",
        parents=[gemeinsam, paket_flaeche, aufnahme_felder],
        help="Sonnenbericht Garten: Sonnenstunden und Lichtlage je Bodenpunkt",
    )
    gt.add_argument("--raster", type=float, default=0.2, help="Stichpunktabstand in m")
    gt.add_argument(
        "--fenster",
        type=float,
        default=1.0,
        help="Boden nur ±m um die Ebene durch die Ecken suchen",
    )
    gt.set_defaults(f=_garten)
    g = sub.add_parser(
        "gegenprobe",
        parents=[gemeinsam, anlage],
        help="pvlib-Kette gegen PVGIS PVcalc (unverschattet)",
    )
    g.add_argument("--breite", type=float, required=True)
    g.add_argument("--laenge", type=float, required=True)
    g.add_argument("lagen", nargs="+", help="Neigung/Azimut (pvlib, Süd 180), z. B. 35/180 90/270")
    g.set_defaults(f=_gegenprobe)
    a = ap.parse_args(argv)
    try:
        return int(a.f(a))
    except (ValueError, RuntimeError) as fehler:
        print(f"scanview-sonne: {fehler}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
