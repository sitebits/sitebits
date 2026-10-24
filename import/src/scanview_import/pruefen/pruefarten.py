"""Prüfarten der Stufen P1 bis P3 und B1: je Katalogzeile ein Urteil mit Grund und Belegen.

Regel für die drei Urteile: Verlangt die Zeile eine Datei oder Deklaration und sie
fehlt oder ist unlesbar, ist das **nicht erfüllt**. **Nicht prüfbar** heißt nur: das Werkzeug fehlt
(Prüfart einer späteren Stufe oder unbekannt). Fehlende oder falsche Parameter einer bekannten
Prüfart sind ein Katalogfehler, kein Urteil.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import PurePosixPath

from pyproj import CRS
from pyproj.exceptions import CRSError

from .. import hoehen, klassen
from . import bodenvergleich, flaechenpruefung, kontrollpunkte
from .bezug import crs_datei, gleich
from .katalog import KatalogFehler, Zeile
from .lieferung import ARTEN, LESEFEHLER, Lieferung, art, lesbar
from .raster import geotiff
from .urteil import (
    ERFUELLT,
    NICHT_ERFUELLT,
    NICHT_PRUEFBAR,
    Ergebnis,
    _param,
    _urteil,
)

__all__ = ["ERFUELLT", "NICHT_ERFUELLT", "NICHT_PRUEFBAR", "Ergebnis", "pruefe"]

# Prüfarten späterer Stufen (Recherche §C.2): bekannt, aber hier ohne Werkzeug.
KUENFTIG = {
    "flaechenbezug": "J5 (Grundrissfläche)",
    "befunde": "J6 (Befunde mit Objektnummer, GeoJSON)",
    "zeitstaende": "B3 (Co-Registrierung und M3C2)",
    "anonymisierung": "später (Erkennung von Personen und Kennzeichen)",
}


def bezugssystem(z: Zeile, lf: Lieferung) -> Ergebnis:
    epsg = _param(z, "epsg", int)
    soll_hoehe = _param(z, "hoehe", str, pflicht=False)
    if soll_hoehe is not None and hoehen.normiere(soll_hoehe) is None:
        raise KatalogFehler(f"{z.id}: Höhensystem „{soll_hoehe}“ unbekannt")
    soll_h = hoehen.normiere(soll_hoehe) if soll_hoehe else None
    muster = _param(z, "dateien", (str, list), pflicht=False)
    dateien = lf.finde(muster) if muster else lf.von_art("las", "laz", "e57", "tiff")
    paket_frame = (lf.paket or {}).get("frame", {}).get("crs", {})
    paket_hoehe = hoehen.normiere(paket_frame.get("verticalDatum"))
    maengel: list[str] = []
    belege: list[str] = []
    if lf.paket is not None:
        try:
            ph = CRS.from_user_input(paket_frame.get("wkt") or "")
        except CRSError:
            maengel.append("siteBits-Paket: Bezugssystem nicht lesbar")
        else:
            belege.append(f"siteBits-Paket: {ph.name}, Höhe {paket_hoehe or 'nicht deklariert'}")
            if not gleich(ph, epsg):
                maengel.append(f"siteBits-Paket: {ph.name} statt EPSG:{epsg}")
    if not dateien and lf.paket is None:
        return Ergebnis(NICHT_ERFUELLT, "keine Geodatei (LAS/LAZ/E57/GeoTIFF) in der Lieferung")
    for rel in dateien:
        try:
            h, v, quelle = crs_datei(lf, rel)
        except LESEFEHLER as e:
            maengel.append(f"{rel}: nicht lesbar ({type(e).__name__})")
            continue
        if v is None and lf.im_paket(rel) and paket_hoehe:
            v, quelle = paket_hoehe, quelle + " + Paket-Höhensystem"
        if h is None:
            maengel.append(f"{rel}: kein Lagebezug deklariert")
            continue
        belege.append(f"{rel}: {h.name}, Höhe {v or 'nicht deklariert'} ({quelle})")
        if not gleich(h, epsg):
            maengel.append(f"{rel}: {h.name} statt EPSG:{epsg}")
        if soll_h and v is None:
            maengel.append(f"{rel}: Höhensystem nicht deklariert")
        elif soll_h and v != soll_h:
            maengel.append(f"{rel}: Höhe {v} statt {soll_h}")
    n = len(dateien) + (lf.paket is not None)
    return _urteil(maengel, f"{n} Deklaration(en) geprüft, alle EPSG:{epsg}", belege)


def _paket_klassen(lf: Lieferung, rel: str) -> dict[str, int] | None:
    if lf.paket is None or not lf.im_paket(rel):
        return None
    innen = rel.removeprefix(lf.paket_rel + "/") if lf.paket_rel else rel
    for ebene in lf.paket.get("layers", []):
        if ebene.get("kind") == "pointcloud" and ebene.get("url") == innen:
            k = ebene.get("classes")
            return {str(a): int(b) for a, b in k.items()} if isinstance(k, dict) else None
    return None


def klassen_vorhanden(z: Zeile, lf: Lieferung) -> Ergebnis:
    soll = _param(z, "klassen", list)
    gruppen: list[list[int]] = []
    for g in soll:
        werte = g if isinstance(g, list) else [g]
        if not werte or not all(isinstance(w, int) and 0 <= w <= 255 for w in werte):
            raise KatalogFehler(f"{z.id}: Klassen sind ganze Zahlen 0 bis 255 oder Listen davon")
        gruppen.append(werte)
    muster = _param(z, "dateien", (str, list), pflicht=False)
    dateien = lf.finde(muster) if muster else lf.von_art("las", "laz")
    if not dateien:
        return Ergebnis(NICHT_ERFUELLT, "keine Punktwolke (LAS/LAZ) in der Lieferung")
    summe: dict[int, int] = {}
    belege: list[str] = []
    for rel in dateien:
        hist = _paket_klassen(lf, rel)
        quelle = "Paket-Metadaten (beim Import gezählt)"
        if hist is None:
            hist, quelle = klassen.zaehle(lf.pfad(rel)).klassen, "gezählt"
        for k, n in hist.items():
            summe[int(k)] = summe.get(int(k), 0) + n
        belege.append(
            f"{rel}: "
            + ", ".join(
                f"Klasse {k}: {n}" for k, n in sorted(hist.items(), key=lambda x: int(x[0]))
            )
            + f" ({quelle})"
        )
    maengel = [
        f"Klasse {' oder '.join(map(str, g))} fehlt"
        for g in gruppen
        if not any(summe.get(w, 0) > 0 for w in g)
    ]
    return _urteil(maengel, "alle verlangten Klassen kommen vor", belege)


def raster(z: Zeile, lf: Lieferung) -> Ergebnis:
    produkte = _param(z, "produkte", list)
    raster_m = float(_param(z, "raster_m", (int, float)))
    epsg = _param(z, "epsg", int, pflicht=False)
    maengel: list[str] = []
    belege: list[str] = []
    for pr in produkte:
        if not isinstance(pr, dict) or not {"name", "muster"} <= set(pr):
            raise KatalogFehler(f"{z.id}: jedes Produkt braucht „name“ und „muster“")
        dateien = [r for r in lf.finde(pr["muster"]) if art(r) == "tiff"]
        if not dateien:
            maengel.append(f"{pr['name']}: keine GeoTIFF-Datei ({pr['muster']})")
        for rel in dateien:
            try:
                g = geotiff(lf.pfad(rel))
            except LESEFEHLER as e:
                maengel.append(f"{rel}: nicht lesbar ({type(e).__name__})")
                continue
            px = g["pixel_m"]
            belege.append(
                f"{pr['name']} {rel}: {g['breite']}\u00d7{g['hoehe']} px, Raster "
                + (f"{px[0]:g} \u00d7 {px[1]:g} m" if px else "unbekannt")
                + f", EPSG {g['epsg'] or 'fehlt'}"
            )
            if not g["georeferenziert"] or px is None:
                maengel.append(f"{rel}: nicht georeferenziert")
                continue
            if max(px) > raster_m + 1e-9:
                maengel.append(f"{rel}: Raster {max(px):g} m > {raster_m:g} m")
            if epsg is not None and g["epsg"] != epsg:
                maengel.append(f"{rel}: EPSG {g['epsg'] or 'fehlt'} statt {epsg}")
    return _urteil(maengel, f"alle Raster ≤ {raster_m:g} m", belege)


_ARTNAMEN = set(ARTEN.values())


def formate(z: Zeile, lf: Lieferung) -> Ergebnis:
    arten = _param(z, "arten", list)
    maengel: list[str] = []
    belege: list[str] = []
    nur_da: list[str] = []
    for eintrag in arten:
        alt = str(eintrag).split("|")
        if not set(alt) <= _ARTNAMEN:
            raise KatalogFehler(f"{z.id}: unbekannte Dateiart in „{eintrag}“")
        dateien = lf.von_art(*alt)
        if not dateien:
            maengel.append(f"{eintrag.upper()}: keine Datei")
            continue
        for rel in dateien:
            ok, text = lesbar(lf, rel)
            belege.append(f"{rel}: {text}")
            if not ok:
                maengel.append(f"{rel}: {text}")
            elif text.startswith("nur vorhanden"):
                nur_da.append(art(rel).upper())
    ok_text = "alle verlangten Formate vorhanden und lesbar"
    if nur_da:
        ok_text += f" ({', '.join(sorted(set(nur_da)))} nur auf Vorhandensein geprüft)"
    return _urteil(maengel, ok_text, belege)


_SUMMENZEILE = re.compile(r"^([0-9a-fA-F]{64}) [ *](.+)$")
_LISTEN = ["*.sha256", "sha256sums*", "*sha256*.txt"]


def pruefsummen(z: Zeile, lf: Lieferung) -> Ergebnis:
    muster = _param(z, "liste", (str, list), pflicht=False) or _LISTEN
    vollstaendig = _param(z, "vollstaendig", bool, pflicht=False)
    listen = lf.finde(muster)
    if not listen:
        return Ergebnis(NICHT_ERFUELLT, "keine Prüfsummenliste in der Lieferung")
    eintraege: dict[str, str] = {}
    unzulaessig, kaputt, fehlt, abweichend, links = [], [], [], [], []
    for liste in listen:
        for nr, zeile in enumerate(lf.pfad(liste).read_text("utf-8").splitlines(), 1):
            if not zeile.strip():
                continue
            m = _SUMMENZEILE.match(zeile.strip())
            if not m:
                kaputt.append(f"{liste}:{nr}")
                continue
            # sha256sum-Pfade gelten relativ zum Ordner der Liste
            roh = m.group(2).replace("\\", "/")
            basis = PurePosixPath(liste).parent
            rel = None if roh.startswith("/") else lf.sicher((basis / roh).as_posix())
            if rel is None:
                unzulaessig.append(m.group(2))
                continue
            eintraege[rel] = m.group(1).lower()
    for rel, soll in sorted(eintraege.items()):
        if rel in lf.links:
            links.append(rel)
        elif rel not in lf.dateien:
            fehlt.append(rel)
        elif lf.sha256(rel) != soll:
            abweichend.append(rel)
    ueberzaehlig = (
        [r for r in lf.dateien if r not in eintraege and r not in listen]
        if vollstaendig is not False
        else []
    )
    maengel = [
        f"{titel} ({len(xs)}): {', '.join(xs[:5])}{' …' if len(xs) > 5 else ''}"
        for titel, xs in (
            ("Pfad außerhalb der Lieferung, nicht gelesen", unzulaessig),
            ("Zeile nicht im sha256sum-Format", kaputt),
            ("gelistet, aber nicht geliefert", fehlt),
            ("Prüfsumme weicht ab", abweichend),
            ("symbolischer Link, nicht verfolgt", links),
            ("geliefert, aber nicht gelistet", ueberzaehlig),
        )
        if xs
    ]
    beleg = [f"Liste {liste}" for liste in listen] + [
        f"{len(eintraege)} Einträge, {len(eintraege) - len(fehlt + abweichend + links)} stimmen"
    ]
    return _urteil(maengel, f"{len(eintraege)} Dateien, alle Prüfsummen stimmen", beleg)


def anhang(z: Zeile, lf: Lieferung) -> Ergebnis:
    muster = _param(z, "muster", (str, list))
    name = _param(z, "bezeichnung", str, pflicht=False) or "Anhang"
    dateien = lf.finde(muster)
    if not dateien:
        return Ergebnis(NICHT_ERFUELLT, f"{name} fehlt (gesucht: {muster})")
    maengel, belege = [], []
    for rel in dateien:
        ok, text = lesbar(lf, rel)
        belege.append(f"{rel}: {text}, SHA-256 {lf.sha256(rel)[:16]}…")
        if not ok:
            maengel.append(f"{rel}: {text}")
    return _urteil(
        maengel, f"{name} liegt bei ({len(dateien)} Datei(en)); Inhalt nicht bewertet", belege
    )


PRUEFARTEN: dict[str, Callable[[Zeile, Lieferung], Ergebnis]] = {
    "bezugssystem": bezugssystem,
    "klassen": klassen_vorhanden,
    "raster": raster,
    "formate": formate,
    "pruefsummen": pruefsummen,
    "anhang": anhang,
    "dichte": flaechenpruefung.dichte,
    "luecken": flaechenpruefung.luecken,
    "gsd": flaechenpruefung.gsd,
    "kontrollpunkte": kontrollpunkte.kontrollpunkte,
    "massstab": kontrollpunkte.massstab,
    "einpassung": bodenvergleich.einpassung,
    "klassen_richtig": bodenvergleich.klassen_richtig,
}
# „bericht“ prüft den fertig zusammengestellten Prüfbericht selbst (bericht.pflichtteile).
BERICHT = "bericht"


def pruefe(z: Zeile, lf: Lieferung) -> Ergebnis:
    if z.pruefart in PRUEFARTEN:
        try:
            return PRUEFARTEN[z.pruefart](z, lf)
        except KatalogFehler:
            raise
        except LESEFEHLER as e:
            # Eine kaputte Lieferdatei ist ein Befund der Zeile, kein Absturz des Prüfers
            return Ergebnis(
                NICHT_ERFUELLT,
                f"eine Lieferdatei ist nicht lesbar ({type(e).__name__}: {str(e)[:200]})",
            )
    if z.pruefart in KUENFTIG:
        return Ergebnis(NICHT_PRUEFBAR, f"Werkzeug fehlt: {KUENFTIG[z.pruefart]}")
    return Ergebnis(NICHT_PRUEFBAR, f"Werkzeug fehlt: Prüfart „{z.pruefart}“ unbekannt")
