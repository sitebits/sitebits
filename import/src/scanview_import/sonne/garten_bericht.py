"""Gartenbericht: Kategorienkarte, Stundenkarten, HTML nach der Berichts-Norm (Seite 1 Laienteil).

Jede Zahl erscheint als Spanne zwischen zwei Rechnungen: Kronen geschlossen (weniger Sonne) und
Mesh wie aufgenommen (mehr Sonne, Kronen löchrig). So steht keine nackte Zahl im Bericht.
"""

from __future__ import annotations

import base64
import html
from pathlib import Path
from string import Template
from typing import Any

import numpy as np

from ..berichtsatz import _prozent, _tabelle, _z
from .bericht import FEHLT, _aufnahme, _kette, _lage, vertrauen
from .garten import GRENZBEREICH_H, KATEGORIEN, SCHWELLEN_H, SCHWELLEN_QUELLE
from .pvgis import QUELLE, QUELLE_URL

VORLAGE = Template((Path(__file__).parent / "garten_vorlage.html").read_text(encoding="utf-8"))
MONATE = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]
# schattig, mäßig schattig, halbschattig, sonnig; unsichere Punkte zur Hälfte aufgehellt
KATEGORIE_FARBEN = np.array([[40, 56, 105], [86, 128, 168], [120, 178, 110], [246, 205, 60]], float)

GRENZEN = [
    "Schätzung, keine zugesicherte Eigenschaft.",
    "Die Stunden sind geometrisch: Sonne sichtbar, ohne Wolken, so wie die Lichtschwellen der "
    "Gartenliteratur gemeint sind. Wie oft die Sonne tatsächlich scheint, hängt vom Wetter ab und "
    "ist deutlich weniger.",
    "Vegetation gilt als undurchsichtig und so belaubt, wie sie bei der Aufnahme war. Belaubt "
    "aufgenommen: Im Frühjahr vor dem Austrieb und im Winter ist es heller als berechnet. Kahl "
    "aufgenommen: Im Sommer ist es dunkler als berechnet.",
    "Baumkronen sind im 3D-Modell aus Fotos lückenhaft; durch die Lücken fällt rechnerisch Sonne. "
    "Deshalb wird zweimal gerechnet, mit dem Modell wie aufgenommen und mit geschlossenen Kronen; "
    "die Wahrheit liegt dazwischen, eine echte Krone lässt auch etwas Licht durch.",
    "Dünne Hindernisse (Masten, Stämme, Äste) können eine Viertelstunde Schatten zu viel oder zu "
    "wenig zeigen; Schattenkanten sind auf eine Zelle des Richtungsrasters genau.",
    "Punkte unter Gegenständen (Anhänger, Paletten, Möbel) gelten als verschattet, solange die "
    "Gegenstände dort stehen.",
    "Schattenwerfer außerhalb des 3D-Modells (Nachbarhäuser, Bäume jenseits der Aufnahme) fehlen; "
    "nur der Geländehorizont aus einem 90-m-Höhenmodell ist berücksichtigt.",
    "Wo das Modell keinen Boden hat, wird nicht gerechnet (Abdeckung s. Fläche).",
    "Boden, pH-Wert, Feuchte, Verdichtung, Wurzeldruck und Kaltluft sind nicht gemessen.",
    "Die Lichtschwellen sind nicht genormt; eine Ellenberg-Lichtzahl wird nicht abgeleitet.",
    "Kein Ersatz für eine Garten- oder Pflanzplanung vor Ort.",
]

STANDARD_GARTEN = (
    "Vorgabe für Gartenaufträge (hier nicht belegt): nur über dem eigenen Grundstück fliegen, "
    "Texturen und Fotos auf die Grundstücksgrenze beschneiden, Nachbarn nur als Geometrie, "
    "Rohfotos nach der Verarbeitung löschen, Personen vorher informieren."
)

PFLICHTFRAGEN = [
    "Boden: Art (sandig, lehmig, tonig), pH-Wert, verdichtet oder locker?",
    "Feuchte: trocken, frisch oder nass; Staunässe nach Regen?",
    "Wurzeldruck großer Gehölze in der Nähe?",
    "Kaltluft: Liegt die Fläche in einer Senke, wo sich Frost hält?",
    "Winterhärtezone und Lage zum Wind",
]


def kategorie_farben(kategorie: np.ndarray, aufhellen: np.ndarray) -> np.ndarray:
    f = KATEGORIE_FARBEN[kategorie]
    f[aufhellen] = 0.5 * f[aufhellen] + 0.5 * 255
    return np.asarray(f.round(), dtype=np.uint8)


def _spanne(a: float, b: float, stellen: int = 1, prozent: bool = False) -> str:
    """„a … b“ aufsteigend; gleich gerundete Werte nur einmal."""
    lo, hi = ((_prozent(x, stellen) if prozent else _z(x, stellen)) for x in sorted((a, b)))
    return lo if lo == hi else f"{lo} … {hi}"


