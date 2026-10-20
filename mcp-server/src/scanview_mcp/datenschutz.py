"""Datensparsamer Modus ab Werk (Befund B-TE-M3).

Alles, was ein Werkzeug zurückgibt, geht an den Anbieter des Sprachmodells. Standardmäßig
verlassen deshalb keine Klarnamen anderer Konten und keine Freitexte den Rechner:

- Namen und Konto-IDs (`geaendertVonName`/`geaendertVon`, `erstelltVonName`/`erstelltVon` bei
  POIs und Messungen, `nutzer`/`nutzerId` im Verlauf) werden zu `Konto-<8 hex>`, Token-Namen
  (`token` im Verlauf) zu `Token-<8 hex>`, das `objekt` von Rechte-/Kontoereignissen
  (`recht.*`, `konto.*`, `beteiligter.*`: die Konto-ID des betroffenen Kontos) zu dessen `Konto-…` —
  HMAC-SHA-256 über die Konto-ID mit einem geheimen Salz. Stabil innerhalb eines Laufs (und
  über Läufe, wenn `SCANVIEW_MCP_PSEUDONYM_SALZ` gesetzt ist), ohne das Salz nicht auf die
  Konto-ID zurückzuführen. Die rohe Konto-ID wird mit ersetzt, sonst wäre das Pseudonym wertlos.
- Freitexte (`note` bei POIs, `beschreibung` bei Projekt, Export und Verlauf, `text` der
  Befund-Faden-Einträge, Adresse und Titel eines Links) werden zu `AUSGEBLENDET`, auch in
  `vorher`/`nachher` des Verlaufs und im Konfliktstand (409). Leere Texte bleiben leer. Titel,
  Kategorien, Standpunkte, Geometrie und Maße bleiben; im Faden Art, Status, Frist, Zeiten und
  die Zuständigkeit (Gewerk-ID, Person als Pseudonym).

`SCANVIEW_MCP_PERSONENDATEN=voll` schaltet das ab (Betreiberentscheidung, AVV beim
Verantwortlichen) und schreibt beim Start eine Warnung auf stderr. Jeder andere Wert bleibt
datensparsam; ein unbekannter Wert warnt. Meldungen nennen nie den Wert einer Variable.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, TextIO

MODUS_VARIABLE = "SCANVIEW_MCP_PERSONENDATEN"
SALZ_VARIABLE = "SCANVIEW_MCP_PSEUDONYM_SALZ"
VOLL = "voll"
DATENSPARSAM = "datensparsam"
MIN_SALZ = 16

AUSGEBLENDET = "Freitext ausgeblendet (datensparsamer Modus)"
#: Freitext-Schlüssel in API-Antworten (POI-Daten, Projektkarte, Export, Verlauf).
FREITEXT = frozenset({"note", "beschreibung", "text"})
#: Konto-ID-Felder (int) und die zugehörigen Namensfelder (str); `nutzer_id` (Zuständigkeit) und
#: `erstellt_von` (Faden-Eintrag im Verlauf) sind ebenfalls Konto-IDs.
KONTO_ID = frozenset({"geaendertVon", "erstelltVon", "nutzerId", "nutzer_id", "erstellt_von"})
NAME_ZU_ID = {
    "geaendertVonName": "geaendertVon", "erstelltVonName": "erstelltVon", "nutzer": "nutzerId",
}
#: Ereignisarten, deren `objekt` die Konto-ID (als Text) des betroffenen Kontos ist.
KONTO_OBJEKT_ARTEN = ("recht.", "konto.", "beteiligter.")

WARNUNG_VOLL = (
    f"scanview-mcp: WARNUNG: {MODUS_VARIABLE}=voll — Namen anderer Beteiligter, Token-Namen und "
    "Freitexte (Notizen, Beschreibungen) gehen unverändert an den KI-Anbieter. Nur mit Freigabe "
    "des Verantwortlichen (AVV) nutzen."
)
WARNUNG_UNBEKANNT = (
    f"scanview-mcp: WARNUNG: {MODUS_VARIABLE} hat einen unbekannten Wert — der datensparsame "
    f"Modus bleibt aktiv (nur „{VOLL}“ schaltet ihn ab)."
)
WARNUNG_SALZ = (
    f"scanview-mcp: WARNUNG: {SALZ_VARIABLE} ist kürzer als {MIN_SALZ} Zeichen und wird nicht "
    "verwendet — Pseudonyme gelten nur für diesen Lauf."
)


def ist_platzhalter(text: str | None) -> bool:
    """True, wenn `text` der Platzhalter ist — die KI soll ihn nie als Inhalt zurückschreiben."""
    return text is not None and text.strip() == AUSGEBLENDET


@dataclass(frozen=True)
class Datenschutz:
    """`voll` = Originaldaten; sonst Pseudonyme und ausgeblendete Freitexte mit `salz`."""

    voll: bool = False
    salz: bytes = field(default_factory=lambda: secrets.token_bytes(32), repr=False)

    def _kurz(self, art: str, wert: str) -> str:
        return hmac.new(self.salz, f"{art}:{wert}".encode(), hashlib.sha256).hexdigest()[:8]

    def konto(self, konto_id: Any, name: Any) -> str | None:
        """Pseudonym eines Kontos: aus der ID, ersatzweise aus dem Namen; None, wenn beides
        fehlt (z. B. Systemereignis)."""
        if isinstance(konto_id, int) and not isinstance(konto_id, bool):
            return "Konto-" + self._kurz("konto", str(konto_id))
        if isinstance(name, str) and name:
            return "Konto-" + self._kurz("name", name)
        return None

    def token(self, name: str, konto_id: Any) -> str:
        """Pseudonym eines Token-Namens; Token-Namen sind nur je Konto eindeutig."""
        return "Token-" + self._kurz("token", f"{konto_id}:{name}")

    def filtern(self, wert: Any) -> Any:
        """Antwort (JSON-Struktur) für die KI; im Modus `voll` unverändert."""
        return wert if self.voll else self._filtern(wert)

    def _filtern(self, wert: Any) -> Any:
        if isinstance(wert, list):
            return [self._filtern(v) for v in wert]
        if not isinstance(wert, dict):
            return wert
        aus: dict[str, Any] = {}
        for k, v in wert.items():
            if k == "erstellt_von" and isinstance(v, dict):
                # Faden-Eintrag: {id, name} → beides dasselbe Pseudonym.
                aus[k] = {"id": self.konto(v.get("id"), None),
                          "name": self.konto(v.get("id"), v.get("name"))}
            elif k == "wert" and wert.get("art") == "link" and isinstance(v, dict):
                aus[k] = {lk: AUSGEBLENDET if isinstance(lv, str) and lv.strip() else lv
                          for lk, lv in v.items()}
            elif k in FREITEXT and isinstance(v, str):
                aus[k] = AUSGEBLENDET if v.strip() else v
            elif k in KONTO_ID and isinstance(v, int) and not isinstance(v, bool):
                aus[k] = self.konto(v, None)
            elif k in NAME_ZU_ID and isinstance(v, str):
                aus[k] = self.konto(wert.get(NAME_ZU_ID[k]), v)
            elif k == "token" and isinstance(v, str):
                aus[k] = self.token(v, wert.get("nutzerId"))
            elif k == "objekt" and isinstance(v, str) and self._konto_objekt(wert):
                aus[k] = self.konto(int(v), None) if v.isdecimal() else self.konto(None, v)
            else:
                aus[k] = self._filtern(v)
        return aus

    @staticmethod
    def _konto_objekt(ereignis: dict[str, Any]) -> bool:
        art = ereignis.get("art")
        return isinstance(art, str) and art.startswith(KONTO_OBJEKT_ARTEN)


def aus_umgebung(env: Mapping[str, str], fehlerausgabe: TextIO | None = None) -> Datenschutz:
    """Liest Schalter und Salz; Warnungen gehen nach `fehlerausgabe` (Standard: sys.stderr)."""
    aus = sys.stderr if fehlerausgabe is None else fehlerausgabe
    modus = env.get(MODUS_VARIABLE, "")
    voll = modus == VOLL
    if voll:
        print(WARNUNG_VOLL, file=aus)
    elif modus not in ("", DATENSPARSAM):
        print(WARNUNG_UNBEKANNT, file=aus)
    salz_text = env.get(SALZ_VARIABLE, "")
    if len(salz_text) >= MIN_SALZ:
        return Datenschutz(voll=voll, salz=salz_text.encode())
    if salz_text:
        print(WARNUNG_SALZ, file=aus)
    return Datenschutz(voll=voll)
