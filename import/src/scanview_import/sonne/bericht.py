"""Bericht: Wärmekarte (Pillow), HTML ohne externe Ressourcen, PDF über headless Chromium."""

from __future__ import annotations

import base64
import html
import io
from pathlib import Path
from string import Template
from typing import Any

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from ..berichtsatz import _prozent, _tabelle, _z
from .flaeche import Flaeche
from .pvgis import QUELLE, QUELLE_URL

# viridis-Stützstellen (matplotlib, CC0)
FARBEN = np.array(
    [[68, 1, 84], [59, 82, 139], [33, 145, 140], [94, 201, 98], [253, 231, 37]], float
)
VORLAGE = Template((Path(__file__).parent / "bericht_vorlage.html").read_text(encoding="utf-8"))
MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def farbe(t: np.ndarray) -> np.ndarray:
    t = np.clip(t, 0, 1) * (len(FARBEN) - 1)
    i = np.minimum(np.floor(t).astype(int), len(FARBEN) - 2)
    w = (t - i)[..., None]
    return np.asarray((FARBEN[i] * (1 - w) + FARBEN[i + 1] * w).round(), dtype=np.uint8)


def waermekarte(
    fl: Flaeche,
    werte: np.ndarray,
    raster_m: float,
    bereich: tuple[float, float],
    breite_px: int = 900,
    rgb: np.ndarray | None = None,
) -> bytes:
    """PNG der Stichpunktwerte in der Flächenebene (u rechts, v oben), Umriss der Messung.

    `rgb` (P, 3) ersetzt die viridis-Farben, z. B. für Kategorien.
    """
    c = fl.ecken.mean(axis=0)
    poly = np.stack([(fl.ecken - c) @ fl.achse_u, (fl.ecken - c) @ fl.achse_v], axis=1)
    lo, hi = poly.min(axis=0) - raster_m, poly.max(axis=0) + raster_m
    massstab = breite_px / max(hi[0] - lo[0], 1e-6)
    hoehe_px = max(int((hi[1] - lo[1]) * massstab), 40)
    if hoehe_px > 1200:
        massstab *= 1200 / hoehe_px
        hoehe_px = 1200
    bild = Image.new("RGB", (int((hi[0] - lo[0]) * massstab) + 1, hoehe_px + 1), (255, 255, 255))
    zeichnen = ImageDraw.Draw(bild)
    t = (werte - bereich[0]) / max(bereich[1] - bereich[0], 1e-9)
    halb = raster_m / 2
    for (u, v), f in zip(fl.punkte_uv, farbe(t) if rgb is None else rgb, strict=True):
        x0, y0 = (u - halb - lo[0]) * massstab, (hi[1] - (v + halb)) * massstab
        zeichnen.rectangle(
            [x0, y0, x0 + raster_m * massstab, y0 + raster_m * massstab],
            fill=tuple(int(x) for x in f),
        )
    umriss = [((u - lo[0]) * massstab, (hi[1] - v) * massstab) for u, v in poly]
    zeichnen.line([*umriss, umriss[0]], fill=(20, 20, 20), width=2)
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG", optimize=False)
    return puffer.getvalue()