def _anteile(li: dict[str, Any]) -> dict[str, float]:
    return {k["name"]: k["anteil"] for k in li["kategorien"]}


def _schwelle(i: int) -> str:
    unten = "" if i == 0 else f"ab {_z(SCHWELLEN_H[i - 1], 0)} h"
    oben = "" if i == len(SCHWELLEN_H) else f"unter {_z(SCHWELLEN_H[i], 0)} h"
    return " und ".join(t for t in (unten, oben) if t)


def _legende() -> str:
    return "".join(
        f'<span class="feld" style="background:rgb{tuple(int(x) for x in KATEGORIE_FARBEN[i])}">'
        f"</span>{html.escape(name)} "
        for i, name in enumerate(KATEGORIEN)
    )


def _kategorien(e: dict[str, Any]) -> str:
    fl = e["flaeche"]["flaeche_m2"] * e["licht"]["abdeckung"]
    zu, wie = _anteile(e["licht_kronen_zu"]), _anteile(e["licht"])
    zeilen = []
    for i in reversed(range(len(KATEGORIEN))):  # sonnig zuerst
        n = KATEGORIEN[i]
        zeilen.append(
            f"<tr><td>{html.escape(n)}</td><td>{_schwelle(i)}</td>"
            f"<td>{_prozent(zu[n])}</td><td>{_prozent(wie[n])}</td>"
            f"<td>{_spanne(zu[n] * fl, wie[n] * fl, 0)} m²</td></tr>"
        )
    return "".join(zeilen)


def _monatszeilen(e: dict[str, Any]) -> str:
    return "".join(
        f"<tr><td>{MONATE[m['monat'] - 1]}</td><td>{_z(m['ueber_horizont_h'], 1)}</td>"
        f"<td>{_z(m['nur_fernhorizont_h'], 1)}</td><td>{_z(m['mittel_kronen_zu'], 1)}</td>"
        f"<td>{_z(m['mittel'], 1)}</td><td>{_z(m['p10'], 1)} … {_z(m['p90'], 1)}</td>"
        f"<td>{_z(m['min'], 1)} … {_z(m['max'], 1)}</td></tr>"
        for m in e["monate"]
    )


def _flaeche(e: dict[str, Any]) -> str:
    fl = e["flaeche"]
    b = fl["boden_hoehe_m"]
    kr = e["kronen"]
    return _tabelle(
        [
            ("Fläche (Grundriss)", f"{_z(fl['flaeche_m2'], 1)} m²"),
            ("Stichpunkte / ohne Boden im Modell", f"{fl['stichpunkte']} / {fl['ohne_mesh']}"),
            ("Abdeckung", _prozent(e["licht"]["abdeckung"])),
            ("Raster", f"{_z(e['eingaben']['raster_m'] * 100)} cm"),
            ("Bodenhöhe (p10 … p90)", f"{_z(b['p10'], 2)} … {_z(b['p90'], 2)} m"),
            ("Suchfenster um die Eckenebene", f"±{_z(e['eingaben']['suchfenster_m'], 2)} m"),
            ("Rauheit (p95, Nachbarpunkte)", f"{_z(fl['rauheit_p95_m'] * 1000)} mm"),
            (
                "Geschlossene Kronen (Gegenlauf)",
                f"{_z(kr['flaeche_m2'], 0)} m² Kronendach im Umkreis {_z(kr['umkreis_m'], 0)} m",
            ),
        ]
    )


def _frage_antwort(e: dict[str, Any]) -> tuple[str, str]:
    wie, zu = e["licht"], e["licht_kronen_zu"]
    a_wie, a_zu = _anteile(wie), _anteile(zu)
    frage = (
        f"Wo bekommt diese Gartenfläche ({_z(e['flaeche']['flaeche_m2'], 0)} m²) wie viel direkte "
        "Sonne, und welche Lichtlage ergibt das für die Pflanzenwahl?"
    )
    schatten_wie = a_wie["mäßig schattig"] + a_wie["schattig"]
    schatten_zu = a_zu["mäßig schattig"] + a_zu["schattig"]
    antwort = (
        "Im Juni/Juli im Mittel "
        f"{_spanne(zu['mittsommer_h']['mittel'], wie['mittsommer_h']['mittel'])} h direkte Sonne "
        f"am Tag. Sonnig sind {_spanne(a_zu['sonnig'], a_wie['sonnig'], prozent=True)} der "
        f"Fläche, halbschattig "
        f"{_spanne(a_zu['halbschattig'], a_wie['halbschattig'], prozent=True)}, mäßig schattig "
        f"oder schattig {_spanne(schatten_zu, schatten_wie, prozent=True)}."
    )
    return frage, antwort


