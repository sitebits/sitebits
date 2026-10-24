"""Rechenweg einer Fläche: Paket + Flächenmessung + Anlage → Ergebnis (JSON-fähig) und
Stichpunktwerte.

Reihenfolge: Mesh lesen (Paket-Lokalrahmen) → Fläche/Stichpunkte → Richtungskarte (einziger Raycast)
→
PVGIS (Reihe 2005-2023, Typjahr, Fernhorizont, PVcalc-Gegenprobe) → Ertrag ohne/mit Fernhorizont/mit
allem →
Sonnenstunden je Monat (Viertelstunden eines Bezugsjahres) → Werte je Stichpunkt (Typjahr) für die
Wärmekarte.
"""

from __future__ import annotations

import hashlib
import json
import platform
import resource
import time
from dataclasses import dataclass, field, replace
from importlib import metadata
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from pvlib import irradiance

from ..berichtsatz import git_stand
from ..manifest_io import origin_of, read_manifest
from ..mesh_source import read_mesh
from . import ertrag, geo, pvgis
from .flaeche import Flaeche, ecken_lesen, flaeche_aufbauen
from .richtungskarte import (
    Raster,
    direkt_funktion,
    fern_maske,
    karte_rechnen,
    nachschlagen,
    sichtanteile,
)
from .strahlen import Szene

BEZUGSJAHR = 2023
MAX_PUNKTE = 20000