def lagebild(
    paket: Path,
    manifest: dict[str, Any],
    ecken_welt: np.ndarray,
    konvergenz: float,
    breite_px: int = 900,
) -> bytes | None:
    """Ausschnitt der Paket-Draufsicht um die Fläche mit Umriss, Nordpfeil, Maßstab (sonst None)."""
    tv = manifest.get("topview")
    if not tv or not (paket / tv["url"]).is_file():
        return None
    px_m = float(tv["pixelSizeM"])
    ox, oy = (float(x) for x in tv["upperLeftWorld"])
    pix = np.stack([(ecken_welt[:, 0] - ox) / px_m, (oy - ecken_welt[:, 1]) / px_m], axis=1)
    mitte = pix.mean(axis=0)
    halb = max(20.0 / px_m, 1.5 * float(np.ptp(pix, axis=0).max()))
    kasten = (
        int(mitte[0] - halb),
        int(mitte[1] - halb),
        int(mitte[0] + halb),
        int(mitte[1] + halb),
    )
    with Image.open(paket / tv["url"]) as quelle:
        grund = Image.new("RGB", (kasten[2] - kasten[0], kasten[3] - kasten[1]), (235, 237, 240))
        rgba = quelle.convert("RGBA").crop(kasten)
        grund.paste(rgba, (0, 0), rgba)
    f = breite_px / grund.width
    bild = grund.resize((breite_px, int(grund.height * f)), Image.Resampling.LANCZOS)
    zeichnen = ImageDraw.Draw(bild)
    umriss = [((u - kasten[0]) * f, (v - kasten[1]) * f) for u, v in pix]
    zeichnen.line([*umriss, umriss[0]], fill=(255, 255, 255), width=7)
    zeichnen.line([*umriss, umriss[0]], fill=(200, 20, 60), width=4)
    schrift = ImageFont.load_default(size=22)
    # Nordpfeil: geografisch Nord liegt `konvergenz` Grad im Uhrzeigersinn von Gitter-Nord (oben).
    a = np.radians(konvergenz)
    fuss = np.array([bild.width - 50.0, 120.0])
    zeichnen.ellipse(
        [fuss[0] - 42, fuss[1] - 100, fuss[0] + 42, fuss[1] + 14], fill=(255, 255, 255)
    )
    spitze = fuss + 70 * np.array([np.sin(a), -np.cos(a)])
    zeichnen.line([tuple(fuss), tuple(spitze)], fill=(20, 20, 20), width=5)
    zeichnen.polygon(
        [tuple(spitze), tuple(spitze + [-10, 22]), tuple(spitze + [10, 22])], fill=(20, 20, 20)
    )
    zeichnen.text((spitze[0] - 7, spitze[1] - 30), "N", fill=(20, 20, 20), font=schrift)
    meter = 10.0 if halb * px_m > 15 else 5.0
    laenge = meter / px_m * f
    x0, y0 = 30.0, bild.height - 40.0
    zeichnen.rectangle([x0 - 6, y0 - 34, x0 + laenge + 40, y0 + 14], fill=(255, 255, 255))
    zeichnen.line([(x0, y0), (x0 + laenge, y0)], fill=(20, 20, 20), width=6)
    zeichnen.text((x0, y0 - 30), f"{meter:.0f} m", fill=(20, 20, 20), font=schrift)
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG")
    return puffer.getvalue()


def _balken(monate: list[dict[str, float]], frei: list[dict[str, float]]) -> str:
    """SVG-Balken je Monat: Mittel (Balken), P90 (Strich), unverschattet (Rahmen)."""
    b, h, rand = 640, 170, 34
    roh = max(max(m["mittel"] for m in frei), 1e-9)
    stufe = 10 ** np.floor(np.log10(roh / 4))
    schritt = next(k * stufe for k in (1, 2, 2.5, 5, 10) if k * stufe * 4 >= roh)
    top = 4 * schritt
    breite = (b - rand) / 12
    teile = [
        f'<svg viewBox="0 -8 {b} {h + 32}" role="img" aria-label="Ertrag je Monat" class="balken">'
    ]
    for i in range(5):
        y = h - h * i / 4
        teile.append(
            f'<line x1="{rand}" x2="{b}" y1="{y:.1f}" y2="{y:.1f}" class="gitter"/>'
            f'<text x="{rand - 4}" y="{y + 4:.1f}" text-anchor="end">{_z(top * i / 4)}</text>'
        )
    for i, (m, f) in enumerate(zip(monate, frei, strict=True)):
        x = rand + i * breite + breite * 0.15
        w = breite * 0.7
        hf, hm, h9 = (h * f["mittel"] / top, h * m["mittel"] / top, h * m["p90"] / top)
        teile.append(
            f'<rect x="{x:.1f}" y="{h - hf:.1f}" width="{w:.1f}" height="{hf:.1f}" class="frei"/>'
        )
        teile.append(
            f'<rect x="{x:.1f}" y="{h - hm:.1f}" width="{w:.1f}" height="{hm:.1f}" class="mittel"/>'
        )
        teile.append(
            f'<line x1="{x:.1f}" x2="{x + w:.1f}" y1="{h - h9:.1f}" y2="{h - h9:.1f}" class="p90"/>'
        )
        teile.append(
            f'<text x="{x + w / 2:.1f}" y="{h + 16}" text-anchor="middle">{MONATE[i]}</text>'
        )
    teile.append("</svg>")
    return "".join(teile)