def _schwellen(e: dict[str, Any]) -> str:
    fl, ein, kr = e["flaeche"], e["eingaben"], e["kronen"]
    rr = ein["richtungsraster_grad"]
    punkte = [
        f"Lichtlage nach {SCHWELLEN_QUELLE}: sonnig ab {_z(SCHWELLEN_H[2], 0)} h, halbschattig "
        f"{_z(SCHWELLEN_H[1], 0)} bis {_z(SCHWELLEN_H[2], 0)} h, mäßig schattig "
        f"{_z(SCHWELLEN_H[0], 0)} bis {_z(SCHWELLEN_H[1], 0)} h, schattig unter "
        f"{_z(SCHWELLEN_H[0], 0)} h direkte Sonne je Tag im Mittsommer. Nicht genormt; eine "
        "deutsche Fachquelle mit Stundenschwellen gibt es nicht.",
        "Mittsommer = Mittel Juni und Juli; daneben das Mittel April bis September und der "
        "Monatsverlauf, damit Sommer- und Jahreswerte nicht verwechselt werden.",
        f"Grenzbereich: bis ±{_z(GRENZBEREICH_H, 1)} h um eine Schwelle. Auf der Karte "
        "aufgehellt ist, was im Grenzbereich liegt oder je nach Kronen anders ausfällt.",
        "Kronen geschlossen: je Zelle von "
        f"{_z(kr['zelle_m'] * 100)} cm die höchste Stelle des Modells, wenn sie mehr als "
        f"{_z(kr['hoehe_min_m'], 0)} m über dem Boden liegt, als dichte Fläche; einzelne Lücken "
        "geschlossen, Kronen dadurch um bis zu eine Zelle breiter.",
        "„Sonne“: Sonnenmitte vom Bodenpunkt aus sichtbar; Viertelstunden des Jahres "
        f"{ein['bezugsjahr_sonnenstunden']} (NREL SPA); Richtungsraster {_z(rr[0], 1)}° mal "
        f"{_z(rr[1], 1)}°, dazwischen linear gemittelt.",
        f"Boden: erster Treffer eines senkrechten Strahls von unten, nur im Fenster "
        f"±{_z(ein['suchfenster_m'], 2)} m um die Ebene durch die Ecken (sonst könnte ein Loch "
        "im Modell zur Baumkrone durchreichen).",
        f"Selbstverschattung: Strahlen beginnen 10 mm über dem Boden und zählen Treffer erst ab "
        f"{_z(fl['mindestabstand_m'] * 1000)} mm (über der Rauheit "
        f"{_z(fl['rauheit_p95_m'] * 1000)} mm, p95).",
        "Himmelssicht: kosinusgewichteter Anteil freien Himmels, relativ zu unverbaut "
        "(Maß für das Streulicht).",
    ]
    return "".join(f"<li>{html.escape(x)}</li>" for x in punkte)


def _gegenprobe() -> str:
    return _tabelle(
        [
            (
                "Synthetische Szene (Wand + Mast, analytischer Strahltest)",
                "Schattenwechsel ±1 Viertelstunde, an dünnen Hindernissen auf eine Rasterzelle "
                "genau (Test im Rechenkern)",
            ),
            ("Kronen geschlossen gegen Modell wie aufgenommen", "Spanne in allen Zahlen"),
            ("Schattenkante auf Foto mit Zeitstempel", FEHLT),
            ("Messung vor Ort (Lichtmesser, 3 bis 5 Punkte)", FEHLT),
        ]
    )


def _datenschutz(e: dict[str, Any]) -> str:
    au = e["aufnahme"]
    zeilen = [
        (
            "In die Rechnung",
            "nur 3D-Modell (Geometrie) und Flächenmessung; Fotos und Personendaten gehen nicht "
            "ein und stehen nicht im Bericht",
        ),
        ("An PVGIS übermittelt", "nur der Ort (Breite/Länge, auf 4 Stellen gerundet)"),
        ("Aufgenommen", f"{au.get('fotos_im_paket', 0)} Fotos im Paket"),
        ("Beschnitt auf die Grundstücksgrenze", FEHLT),
        ("Rohfotos gelöscht", FEHLT),
        ("Ablageort und Löschfrist der Ergebnisse", FEHLT),
    ]
    return _tabelle([(html.escape(a), b if b == FEHLT else html.escape(b)) for a, b in zeilen])


