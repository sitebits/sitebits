"""Höhensystem eines Pakets: Namen normieren und zwischen ellipsoidisch und DHHN2016 umrechnen.

Entscheidung 0015: Standard ist DHHN2016 (NHN), ellipsoidisch (ETRS89/GRS80) ist wählbar.
Umgerechnet wird über das Quasigeoid GCG2016 des BKG, und zwar nur über eine **explizite**
PROJ-Pipeline. Die automatische Transformationsauswahl von PROJ nimmt bei fehlendem Gitter still
eine Nulloperation („ballpark“) — auch mit ``only_best`` — und die Höhe wäre dann um rund 40 m
falsch, ohne Fehler.
Die explizite Pipeline bricht dagegen ab, wenn das Gitter fehlt oder ein Punkt außerhalb liegt.

Unterstützt sind Quell-CRS mit ETRS89-Bezug in UTM 32N und 33N. Lokale Rahmen und andere CRS
werden nicht umgerechnet.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from pyproj import CRS, Transformer
from pyproj.exceptions import CRSError, ProjError

DHHN2016 = "dhhn2016"
ELLIPSOIDISCH = "ellipsoidal-etrs89"
DHHN92 = "dhhn92"
HOEHENSYSTEME = (DHHN2016, ELLIPSOIDISCH, DHHN92)

# Ältere Schreibweisen aus Paketen, Kameraexporten und der CLI → Normwert.
_SCHREIBWEISEN = {
    "dhhn2016": DHHN2016,
    "nhn": DHHN2016,
    "dhhn2016 height": DHHN2016,
    "ellipsoidisch": ELLIPSOIDISCH,
    "ellipsoidal": ELLIPSOIDISCH,
    "ellipsoidal-etrs89": ELLIPSOIDISCH,
    "etrs89": ELLIPSOIDISCH,
    "dhhn92": DHHN92,
}

GITTER_NAME = "de_bkg_gcg2016.tif"
GITTER = Path(__file__).parent / "gitter" / GITTER_NAME
# PROJ-data `de_bkg/de_bkg_gcg2016.tif` (CC BY 4.0, © Bundesamt für Kartographie und Geodäsie).
GITTER_SHA256 = "598f18324dea7f8e72421d18add7ac6228259adf91eeb335cc9c27d98484f7ac"

_UTM_ZONEN = {25832: 32, 25833: 33}
# Richtung der Gitterkorrektur: vgridshift addiert multiplier mal Quasigeoidhöhe.
_MULTIPLIKATOR = {(ELLIPSOIDISCH, DHHN2016): -1, (DHHN2016, ELLIPSOIDISCH): 1}


class HoehenFehler(ValueError):
    """Umrechnung nicht möglich — der Import bricht ab, statt eine falsche Höhe zu schreiben."""


def umrechenbar(quelle: str, ziel: str) -> bool:
    """Gibt es eine Umrechnung zwischen den (normierten) Systemen?"""
    return quelle == ziel or (quelle, ziel) in _MULTIPLIKATOR


def normiere(wert: str | None) -> str | None:
    """Normwert zu einer Schreibweise, ``None`` für unbekannt (wird angezeigt, nie umgerechnet)."""
    if wert is None:
        return None
    return _SCHREIBWEISEN.get(wert.strip().lower())


def pruefe_gitter(pfad: Path | None = None) -> Path:
    """Gitterdatei vorhanden und unverändert? Gibt den Pfad zurück."""
    pfad = GITTER if pfad is None else pfad
    if not pfad.is_file():
        raise HoehenFehler(f"Quasigeoid-Gitter fehlt: {pfad}")
    ist = hashlib.sha256(pfad.read_bytes()).hexdigest()
    if ist != GITTER_SHA256:
        raise HoehenFehler(f"Quasigeoid-Gitter hat eine falsche Prüfsumme: {pfad}")
    return pfad


def utm_zone(crs: int | str) -> int:
    """UTM-Zone (32/33) eines ETRS89-UTM-CRS, gegeben als EPSG-Nummer oder WKT.

    Erkennt auch Varianten mit anderer Achsfolge (z. B. EPSG:3045 „N-E“ in LGB-Dateien) — LAS
    speichert X immer als Rechtswert, nur die WKT-Beschreibung unterscheidet sich.
    """
    if isinstance(crs, int):
        zone = _UTM_ZONEN.get(crs)
        if zone is None:
            raise HoehenFehler(
                f"Höhenumrechnung für EPSG:{crs} nicht unterstützt (nur UTM 32N/33N)"
            )
        return zone
    try:
        parsed = CRS.from_user_input(crs)
    except CRSError as exc:
        raise HoehenFehler(f"Koordinatensystem nicht lesbar: {exc}") from exc
    epsg = parsed.to_epsg()
    if epsg in _UTM_ZONEN:
        return _UTM_ZONEN[epsg]
    datum = parsed.datum.name if parsed.datum is not None else ""
    utm = parsed.utm_zone
    if parsed.is_projected and "European Terrestrial Reference System 1989" in datum and utm:
        zone = int(utm[:-1]) if utm[-1] == "N" else 0
        if zone in _UTM_ZONEN.values():
            return zone
    raise HoehenFehler(
        f"Höhenumrechnung für {parsed.name!r} nicht unterstützt (nur ETRS89 UTM 32N/33N)"
    )


def _pipeline(zone: int, multiplikator: int, gitter: Path) -> str:
    if any(c.isspace() for c in str(gitter)):
        raise HoehenFehler(f"Gitterpfad mit Leerzeichen: {gitter}")
    utm = f"+proj=utm +zone={zone} +ellps=GRS80"
    return (
        f"+proj=pipeline +step +inv {utm} "
        f"+step +proj=vgridshift +grids={gitter} +multiplier={multiplikator} "
        f"+step {utm}"
    )


def rechne_um(
    x: NDArray[np.float64],
    y: NDArray[np.float64],
    z: NDArray[np.float64],
    *,
    crs: int | str,
    quelle: str | None,
    ziel: str | None,
) -> NDArray[np.float64]:
    """Höhen ``z`` von ``quelle`` nach ``ziel`` umrechnen; Lage bleibt unverändert."""
    q, zl = normiere(quelle), normiere(ziel)
    if q is None or zl is None:
        raise HoehenFehler(f"Höhensystem unbekannt: Quelle {quelle!r}, Ziel {ziel!r}")
    if q == zl:
        return np.asarray(z, dtype=np.float64)
    multiplikator = _MULTIPLIKATOR.get((q, zl))
    if multiplikator is None:
        raise HoehenFehler(f"Umrechnung {q} → {zl} nicht unterstützt")
    pipeline = _pipeline(utm_zone(crs), multiplikator, pruefe_gitter(GITTER))
    try:
        transformer = Transformer.from_pipeline(pipeline)
        _, _, out = transformer.transform(
            np.asarray(x, dtype=np.float64),
            np.asarray(y, dtype=np.float64),
            np.asarray(z, dtype=np.float64),
            errcheck=True,
        )
    except ProjError as exc:
        if "outside" in str(exc):
            raise HoehenFehler("Punkt außerhalb des Quasigeoid-Gitters (GCG2016)") from exc
        raise HoehenFehler(f"Höhenumrechnung fehlgeschlagen: {exc}") from exc
    result = np.asarray(out, dtype=np.float64)
    if not np.isfinite(result).all():
        raise HoehenFehler("Höhenumrechnung lieferte ungültige Werte")
    return result


def festlegen(quelle: str | None, ziel: str | None) -> tuple[str | None, str | None]:
    """Quell- und Zielhöhensystem aus den CLI-Angaben; Standard-Ziel DHHN2016 (Entscheidung 0015).

    Ohne Quellangabe bleibt alles unbekannt (``None, None``) — keine Annahme. Ein Ziel ohne Quelle
    ist ein Fehler: Umrechnen setzt voraus, dass bekannt ist, wovon.
    """
    if quelle is None:
        if ziel is not None:
            raise HoehenFehler("Zielhöhensystem ohne Quellhöhensystem: --quell-hoehensystem fehlt")
        return None, None
    q = normiere(quelle)
    if q is None:
        raise HoehenFehler(f"Quellhöhensystem unbekannt: {quelle!r}")
    zl = DHHN2016 if ziel is None else normiere(ziel)
    if zl is None:
        raise HoehenFehler(f"Zielhöhensystem unbekannt: {ziel!r}")
    return q, zl


def las_umrechnen(
    src: Path, dst: Path, *, crs: int | str, quelle: str, ziel: str, chunk: int = 2_000_000
) -> None:
    """LAS/LAZ ``src`` nach ``dst`` mit umgerechneten Höhen schreiben (alle anderen Felder gleich).

    Skala und Offset bleiben; die neue Höhe wird auf die Z-Skala gerundet (Fehler ≤ halber
    Skalenschritt). Die Kopfzeilen-Grenzen setzt laspy beim Schreiben neu.
    """
    from .lage import las_anpassen

    las_anpassen(src, dst, crs=crs, hoehe=(quelle, ziel), matrix=None, chunk=chunk)


def quellblock(quelle: str, ziel: str) -> dict[str, str]:
    """Eintrag für ``sourceOptions.hoehe``: was umgerechnet wurde und womit."""
    return {"quelle": quelle, "ziel": ziel, "gitter": GITTER_NAME, "gitterSha256": GITTER_SHA256}


def positionen_umrechnen(
    punkte: NDArray[np.float64], *, crs: int | str, quelle: str, ziel: str
) -> NDArray[np.float64]:
    """Nx3-Weltpositionen: Kopie mit umgerechneter Höhe (Lage unverändert)."""
    out = np.array(punkte, dtype=np.float64, copy=True)
    if len(out):
        out[:, 2] = rechne_um(out[:, 0], out[:, 1], out[:, 2], crs=crs, quelle=quelle, ziel=ziel)
    return out


# Zweithöhe (F23 §2 Nr. 5): größte zulässige Änderung der Quasigeoidhöhe über das Paket, damit eine
# einzige Zahl am Ursprung für die Anzeige reicht.
UNDULATION_MAX_ABWEICHUNG = 0.01


def undulation(frame: dict[str, Any], ebenen: list[Any]) -> dict[str, Any] | None:
    """``frame.undulation`` = {atOriginM, maxDeviationM, grid}.

    Quasigeoidhöhe ζ (ellipsoidisch minus NHN) am Ursprung und ihre größte Abweichung an den Ecken
    der Paketausdehnung. ``None``, wenn das Paket kein umrechenbares Höhensystem trägt, nicht in
    ETRS89/UTM 32/33 liegt oder außerhalb des Gitters — nie geraten."""
    roh = frame.get("crs")
    crs: dict[str, Any] = roh if isinstance(roh, dict) else {}
    if normiere(crs.get("verticalDatum")) not in (DHHN2016, ELLIPSOIDISCH):
        return None
    wkt = crs.get("wkt")
    ursprung = frame.get("originWorld")
    if not isinstance(wkt, str) or not wkt.strip() or not isinstance(ursprung, list):
        return None
    ecken: list[tuple[float, float]] = []
    for e in ebenen:
        b = e.get("boundsWorld") if isinstance(e, dict) else None
        if isinstance(b, list) and len(b) == 2:
            (x0, y0, *_), (x1, y1, *_) = b
            ecken += [(x0, y0), (x0, y1), (x1, y0), (x1, y1)]
    x = np.array([float(ursprung[0]), *(p[0] for p in ecken)], dtype=np.float64)
    y = np.array([float(ursprung[1]), *(p[1] for p in ecken)], dtype=np.float64)
    try:
        nhn = rechne_um(x, y, np.zeros_like(x), crs=wkt, quelle=ELLIPSOIDISCH, ziel=DHHN2016)
    except HoehenFehler:
        return None
    zeta = -nhn  # ellipsoidische Höhe 0 ergibt NHN-Höhe minus ζ
    return {
        "atOriginM": round(float(zeta[0]), 4),
        "maxDeviationM": round(float(np.abs(zeta - zeta[0]).max()), 4),
        "grid": "GCG2016",
    }