GRENZEN = [
    "Schätzung, keine zugesicherte Eigenschaft. Grundlage sind langjährige Wetterdaten; "
    "einzelne Jahre weichen ab (Band P50/P90).",
    "Vegetation gilt als undurchsichtig und ganzjährig belaubt, so wie sie im Modell steht: "
    "Im Winter wird die Verschattung durch Laubbäume überschätzt. Was im Modell fehlt "
    "(z. B. bereinigte Bäume), verschattet nicht.",
    "Teilverschattung wird über den Mittelwert der Stichpunkte gerechnet. Bei Modulen mit "
    "Bypassdioden kann schon ein kleiner Schatten mehr kosten; die Schätzung ist dann zu günstig.",
    "Fernhorizont außerhalb des Modells aus dem PVGIS-Geländemodell (90-m-Raster, 7,5°-Schritte); "
    "Gebäude und Bäume außerhalb des Modells fehlen.",
    "Neigung und Ausrichtung stammen aus dem Modell. Ohne RTK-Bezug kann das Modell geneigt sein "
    "(Erfahrungswert bis ≈ 0,8°); das verschiebt Neigung und Azimut der Fläche entsprechend.",
    "Bodenreflexion wird unverschattet mit Albedo 0,2 gerechnet; Spektraleffekte sind nicht "
    "modelliert.",
    "Kein Ertragsversprechen und keine Planung: keine Statik, keine Elektro- oder Stringplanung, "
    "keine Anmeldung oder Normprüfung der Anlage.",
    "Wechselrichter-Kappung aus Stundenmitteln: kurze Spitzen innerhalb einer Stunde fehlen, "
    "der Kappungsverlust ist eher unterschätzt.",
    "Lage und Maßstab des Modells sind ohne Genauigkeitsnachweis nicht belegt (Modellmaß).",
    "Nordfassaden: Das Diffusmodell (Perez) liegt dort deutlich über PVGIS (Gegenprobe senkrecht "
    "Nord: +17 %).",
]


def _monatszeilen(e: dict[str, Any]) -> str:
    v, er = e["verschattung"], e["ertrag"]
    zeilen = []
    for vm, m, f in zip(v["monate"], er["monate"], er["monate_unverschattet"], strict=True):
        zellen = [
            MONATE[vm["monat"] - 1],
            _z(vm["sonne_vor_flaeche_h"]),
            _z(vm["besonnt_h"]),
            _prozent(vm["verschattet_anteil"], 0),
            _z(m["mittel"], 1),
            _z(m["p50"], 1),
            _z(m["p90"], 1),
            _z(f["mittel"], 1),
        ]
        zeilen.append("<tr>" + "".join(f"<td>{z}</td>" for z in zellen) + "</tr>")
    vor = sum(m["sonne_vor_flaeche_h"] for m in v["monate"])
    bes = sum(m["besonnt_h"] for m in v["monate"])
    j = er["jahr"]
    jahr = ["Jahr", _z(vor), _z(bes), _prozent(1 - bes / vor if vor else None, 0)]
    jahr += [_z(j["mittel"]), _z(j["p50"]), _z(j["p90"]), _z(j["unverschattet"])]
    zeilen.append("<tr>" + "".join(f"<td><b>{z}</b></td>" for z in jahr) + "</tr>")
    return "\n".join(zeilen)


