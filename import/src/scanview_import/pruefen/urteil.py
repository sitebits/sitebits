"""Urteil einer Katalogzeile (erfüllt, nicht erfüllt, nicht prüfbar) mit Grund, Belegen, Karten."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .katalog import KatalogFehler, Zeile

ERFUELLT, NICHT_ERFUELLT, NICHT_PRUEFBAR = "erfüllt", "nicht erfüllt", "nicht prüfbar"


@dataclass
class Ergebnis:
    urteil: str
    grund: str
    belege: list[str] = field(default_factory=list)
    # PNG-Karten (Name → Bytes); bewusst nicht in als_dict und damit nicht im Ergebnis-Hash
    bilder: dict[str, bytes] = field(default_factory=dict)
    # Messwerte je Einzelfall (z. B. je Kontrollpunkt): im Ergebnis und als CSV-Anhang
    tabelle: list[dict[str, Any]] = field(default_factory=list)

    def als_dict(self) -> dict[str, Any]:
        d = {"urteil": self.urteil, "grund": self.grund, "belege": self.belege}
        return d | ({"tabelle": self.tabelle} if self.tabelle else {})


def _param(z: Zeile, k: str, typ: type | tuple[type, ...], pflicht: bool = True) -> Any:
    v = z.parameter.get(k)
    if v is None:
        if pflicht:
            raise KatalogFehler(f"{z.id}: Parameter „{k}“ fehlt ({z.pruefart})")
        return None
    if not isinstance(v, typ) or (isinstance(v, bool) and typ is not bool):
        raise KatalogFehler(f"{z.id}: Parameter „{k}“ hat den falschen Typ")
    return v


def _urteil(maengel: list[str], ok: str, belege: list[str]) -> Ergebnis:
    if maengel:
        return Ergebnis(NICHT_ERFUELLT, "; ".join(maengel), belege)
    return Ergebnis(ERFUELLT, ok, belege)
