"""Boden der Lieferung gegen das Referenz-DGM des Auftraggebers (B1 und L04).

Verglichen wird **je DGM-Pixel**, nicht interpoliert: Mittelwert der Bodenpunkte (Klasse 2) im
Pixel minus DGM-Höhe. Der Mittelwert statt des tiefsten Punkts, weil ein einzelner tiefer
Rauschpunkt sonst das ganze Pixel nach unten zieht.

- ``einpassung`` (L04): mittlere absolute Abweichung über **alle** verglichenen Pixel. Große
  Abweichungen werden nicht als „Dach“ herausgenommen — sonst prüfte die Zeile nur noch, was sie
  vorher durchgelassen hat.
- ``klassen_richtig`` (B1): Anteil der Pixel, deren Boden mehr als die Toleranz vom DGM abweicht,
  getrennt nach darüber (Dächer, Bewuchs als Boden) und darunter (Rauschen, Gruben).

Ohne Referenz-DGM (``--dgm``) sind beide „nicht prüfbar (Grundlage fehlt)“: Das DGM gibt der
Auftraggeber vor, die Lieferung schuldet es nicht.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import laspy
import numpy as np
from PIL import Image

from .bezug import hoehensystem_wolke
from .dgm import Referenz
from .flaechenpruefung import AUSSEN, DUENN, GUT, LEER, _de
from .katalog import KatalogFehler, Zeile
from .lieferung import Lieferung
from .punktraster import BLOCK, BODEN, gemeinsames_crs
from .urteil import ERFUELLT, NICHT_ERFUELLT, NICHT_PRUEFBAR, Ergebnis, _param

OHNE_DGM = "Grundlage fehlt: kein Referenz-DGM angegeben (--dgm)"
UNTER, FREI = (110, 60, 160), (255, 255, 255)


@dataclass
class Vergleich:
    ref: Referenz
    boden: np.ndarray  # Mittel der Klasse-2-Höhen je Pixel, NaN ohne
    tief: np.ndarray  # tiefster Punkt aller Klassen je Pixel, NaN ohne
    betrachtet: np.ndarray  # Pixel, die zählen (Fläche oder Pixel mit Punkten)
    ausserhalb: int  # Punkte außerhalb des DGM-Ausschnitts
    dateien: tuple[str, ...]
    mit_flaeche: bool

    @property
    def verglichen(self) -> np.ndarray:
        return self.betrachtet & np.isfinite(self.ref.z) & np.isfinite(self.boden)

    @property
    def dh(self) -> np.ndarray:
        d: np.ndarray = (self.boden - self.ref.z)[self.verglichen]
        return d


def _vergleich(lf: Lieferung, dateien: list[str]) -> Vergleich | str:
    """Ein Lesedurchgang je Dateiliste (zwischengespeichert); Mangeltext bei falschem Bezug."""
    schluessel = ("bodenvergleich", tuple(dateien))
    if schluessel in lf.cache:
        return lf.cache[schluessel]  # type: ignore[no-any-return]
    ref: Referenz = lf.dgm
    crs = gemeinsames_crs(lf, dateien)
    if isinstance(crs, str):
        return crs
    if not crs.equals(ref.crs, ignore_axis_order=True):
        return f"Lagebezug der Wolke {crs.name} ≠ Referenz-DGM {ref.crs.name}"
    v = hoehensystem_wolke(lf, dateien)
    if v is None:
        return "Höhensystem der Punktwolke nicht (einheitlich) deklariert, nicht vergleichbar"
    if v != ref.hoehe:
        return f"Höhe der Wolke {v} ≠ Referenz-DGM {ref.hoehe}; keine Umrechnung"
    ny, nx = ref.z.shape
    summe = np.zeros(ny * nx)
    anzahl = np.zeros(ny * nx, np.int64)
    tief = np.full(ny * nx, np.inf)
    jede = np.zeros(ny * nx, np.int64)
    ausserhalb = 0
    for rel in dateien:
        with laspy.open(lf.pfad(rel), laz_backend=laspy.LazBackend.LazrsParallel) as f:
            for block in f.chunk_iterator(BLOCK):
                ix, iy = ref.spalte_zeile(np.asarray(block.x), np.asarray(block.y))
                ok = (ix >= 0) & (ix < nx) & (iy >= 0) & (iy < ny)
                ausserhalb += int((~ok).sum())
                idx = iy[ok] * nx + ix[ok]
                z = np.asarray(block.z)[ok]
                b = np.asarray(block.classification)[ok] == BODEN
                summe += np.bincount(idx[b], weights=z[b], minlength=ny * nx)
                anzahl += np.bincount(idx[b], minlength=ny * nx)
                jede += np.bincount(idx, minlength=ny * nx)
                np.minimum.at(tief, idx, z)
    with np.errstate(invalid="ignore", divide="ignore"):
        boden = np.where(anzahl > 0, summe / np.maximum(anzahl, 1), np.nan)
    if lf.flaeche is not None:
        mx, my = ref.mitten()
        betrachtet = lf.flaeche.in_crs(ref.crs).innen(mx, my)
    else:
        betrachtet = (jede > 0).reshape(ny, nx)
    erg = Vergleich(
        ref,
        boden.reshape(ny, nx),
        np.where(np.isfinite(tief), tief, np.nan).reshape(ny, nx),
        betrachtet,
        ausserhalb,
        tuple(dateien),
        lf.flaeche is not None,
    )
    lf.cache[schluessel] = erg
    return erg


def _einstieg(z: Zeile, lf: Lieferung) -> Vergleich | Ergebnis:
    if lf.dgm is None:
        return Ergebnis(NICHT_PRUEFBAR, OHNE_DGM)
    muster = _param(z, "dateien", (str, list), pflicht=False)
    dateien = lf.finde(muster) if muster else lf.von_art("las", "laz")
    if not dateien:
        return Ergebnis(NICHT_ERFUELLT, "keine Punktwolke (LAS/LAZ) in der Lieferung")
    v = _vergleich(lf, dateien)
    if isinstance(v, str):
        return Ergebnis(NICHT_ERFUELLT, v)
    if not np.isfinite(v.boden).any():
        return Ergebnis(
            NICHT_ERFUELLT, "keine Bodenpunkte (Klasse 2) über dem Referenz-DGM, nicht vergleichbar"
        )
    n_betr, n_vgl = int(v.betrachtet.sum()), int(v.verglichen.sum())
    mindest = float(_param(z, "min_ueberdeckung", (int, float), pflicht=False) or 0.0)
    if not 0 <= mindest <= 1:
        raise KatalogFehler(f"{z.id}: min_ueberdeckung ist ein Anteil 0 bis 1")
    if n_vgl == 0:
        return Ergebnis(NICHT_ERFUELLT, "kein Pixel mit Bodenpunkten und DGM-Höhe")
    if n_vgl / n_betr + 1e-12 < mindest:
        return Ergebnis(
            NICHT_ERFUELLT,
            f"nur {_de(100 * n_vgl / n_betr)} % der Pixel vergleichbar, verlangt "
            f"{_de(100 * mindest)} %",
            _grundbelege(v),
        )
    return v


def _grundbelege(v: Vergleich) -> list[str]:
    r = v.ref
    betr = v.betrachtet
    ohne_dgm = int((betr & ~np.isfinite(r.z)).sum())
    ohne_boden = int((betr & np.isfinite(r.z) & ~np.isfinite(v.boden)).sum())
    return [
        "Referenz-DGM: "
        + ", ".join(n for n, _ in r.dateien)
        + f" ({r.px:g} \u00d7 {r.py:g} m, {r.crs.name}, Höhe {r.hoehe}); Angabe des Auftraggebers",
        "Punktdateien: " + ", ".join(v.dateien),
        (
            "betrachtet: DGM-Pixel mit Mittelpunkt in der Auftragsfläche"
            if v.mit_flaeche
            else "betrachtet: DGM-Pixel mit mindestens einem Punkt (keine Auftragsfläche)"
        )
        + f" — {int(betr.sum())}, davon verglichen {int(v.verglichen.sum())}, ohne DGM-Höhe "
        f"{ohne_dgm}, ohne Bodenpunkt {ohne_boden}"
        + (f"; {v.ausserhalb} Punkte außerhalb des DGM-Ausschnitts" if v.ausserhalb else ""),
        "je Pixel: Mittel der Bodenpunkte (Klasse 2) minus DGM-Höhe, nicht interpoliert",
    ]


def einpassung(z: Zeile, lf: Lieferung) -> Ergebnis:
    mad = float(_param(z, "mad_m", (int, float)))
    v = _einstieg(z, lf)
    if not isinstance(v, Vergleich):
        return v
    dh = v.dh
    ist = float(np.abs(dh).mean())
    belege = _grundbelege(v) + [
        f"mittlere absolute Abweichung {_de(ist, 3)} m (erlaubt {_de(mad, 3)} m), Median "
        f"{_de(float(np.median(dh)), 3)} m, 95-%-Quantil |dh| "
        f"{_de(float(np.percentile(np.abs(dh), 95)), 3)} m, Anteil |dh| > 1 m "
        f"{_de(100 * float((np.abs(dh) > 1).mean()))} %",
        "alle verglichenen Pixel zählen, auch große Abweichungen (z. B. Dach als Boden)",
    ]
    text = f"mittlere absolute Abweichung zum Referenz-DGM {_de(ist, 3)} m"
    nur = "; nur Höhe gegen DGM (bDOM und Lage: J4)"
    if ist > mad + 1e-12:
        return Ergebnis(NICHT_ERFUELLT, text + f", erlaubt {_de(mad, 3)} m" + nur, belege)
    return Ergebnis(ERFUELLT, text + nur, belege)


def karte(v: Vergleich, toleranz: float, breite_px: int = 900) -> bytes:
    """Pixelkarte (Norden oben): Boden passt dunkelblau, darüber rot, darunter violett,
    Boden nicht klassifiziert hellblau, ohne Boden in DGM-Nähe weiß, außerhalb grau."""
    r = v.ref
    rgb = np.empty((*r.z.shape, 3), np.uint8)
    rgb[:] = AUSSEN
    mit_dgm = v.betrachtet & np.isfinite(r.z)
    rgb[mit_dgm] = FREI
    nahe = np.abs(v.tief - r.z) <= toleranz
    rgb[mit_dgm & ~np.isfinite(v.boden) & nahe] = DUENN
    dh = v.boden - r.z
    vgl = v.verglichen
    rgb[vgl & (np.abs(dh) <= toleranz)] = GUT
    rgb[vgl & (dh > toleranz)] = LEER
    rgb[vgl & (dh < -toleranz)] = UNTER
    # auf die betrachteten Pixel zuschneiden (das DGM ist oft größer als Fläche oder Wolke)
    zeilen, spalten = np.flatnonzero(v.betrachtet.any(1)), np.flatnonzero(v.betrachtet.any(0))
    if zeilen.size:
        rgb = rgb[zeilen[0] : zeilen[-1] + 1, spalten[0] : spalten[-1] + 1]
    ny, nx = rgb.shape[:2]
    f = max(breite_px // nx, 1) if nx <= breite_px else breite_px / nx
    bild = Image.fromarray(rgb).resize(
        (max(int(nx * f), 1), max(int(ny * f), 1)), Image.Resampling.NEAREST
    )
    puffer = io.BytesIO()
    bild.save(puffer, format="PNG", optimize=False)
    return puffer.getvalue()


def klassen_richtig(z: Zeile, lf: Lieferung) -> Ergebnis:
    toleranz = float(_param(z, "toleranz_m", (int, float)))
    anteil = float(_param(z, "anteil", (int, float)))
    if toleranz <= 0 or not 0 <= anteil <= 1:
        raise KatalogFehler(f"{z.id}: toleranz_m > 0, anteil 0 bis 1")
    v = _einstieg(z, lf)
    if not isinstance(v, Vergleich):
        return v
    dh = v.dh
    n = dh.size
    ueber, unter = int((dh > toleranz).sum()), int((dh < -toleranz).sum())
    falsch = (ueber + unter) / n
    r = v.ref
    mit_dgm = v.betrachtet & np.isfinite(r.z)
    fehlt = int((mit_dgm & ~np.isfinite(v.boden) & (np.abs(v.tief - r.z) <= toleranz)).sum())
    pix = r.px * r.py
    belege = _grundbelege(v) + [
        f"Boden weicht mehr als {_de(toleranz, 2)} m ab: {ueber + unter} von {n} Pixeln = "
        f"{_de(100 * falsch, 2)} % (erlaubt {_de(100 * anteil, 2)} %) — darüber {ueber} "
        f"({_de(ueber * pix, 0)} m², z. B. Dach oder Bewuchs als Boden), darunter {unter} "
        f"({_de(unter * pix, 0)} m²)",
        f"größte Abweichung {_de(float(dh.max()), 2)} m darüber, {_de(float(dh.min()), 2)} m "
        f"darunter; Median {_de(float(np.median(dh)), 3)} m (systematischer Versatz → L04)",
        f"Hinweis, nicht bewertet: {fehlt} Pixel ohne Bodenpunkt, deren tiefster Punkt innerhalb "
        f"{_de(toleranz, 2)} m am DGM liegt (Boden womöglich nicht klassifiziert)",
        "Karte: dunkelblau Boden passt, rot Boden über dem DGM, violett darunter, hellblau Boden "
        "nicht klassifiziert, weiß kein Boden in DGM-Nähe (Gebäude, Bewuchs), grau außerhalb",
    ]
    bilder = {"boden": karte(v, toleranz)}
    text = f"{_de(100 * falsch, 2)} % der Pixel mit Boden mehr als {_de(toleranz, 2)} m vom DGM"
    if falsch > anteil + 1e-12:
        return Ergebnis(
            NICHT_ERFUELLT, text + f", erlaubt {_de(100 * anteil, 2)} %", belege, bilder
        )
    return Ergebnis(ERFUELLT, text, belege, bilder)