def _flaeche(e: dict[str, Any]) -> str:
    fl, ort = e["flaeche"], e["ort"]
    rauheit = f"{_z(fl['rauheit_p95_m'] * 1000)} / {_z(fl['mindestabstand_m'] * 1000)} mm"
    gerechnet = (
        f"{_z(fl['gerechnet_neigung_grad'], 1)}° / {_z(fl['gerechnet_azimut_geo_grad'], 1)}°"
    )
    return _tabelle(
        [
            ("Fläche", f"{_z(fl['flaeche_m2'], 2)} m²"),
            ("Neigung", f"{_z(fl['neigung_grad'], 1)}°"),
            ("Azimut geografisch (Nord 0°, Ost 90°)", f"{_z(fl['azimut_geo_grad'], 1)}°"),
            ("Meridiankonvergenz", f"{_z(ort['konvergenz_grad'], 2)}°"),
            ("Mittl. Abweichung der Dreiecksnormalen", f"{_z(fl['normalen_abweichung_grad'], 1)}°"),
            ("Rauheit (p95) / Mindestabstand", rauheit),
            ("Stichpunkte (ohne Mesh)", f"{fl['stichpunkte']} ({fl['ohne_mesh']})"),
            ("Himmelssicht (kosinusgewichtet)", _prozent(e["verschattung"]["himmelssicht"])),
            ("Gerechnet mit Neigung / Azimut", gerechnet),
            ("Ort", f"{_z(ort['breite'], 4)}° N, {_z(ort['laenge'], 4)}° O"),
        ]
    )


def _kappung(an: dict[str, Any]) -> str:
    return f"{_z(an['kappung_w'])} W" if an["kappung_w"] else "keine"


def _anlage(e: dict[str, Any]) -> str:
    an, ein = e["eingaben"]["anlage"], e["eingaben"]
    temp = f"{html.escape(an['modulmodell'])} c-Si / Faiman {_z(an['faiman_u0'], 1)}, "
    temp += _z(an["faiman_u1"], 1)
    raster = ein["richtungsraster_grad"]
    return _tabelle(
        [
            ("Modulleistung \u00d7 Anzahl", f"{_z(an['modul_wp'])} Wp \u00d7 {an['anzahl']}"),
            ("AC-Kappung", _kappung(an)),
            ("Systemverluste (inkl. Wechselrichter)", f"{_z(an['verluste_prozent'], 1)} %"),
            ("Modulmodell / Temperatur", temp),
            (
                "Einfallswinkel / Albedo",
                f"Martin-Ruiz {_z(an['iam_ar'], 2)} / {_z(an['albedo'], 2)}",
            ),
            ("Transposition", "Perez, Anteile getrennt"),
            ("Richtungsraster", f"{_z(raster[0], 1)}° \u00d7 {_z(raster[1], 1)}°"),
        ]
    )


FEHLT = '<span class="fehlt">fehlt noch</span>'


def _gegenprobe(gp: dict[str, Any]) -> str:
    unverschattet = f"{_z(gp['pvlib_unverschattet_ohne_kappung_kwh'])} kWh/a"
    return _tabelle(
        [
            (
                "PVGIS PVcalc (unabhängiges Modell), gleiche Lage, ohne Verschattung",
                f"{_z(gp['pvgis_e_y_kwh'])} kWh/a",
            ),
            ("dieser Rechenweg, ohne Verschattung und Kappung", unverschattet),
            ("Abweichung (Abnahmegrenze ±5 %)", _prozent(gp["abweichung"])),
            ("Schattenkante gegen Foto mit Zeitstempel", FEHLT),
            ("Nachmessung am Ort (Ertrag einer echten Anlage)", FEHLT),
        ]
    )


