"""Ertragskette mit pvlib: Sonnenstand (NREL SPA), Perez getrennt, Faiman, PVWatts DC,
Wechselrichter mit Kappung.

Verschattung greift getrennt nach Anteilen: Direkt und zirkumsolar über die Direktmaske bei
Sonnenstand,
isotroper Himmel über den Sky-View-Faktor (kosinusgewichtet zur Flächennormalen), Horizontaufhellung
über das
Horizontband. Bodenreflexion bleibt unverschattet (Grenze, im Bericht genannt).
Verluste wie PVGIS `loss`: eine Pauschale einschließlich Wechselrichter; der
Wechselrichter-Nennwirkungsgrad wird
daraus herausgerechnet, damit er nicht doppelt zählt.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd
from pvlib import atmosphere, iam, irradiance, pvarray, pvsystem, solarposition, temperature

DirektFunktion = Callable[[np.ndarray, np.ndarray], np.ndarray]


@dataclass(frozen=True)
class Anlage:
    modul_wp: float
    anzahl: int = 1
    kappung_w: float | None = None
    verluste_prozent: float = 14.0
    wr_wirkungsgrad: float = 0.96
    gamma_pdc: float = -0.004
    faiman_u0: float = 26.9  # PVGIS freistehend c-Si (Huld & Amillo 2015)
    faiman_u1: float = 6.2
    albedo: float = 0.2
    iam_ar: float = 0.16  # Martin & Ruiz, wie PVGIS
    modulmodell: str = (
        "huld"  # PVGIS-Modell (c-Si, Koeffizienten PVGIS 5); "pvwatts" als Gegenprobe
    )

    @property
    def wp(self) -> float:
        return self.modul_wp * self.anzahl

    def als_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class Verschattung:
    """Sichtbarkeit 0..1: `direkt(azimut_geo, hoehe_scheinbar)` je Zeitpunkt; Himmel, Band."""

    direkt: DirektFunktion
    himmel: float = 1.0
    horizontband: float = 1.0


def sonnenstand(
    index: pd.DatetimeIndex, breite: float, laenge: float, hoehe_m: float
) -> pd.DataFrame:
    return solarposition.get_solarposition(
        index, breite, laenge, altitude=hoehe_m, method="nrel_numpy"
    )


def mit_dni(wetter: pd.DataFrame, sonne: pd.DataFrame) -> pd.DataFrame:
    """seriescalc liefert Direkt horizontal (`bh`); DNI = bh / cos(Zenit), tiefe Sonne begrenzt."""
    if "dni" in wetter:
        return wetter
    w = wetter.copy()
    cosz = np.cos(np.radians(sonne["apparent_zenith"].to_numpy()))
    extra = irradiance.get_extra_radiation(w.index).to_numpy()
    dni = np.where(cosz > 0.0175, w["bh"].to_numpy() / np.maximum(cosz, 0.0175), 0.0)
    w["dni"] = np.clip(dni, 0.0, extra)
    return w


def wr_ungekappt(
    pdc: np.ndarray, pdc0: float, eta_nenn: float, eta_ref: float = 0.9637
) -> np.ndarray:
    """PVWatts-Wechselrichterkurve (wie `pvlib.inverter.pvwatts`) ohne Nennleistungsgrenze."""
    zeta = np.maximum(pdc / pdc0, 1e-9)
    eta = eta_nenn / eta_ref * (-0.0162 * zeta - 0.0059 / zeta + 0.9858)
    return np.where(pdc > 0, np.maximum(eta * pdc, 0.0), 0.0)


def rechnen(
    wetter: pd.DataFrame,
    breite: float,
    laenge: float,
    hoehe_m: float,
    neigung: float,
    azimut: float,
    anlage: Anlage,
    schatten: Verschattung | None = None,
    sonne: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Stundenwerte: Einstrahlung in Modulebene (W/m², Anteile), Zelltemperatur, DC/AC in W."""
    if sonne is None:
        sonne = sonnenstand(pd.DatetimeIndex(wetter.index), breite, laenge, hoehe_m)
    w = mit_dni(wetter, sonne)
    zen = sonne["apparent_zenith"].to_numpy()
    saz = sonne["azimuth"].to_numpy()
    tag = zen < 90.0
    extra = irradiance.get_extra_radiation(w.index).to_numpy()
    am = atmosphere.get_relative_airmass(zen)
    teile = irradiance.perez(
        neigung,
        azimut,
        w["dhi"].to_numpy(),
        w["dni"].to_numpy(),
        extra,
        zen,
        saz,
        am,
        return_components=True,
    )
    iso = np.nan_to_num(np.asarray(teile["poa_isotropic"], float))
    zirk = np.nan_to_num(np.asarray(teile["poa_circumsolar"], float))
    hor = np.nan_to_num(np.asarray(teile["poa_horizon"], float))
    einfall = irradiance.aoi(neigung, azimut, zen, saz)
    cos_e = np.cos(np.radians(einfall))
    direkt = np.where(tag & (cos_e > 0), w["dni"].to_numpy() * cos_e, 0.0)
    zirk = np.where(cos_e > 0, zirk, 0.0)
    boden = np.asarray(
        irradiance.get_ground_diffuse(neigung, w["ghi"].to_numpy(), albedo=anlage.albedo), float
    )
    f_dir = np.ones(len(w))
    f_him = f_hor = 1.0
    if schatten is not None:
        f_dir = np.where(tag, schatten.direkt(saz, 90.0 - zen), 0.0)
        f_him, f_hor = schatten.himmel, schatten.horizontband
    direkt_v, zirk_v, iso_v, hor_v = direkt * f_dir, zirk * f_dir, iso * f_him, hor * f_hor
    poa = direkt_v + zirk_v + iso_v + hor_v + boden
    iam_d = np.nan_to_num(np.asarray(iam.martin_ruiz(einfall, a_r=anlage.iam_ar), float))
    iam_diffus = iam.martin_ruiz_diffuse(neigung, a_r=anlage.iam_ar)
    iam_h, iam_b = float(iam_diffus["sky"]), float(iam_diffus["ground"])
    wirksam = (direkt_v + zirk_v) * iam_d + (iso_v + hor_v) * iam_h + boden * iam_b
    tz = temperature.faiman(
        poa,
        w["temp_air"].to_numpy(),
        w["wind_speed"].to_numpy(),
        anlage.faiman_u0,
        anlage.faiman_u1,
    )
    if anlage.modulmodell == "huld":
        pdc = np.asarray(
            pvarray.huld(np.maximum(wirksam, 0.0), tz, anlage.wp, cell_type="cSi"), float
        )
        pdc = np.where(wirksam > 0, np.nan_to_num(pdc), 0.0)
    elif anlage.modulmodell == "pvwatts":
        pdc = np.asarray(pvsystem.pvwatts_dc(wirksam, tz, anlage.wp, anlage.gamma_pdc), float)
    else:
        raise ValueError(f"Unbekanntes Modulmodell: {anlage.modulmodell}")
    rest = 1.0 - (1.0 - anlage.verluste_prozent / 100.0) / anlage.wr_wirkungsgrad
    pdc = np.maximum(pdc * (1.0 - rest), 0.0)
    nenn_ac = anlage.kappung_w if anlage.kappung_w else anlage.wp * anlage.wr_wirkungsgrad
    pac_roh = wr_ungekappt(pdc, nenn_ac / anlage.wr_wirkungsgrad, anlage.wr_wirkungsgrad)
    pac = np.minimum(pac_roh, nenn_ac)
    return pd.DataFrame(
        {
            "poa": poa,
            "direkt": direkt_v,
            "zirkumsolar": zirk_v,
            "isotrop": iso_v,
            "horizont": hor_v,
            "boden": boden,
            "direkt_frei": direkt + zirk,
            "f_direkt": f_dir,
            "t_zelle": tz,
            "p_dc": pdc,
            "p_ac": pac,
            "p_ac_ohne_kappung": pac_roh,
            "sonne_hoehe": 90.0 - zen,
            "sonne_azimut": saz,
        },
        index=w.index,
    )
