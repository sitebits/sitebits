"""Genauigkeitsnachweis (F15 Teil 1): ``genauigkeit.json`` prüfen, bewerten, ins Paket schreiben.

Grundsätze: absolut und Maßstab getrennt; nur **Kontroll**punkte und
**Kontroll**strecken (nicht im Ausgleich) ergeben einen Nachweis. Passpunkt- und
Kamera-GNSS-Residuen sind Ausgleichsresiduen — sie stehen im Paket, aber nie als ±-Wert.
Kennzahlen rechnet der Import selbst aus den Koordinaten; Metashapes eigene Fehlerwerte werden
nicht übernommen. Ein Bericht-PDF wird nur mit Name und Prüfsumme vermerkt, nicht ins Paket kopiert.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from . import hoehen
from .manifest_io import read_manifest, write_manifest
from .metashape_import import kamera_umrechnung, pruefe_paket_crs

FORMAT_VERSION = 1
MAX_EINTRAEGE = 10_000
MAX_NAME = 200
ROLLEN = ("control", "check")
# Kontrollstrecke gilt als bestätigt, wenn |Abweichung| ≤ FAKTOR * Sollgenauigkeit.
SIGMA_FAKTOR = 3.0
MESSBARE_EBENEN = ("pointcloud", "mesh")
DATEINAME = "genauigkeit.json"

_HEX64 = re.compile(r"[0-9a-f]{64}")
_OBEN = {"formatVersion", "crs", "source", "markers", "scalebars", "cameraGroups", "report"}
_QUELLE = {"software", "version", "created", "projectSha256"}
_MARKER = {"name", "role", "reference", "referenceAccuracy", "estimated", "images"}
_STRECKE = {"name", "role", "reference", "referenceAccuracy", "estimated"}
_GRUPPE = {"name", "n", "xyMedian", "xy95", "dzMedian", "dz95"}


@dataclass(frozen=True)
class Punkt:
    name: str
    rolle: str
    soll: tuple[float, float, float]
    ist: tuple[float, float, float] | None
    soll_genauigkeit: tuple[float, float, float] | None
    bilder: int


@dataclass(frozen=True)
class Strecke:
    name: str
    rolle: str
    soll: float
    soll_genauigkeit: float | None
    ist: float | None


@dataclass(frozen=True)
class Export:
    epsg: int
    hoehensystem: str
    quelle: dict[str, str]
    punkte: list[Punkt]
    strecken: list[Strecke]
    kameragruppen: list[dict[str, Any]]
    bericht: dict[str, str] | None


# --- Laden ------------------------------------------------------------------------------------


def _schluessel(obj: Any, erlaubt: set[str], wo: str) -> dict[str, Any]:
    if not isinstance(obj, dict):
        raise ValueError(f"{wo}: Objekt erwartet")
    fremd = set(obj) - erlaubt
    if fremd:
        raise ValueError(f"{wo}: unbekannte Felder {sorted(fremd)}")
    return obj


def _zahl(x: Any, wo: str) -> float:
    if isinstance(x, bool) or not isinstance(x, int | float) or not math.isfinite(x):
        raise ValueError(f"{wo}: endliche Zahl erwartet")
    return float(x)


def _xyz(x: Any, wo: str) -> tuple[float, float, float]:
    if not isinstance(x, list) or len(x) != 3:
        raise ValueError(f"{wo}: drei Koordinaten erwartet")
    a, b, c = (_zahl(v, wo) for v in x)
    return a, b, c


def _name(x: Any, wo: str) -> str:
    if not isinstance(x, str) or not x.strip() or len(x) > MAX_NAME:
        raise ValueError(f"{wo}: Name fehlt oder ist länger als {MAX_NAME} Zeichen")
    return x


def _rolle(x: Any, wo: str) -> str:
    if x not in ROLLEN:
        raise ValueError(f"{wo}: role muss control oder check sein")
    return str(x)


def _liste(d: dict[str, Any], feld: str) -> list[Any]:
    werte = d.get(feld, [])
    if not isinstance(werte, list):
        raise ValueError(f"{feld}: Liste erwartet")
    if len(werte) > MAX_EINTRAEGE:
        raise ValueError(f"{feld}: höchstens {MAX_EINTRAEGE} Einträge")
    namen = [e.get("name") if isinstance(e, dict) else None for e in werte]
    if len(set(namen)) != len(namen):
        raise ValueError(f"{feld}: Name doppelt")
    return werte


def _punkt(e: Any, i: int) -> Punkt:
    wo = f"markers[{i}]"
    e = _schluessel(e, _MARKER, wo)
    bilder = e.get("images", 0)
    if isinstance(bilder, bool) or not isinstance(bilder, int) or bilder < 0:
        raise ValueError(f"{wo}: images muss eine Zahl ≥ 0 sein")
    sg = e.get("referenceAccuracy")
    ist = e.get("estimated")
    return Punkt(
        name=_name(e.get("name"), wo),
        rolle=_rolle(e.get("role"), wo),
        soll=_xyz(e.get("reference"), f"{wo}.reference"),
        ist=None if ist is None else _xyz(ist, f"{wo}.estimated"),
        soll_genauigkeit=None if sg is None else _xyz(sg, f"{wo}.referenceAccuracy"),
        bilder=bilder,
    )


def _strecke(e: Any, i: int) -> Strecke:
    wo = f"scalebars[{i}]"
    e = _schluessel(e, _STRECKE, wo)
    rolle = _rolle(e.get("role"), wo)
    soll = _zahl(e.get("reference"), f"{wo}.reference")
    if soll <= 0:
        raise ValueError(f"{wo}: reference muss positiv sein")
    sg = e.get("referenceAccuracy")
    if sg is None and rolle == "check":
        raise ValueError(f"{wo}: Kontrollstrecke braucht referenceAccuracy")
    genau = None if sg is None else _zahl(sg, f"{wo}.referenceAccuracy")
    if genau is not None and genau <= 0:
        raise ValueError(f"{wo}: referenceAccuracy muss positiv sein")
    ist = e.get("estimated")
    return Strecke(
        name=_name(e.get("name"), wo),
        rolle=rolle,
        soll=soll,
        soll_genauigkeit=genau,
        ist=None if ist is None else _zahl(ist, f"{wo}.estimated"),
    )


def _gruppe(e: Any, i: int) -> dict[str, Any]:
    wo = f"cameraGroups[{i}]"
    e = _schluessel(e, _GRUPPE, wo)
    n = e.get("n")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"{wo}: n muss eine Zahl ≥ 1 sein")
    out: dict[str, Any] = {"name": _name(e.get("name"), wo), "n": n}
    for k in ("xyMedian", "xy95", "dzMedian", "dz95"):
        out[k] = _zahl(e.get(k), f"{wo}.{k}")
    return out


def _bericht(x: Any) -> dict[str, str] | None:
    if x is None:
        return None
    x = _schluessel(x, {"filename", "sha256"}, "report")
    name = _name(x.get("filename"), "report")
    if "/" in name or "\\" in name:
        raise ValueError("report: Dateiname ohne Pfad erwartet")
    sha = x.get("sha256")
    if not isinstance(sha, str) or not _HEX64.fullmatch(sha):
        raise ValueError("report: sha256 erwartet")
    return {"filename": name, "sha256": sha}


def lade(path: Path) -> Export:
    """``genauigkeit.json`` streng prüfen."""
    d = _schluessel(json.loads(path.read_text(encoding="utf-8")), _OBEN, "genauigkeit.json")
    if d.get("formatVersion") != FORMAT_VERSION:
        raise ValueError(f"formatVersion {FORMAT_VERSION} erwartet")
    crs = _schluessel(d.get("crs"), {"epsg", "verticalDatum"}, "crs")
    epsg = crs.get("epsg")
    if isinstance(epsg, bool) or not isinstance(epsg, int) or epsg <= 0:
        raise ValueError("crs.epsg muss eine positive Ganzzahl sein")
    datum = crs.get("verticalDatum")
    if not isinstance(datum, str) or not datum.strip():
        raise ValueError("crs.verticalDatum fehlt")
    quelle = _schluessel(d.get("source"), _QUELLE, "source")
    for k in ("software", "version", "created"):
        _name(quelle.get(k), f"source.{k}")
    if not isinstance(quelle.get("projectSha256"), str) or not _HEX64.fullmatch(
        quelle["projectSha256"]
    ):
        raise ValueError("source.projectSha256 muss SHA-256 (hex) sein")
    return Export(
        epsg=epsg,
        hoehensystem=datum,
        quelle={k: str(quelle[k]) for k in sorted(_QUELLE)},
        punkte=[_punkt(e, i) for i, e in enumerate(_liste(d, "markers"))],
        strecken=[_strecke(e, i) for i, e in enumerate(_liste(d, "scalebars"))],
        kameragruppen=[_gruppe(e, i) for i, e in enumerate(_liste(d, "cameraGroups"))],
        bericht=_bericht(d.get("report")),
    )


# --- Bewerten ---------------------------------------------------------------------------------


def _statistik(fehler: np.ndarray) -> dict[str, Any]:
    xy = np.hypot(fehler[:, 0], fehler[:, 1])
    rmse = np.sqrt(np.mean(fehler**2, axis=0))
    return {
        "n": len(fehler),
        "rmse": {
            "x": float(rmse[0]),
            "y": float(rmse[1]),
            "z": float(rmse[2]),
            "xy": float(np.sqrt(np.mean(xy**2))),
        },
        "max": {"xy": float(xy.max()), "z": float(np.abs(fehler[:, 2]).max())},
    }


def _fehler(punkte: list[Punkt]) -> np.ndarray:
    return np.array(
        [np.subtract(p.ist, p.soll) for p in punkte if p.ist is not None], dtype=np.float64
    ).reshape(-1, 3)


def _absolut(exp: Export) -> dict[str, Any]:
    kontrolle = [p for p in exp.punkte if p.rolle == "check"]
    pass_ = [p for p in exp.punkte if p.rolle == "control"]
    fk, fp = _fehler(kontrolle), _fehler(pass_)
    residuen = _statistik(fp) if len(fp) else None
    if len(fk):
        return {
            "status": "checked",
            **_statistik(fk),
            "withoutEstimate": len(kontrolle) - len(fk),
            "controlResiduals": residuen,
        }
    if residuen is not None or exp.kameragruppen:
        return {
            "status": "adjustedOnly",
            "controlResiduals": residuen,
            "cameraGroups": exp.kameragruppen,
        }
    return {"status": "none"}


def _massstab(exp: Export) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    einzeln: list[dict[str, Any]] = []
    for s in exp.strecken:
        e: dict[str, Any] = {
            "name": s.name,
            "role": s.rolle,
            "reference": s.soll,
            "referenceAccuracy": s.soll_genauigkeit,
            "estimated": s.ist,
            "error": None if s.ist is None else s.ist - s.soll,
            "errorPct": None if s.ist is None else (s.ist - s.soll) / s.soll * 100,
        }
        if s.rolle == "check" and s.ist is not None and s.soll_genauigkeit is not None:
            e["withinTolerance"] = abs(s.ist - s.soll) <= SIGMA_FAKTOR * s.soll_genauigkeit
        einzeln.append(e)
    geprueft = [e for e in einzeln if "withinTolerance" in e]
    gesetzt = [e for e in einzeln if e["role"] == "control"]
    if geprueft:
        status = "checked" if all(e["withinTolerance"] for e in geprueft) else "deviates"
        return {
            "status": status,
            "n": len(geprueft),
            "maxErrorPct": max(abs(e["errorPct"]) for e in geprueft),
            "toleranceFactor": SIGMA_FAKTOR,
            "setFrom": [e["name"] for e in gesetzt],
        }, einzeln
    if gesetzt:
        return {"status": "set", "setFrom": [e["name"] for e in gesetzt]}, einzeln
    return {"status": "none"}, einzeln


def bewerte(exp: Export) -> dict[str, Any]:
    """``provenance.accuracy`` aus einem geprüften Export (Koordinaten im Paket-Höhensystem)."""
    massstab, strecken = _massstab(exp)
    return {
        "formatVersion": FORMAT_VERSION,
        "absolute": _absolut(exp),
        "scale": massstab,
        "points": [
            {
                "name": p.name,
                "role": p.rolle,
                "reference": list(p.soll),
                "referenceAccuracy": None
                if p.soll_genauigkeit is None
                else list(p.soll_genauigkeit),
                "estimated": None if p.ist is None else list(p.ist),
                "error": None if p.ist is None else np.subtract(p.ist, p.soll).tolist(),
                "images": p.bilder,
            }
            for p in exp.punkte
        ],
        "distances": strecken,
        "source": {**exp.quelle, "crs": f"EPSG:{exp.epsg}"},
        "report": exp.bericht,
    }


# --- Ins Paket --------------------------------------------------------------------------------


def _umrechnen(exp: Export, quelle: str, ziel: str) -> Export:
    """Soll und Ist jedes Punkts ins Paket-Höhensystem (jeweils an der eigenen Lage)."""

    def z(xyz: tuple[float, float, float]) -> tuple[float, float, float]:
        neu = hoehen.rechne_um(
            np.array([xyz[0]]),
            np.array([xyz[1]]),
            np.array([xyz[2]]),
            crs=exp.epsg,
            quelle=quelle,
            ziel=ziel,
        )
        return xyz[0], xyz[1], float(neu[0])

    punkte = [
        Punkt(
            p.name,
            p.rolle,
            z(p.soll),
            None if p.ist is None else z(p.ist),
            p.soll_genauigkeit,
            p.bilder,
        )
        for p in exp.punkte
    ]
    return Export(exp.epsg, ziel, exp.quelle, punkte, exp.strecken, exp.kameragruppen, exp.bericht)


def _pruefe_bericht(exp: Export, bericht: Path | None) -> None:
    if bericht is None:
        return
    if exp.bericht is None:
        raise ValueError("Bericht angegeben, aber genauigkeit.json nennt keinen (report)")
    ist = hashlib.sha256(bericht.read_bytes()).hexdigest()
    if ist != exp.bericht["sha256"]:
        raise ValueError("Bericht hat eine andere Prüfsumme als in genauigkeit.json")


def importiere(
    pkg: Path,
    datei: Path,
    *,
    bericht: Path | None = None,
    ebenen: list[str] | None = None,
) -> dict[str, Any]:
    """Genauigkeit an die messbaren Ebenen des Pakets schreiben; gibt ``accuracy`` zurück.

    Erst alles prüfen, dann schreiben: bei jedem Fehler bleibt das Paket unverändert.
    """
    exp = lade(datei)
    _pruefe_bericht(exp, bericht)
    if read_manifest(pkg).get("frame", {}).get("crs", {}).get("verticalDatum") is None:
        raise ValueError(
            "Paket-Höhensystem unbekannt; erst die Wolke mit --quell-hoehensystem importieren"
        )
    _, datum = pruefe_paket_crs(pkg, exp.epsg, exp.hoehensystem, None)
    umrechnung = kamera_umrechnung(exp.hoehensystem, datum)
    if umrechnung is not None:
        exp = _umrechnen(exp, *umrechnung)
    acc = bewerte(exp)
    acc["heightConversion"] = (
        None if umrechnung is None else {"from": umrechnung[0], "to": umrechnung[1]}
    )

    m = read_manifest(pkg)
    ziele = [
        layer
        for layer in m.get("layers", [])
        if layer.get("kind") in MESSBARE_EBENEN and (ebenen is None or layer.get("id") in ebenen)
    ]
    if not ziele or (ebenen is not None and len(ziele) != len(set(ebenen))):
        raise ValueError("Ebene nicht gefunden oder nicht messbar (Punktwolke/Mesh)")
    strecken = acc["scale"].get("setFrom", []) + [
        e["name"] for e in acc["distances"] if "withinTolerance" in e
    ]
    for layer in ziele:
        prov = layer.setdefault("provenance", {})
        prov["accuracy"] = acc
        if acc["scale"]["status"] != "none":
            prov["scaleVerified"] = True
            prov["scaleSource"] = "strecke:" + ",".join(strecken)
    (pkg / DATEINAME).write_text(datei.read_text(encoding="utf-8"), encoding="utf-8")
    write_manifest(pkg, m)
    return acc
