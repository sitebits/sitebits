"""Sonnenbericht Garten: direkte Sonnenstunden je Stichpunkt auf dem Boden und Lichtkategorie.

Reihenfolge: Mesh lesen → Boden im Grundriss (Strahl von unten, Fenster um die Eckenebene) →
Richtungskarte → dieselbe mit geschlossenen Baumkronen (Gegenlauf) → Fernhorizont aus PVGIS
`printhorizon` → Sonnenstände in Viertelstunden des Bezugsjahres (NREL SPA) → Stunden je Punkt und
Monat → Kategorie nach RHS
(Mittsommer = Juni/Juli-Mittel), Schwellen offen, Grenzbereich ausgewiesen.

Die Stunden sind geometrisch (Sonne sichtbar, ohne Wolken), so wie die RHS-Schwellen gemeint sind.
Baumkronen sind im Mesh aus Fotos löchrig; deshalb wird zweimal gerechnet: mit dem Mesh wie es
ist (eher zu viel Sonne) und mit einem Kronendach, das jede Zelle über Wuchshöhe schließt (eher zu
wenig).
Der Bericht nennt die Spanne.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy import ndimage

from ..manifest_io import origin_of, read_manifest
from ..mesh_source import read_mesh
from . import ertrag, geo, pvgis
from .flaeche import Flaeche, boden_aufbauen, ecken_lesen
from .rechnung import (
    BEZUGSJAHR,
    MAX_PUNKTE,
    Lauf,
    _quelle,
    _sha256,
    aufnahme,
    lage_und_massstab,
    mesh_ebene,
    versionen,
)
from .richtungskarte import (
    Raster,
    fern_maske,
    horizont_bei,
    karte_rechnen,
    nachschlagen,
    sichtanteile,
)
from .strahlen import Szene

# RHS „Shade gardening“: > 6 h sonnig, 3 bis 6 h Halbschatten, 2 bis 3 h mäßiger Schatten,
# < 2 h Schatten, direkte Sonne je Tag im Mittsommer. Nicht genormt; Grenzbereich ±0,4 h
# (Vorschlag der Recherche).
SCHWELLEN_H = (2.0, 3.0, 6.0)
KATEGORIEN = ("schattig", "mäßig schattig", "halbschattig", "sonnig")
GRENZBEREICH_H = 0.4
MITTSOMMER = (6, 7)
VEGETATIONSZEIT = (4, 5, 6, 7, 8, 9)
SCHWELLEN_QUELLE = "RHS „Shade gardening“ (rhs.org.uk/advice/profile?pid=934)"
# Kronendach für den Gegenlauf: höchste Mesh-Ecke je Zelle, Lücken von einer Zelle geschlossen.
KRONEN_ZELLE_M = 0.5
KRONEN_HOEHE_MIN_M = 2.0
KRONEN_UMKREIS_M = 40.0
KRONEN_BODEN_FENSTER_M = 5.0
KRONEN_BODEN_PERZENTIL = 10


@dataclass
class GartenAuswertung:
    ergebnis: dict[str, Any]
    flaeche: Flaeche
    stunden_monat: np.ndarray  # (P, 12) direkte Sonne h je Tag, Monatsmittel
    mittsommer: np.ndarray  # (P,) h je Tag
    kategorie: np.ndarray  # (P,) Index in KATEGORIEN
    grenzbereich: np.ndarray  # (P,) bool
    himmelssicht: np.ndarray  # (P,) kosinusgewichtet, relativ zu unverbaut
    stunden_zu: np.ndarray  # (P, 12) wie oben, Kronen geschlossen
    mittsommer_zu: np.ndarray
    kategorie_zu: np.ndarray
    grenzbereich_zu: np.ndarray
    lauf: Lauf


def kategorie(stunden: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Index in KATEGORIEN (Schwelle zählt zur oberen Klasse) und Grenzbereich ±GRENZBEREICH_H."""
    s = np.asarray(stunden, dtype=np.float64)
    idx = np.searchsorted(np.asarray(SCHWELLEN_H), s, side="right")
    abstand = np.min(np.abs(s[..., None] - np.asarray(SCHWELLEN_H)), axis=-1)
    return idx, abstand <= GRENZBEREICH_H + 1e-9