def _anhang() -> str:
    teile = [
        "ergebnis.json: alle Zahlen dieses Berichts mit Eingaben, Versionen und Quellen",
        "stichpunkte.csv / stichpunkte.geojson: jeder Bodenpunkt mit Lage, Stunden je Monat, "
        "Mittsommer und Lichtlage in beiden Rechnungen, Himmelssicht (Paket-CRS bzw. WGS84) "
        "zum Nachmessen",
        "kategorien.png, stunden.png, stunden_kronen_zu.png, lauf.json (Dauer, RAM, Prüfsumme)",
    ]
    aus = "".join(f"<li>{html.escape(t)}</li>" for t in teile)
    return aus + f"<li>Nur-Lese-Link zur Fläche im Viewer: {FEHLT}</li>"


def _bild(png: bytes, alt: str) -> str:
    daten = base64.b64encode(png).decode()
    return f'<img alt="{html.escape(alt)}" src="data:image/png;base64,{daten}">'


def html_bericht(
    e: dict[str, Any],
    bilder: dict[str, bytes],
    lage_png: bytes | None,
    bereich: tuple[float, float],
    lauf: dict[str, Any],
    ergebnis_sha: str,
    erstellt: str,
) -> str:
    """`bilder`: kategorien, stunden, stunden_kronen_zu (PNG)."""
    ein, wie, zu = e["eingaben"], e["licht"], e["licht_kronen_zu"]
    frage, antwort = _frage_antwort(e)
    stufe, grund = vertrauen(e)
    abrufe = "".join(
        f"<li><code>{html.escape(q['werkzeug'])}</code>, abgerufen {html.escape(q['abgerufen'])}: "
        f"<span class=url>{html.escape(q['url'])}</span></li>"
        for q in e["quellen"]
    )

    def mittel(li: dict[str, Any], k: str, stellen: int = 1) -> str:
        return _spanne(zu[k]["mittel"], li[k]["mittel"], stellen)

    werte = {
        "paket": html.escape(str(ein["paket"])),
        "flaeche_datei": html.escape(ein["flaeche_datei"]),
        "erstellt": html.escape(erstellt),
        "git": html.escape(e["versionen"]["git"]),
        "frage": html.escape(frage),
        "antwort": html.escape(antwort),
        "vertrauen": stufe,
        "vertrauen_grund": html.escape(grund),
        "mittsommer": mittel(wie, "mittsommer_h"),
        "mittsommer_band": (
            f"{_z(zu['mittsommer_h']['p10'], 1)} … {_z(wie['mittsommer_h']['p90'], 1)}"
        ),
        "vegetation": mittel(wie, "vegetationszeit_h"),
        "vegetation_band": (
            f"{_z(zu['vegetationszeit_h']['p10'], 1)} … {_z(wie['vegetationszeit_h']['p90'], 1)}"
        ),
        "sonnig": _spanne(_anteile(zu)["sonnig"], _anteile(wie)["sonnig"], prozent=True),
        "himmel": _spanne(
            zu["himmelssicht"]["mittel"], wie["himmelssicht"]["mittel"], 0, prozent=True
        ),
        "lagebild": _bild(lage_png, "Lage der Fläche")
        if lage_png
        else f"<p>Lagebild: {FEHLT} (Paket ohne Draufsicht)</p>",
        "kategorien_bild": _bild(bilder["kategorien"], "Lichtlage je Bodenpunkt"),
        "stunden_bild": _bild(bilder["stunden"], "Sonnenstunden, Modell wie aufgenommen"),
        "stunden_zu_bild": _bild(bilder["stunden_kronen_zu"], "Sonnenstunden, Kronen geschlossen"),
        "legende": _legende(),
        "skala": f"{_z(bereich[0])} … {_z(bereich[1])}",
        "raster": _z(ein["raster_m"] * 100),
        "kategorien": _kategorien(e),
        "unsicher": _prozent(e["unsicher_anteil"]),
        "zeilen": _monatszeilen(e),
        "bezugsjahr": str(ein["bezugsjahr_sonnenstunden"]),
        "flaeche": _flaeche(e),
        "lage": _lage(e),
        "aufnahme": _aufnahme(e),
        "kette": _kette(e, lauf),
        "schwellen": _schwellen(e),
        "gegenprobe": _gegenprobe(),
        "pflichtfragen": "".join(f"<li>{html.escape(f)}</li>" for f in PFLICHTFRAGEN),
        "fehlt": FEHLT,
        "quelle": html.escape(QUELLE),
        "quelle_url": html.escape(QUELLE_URL),
        "abrufe": abrufe,
        "grenzen": "".join(f"<li>{html.escape(g)}</li>" for g in GRENZEN),
        "datenschutz": _datenschutz(e),
        "standard_garten": html.escape(STANDARD_GARTEN),
        "anhang": _anhang(),
        "laufzeit": _z(lauf["gesamt_s"], 1),
        "schritte": ", ".join(f"{k} {_z(s, 1)} s" for k, s in lauf["sekunden"].items()),
        "ram": _z(lauf["ram_spitze_mb"]),
        "sha": ergebnis_sha,
    }
    return VORLAGE.substitute(werte)
