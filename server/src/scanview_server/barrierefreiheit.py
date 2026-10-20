"""Erklärung zur Barrierefreiheit und Feedback-Weg je Instanz.

Beides sind Instanz-Einstellungen aus der Umgebung (es gibt bewusst kein Einstellmenü), geprüft
beim Start — eine ungültige Einstellung beendet den Dienst (wie SCANVIEW_QUELLTEXT_URL), damit nie
ein toter oder gefährlicher Link und nie eine halb ausgefüllte Erklärung im Portal steht.
Verantwortlich für die Erklärung ist der Betreiber; die Vorlage liegt genau einmal im Paket
(VORLAGE)."""

from __future__ import annotations

import datetime as dt
import logging
import os
import re
import stat
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Literal

_LOG = logging.getLogger("scanview.barrierefreiheit")

VORLAGE = "barrierefreiheit_vorlage.toml"
MAX_BYTES = 64 * 1024
MAX_TEXT = 2000
MAX_EINTRAEGE = 50
STAENDE = ("vollstaendig", "teilweise", "nicht")
METHODEN = ("Selbstbewertung", "Prüfung durch Dritte")
_PLATZHALTER = re.compile(r"@[A-Z_]+@")
_HTTPS = re.compile(r"https://[^\s/?#]+(?:[/?#]\S*)?")
# Nur eine nackte Adresse: kein ?, &, %, Leerraum — Betreff und Text setzt das Portal selbst.
_MAILTO = re.compile(r"mailto:[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
# Strikt JJJJ-MM-TT mit Bindestrichen; [0-9] statt \d, das auf str-Patterns auch andere
# Unicode-Ziffern zuließe. Nur die Form — Wertebereich (z. B. Monat 13) prüft fromisoformat.
_DATUM = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_OBEN = {"schema", "stelle", "geltungsbereich", "stand", "erstellt", "methode", "methode_text",
         "nicht_barrierefrei", "durchsetzung"}
_EINTRAG = {"inhalt", "grund", "alternative"}
_DURCHSETZUNG = {"text", "stelle", "url"}


class KonfigFehler(ValueError):
    """Ungültige Barrierefreiheits-Einstellung; die Nachricht nennt Datei bzw. Feld."""


@dataclass(frozen=True)
class Kontakt:
    art: Literal["mailto", "formular"]
    ziel: str


def pruefe_kontakt(wert: str) -> Kontakt:
    if len(wert) > MAX_TEXT:
        raise KonfigFehler(
            f"SCANVIEW_BARRIEREFREIHEIT_KONTAKT ist länger als {MAX_TEXT} Zeichen.")
    if _MAILTO.fullmatch(wert):
        return Kontakt("mailto", wert)
    if _HTTPS.fullmatch(wert):
        return Kontakt("formular", wert)
    raise KonfigFehler("SCANVIEW_BARRIEREFREIHEIT_KONTAKT muss mailto:<adresse> (ohne ?, &, %) "
                       "oder eine https://-Adresse sein.")


def _text(d: Mapping[str, Any], feld: str, wo: str) -> str:
    v = d.get(feld)
    if not isinstance(v, str) or not v.strip():
        raise KonfigFehler(f"{wo}: Feld „{feld}“ fehlt oder ist leer.")
    if len(v) > MAX_TEXT:
        raise KonfigFehler(f"{wo}: Feld „{feld}“ ist länger als {MAX_TEXT} Zeichen.")
    return v.strip()


def _nur(d: Mapping[str, Any], erlaubt: set[str], wo: str) -> None:
    fremd = sorted(set(d) - erlaubt)
    if fremd:
        raise KonfigFehler(f"{wo}: unbekannte Schlüssel {', '.join(fremd)}.")
    fehlt = sorted(erlaubt - set(d))
    if fehlt:
        raise KonfigFehler(f"{wo}: Feld „{fehlt[0]}“ fehlt.")


def _eintraege(roh: Any, wo: str) -> list[dict[str, str]]:
    if not isinstance(roh, list) or not roh or len(roh) > MAX_EINTRAEGE:
        raise KonfigFehler(f"{wo}: [[nicht_barrierefrei]] braucht 1 bis {MAX_EINTRAEGE} Einträge.")
    aus: list[dict[str, str]] = []
    for nr, e in enumerate(roh, start=1):
        if not isinstance(e, dict):
            raise KonfigFehler(f"{wo}: nicht_barrierefrei[{nr}] ist keine Tabelle.")
        _nur(e, _EINTRAG, f"{wo}: nicht_barrierefrei[{nr}]")
        aus.append({f: _text(e, f, f"{wo}: nicht_barrierefrei[{nr}]") for f in ("inhalt", "grund",
                                                                                 "alternative")})
    return aus


def _lies(pfad: Path) -> str:
    # Nicht read_bytes(): das läse eine Spezialdatei (FIFO ohne Schreiber, /dev/zero) vollständig,
    # bevor die eigene Größenprüfung greift — blockiert bzw. wächst unbegrenzt.
    # O_NONBLOCK verhindert das Blockieren beim Öffnen einer leeren FIFO; auf regulären Dateien
    # wirkt es nicht. fstat vor dem Lesen weist alles ab, was keine reguläre Datei ist, und die
    # Lesegrenze MAX_BYTES + 1 reicht, um „zu groß“ zu erkennen, ohne mehr einzulesen.
    try:
        fd = os.open(pfad, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise KonfigFehler(f"{pfad}: keine reguläre Datei.")
            with os.fdopen(fd, "rb", closefd=False) as f:
                roh = f.read(MAX_BYTES + 1)
        finally:
            os.close(fd)
    except OSError as e:
        raise KonfigFehler(f"{pfad}: nicht lesbar ({e.strerror}).") from e
    if len(roh) > MAX_BYTES:
        raise KonfigFehler(f"{pfad}: größer als {MAX_BYTES // 1024} KiB.")
    try:
        return roh.decode("utf-8")
    except UnicodeDecodeError as e:
        raise KonfigFehler(f"{pfad}: kein gültiges UTF-8.") from e


def lade_erklaerung(pfad: Path) -> dict[str, Any]:
    text = _lies(pfad)
    rest = _PLATZHALTER.search(text)
    if rest:
        raise KonfigFehler(f"{pfad}: Platzhalter {rest.group(0)} ist nicht ersetzt.")
    try:
        d = tomllib.loads(text)
    except tomllib.TOMLDecodeError as e:
        raise KonfigFehler(f"{pfad}: kein gültiges TOML ({e}).") from e
    wo = str(pfad)
    _nur(d, _OBEN, wo)
    if type(d["schema"]) is not int or d["schema"] != 1:
        raise KonfigFehler(f"{wo}: Feld „schema“ muss 1 sein.")
    stand = _text(d, "stand", wo)
    if stand not in STAENDE:
        raise KonfigFehler(f"{wo}: Feld „stand“ muss einer von {', '.join(STAENDE)} sein.")
    methode = _text(d, "methode", wo)
    if methode not in METHODEN:
        raise KonfigFehler(f"{wo}: Feld „methode“ muss einer von {', '.join(METHODEN)} sein.")
    erstellt = _text(d, "erstellt", wo)
    if not _DATUM.fullmatch(erstellt):
        raise KonfigFehler(f"{wo}: Feld „erstellt“ ist kein Datum JJJJ-MM-TT.")
    try:
        dt.date.fromisoformat(erstellt)
    except ValueError as e:
        raise KonfigFehler(f"{wo}: Feld „erstellt“ ist kein Datum JJJJ-MM-TT.") from e
    geltung = _text(d, "geltungsbereich", wo)
    if not _HTTPS.fullmatch(geltung):
        raise KonfigFehler(f"{wo}: Feld „geltungsbereich“ muss eine https://-Adresse sein.")
    ds = d["durchsetzung"]
    if not isinstance(ds, dict):
        raise KonfigFehler(f"{wo}: [durchsetzung] ist keine Tabelle.")
    _nur(ds, _DURCHSETZUNG, f"{wo}: durchsetzung")
    url = _text(ds, "url", f"{wo}: durchsetzung")
    if not _HTTPS.fullmatch(url):
        raise KonfigFehler(f"{wo}: durchsetzung: Feld „url“ muss eine https://-Adresse sein.")
    return {
        "schema": 1, "stelle": _text(d, "stelle", wo), "geltungsbereich": geltung,
        "stand": stand, "erstellt": erstellt, "methode": methode,
        "methode_text": _text(d, "methode_text", wo),
        "nicht_barrierefrei": _eintraege(d["nicht_barrierefrei"], wo),
        "durchsetzung": {"text": _text(ds, "text", f"{wo}: durchsetzung"),
                         "stelle": _text(ds, "stelle", f"{wo}: durchsetzung"), "url": url},
    }


def produkt_einschraenkungen() -> list[dict[str, str]]:
    """Produktseitige Einschränkungen aus der Vorlage — gezeigt, solange der Betreiber keine eigene
    Erklärung hinterlegt hat. Die Vorlage enthält dort keine Platzhalter (Test)."""
    text = (resources.files("scanview_server") / VORLAGE).read_text(encoding="utf-8")
    return _eintraege(tomllib.loads(text)["nicht_barrierefrei"], VORLAGE)


@dataclass(frozen=True)
class Barrierefreiheit:
    kontakt: Kontakt | None = None
    erklaerung: dict[str, Any] | None = None

    def als_json(self) -> dict[str, Any]:
        k = self.kontakt
        return {"kontakt": {"art": k.art, "ziel": k.ziel} if k else None,
                "erklaerung": self.erklaerung}

    @staticmethod
    def aus_umgebung(env: Mapping[str, str]) -> Barrierefreiheit:
        roh_k = env.get("SCANVIEW_BARRIEREFREIHEIT_KONTAKT", "").strip()
        roh_e = env.get("SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG", "").strip()
        try:
            kontakt = pruefe_kontakt(roh_k) if roh_k else None
            erklaerung = lade_erklaerung(Path(roh_e)) if roh_e else None
        except KonfigFehler as e:
            raise SystemExit(str(e)) from e
        if kontakt is None:
            _LOG.warning("SCANVIEW_BARRIEREFREIHEIT_KONTAKT fehlt: „Barriere melden“ zeigt nur "
                         "einen Hinweis für den Betreiber.")
        if erklaerung is None:
            _LOG.warning("SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG fehlt: die Seite „Barrierefreiheit“ "
                         "zeigt keine Erklärung der Stelle (Vorlage: %s).", VORLAGE)
        return Barrierefreiheit(kontakt, erklaerung)