def unsicher(
    kat: np.ndarray, grenz: np.ndarray, kat_zu: np.ndarray, grenz_zu: np.ndarray
) -> np.ndarray:
    """Lichtlage hängt von den Kronen ab oder liegt in einer der Rechnungen im Grenzbereich."""
    return np.asarray((kat != kat_zu) | grenz | grenz_zu)


def tagesmittel(stunden_monat: np.ndarray, monate: tuple[int, ...], jahr: int) -> np.ndarray:
    """Mittel h je Tag über die genannten Monate, nach Tagen gewichtet."""
    tage = np.array([pd.Period(f"{jahr}-{m:02d}").days_in_month for m in monate], dtype=np.float64)
    spalten = np.asarray(stunden_monat)[..., [m - 1 for m in monate]]
    return np.asarray(spalten @ tage / tage.sum())


def sonnenschritte(
    sonne: pd.DataFrame, konvergenz: float, horizont: tuple[np.ndarray, np.ndarray]
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Je Zeitschritt: über dem Horizont, über dem Geländehorizont; dort Gitter-Azimut und Höhe.

    Fläche waagerecht: vor der Fläche heißt über dem Horizont. Der Fernhorizont wird exakt am
    Sonnenstand angewandt, nicht über das Richtungsraster.
    """
    h = sonne["apparent_elevation"].to_numpy()
    az = sonne["azimuth"].to_numpy()
    ueber = h > 0
    fern = ueber & (h > horizont_bei(az, *horizont))
    return ueber, fern, az[fern] + konvergenz, h[fern]


def sonnenstunden(
    frei: np.ndarray,
    raster: Raster,
    konvergenz: float,
    horizont: tuple[np.ndarray, np.ndarray],
    sonne: pd.DataFrame,
) -> tuple[np.ndarray, dict[str, np.ndarray]]:
    """(P, 12) direkte Sonne h je Tag (Monatsmittel) aus der Richtungskarte `frei` (P, nAz, nH).

    `sonne`: Sonnenstände (pvlib) in gleichen Schritten, Index in UTC.
    """
    zeit = pd.DatetimeIndex(sonne.index)
    tage = np.array(
        [pd.Period(f"{zeit[0].year}-{m:02d}").days_in_month for m in range(1, 13)], np.float64
    )
    schritt_h = (zeit[1] - zeit[0]).total_seconds() / 3600
    monat = zeit.month.to_numpy() - 1
    ueber, fern, a_g, h_g = sonnenschritte(sonne, konvergenz, horizont)
    gewicht = np.zeros((int(fern.sum()), 12), dtype=np.float32)
    gewicht[np.arange(len(gewicht)), monat[fern]] = schritt_h / tage[monat[fern]]
    aus = np.empty((len(frei), 12), dtype=np.float64)
    for a in range(0, len(frei), 256):
        sicht = nachschlagen(frei[a : a + 256], raster, a_g, h_g).astype(np.float32)
        aus[a : a + 256] = sicht @ gewicht
    je_monat = {
        "ueber_horizont": np.bincount(monat[ueber], minlength=12) * schritt_h / tage,
        "nur_fern": np.bincount(monat[fern], minlength=12) * schritt_h / tage,
    }
    return aus, je_monat


def kronendach(
    positionen: np.ndarray, mitte: np.ndarray, bodenpunkte: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray, dict[str, float]]:
    """Deckflächen (Positionen, Dreiecke) über allem, was mehr als KRONEN_HOEHE_MIN_M über dem Boden
    steht, im Umkreis um `mitte` (lokal). Je Zelle die höchste Mesh-Ecke, einzelne Lücken per 3x3-
    Maximum geschlossen (Kronen dadurch um bis zu eine Zelle breiter). Pessimistisch: Kronen gelten
    als ganz dicht.

    Boden je Zelle: 10. Perzentil der tiefsten Ecken im Umkreis von KRONEN_BODEN_FENSTER_M (eine
    Senke daneben macht den Garten nicht zur Krone); wo `bodenpunkte` (ausgewertete Stichpunkte)
    liegen, zählt deren Höhe. So liegt kein Deckel weniger als KRONEN_HOEHE_MIN_M über einem
    ausgewerteten Punkt.
    """
    z = KRONEN_ZELLE_M
    lo = np.asarray(mitte[:2]) - KRONEN_UMKREIS_M
    n = round(2 * KRONEN_UMKREIS_M / z)
    q = positionen[np.abs(positionen[:, :2] - mitte[:2]).max(axis=1) < KRONEN_UMKREIS_M]
    ij = np.clip(np.floor((q[:, :2] - lo) / z).astype(np.int64), 0, n - 1)
    zmax = np.full((n, n), -np.inf)
    zmin = np.full((n, n), np.inf)
    np.maximum.at(zmax, (ij[:, 0], ij[:, 1]), q[:, 2])
    np.minimum.at(zmin, (ij[:, 0], ij[:, 1]), q[:, 2])
    leer = ~np.isfinite(zmin)
    boden = ndimage.percentile_filter(
        np.where(leer, np.finfo(np.float64).max, zmin),
        KRONEN_BODEN_PERZENTIL,
        size=2 * round(KRONEN_BODEN_FENSTER_M / z) + 1,
    )
    boden[boden >= np.finfo(np.float64).max] = np.inf
    if bodenpunkte is not None and len(bodenpunkte):
        bij = np.floor((bodenpunkte[:, :2] - lo) / z).astype(np.int64)
        drin = ((bij >= 0) & (bij < n)).all(axis=1)
        gemessen = np.full((n, n), -np.inf)
        np.maximum.at(gemessen, (bij[drin, 0], bij[drin, 1]), bodenpunkte[drin, 2])
        boden = np.where(np.isfinite(gemessen), gemessen, boden)
    dach = ndimage.maximum_filter(zmax, size=3)
    ii, jj = np.nonzero(
        np.isfinite(dach) & np.isfinite(boden) & (dach - boden > KRONEN_HOEHE_MIN_M)
    )
    x0, y0, h = lo[0] + ii * z, lo[1] + jj * z, dach[ii, jj]
    ecken = np.stack(
        [np.c_[x0, y0, h], np.c_[x0 + z, y0, h], np.c_[x0 + z, y0 + z, h], np.c_[x0, y0 + z, h]],
        axis=1,
    ).reshape(-1, 3)
    b = 4 * np.arange(len(ii))
    dreiecke = np.concatenate([np.c_[b, b + 1, b + 2], np.c_[b, b + 2, b + 3]])
    info = {
        "zellen": len(ii),
        "flaeche_m2": round(len(ii) * z * z, 1),
        "zelle_m": z,
        "hoehe_min_m": KRONEN_HOEHE_MIN_M,
        "umkreis_m": KRONEN_UMKREIS_M,
        "boden_fenster_m": KRONEN_BODEN_FENSTER_M,
    }
    return ecken, dreiecke, info


def _licht(
    stunden: np.ndarray, him: np.ndarray, abdeckung: float
) -> tuple[dict[str, Any], np.ndarray, np.ndarray, np.ndarray]:
    mittsommer = tagesmittel(stunden, MITTSOMMER, BEZUGSJAHR)
    vegetation = tagesmittel(stunden, VEGETATIONSZEIT, BEZUGSJAHR)
    kat, grenz = kategorie(mittsommer)
    licht = {
        "mittsommer_h": _verteilung(mittsommer),
        "vegetationszeit_h": _verteilung(vegetation),
        "himmelssicht": _verteilung(him, 4),
        "kategorien": [
            {
                "name": name,
                "anteil": round(float((kat == i).sum() / len(kat)), 4),
                "davon_grenzbereich": round(float(((kat == i) & grenz).sum() / len(kat)), 4),
            }
            for i, name in enumerate(KATEGORIEN)
        ],
        "grenzbereich_anteil": round(float(grenz.mean()), 4),
        "abdeckung": round(abdeckung, 4),
    }
    return licht, mittsommer, kat, grenz


def _verteilung(x: np.ndarray, stellen: int = 2) -> dict[str, float]:
    return {
        "mittel": round(float(x.mean()), stellen),
        "p10": round(float(np.percentile(x, 10)), stellen),
        "p50": round(float(np.median(x)), stellen),
        "p90": round(float(np.percentile(x, 90)), stellen),
        "min": round(float(x.min()), stellen),
        "max": round(float(x.max()), stellen),
    }


def auswerten_garten(
    paket: Path,
    flaeche_datei: Path,
    cache: Path,
    *,
    raster_m: float = 0.2,
    richtungsraster: Raster | None = None,
    ebene: str | None = None,
    offline: bool = False,
    fenster_m: float = 1.0,
    aufnahme_angaben: dict[str, str] | None = None,
) -> GartenAuswertung:
    lauf = Lauf()
    rr = richtungsraster or Raster()
    manifest = read_manifest(paket)
    ursprung_welt = np.asarray(origin_of(manifest), dtype=np.float64)
    crs = manifest["frame"]["crs"]["wkt"] if "frame" in manifest else manifest["crs"]
    schicht = mesh_ebene(manifest, ebene)
    netz = read_mesh(paket / schicht["url"])
    lauf.schritt("mesh_lesen")
    ecken_welt = ecken_lesen(flaeche_datei)
    ecken = ecken_welt - ursprung_welt
    zentrum = ecken.mean(axis=0)
    szene = Szene(netz.positions, netz.triangles, zentrum)
    lauf.schritt("szene_bauen")
    fl = boden_aufbauen(ecken, szene, raster_m, fenster_m)
    if len(fl.punkte) > MAX_PUNKTE:
        raise ValueError(
            f"{len(fl.punkte)} Stichpunkte > {MAX_PUNKTE}: Raster vergrößern (--raster)"
        )
    lauf.schritt("boden")
    verdeckt = karte_rechnen(szene, fl.ursprung, fl.normale, fl.tnear, rr)
    lauf.schritt("richtungskarte")
    k_pos, k_tri, kronen = kronendach(netz.positions, zentrum, fl.punkte)
    szene_zu = Szene(
        np.concatenate([netz.positions, k_pos]),
        np.concatenate([netz.triangles, k_tri + len(netz.positions)]),
        zentrum,
    )
    verdeckt_zu = karte_rechnen(szene_zu, fl.ursprung, fl.normale, fl.tnear, rr)
    del szene_zu
    lauf.schritt("kronen_geschlossen")

    mitte = zentrum + ursprung_welt
    breite, laenge = geo.lage_geo(crs, float(mitte[0]), float(mitte[1]))
    konv = geo.konvergenz_grad(crs, breite, laenge)
    hor_az, hor_h, a_hor = pvgis.horizont(pvgis.Pvgis(cache, offline=offline), breite, laenge)
    lauf.schritt("pvgis")

    fern = fern_maske(rr, hor_az, hor_h, konv)[None]
    him, _ = sichtanteile((~verdeckt & ~fern).astype(np.float32), rr, fl.normale)
    him_zu, _ = sichtanteile((~verdeckt_zu & ~fern).astype(np.float32), rr, fl.normale)
    hoehe_boden = float(np.median(fl.punkte[:, 2]) + ursprung_welt[2])
    ref = pd.date_range(
        f"{BEZUGSJAHR}-01-01 00:07:30", f"{BEZUGSJAHR}-12-31 23:52:30", freq="15min", tz="UTC"
    )
    sonne = ertrag.sonnenstand(ref, breite, laenge, hoehe_boden)
    hor = (hor_az, hor_h)
    stunden, je_monat = sonnenstunden((~verdeckt).astype(np.float32), rr, konv, hor, sonne)
    stunden_zu, _ = sonnenstunden((~verdeckt_zu).astype(np.float32), rr, konv, hor, sonne)
    lauf.schritt("sonnenstunden")

    gesamt = len(fl.punkte) + fl.ohne_mesh
    licht, mittsommer, kat, grenz = _licht(stunden, him, len(fl.punkte) / gesamt)
    licht_zu, mittsommer_zu, kat_zu, grenz_zu = _licht(stunden_zu, him_zu, len(fl.punkte) / gesamt)
    lage = lage_und_massstab(paket, manifest, schicht)
    lage["flaechenbezug"] = "Grundriss (horizontal)"
    e: dict[str, Any] = {
        "eingaben": {
            "paket": manifest.get("projectId"),
            "mesh_ebene": schicht.get("id"),
            "mesh_url": schicht["url"],
            "mesh_sha256": _sha256(paket / schicht["url"]),
            "dreiecke": int(szene.dreiecke),
            "flaeche_datei": flaeche_datei.name,
            "flaeche_sha256": _sha256(flaeche_datei),
            "ecken_welt": np.round(ecken_welt, 4).tolist(),
            "raster_m": raster_m,
            "richtungsraster_grad": [rr.az_schritt, rr.h_schritt],
            "bezugsjahr_sonnenstunden": BEZUGSJAHR,
            "suchfenster_m": fenster_m,
            "schwellen_h": list(SCHWELLEN_H),
            "grenzbereich_h": GRENZBEREICH_H,
        },
        "lage": lage,
        "aufnahme": aufnahme(paket, schicht, aufnahme_angaben),
        "ort": {
            "crs": crs,
            "breite": round(breite, 6),
            "laenge": round(laenge, 6),
            "konvergenz_grad": round(konv, 4),
            "hoehe_boden_m": round(hoehe_boden, 2),
        },
        "flaeche": {
            "flaeche_m2": round(fl.flaeche_m2, 3),
            "stichpunkte": len(fl.punkte),
            "ohne_mesh": fl.ohne_mesh,
            "boden_hoehe_m": _verteilung(fl.punkte[:, 2] + ursprung_welt[2]),
            "rauheit_p95_m": round(fl.rauheit_p95, 4),
            "mindestabstand_m": round(fl.tnear, 4),
            "ebenheit_ecken_m": round(fl.ebenheit_m, 4),
            "neigung_boden_mittel_grad": round(fl.normalen_streuung_grad, 2),
        },
        "licht": licht,
        "licht_kronen_zu": licht_zu,
        "kronen": kronen,
        "unsicher_anteil": round(float(unsicher(kat, grenz, kat_zu, grenz_zu).mean()), 4),
        "monate": [
            {
                "monat": m + 1,
                "ueber_horizont_h": round(float(je_monat["ueber_horizont"][m]), 2),
                "nur_fernhorizont_h": round(float(je_monat["nur_fern"][m]), 2),
                **{k: v for k, v in _verteilung(stunden[:, m]).items() if k != "p50"},
                "mittel_kronen_zu": round(float(stunden_zu[:, m].mean()), 2),
            }
            for m in range(12)
        ],
        "quellen": [_quelle(a_hor)],
        "versionen": versionen(),
    }
    lauf.schritt("zusammenfassen")
    return GartenAuswertung(
        e,
        fl,
        stunden,
        mittsommer,
        kat,
        grenz,
        him,
        stunden_zu,
        mittsommer_zu,
        kat_zu,
        grenz_zu,
        lauf,
    )
