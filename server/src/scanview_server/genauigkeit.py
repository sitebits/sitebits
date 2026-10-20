"""Genauigkeitsnachweis je Ebene (F15) für API, MCP, Exporte und Git-Export.

Spiegel von web/src/genauigkeit.ts (`lageText`, `massstabText`, `herkunftText`, `messhinweise`)
und web/src/pointSources.ts (`measurementNotes`) — gleiche Regeln, gleiche Texte;
tests/test_genauigkeit.py prüft dieselben Fälle wie web/tests/genauigkeit.test.ts.
Ausgleichsresiduen und Kamera-GNSS ergeben nie einen ±-Wert; ein unlesbarer Nachweis gilt hier als
fehlend (der Viewer lehnt das Paket dann ohnehin ab, der Dienst läuft weiter).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, TypeGuard

MESSBAR = ("pointcloud", "mesh")
_ABSOLUT = ("checked", "adjustedOnly", "none")
_MASSSTAB = ("checked", "deviates", "set", "none")


@dataclass(frozen=True)
class EbeneGenauigkeit:
    id: str
    titel: str
    art: str
    # Zeilen wie im Ebenen-Panel des Viewers.
    zeilen: tuple[str, ...]
    # Hinweise an Messwerten, deren Eckpunkte aus dieser Ebene stammen.
    hinweise: tuple[str, ...]
    # Maschinenlesbar für Agenten (KI-Parität): Status und Kennzahlen, ohne Kamera-GNSS-Werte.
    werte: dict[str, Any] | None = None

    def als_json(self) -> dict[str, Any]:
        return {"ebene": self.id, "titel": self.titel, "art": self.art, "zeilen": list(self.zeilen),
                "nachweis": self.werte}


def _werte(acc: dict[str, Any]) -> dict[str, Any]:
    """Status und Kennzahlen [m bzw. %]; Kontrollpunkt-Statistik nur bei `checked` (Grundsatz 2)."""
    a, s = acc["absolute"], acc["scale"]
    lage: dict[str, Any] = {"status": a["status"]}
    if a["status"] == "checked":
        lage |= {"n": a["n"], "rmse": a["rmse"], "max": a["max"]}
    massstab: dict[str, Any] = {"status": s["status"]}
    for k in ("n", "maxErrorPct", "setFrom"):
        if k in s:
            massstab[k] = s[k]
    return {"lage": lage, "massstab": massstab}


def _zahl(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def _cm(m: float) -> str:
    t = f"{m * 100:.1f}"
    return f"{t[:-2] if t.endswith('.0') else t} cm".replace(".", ",")


def _prozent(p: float, vorzeichen: bool) -> str:
    return f"{'+' if vorzeichen and p > 0 else ''}{p:.2f} %".replace(".", ",")


def _anzahl(n: int, eins: str, viele: str) -> str:
    return f"{n} {eins if n == 1 else viele}"


def _statistik(x: Any) -> dict[str, Any] | None:
    if not isinstance(x, dict):
        return None
    r, m = x.get("rmse"), x.get("max")
    if not (isinstance(r, dict) and isinstance(m, dict) and _zahl(x.get("n"))):
        return None
    if not all(_zahl(r.get(k)) for k in "xyz") or not all(_zahl(m.get(k)) for k in ("xy", "z")):
        return None
    return x


def lesbar(acc: Any) -> TypeGuard[dict[str, Any]]:
    if not isinstance(acc, dict):
        return False
    a, s = acc.get("absolute"), acc.get("scale")
    if not (isinstance(a, dict) and isinstance(s, dict)):
        return False
    if a.get("status") not in _ABSOLUT or s.get("status") not in _MASSSTAB:
        return False
    if a["status"] == "checked" and _statistik(a) is None:
        return False
    return not (s["status"] in ("checked", "deviates") and not _zahl(s.get("maxErrorPct")))


def lage_text(acc: dict[str, Any]) -> str:
    a = acc["absolute"]
    if a["status"] == "checked":
        r, m = a["rmse"], a["max"]
        return (f"Lage geprüft an {_anzahl(int(a['n']), 'Kontrollpunkt', 'Kontrollpunkten')}: "
                f"RMSE Lage {_cm(math.hypot(r['x'], r['y']))}, Höhe {_cm(r['z'])} "
                f"(max. {_cm(m['xy'])} / {_cm(m['z'])})")
    if a["status"] == "adjustedOnly":
        pass_ = _statistik(a.get("controlResiduals"))
        if pass_ is not None:
            return ("Lage nicht unabhängig geprüft (nur Ausgleichsresiduen an "
                    f"{_anzahl(int(pass_['n']), 'Passpunkt', 'Passpunkten')})")
        return "Lage nicht unabhängig geprüft (nur Drohnen-GNSS)"
    return "Lage: kein Nachweis"


def massstab_text(acc: dict[str, Any]) -> str:
    s = acc["scale"]
    roh = acc.get("distances")
    strecken: list[Any] = roh if isinstance(roh, list) else []
    geprueft = [d for d in strecken
                if isinstance(d, dict) and d.get("role") == "check" and _zahl(d.get("errorPct"))]
    n = int(s["n"]) if _zahl(s.get("n")) else len(geprueft)
    if n == 1 and len(geprueft) == 1:
        wert = _prozent(float(geprueft[0]["errorPct"]), True)
    else:
        wert = f"max. {_prozent(float(s.get('maxErrorPct') or 0), False)}"
    kontrolle = _anzahl(n, "Kontrollstrecke", "Kontrollstrecken")
    if s["status"] == "checked":
        return f"Maßstab geprüft: {wert} an {kontrolle}"
    if s["status"] == "deviates":
        return f"Maßstab weicht ab: {wert} an {kontrolle}"
    if s["status"] == "set":
        roh = s.get("setFrom")
        namen = [x for x in roh if isinstance(x, str)] if isinstance(roh, list) else []
        klammer = f" ({', '.join(namen)})" if namen else ""
        return (f"Maßstab aus {_anzahl(len(namen) or 1, 'Strecke', 'Strecken')} gesetzt{klammer}, "
                "nicht geprüft")
    return "Maßstab: kein Nachweis"


def herkunft_text(acc: dict[str, Any]) -> list[str]:
    roh = acc.get("source")
    q: dict[str, Any] = roh if isinstance(roh, dict) else {}

    def t(k: str) -> str | None:
        v = q.get(k)
        return v if isinstance(v, str) else None

    software = " ".join(x for x in (t("software"), t("version")) if x)
    sha = t("projectSha256")
    teile = [x for x in (software, (t("created") or "")[:10],
                         f"Projekt {sha[:8]}…" if sha else "") if x]
    out = [f"Quelle: {', '.join(teile)}"] if teile else []
    rep = acc.get("report")
    if isinstance(rep, dict) and isinstance(rep.get("filename"), str) and isinstance(
            rep.get("sha256"), str):
        out.append(f"Bericht: {rep['filename']} (SHA-256 {rep['sha256'][:8]}…)")
    return out


def panel_zeilen(acc: Any) -> list[str]:
    if not lesbar(acc):
        return ["kein Genauigkeitsnachweis"]
    return [lage_text(acc), massstab_text(acc), *herkunft_text(acc)]


def messhinweise(acc: dict[str, Any]) -> list[str]:
    out = [massstab_text(acc)]
    if acc["absolute"]["status"] != "checked":
        out.append(lage_text(acc))
    return out


def _hinweise(art: str, prov: dict[str, Any]) -> tuple[str, ...]:
    """Wie pointSources.measurementNotes für eine Quelle aus dieser Ebene."""
    acc = prov.get("accuracy")
    out: list[str] = []
    if lesbar(acc):
        out += messhinweise(acc)
    elif prov.get("scaleVerified") is False:
        roh = prov.get("scaleSource")
        quelle = roh if isinstance(roh, str) else "unbekannt"
        out.append(f"Modellmaß — Maßstab nicht belegt (Quelle: {quelle})")
    if art == "mesh" and prov.get("interpolated") is True:
        out.append("Modelloberfläche (teils interpoliert)")
    return tuple(out)


def ebenen(layers: list[Any]) -> tuple[EbeneGenauigkeit, ...]:
    """Punktwolken und Meshes des Pakets, in Paketreihenfolge."""
    out = []
    for e in layers:
        if not isinstance(e, dict) or e.get("kind") not in MESSBAR:
            continue
        if not isinstance(e.get("id"), str):
            continue
        roh = e.get("provenance")
        prov: dict[str, Any] = roh if isinstance(roh, dict) else {}
        t = e.get("title")
        titel: str = t if isinstance(t, str) and t else e["id"]
        acc = prov.get("accuracy")
        out.append(EbeneGenauigkeit(
            id=e["id"], titel=titel, art=e["kind"],
            zeilen=tuple(panel_zeilen(acc)), hinweise=_hinweise(e["kind"], prov),
            werte=_werte(acc) if lesbar(acc) else None,
        ))
    return tuple(out)


def mess_hinweis(punktquellen: Any, je_ebene: Mapping[str, tuple[str, ...]]) -> str:
    """Spalte „Hinweis“ einer Messung: Hinweise aller Quell-Ebenen, ohne Doppelte, mit „ · “."""
    gesehen: dict[str, None] = {}
    for q in punktquellen if isinstance(punktquellen, list) else []:
        if isinstance(q, dict) and isinstance(q.get("layerId"), str):
            for h in je_ebene.get(q["layerId"], ()):
                gesehen.setdefault(h)
    return " · ".join(gesehen)