def vertrauen(e: dict[str, Any]) -> tuple[str, str]:
    fl = e["flaeche"]
    luecke = fl["ohne_mesh"] / max(fl["stichpunkte"] + fl["ohne_mesh"], 1)
    if luecke > 0.01:
        return "unvollständig", f"({_prozent(luecke)} der Fläche ohne Modell)"
    return "geschätzt", "(Rechnung aus Modell und Wetterstatistik, nicht am Ort nachgemessen)"


def _frage_antwort(e: dict[str, Any]) -> tuple[str, str]:
    an, fl, j = e["eingaben"]["anlage"], e["flaeche"], e["ertrag"]["jahr"]
    anlage = f"{_z(an['modul_wp'] * an['anzahl'])} Wp"
    if an["kappung_w"]:
        anlage += f" und {_z(an['kappung_w'])} W Einspeisegrenze"
    frage = (
        f"Wie viel Sonne bekommt diese Fläche ({_z(fl['flaeche_m2'], 1)} m²), und wie viel Strom "
        f"bringt eine Anlage mit {anlage} hier im Jahr?"
    )
    verlust = 1 - j["mittel"] / j["unverschattet"] if j["unverschattet"] else 0.0
    antwort = (
        f"Im Mittel etwa {_z(j['mittel'])} kWh im Jahr, in 9 von 10 Jahren mindestens "
        f"{_z(j['p90'])} kWh; Schatten kosten rund {_prozent(verlust)}."
    )
    return frage, antwort


def _lage(e: dict[str, Any]) -> str:
    la = e["lage"]
    zeilen = [
        (
            "Koordinatensystem / Höhen",
            f"{html.escape(str(la['crs']))} / {html.escape(str(la['hoehensystem']))}",
        ),
        ("Maßstab", html.escape(la["genauigkeit"])),
        ("Flächenbezug", html.escape(la["flaechenbezug"])),
    ]
    ein = la["einpassung"]
    if ein:
        d = ein.get("drehung_grad") or {}
        zeilen.append(
            (
                "Einpassung (Drehung N/O-Achse)",
                f"{_z(d.get('um_nord_achse'), 2)}° / {_z(d.get('um_ost_achse'), 2)}°",
            )
        )
        zeilen.append(
            (
                "Restneigung gegen bDOM (unabhängig)",
                f"{_z(ein.get('rest_neigung_dsm_bdom_grad'), 2)}°",
            )
        )
        zeilen.append(
            (
                "Restabweichung Höhe (MAD) bDOM / DGM1",
                f"{_z(ein.get('rest_mad_dsm_bdom_m'), 2)} / "
                f"{_z(ein.get('rest_mad_dtm_dgm1_m'), 2)} m",
            )
        )
    else:
        zeilen.append(("Einpassung gegen DGM1/bDOM", "keine im Paket vermerkt; Neigung ungeprüft"))
    return _tabelle(zeilen)


def _aufnahme(e: dict[str, Any]) -> str:
    au = e["aufnahme"]
    an = au.get("angaben", {})

    def feld(schluessel: str) -> str:
        return html.escape(an[schluessel]) + " (Angabe)" if schluessel in an else FEHLT

    tage = au.get("fototage") or []
    if "datum" in an:
        datum = feld("datum")
    elif tage:
        datum = f"{html.escape(', '.join(tage))} (Fototage im Paket, Uhrzeit fehlt noch)"
    else:
        datum = FEHLT
    zeilen = [
        ("Datum", datum),
        ("Gerät", feld("geraet")),
        ("Flughöhe / Bodenauflösung", feld("flughoehe")),
        ("Wetter", feld("wetter")),
        ("Belaubung", feld("belaubung")),
        ("Pilot", feld("pilot")),
        ("Flugrechtsgrundlage", feld("flugrecht")),
        ("Fotos im Paket", str(au.get("fotos_im_paket", 0))),
        ("Quelle des Modells", html.escape(str(au["quelldatei"]))),
        ("Erzeugt mit", html.escape(str(au["erzeuger"])) if au["erzeuger"] else FEHLT),
    ]
    return _tabelle(zeilen)


