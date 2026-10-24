"""Prüfarten der Stufe P2 über der Auftragsfläche: Punktdichte, Lücken, Bodenauflösung (GSD).

Ohne Auftragsfläche (``--flaeche``) sind sie „nicht prüfbar (Grundlage fehlt)“: Die Fläche gibt
der Auftraggeber vor, die Lieferung schuldet sie nicht.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable
from typing import Any

import numpy as np
from PIL import Image, ImageDraw
from pyproj import CRS

from ..fotos_pruefung import _paket_rotation
from .bezug import hoehe_norm
from .katalog import KatalogFehler, Zeile
from .lieferung import Lieferung
from .punktraster import Punktraster, rastern
from .urteil import ERFUELLT, NICHT_ERFUELLT, NICHT_PRUEFBAR, Ergebnis, _param

OHNE_FLAECHE = "Grundlage fehlt: keine Auftragsfläche angegeben (--flaeche)"
LEER, DUENN, GUT, AUSSEN = (190, 30, 60), (150, 182, 215), (44, 93, 138), (236, 238, 241)


def _de(x: float, stellen: int = 1) -> str:
    return f"{x:,.{stellen}f}".replace(",", " ").replace(".", ",")


def karte(r: Punktraster, schwelle: float | None, breite_px: int = 900) -> bytes:
    """Zellkarte (Norden oben): Lücke rot, unter Schwelle hellblau, erfüllt dunkelblau, Umriss."""
    dichte = r.anzahl / r.zelle**2
    rgb = np.empty((*r.anzahl.shape, 3), np.uint8)
    rgb[:] = AUSSEN
    gut = dichte >= schwelle if schwelle is not None else r.anzahl > 0
    rgb[r.innen & gut] = GUT
    rgb[r.innen & ~gut] = DUENN
    rgb[r.innen & (r.anzahl == 0)] = LEER
    ny, nx = r.anzahl.shape
    f = max(breite_px // nx, 1) if nx <= breite_px else breite_px / nx
    groesse = (max(int(nx * f), 1), max(int(ny * f), 1))
    bild = Image.fromarray(rgb[::-1]).resize(groesse, Image.Resampling.NEAREST)
    zeichnen = ImageDraw.Draw(bild)
    for poly in r.flaeche.polygone:
        for ring in poly:
            px = [((x - r.x0) / r.zelle * f, (ny - (y - r.y0) / r.zelle) * f) for x, y in ring]
            zeichnen.line(px, fill=(20, 20, 20), width=2)
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG", optimize=False)
    return puffer.getvalue()


def _raster(z: Zeile, lf: Lieferung) -> Punktraster | Ergebnis:
    """Gemeinsamer Einstieg: Fläche, Punktdateien, Raster — oder schon ein Urteil."""
    if lf.flaeche is None:
        return Ergebnis(NICHT_PRUEFBAR, OHNE_FLAECHE)
    zelle = float(_param(z, "zelle_m", (int, float), pflicht=False) or 1.0)
    if zelle <= 0:
        raise KatalogFehler(f"{z.id}: zelle_m muss positiv sein")
    klassen = _param(z, "klassen", list, pflicht=False)
    if klassen is not None and not all(isinstance(k, int) and 0 <= k <= 255 for k in klassen):
        raise KatalogFehler(f"{z.id}: klassen sind ganze Zahlen 0 bis 255")
    muster = _param(z, "dateien", (str, list), pflicht=False)
    dateien = lf.finde(muster) if muster else lf.von_art("las", "laz")
    if not dateien:
        return Ergebnis(NICHT_ERFUELLT, "keine Punktwolke (LAS/LAZ) in der Lieferung")
    r = rastern(lf, dateien, lf.flaeche, zelle, tuple(sorted(klassen)) if klassen else None)
    if isinstance(r, str):
        return Ergebnis(NICHT_ERFUELLT, r)
    if not r.innen.any():
        raise KatalogFehler(f"{z.id}: Auftragsfläche kleiner als eine Zelle ({zelle:g} m)")
    return r


def _grundbelege(r: Punktraster, klassen: list[int] | None) -> list[str]:
    n = int(r.innen.sum())
    return [
        f"Auftragsfläche {r.flaeche.datei}: {_de(r.flaeche.flaeche_m2(), 0)} m², "
        f"{n} Zellen à {r.zelle:g} m in {r.crs.name} "
        "(Zelle zählt, wenn ihr Mittelpunkt innen liegt)",
        "Punktdateien: " + ", ".join(r.dateien) + " (je Zelle Maximum über die Dateien, "
        "keine Doppelzählung; gezählt wird, was geliefert ist)",
        "gezählt: " + (f"Klassen {klassen}" if klassen else "alle Punkte und Echos"),
    ]


def dichte(z: Zeile, lf: Lieferung) -> Ergebnis:
    soll = float(_param(z, "punkte_m2", (int, float)))
    anteil = float(_param(z, "anteil", (int, float)))
    r = _raster(z, lf)
    if isinstance(r, Ergebnis):
        return r
    d = (r.anzahl / r.zelle**2)[r.innen]
    ist = float((d >= soll).mean())
    belege = _grundbelege(r, z.parameter.get("klassen")) + [
        f"Dichte je Zelle: Median {_de(float(np.median(d)))}, 5-%-Quantil "
        f"{_de(float(np.percentile(d, 5)))} Punkte/m²",
        f"{_de(100 * ist)} % der Zellen mit mindestens {soll:g} Punkten/m² (verlangt "
        f"{_de(100 * anteil)} %)",
    ]
    bilder = {"dichte": karte(r, soll)}
    if ist + 1e-12 < anteil:
        return Ergebnis(
            NICHT_ERFUELLT,
            f"nur {_de(100 * ist)} % der Fläche mit ≥ {soll:g} Punkten/m², verlangt "
            f"{_de(100 * anteil)} %",
            belege,
            bilder,
        )
    return Ergebnis(
        ERFUELLT, f"{_de(100 * ist)} % der Fläche mit ≥ {soll:g} Punkten/m²", belege, bilder
    )


def luecken(z: Zeile, lf: Lieferung) -> Ergebnis:
    erlaubt = float(_param(z, "anteil", (int, float)))
    r = _raster(z, lf)
    if isinstance(r, Ergebnis):
        return r
    leer = float((r.anzahl[r.innen] == 0).mean())
    belege = _grundbelege(r, z.parameter.get("klassen")) + [
        f"Zellen ohne Punkt: {int((r.anzahl[r.innen] == 0).sum())} = {_de(100 * leer, 2)} % "
        f"(erlaubt {_de(100 * erlaubt, 2)} %); Karte: siteBits-Auswertung, nicht die Karte "
        "des Dienstleisters",
    ]
    bilder = {"luecken": karte(r, None)}
    if leer > erlaubt + 1e-12:
        text = f"{_de(100 * leer, 2)} % der Fläche ohne Punkte, erlaubt {_de(100 * erlaubt, 2)} %"
        return Ergebnis(NICHT_ERFUELLT, text, belege, bilder)
    return Ergebnis(ERFUELLT, f"{_de(100 * leer, 2)} % der Fläche ohne Punkte", belege, bilder)


def _fotos(lf: Lieferung) -> list[dict[str, Any]] | None:
    if lf.paket is None:
        return None
    ebene = next((e for e in lf.paket.get("layers", []) if e.get("kind") == "images"), None)
    if ebene is None:
        return None
    d = json.loads(lf.paket_datei(str(ebene["url"])).read_text(encoding="utf-8"))
    return list(d.get("images", []))


def _bodenquelle(
    lf: Lieferung, r: Punktraster
) -> tuple[Callable[[np.ndarray, np.ndarray], np.ndarray], str]:
    """Bodenhöhe für die GSD: das Referenz-DGM des Auftraggebers, wenn es passt — die Bodenklasse
    der Lieferung ist ungeprüft, und ein als Boden klassifiziertes Dach machte die GSD zu günstig
    (B1, Pilotprojekt). Sonst die Wolke, mit diesem Vorbehalt im Beleg."""
    ref = lf.dgm
    paket_h = hoehe_norm(str((lf.paket or {}).get("frame", {}).get("crs", {}).get("verticalDatum")))
    if ref is not None:
        if not ref.crs.equals(r.crs, ignore_axis_order=True):
            grund = f"Lage {ref.crs.name} ≠ Wolke"
        elif ref.hoehe != paket_h:
            grund = f"Höhe {ref.hoehe} ≠ Paket {paket_h}"
        else:
            return ref.hoehe_an, (
                "Referenz-DGM des Auftraggebers ("
                + ", ".join(n for n, _ in ref.dateien)
                + "), unabhängig von der Bodenklasse der Lieferung"
            )
        vorbehalt = f"; Referenz-DGM nicht verwendbar ({grund})"
    else:
        vorbehalt = "; ohne Referenz-DGM (--dgm) ungeprüft"
    if r.hat_boden:
        text = (
            f"Klasse 2 (Boden) der Lieferung, Zelle {r.zelle:g} m{vorbehalt}: als Boden "
            "klassifizierte Dächer machen die Bodenauflösung zu günstig"
        )
    else:
        text = f"tiefstem Punkt je Zelle {r.zelle:g} m (keine Klasse 2){vorbehalt}"
    return r.bodenhoehe, text


def gsd(z: Zeile, lf: Lieferung) -> Ergebnis:
    soll = float(_param(z, "gsd_m", (int, float)))
    anteil = float(_param(z, "anteil", (int, float), pflicht=False) or 1.0)
    max_neigung = float(_param(z, "max_neigung_grad", (int, float), pflicht=False) or 20.0)
    if lf.flaeche is None:
        return Ergebnis(NICHT_PRUEFBAR, OHNE_FLAECHE)
    fotos = _fotos(lf)
    if fotos is None:
        return Ergebnis(
            NICHT_PRUEFBAR,
            "Werkzeug fehlt: Bodenauflösung nur aus orientierten Fotos eines siteBits-Pakets "
            "(--paket mit images.json); aus EXIF allein noch nicht",
        )
    r = _raster(z, lf)
    if isinstance(r, Ergebnis):
        return r
    paket_crs = (lf.paket or {}).get("frame", {}).get("crs", {}).get("wkt")
    if not paket_crs or not CRS.from_user_input(paket_crs).equals(r.crs, ignore_axis_order=True):
        return Ergebnis(NICHT_ERFUELLT, "Fotos und Punktwolke nicht im selben Bezugssystem")
    boden, quelle = _bodenquelle(lf, r)
    werte: list[float] = []
    geneigt = aussen = ohne_boden = 0
    for e in fotos:
        pose, intr = e["pose"], e["intrinsics"]
        t = np.asarray(pose["translation"], float)
        blick = -_paket_rotation(pose["rotationQuat"])[:, 2]
        neigung = float(np.degrees(np.arccos(np.clip(-blick[2], -1.0, 1.0))))
        if neigung > max_neigung:
            geneigt += 1
            continue
        if not r.flaeche.innen(t[:1], t[1:2])[0]:
            aussen += 1
            continue
        # Durchstoßpunkt der optischen Achse: Bodenhöhe unter der Kamera, ein Schritt nachgeführt
        g = boden(t[:1], t[1:2])[0]
        if np.isfinite(g) and t[2] > g:
            p = t + (t[2] - g) / -blick[2] * blick
            g2 = boden(p[:1], p[1:2])[0]
            g = g2 if np.isfinite(g2) else g
        if not np.isfinite(g) or t[2] <= g:
            ohne_boden += 1
            continue
        werte.append(float((t[2] - g) / -blick[2] / float(intr["focalPx"])))
    if not werte:
        return Ergebnis(
            NICHT_ERFUELLT,
            f"kein Luftbild mit Neigung ≤ {max_neigung:g}° über der Auftragsfläche "
            f"({geneigt} geneigter, {aussen} außerhalb, {ohne_boden} ohne Bodenhöhe)",
        )
    w = np.asarray(werte)
    ist = float((w <= soll + 1e-12).mean())
    belege = [
        f"{len(w)} Luftbilder aus images.json des Pakets (Neigung ≤ {max_neigung:g}°, Kamera über "
        f"der Fläche); ausgelassen: {geneigt} geneigter, {aussen} außerhalb, {ohne_boden} ohne "
        "Bodenhöhe",
        f"Bodenauflösung in Bildmitte: Median {_de(1000 * float(np.median(w)))} mm, 95-%-Quantil "
        f"{_de(1000 * float(np.percentile(w, 95)))} mm, Maximum {_de(1000 * float(w.max()))} mm",
        f"Entfernung entlang der Blickachse bis zur Bodenhöhe aus {quelle}",
    ]
    text = f"{_de(100 * ist)} % der Luftbilder mit ≤ {_de(1000 * soll)} mm"
    if ist + 1e-12 < anteil:
        return Ergebnis(NICHT_ERFUELLT, text + f", verlangt {_de(100 * anteil)} %", belege)
    return Ergebnis(ERFUELLT, text, belege)
