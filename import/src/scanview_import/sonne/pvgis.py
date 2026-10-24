"""PVGIS 5.3 abrufen (nur Arbeitsplatz-CLI) mit Cache: Antwort, URL und Abrufdatum je Anfrage.

Der Horizont wird nie in die Einstrahlung eingerechnet (`usehorizon=0`): Nah- und Fernhorizont
maskieren
wir selbst, sonst zählt der Fernhorizont doppelt. Zeitstempel bleiben wörtlich (seriescalc `HH:11`),
beim
Typjahr wird `irradiance_time_offset` addiert (Stempel `HH:00`).
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

BASIS = "https://re.jrc.ec.europa.eu/api/v5_3/"
QUELLE = "PVGIS © European Communities, 2001-2026"
QUELLE_URL = "https://joint-research-centre.ec.europa.eu/photovoltaic-geographical-information-system-pvgis_en"


@dataclass(frozen=True)
class Abruf:
    url: str
    abgerufen: str
    antwort: dict[str, Any]


class Pvgis:
    def __init__(self, cache: Path, offline: bool = False, timeout: float = 120.0) -> None:
        self.cache = cache
        self.offline = offline
        self.timeout = timeout

    def abrufen(self, werkzeug: str, **parameter: object) -> Abruf:
        query = urllib.parse.urlencode({**parameter, "outputformat": "json"})
        url = f"{BASIS}{werkzeug}?{query}"
        datei = self.cache / f"{werkzeug}-{hashlib.sha256(url.encode()).hexdigest()[:16]}.json"
        if datei.is_file():
            d = json.loads(datei.read_text())
            return Abruf(d["url"], d["abgerufen"], d["antwort"])
        if self.offline:
            raise RuntimeError(f"PVGIS-Antwort nicht im Cache (offline): {url}")
        anfrage = urllib.request.Request(url, headers={"User-Agent": "scanview-sonne"})
        with urllib.request.urlopen(anfrage, timeout=self.timeout) as r:
            antwort = json.loads(r.read())
        abruf = Abruf(url, dt.datetime.now(dt.UTC).isoformat(timespec="seconds"), antwort)
        self.cache.mkdir(parents=True, exist_ok=True)
        datei.write_text(json.dumps(abruf.__dict__))
        return abruf


def ort(breite: float, laenge: float) -> dict[str, object]:
    return {"lat": round(breite, 4), "lon": round(laenge, 4)}


def jahresreihe(p: Pvgis, breite: float, laenge: float) -> tuple[pd.DataFrame, Abruf]:
    """Stündlich 2005-2023, horizontal (ghi, dhi, dni, temp_air, wind_speed), Index UTC wörtlich."""
    a = p.abrufen(
        "seriescalc",
        **ort(breite, laenge),
        angle=0,
        aspect=0,
        usehorizon=0,
        components=1,
        startyear=2005,
        endyear=2023,
    )
    roh = pd.DataFrame(a.antwort["outputs"]["hourly"])
    zeit = pd.to_datetime(roh["time"], format="%Y%m%d:%H%M", utc=True)
    df = pd.DataFrame(
        {
            "bh": roh["Gb(i)"].to_numpy(float),
            "dhi": roh["Gd(i)"].to_numpy(float),
            "temp_air": roh["T2m"].to_numpy(float),
            "wind_speed": roh["WS10m"].to_numpy(float),
        },
        index=pd.DatetimeIndex(zeit),
    )
    df["ghi"] = df["bh"] + df["dhi"]
    return df, a


def typjahr(p: Pvgis, breite: float, laenge: float) -> tuple[pd.DataFrame, Abruf]:
    """Typjahr (ghi, dni, dhi, temp_air, wind_speed); Index = Stempel + irradiance_time_offset."""
    a = p.abrufen("tmy", **ort(breite, laenge), usehorizon=0)
    roh = pd.DataFrame(a.antwort["outputs"]["tmy_hourly"])
    versatz = float(a.antwort["inputs"]["location"].get("irradiance_time_offset", 0.0))
    zeit = pd.to_datetime(roh["time(UTC)"], format="%Y%m%d:%H%M", utc=True)
    zeit = zeit + pd.to_timedelta(round(versatz * 3600), unit="s")
    df = pd.DataFrame(
        {
            "ghi": roh["G(h)"].to_numpy(float),
            "dni": roh["Gb(n)"].to_numpy(float),
            "dhi": roh["Gd(h)"].to_numpy(float),
            "temp_air": roh["T2m"].to_numpy(float),
            "wind_speed": roh["WS10m"].to_numpy(float),
        },
        index=pd.DatetimeIndex(zeit),
    )
    return df, a


def horizont(p: Pvgis, breite: float, laenge: float) -> tuple[np.ndarray, np.ndarray, Abruf]:
    """Fernhorizont aus dem DEM: (Azimut geografisch wie pvlib; Höhe) in Grad, aufsteigend."""
    a = p.abrufen("printhorizon", **ort(breite, laenge))
    prof = a.antwort["outputs"]["horizon_profile"]
    az = np.array([float(np.mod(r["A"] + 180.0, 360.0)) for r in prof])
    h = np.array([float(r["H_hor"]) for r in prof])
    o = np.argsort(az, kind="stable")
    az, h = az[o], h[o]
    keep = np.concatenate([[True], np.diff(az) > 0])  # -180 und 180 fallen auf denselben Azimut
    return az[keep], h[keep], a


def pvcalc(
    p: Pvgis,
    breite: float,
    laenge: float,
    neigung: float,
    azimut_pvlib: float,
    kwp: float,
    verlust_prozent: float,
) -> Abruf:
    from .geo import pvlib_nach_pvgis

    return p.abrufen(
        "PVcalc",
        **ort(breite, laenge),
        peakpower=kwp,
        loss=verlust_prozent,
        angle=round(neigung, 2),
        aspect=round(pvlib_nach_pvgis(azimut_pvlib), 2),
        usehorizon=0,
    )
