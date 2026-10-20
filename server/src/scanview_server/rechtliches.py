"""Impressum und Datenschutzhinweise je Instanz: nur die Verlinkung.

Den Rechtstext stellt der Betreiber (eigene Seite oder statische Datei derselben Origin), das
Produkt liefert keinen. Beides sind Instanz-Einstellungen aus der Umgebung, geprüft beim Start —
eine ungültige Einstellung beendet den Dienst (wie bei der Barrierefreiheit), damit nie ein
gefährlicher Link oder einer mit Zugangsdaten im Portal steht. Fehlt eine Angabe, warnt der Dienst
beim Start und `einrichten.sh` schreibt eine Warnung ins Update-Protokoll; ein Abbruch ist das
nicht."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

_LOG = logging.getLogger("scanview.rechtliches")

MAX_LAENGE = 2000
# Nur druckbares ASCII ohne Backslash (Browser lesen \ in Adressen wie /).
_ZEICHEN = re.compile(r"[\x21-\x5b\x5d-\x7e]+")
# https mit Host, ohne Zugangsdaten (kein @ vor dem ersten /, ? oder #).
_HTTPS = re.compile(r"https://[^/?#@]+(?:[/?#].*)?")
# Pfad auf derselben Origin: beginnt mit genau einem / (// wäre protokollrelativ, fremde Origin).
_PFAD = re.compile(r"/(?!/).*")

VARIABLEN = {"impressum": "SCANVIEW_IMPRESSUM_URL", "datenschutz": "SCANVIEW_DATENSCHUTZ_URL"}


class KonfigFehler(ValueError):
    """Ungültige Einstellung; die Nachricht nennt die Variable."""


def pruefe_link(variable: str, wert: str) -> str:
    """`https://<host>…` ohne Zugangsdaten oder ein Pfad `/…` auf derselben Origin."""
    if len(wert) > MAX_LAENGE:
        raise KonfigFehler(f"{variable} ist länger als {MAX_LAENGE} Zeichen.")
    if _ZEICHEN.fullmatch(wert) and (_HTTPS.fullmatch(wert) or _PFAD.fullmatch(wert)):
        return wert
    raise KonfigFehler(f"{variable} muss eine https://-Adresse ohne Zugangsdaten oder ein Pfad "
                       "auf derselben Origin (/…, nicht //…) sein, ohne Leerzeichen und "
                       "Backslash.")


@dataclass(frozen=True)
class Rechtliches:
    impressum: str | None = None
    datenschutz: str | None = None

    def als_json(self) -> dict[str, Any]:
        return {"impressum": self.impressum, "datenschutz": self.datenschutz}

    @staticmethod
    def aus_umgebung(env: Mapping[str, str]) -> Rechtliches:
        werte: dict[str, str | None] = {}
        for feld, variable in VARIABLEN.items():
            roh = env.get(variable, "").strip()
            try:
                werte[feld] = pruefe_link(variable, roh) if roh else None
            except KonfigFehler as e:
                raise SystemExit(str(e)) from e
            if werte[feld] is None:
                _LOG.warning("%s fehlt: das Portal zeigt keinen Link „%s“.", variable,
                             "Impressum" if feld == "impressum" else "Datenschutz")
        return Rechtliches(**werte)