def _kette(e: dict[str, Any], lauf: dict[str, Any]) -> str:
    ein, au = e["eingaben"], e["aufnahme"]
    vers = ", ".join(
        f"{html.escape(k)} {html.escape(str(x))}" for k, x in e["versionen"].items() if k != "git"
    )
    return _tabelle(
        [
            ("Rechenstand (git)", f"<code>{html.escape(e['versionen']['git'])}</code>"),
            ("Bibliotheken", vers + "; Raycast Intel Embree"),
            ("Photogrammetrie / Vermaschung", FEHLT),
            (
                "Modell im Paket",
                f"{html.escape(ein['mesh_url'])}, {ein['dreiecke']} Dreiecke, "
                f"SHA-256 <code>{ein['mesh_sha256'][:16]}…</code>",
            ),
            ("Quelle des Modells", f"SHA-256 <code>{str(au['quelle_sha256'])[:16]}…</code>"),
            (
                "Flächenmessung",
                f"{html.escape(ein['flaeche_datei'])}, SHA-256 "
                f"<code>{ein['flaeche_sha256'][:16]}…</code>",
            ),
            (
                "Dauer / Spitzen-RAM",
                f"{_z(lauf['gesamt_s'], 1)} s / {_z(lauf['ram_spitze_mb'])} MB",
            ),
            (
                "Wiederholbarkeit",
                "gleiche Eingaben ergeben dieselbe Ergebnis-Prüfsumme (s. Anhang)",
            ),
        ]
    )


def _schwellen(e: dict[str, Any]) -> str:
    fl = e["flaeche"]
    rr = e["eingaben"]["richtungsraster_grad"]
    an = e["eingaben"]["anlage"]
    punkte = [
        f"„Besonnt“: Sonnenmitte von der Fläche aus sichtbar, Mittel über alle Stichpunkte; "
        f"Richtungsraster {_z(rr[0], 1)}° \u00d7 {_z(rr[1], 1)}°, dazwischen linear gemittelt.",
        f"Selbstverschattung: Strahlen beginnen {_z(10)} mm über dem Modell und zählen Treffer "
        "erst ab "
        f"{_z(fl['mindestabstand_m'] * 1000)} mm (über der gemessenen Rauheit "
        f"{_z(fl['rauheit_p95_m'] * 1000)} mm, p95).",
        "Horizontaufhellung: Band bis 6° über dem Horizont (Annahme nach Perez 1990).",
        f"Verluste pauschal {_z(an['verluste_prozent'], 1)} % wie bei PVGIS, "
        f"Wechselrichter-Nennwirkungsgrad {_prozent(an['wr_wirkungsgrad'], 0)} darin enthalten.",
        "Diese Schwellen sind gesetzt, nicht genormt.",
    ]
    if fl["neigung_grad"] < 3:
        punkte.append(
            "Fläche fast waagerecht: Der Azimut ist kaum bestimmt und für den Ertrag ohne Belang."
        )
    return "".join(f"<li>{html.escape(x)}</li>" for x in punkte)


def _anhang() -> str:
    teile = [
        "ergebnis.json: alle Zahlen dieses Berichts mit Eingaben, Versionen und Quellen",
        "stichpunkte.csv / stichpunkte.geojson: jeder Stichpunkt mit Lage und Einstrahlung "
        "(Paket-CRS bzw. WGS84) zum Nachmessen",
        "waermekarte.png, lauf.json (Dauer, RAM, Prüfsumme)",
    ]
    aus = "".join(f"<li>{html.escape(t)}</li>" for t in teile)
    return aus + f"<li>Nur-Lese-Link zur Fläche im Viewer: {FEHLT}</li>"


DATENSCHUTZ = (
    "Gerechnet wird nur auf dem 3D-Modell (Geometrie) und der Flächenmessung; Fotos und "
    "Personendaten gehen nicht ein und stehen nicht im Bericht. Der Abruf bei PVGIS "
    "übermittelt nur den Ort (Breite/Länge, auf 4 Stellen gerundet). Die Rechnung lief am "
    "Arbeitsplatz; Ablageort und Löschfrist der Ergebnisse: "
)