@dataclass
class Lauf:
    """Laufprotokoll (nicht Teil des Ergebnis-Hashes): Dauer je Schritt, Spitzen-RAM."""

    schritte: dict[str, float] = field(default_factory=dict)
    _t: float = field(default_factory=time.monotonic)

    def schritt(self, name: str) -> None:
        jetzt = time.monotonic()
        self.schritte[name] = round(jetzt - self._t, 2)
        self._t = jetzt

    def als_dict(self) -> dict[str, Any]:
        return {
            "sekunden": self.schritte,
            "gesamt_s": round(sum(self.schritte.values()), 1),
            "ram_spitze_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        }


@dataclass
class Auswertung:
    ergebnis: dict[str, Any]
    flaeche: Flaeche
    punkt_kwh_m2: np.ndarray
    punkt_anteil: np.ndarray
    lauf: Lauf


def _sha256(pfad: Path) -> str:
    h = hashlib.sha256()
    with pfad.open("rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def versionen() -> dict[str, str]:
    v = {
        p: metadata.version(p)
        for p in ("scanview-import", "pvlib", "embreex", "numpy", "pandas", "pyproj")
    }
    v["python"] = platform.python_version()
    v["git"] = git_stand(Path(__file__).resolve().parent)
    return v


def mesh_ebene(manifest: dict[str, Any], ebene: str | None) -> dict[str, Any]:
    netze = [la for la in manifest["layers"] if la.get("kind") == "mesh"]
    if ebene is not None:
        netze = [la for la in netze if la.get("id") == ebene]
    if not netze:
        raise ValueError("Paket hat keine (passende) Mesh-Ebene")
    return dict(netze[0])


def _jahressummen(r: pd.DataFrame, spalte: str) -> pd.Series:
    return r[spalte].groupby(r.index.year).sum() / 1000.0


def _monatsband(r: pd.DataFrame, spalte: str) -> list[dict[str, float]]:
    s = r[spalte].groupby([r.index.year, r.index.month]).sum().unstack() / 1000.0
    return [
        {
            "monat": int(m),
            "mittel": round(float(s[m].mean()), 2),
            "p50": round(float(s[m].median()), 2),
            "p90": round(float(np.percentile(s[m], 10)), 2),
        }
        for m in s.columns
    ]


def lage_und_massstab(
    paket: Path, manifest: dict[str, Any], schicht: dict[str, Any]
) -> dict[str, Any]:
    """Koordinaten-/Höhensystem, Einpassung (falls vermerkt), Genauigkeitsnachweis ja/nein."""
    frame = manifest.get("frame", {})
    lage = (schicht.get("sourceOptions") or {}).get("lage") or {}
    nach = lage.get("pruefung_nach") or {}
    einpassung = None
    if lage:
        einpassung = {
            "drehung_grad": lage.get("drehung_grad"),
            "verschiebung_m": lage.get("verschiebung_m"),
            "rest_neigung_dsm_bdom_grad": nach.get("dsm_bdom_neigung_grad"),
            "rest_versatz_dsm_bdom_m": nach.get("dsm_bdom_versatz_m"),
            "rest_mad_dsm_bdom_m": nach.get("dsm_bdom_rest_mad_m"),
            "rest_mad_dtm_dgm1_m": nach.get("dtm_dgm1_rest_mad_m"),
        }
    nachweis = (paket / "genauigkeit.json").is_file()
    return {
        "crs": (frame.get("crs") or {}).get("wkt"),
        "hoehensystem": (frame.get("crs") or {}).get("verticalDatum"),
        "einpassung": einpassung,
        "genauigkeit": "Genauigkeitsnachweis im Paket" if nachweis else "Modellmaß, nicht belegt",
        "flaechenbezug": "geneigt (Ausgleichsebene)",
    }


AUFNAHME_FELDER = ("datum", "geraet", "flughoehe", "wetter", "belaubung", "pilot", "flugrecht")


def aufnahme(
    paket: Path, schicht: dict[str, Any], angaben: dict[str, str] | None = None
) -> dict[str, Any]:
    """Aufnahme: Herkunft des Modells, Fototage aus `images.json`, übrige Felder aus der CLI."""
    q = schicht.get("source") or {}
    fototage: list[str] = []
    fotos = 0
    bilder = paket / "images.json"
    if bilder.is_file():
        daten = json.loads(bilder.read_text())
        fotos = len(daten.get("images", []))
        tage = {str(g.get("id", "")).split("/")[0] for g in daten.get("groups", [])}
        fototage = sorted(t for t in tage if len(t) == 10 and t[4] == "-" and t[7] == "-")
    angaben = {k: v for k, v in (angaben or {}).items() if v}
    unbekannt = set(angaben) - set(AUFNAHME_FELDER)
    if unbekannt:
        raise ValueError(f"Unbekannte Aufnahmeangaben: {sorted(unbekannt)}")
    return {
        "quelldatei": q.get("filename"),
        "quelle_sha256": q.get("sha256"),
        "erzeuger": q.get("producer"),
        "dreiecke_quelle": schicht.get("triangleCount"),
        "fotos_im_paket": fotos,
        "fototage": fototage,
        "angaben": angaben,
    }


def _quelle(a: pvgis.Abruf) -> dict[str, str]:
    return {"werkzeug": a.url.split("/")[-1].split("?")[0], "url": a.url, "abgerufen": a.abgerufen}


def auswerten(
    paket: Path,
    flaeche_datei: Path,
    anlage: ertrag.Anlage,
    cache: Path,
    *,
    raster_m: float = 0.1,
    richtungsraster: Raster | None = None,
    ebene: str | None = None,
    offline: bool = False,
    neigung: float | None = None,
    azimut_geo: float | None = None,
    aufnahme_angaben: dict[str, str] | None = None,
) -> Auswertung:
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
    fl = flaeche_aufbauen(ecken, szene, raster_m)
    if len(fl.punkte) > MAX_PUNKTE:
        raise ValueError(
            f"{len(fl.punkte)} Stichpunkte > {MAX_PUNKTE}: Raster vergrößern (--raster)"
        )
    lauf.schritt("flaeche")
    verdeckt = karte_rechnen(szene, fl.ursprung, fl.normale, fl.tnear, rr)
    lauf.schritt("richtungskarte")

    mitte = zentrum + ursprung_welt
    breite, laenge = geo.lage_geo(crs, float(mitte[0]), float(mitte[1]))
    konv = geo.konvergenz_grad(crs, breite, laenge)
    neig_mod, az_gitter = geo.neigung_azimut(fl.normale)
    az_mod = float(geo.gitter_nach_geo(az_gitter, konv))
    neig = neig_mod if neigung is None else neigung
    az = az_mod if azimut_geo is None else azimut_geo
    n_rechnen = geo.richtung(np.array(float(geo.geo_nach_gitter(az, konv))), np.array(90.0 - neig))

    p = pvgis.Pvgis(cache, offline=offline)
    reihe, a_reihe = pvgis.jahresreihe(p, breite, laenge)
    tmy, a_tmy = pvgis.typjahr(p, breite, laenge)
    hor_az, hor_h, a_hor = pvgis.horizont(p, breite, laenge)
    a_calc = pvgis.pvcalc(p, breite, laenge, neig, az, anlage.wp / 1000.0, anlage.verluste_prozent)
    hoehe_ort = float(a_reihe.antwort["inputs"]["location"]["elevation"])
    lauf.schritt("pvgis")

    fern = fern_maske(rr, hor_az, hor_h, konv)
    frei_alle = (~verdeckt & ~fern[None]).astype(np.float32)
    frei_fern = np.broadcast_to(~fern, verdeckt.shape[1:]).astype(np.float32)[None]
    him_p, band_p = sichtanteile(frei_alle, rr, n_rechnen)
    him_f, band_f = sichtanteile(frei_fern, rr, n_rechnen)
    mittel = frei_alle.mean(axis=0)
    s_alle = ertrag.Verschattung(
        direkt_funktion(mittel, rr, konv, (hor_az, hor_h)),
        float(him_p.mean()),
        float(band_p.mean()),
    )
    s_fern = ertrag.Verschattung(
        direkt_funktion(np.ones_like(mittel), rr, konv, (hor_az, hor_h)),
        float(him_f[0]),
        float(band_f[0]),
    )
    sonne_r = ertrag.sonnenstand(pd.DatetimeIndex(reihe.index), breite, laenge, hoehe_ort)
    ohne_kappung = replace(anlage, kappung_w=None)
    r_frei = ertrag.rechnen(reihe, breite, laenge, hoehe_ort, neig, az, anlage, None, sonne_r)
    r_fern = ertrag.rechnen(reihe, breite, laenge, hoehe_ort, neig, az, anlage, s_fern, sonne_r)
    r_alle = ertrag.rechnen(reihe, breite, laenge, hoehe_ort, neig, az, anlage, s_alle, sonne_r)
    r_gegen = ertrag.rechnen(
        reihe, breite, laenge, hoehe_ort, neig, az, ohne_kappung, None, sonne_r
    )
    lauf.schritt("ertrag_reihe")

    sonne_t = ertrag.sonnenstand(pd.DatetimeIndex(tmy.index), breite, laenge, hoehe_ort)
    t_frei = ertrag.rechnen(tmy, breite, laenge, hoehe_ort, neig, az, anlage, None, sonne_t)
    t_alle = ertrag.rechnen(tmy, breite, laenge, hoehe_ort, neig, az, anlage, s_alle, sonne_t)
    tag = (t_frei["sonne_hoehe"] > 0).to_numpy()
    sa = t_frei["sonne_azimut"].to_numpy()[tag]
    sh = t_frei["sonne_hoehe"].to_numpy()[tag]
    fern_t = sh > np.interp(sa, hor_az, hor_h, period=360.0)
    dz = t_frei[["direkt", "zirkumsolar"]].sum(axis=1).to_numpy()[tag]
    summe_dz = np.zeros(len(fl.punkte))
    for a in range(0, len(fl.punkte), 256):
        vis = nachschlagen(frei_alle[a : a + 256], rr, sa + konv, sh) * fern_t
        summe_dz[a : a + 256] = vis @ dz
    poa_frei = float(t_frei["poa"].sum())
    punkt_wh = (
        summe_dz
        + him_p * t_frei["isotrop"].sum()
        + band_p * t_frei["horizont"].sum()
        + t_frei["boden"].sum()
    )
    lauf.schritt("stichpunkte")

    ref = pd.date_range(
        f"{BEZUGSJAHR}-01-01 00:07:30", f"{BEZUGSJAHR}-12-31 23:52:30", freq="15min", tz="UTC"
    )
    s_ref = ertrag.sonnenstand(ref, breite, laenge, hoehe_ort)
    h_ref = s_ref["apparent_elevation"].to_numpy()
    a_ref = s_ref["azimuth"].to_numpy()
    cos_e = np.cos(
        np.radians(np.asarray(irradiance.aoi(neig, az, s_ref["apparent_zenith"], a_ref), float))
    )
    vor = (h_ref > 0) & (cos_e > 0)
    besonnt = np.where(vor, s_alle.direkt(a_ref, h_ref), 0.0)
    besonnt_fern = np.where(vor, s_fern.direkt(a_ref, h_ref), 0.0)
    df = (
        pd.DataFrame(
            {"vor": vor * 0.25, "besonnt": besonnt * 0.25, "fern": besonnt_fern * 0.25}, index=ref
        )
        .groupby(ref.month)
        .sum()
    )
    lauf.schritt("sonnenstunden")

    jahre_alle = _jahressummen(r_alle, "p_ac")
    e_y = float(a_calc.antwort["outputs"]["totals"]["fixed"]["E_y"])
    gegen = float(_jahressummen(r_gegen, "p_ac").mean())
    kappung_verlust = float(
        (r_alle["p_ac_ohne_kappung"] - r_alle["p_ac"]).groupby(r_alle.index.year).sum().mean()
        / 1000
    )
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
            "anlage": anlage.als_dict(),
            "raster_m": raster_m,
            "richtungsraster_grad": [rr.az_schritt, rr.h_schritt],
            "bezugsjahr_sonnenstunden": BEZUGSJAHR,
            "neigung_vorgegeben": neigung,
            "azimut_vorgegeben": azimut_geo,
        },
        "lage": lage_und_massstab(paket, manifest, schicht),
        "aufnahme": aufnahme(paket, schicht, aufnahme_angaben),
        "ort": {
            "crs": crs,
            "breite": round(breite, 6),
            "laenge": round(laenge, 6),
            "konvergenz_grad": round(konv, 4),
            "hoehe_pvgis_m": hoehe_ort,
        },
        "flaeche": {
            "flaeche_m2": round(fl.flaeche_m2, 3),
            "stichpunkte": len(fl.punkte),
            "ohne_mesh": fl.ohne_mesh,
            "neigung_grad": round(neig_mod, 2),
            "azimut_geo_grad": round(az_mod, 2),
            "azimut_gitter_grad": round(az_gitter, 2),
            "normalen_abweichung_grad": round(fl.normalen_streuung_grad, 2),
            "rauheit_p95_m": round(fl.rauheit_p95, 4),
            "mindestabstand_m": round(fl.tnear, 4),
            "ebenheit_ecken_m": round(fl.ebenheit_m, 4),
            "freier_himmel_seiten": [round(fl.seite_frei[0], 3), round(fl.seite_frei[1], 3)],
            "gerechnet_neigung_grad": round(neig, 2),
            "gerechnet_azimut_geo_grad": round(az, 2),
        },
        "verschattung": {
            "himmelssicht": round(float(him_p.mean()), 4),
            "himmelssicht_nur_fern": round(float(him_f[0]), 4),
            "horizontband": round(float(band_p.mean()), 4),
            "einstrahlung_anteil_typjahr": round(float(t_alle["poa"].sum() / poa_frei), 4),
            "monate": [
                {
                    "monat": int(m),
                    "sonne_vor_flaeche_h": round(float(df.loc[m, "vor"]), 1),
                    "besonnt_h": round(float(df.loc[m, "besonnt"]), 1),
                    "besonnt_nur_fern_h": round(float(df.loc[m, "fern"]), 1),
                    "verschattet_anteil": round(
                        float(1 - df.loc[m, "besonnt"] / df.loc[m, "vor"]), 4
                    )
                    if df.loc[m, "vor"] > 0
                    else None,
                }
                for m in df.index
            ],
        },
        "ertrag": {
            "jahr": {
                "mittel": round(float(jahre_alle.mean()), 1),
                "p50": round(float(jahre_alle.median()), 1),
                "p90": round(float(np.percentile(jahre_alle, 10)), 1),
                "unverschattet": round(float(_jahressummen(r_frei, "p_ac").mean()), 1),
                "nur_fernhorizont": round(float(_jahressummen(r_fern, "p_ac").mean()), 1),
                "kappungsverlust": round(kappung_verlust, 1),
                "typjahr": round(float(t_alle["p_ac"].sum() / 1000), 1),
                "einstrahlung_kwh_m2": round(float(_jahressummen(r_alle, "poa").mean()), 1),
            },
            "jahre": {int(j): round(float(v), 1) for j, v in jahre_alle.items()},
            "monate": _monatsband(r_alle, "p_ac"),
            "jahr_unverschattet_band": {
                "p50": round(float(_jahressummen(r_frei, "p_ac").median()), 1),
                "p90": round(float(np.percentile(_jahressummen(r_frei, "p_ac"), 10)), 1),
            },
            "monate_unverschattet": _monatsband(r_frei, "p_ac"),
        },
        "gegenprobe_pvcalc": {
            "pvgis_e_y_kwh": e_y,
            "pvlib_unverschattet_ohne_kappung_kwh": round(gegen, 1),
            "abweichung": round(gegen / e_y - 1, 4) if e_y else None,
        },
        "quellen": [_quelle(a) for a in (a_reihe, a_tmy, a_hor, a_calc)],
        "versionen": versionen(),
    }
    lauf.schritt("zusammenfassen")
    return Auswertung(e, fl, punkt_wh / 1000.0, punkt_wh / poa_frei, lauf)


def ergebnis_hash(e: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(e, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