def html_bericht(
    e: dict[str, Any],
    karte_png: bytes,
    lage_png: bytes | None,
    bereich: tuple[float, float],
    lauf: dict[str, Any],
    ergebnis_sha: str,
    erstellt: str,
) -> str:
    ein, er = e["eingaben"], e["ertrag"]
    j = er["jahr"]
    jahre = list(er["jahre"].values())
    abrufe = "".join(
        f"<li><code>{html.escape(q['werkzeug'])}</code>, abgerufen {html.escape(q['abgerufen'])}: "
        f"<span class=url>{html.escape(q['url'])}</span></li>"
        for q in e["quellen"]
    )
    frage, antwort = _frage_antwort(e)
    stufe, grund = vertrauen(e)
    frei_band = er.get("jahr_unverschattet_band", {})
    werte = {
        "paket": html.escape(str(ein["paket"])),
        "flaeche_datei": html.escape(ein["flaeche_datei"]),
        "erstellt": html.escape(erstellt),
        "git": html.escape(e["versionen"]["git"]),
        "frage": html.escape(frage),
        "antwort": html.escape(antwort),
        "vertrauen": stufe,
        "vertrauen_grund": html.escape(grund),
        "mittel": _z(j["mittel"]),
        "p50": _z(j["p50"]),
        "p90": _z(j["p90"]),
        "frei_p50": _z(frei_band.get("p50")),
        "frei_p90": _z(frei_band.get("p90")),
        "verlust": _prozent(1 - j["mittel"] / j["unverschattet"] if j["unverschattet"] else None),
        "sonnenstunden": _z(sum(m["besonnt_h"] for m in e["verschattung"]["monate"])),
        "frei": _z(j["unverschattet"]),
        "fern": _z(j["nur_fernhorizont"]),
        "nah": _z(j["nur_fernhorizont"] - j["mittel"]),
        "kappung": _kappung(ein["anlage"]),
        "kappungsverlust": _z(j["kappungsverlust"], 1),
        "einstrahlung": _z(j["einstrahlung_kwh_m2"]),
        "spanne": f"{_z(min(jahre))} … {_z(max(jahre))}",
        "typjahr": _z(j["typjahr"]),
        "balken": _balken(er["monate"], er["monate_unverschattet"]),
        "zeilen": _monatszeilen(e),
        "bezugsjahr": str(ein["bezugsjahr_sonnenstunden"]),
        "karte": base64.b64encode(karte_png).decode(),
        "lagebild": (
            f'<img alt="Lage der Fläche" src="data:image/png;base64,'
            f'{base64.b64encode(lage_png).decode()}">'
            if lage_png
            else f"<p>Lagebild: {FEHLT} (Paket ohne Draufsicht)</p>"
        ),
        "skala": f"{_z(bereich[0])} … {_z(bereich[1])}",
        "raster": _z(ein["raster_m"] * 100),
        "flaeche": _flaeche(e),
        "lage": _lage(e),
        "aufnahme": _aufnahme(e),
        "anlage": _anlage(e),
        "kette": _kette(e, lauf),
        "schwellen": _schwellen(e),
        "gegenprobe": _gegenprobe(e["gegenprobe_pvcalc"]),
        "quelle": html.escape(QUELLE),
        "quelle_url": html.escape(QUELLE_URL),
        "abrufe": abrufe,
        "grenzen": "".join(f"<li>{html.escape(g)}</li>" for g in GRENZEN),
        "datenschutz": html.escape(DATENSCHUTZ) + FEHLT,
        "anhang": _anhang(),
        "laufzeit": _z(lauf["gesamt_s"], 1),
        "schritte": ", ".join(f"{k} {_z(s, 1)} s" for k, s in lauf["sekunden"].items()),
        "ram": _z(lauf["ram_spitze_mb"]),
        "sha": ergebnis_sha,
    }
    return VORLAGE.substitute(werte)
