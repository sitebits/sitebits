"""Tor für orientierte Fotos (Ebene `fotos`): Personen auf Baustellenfotos.

Solange `images.json` nicht `anonymization.status == "released"` trägt, liefert `/api/authz`
für `images.json` und alles unter `fotos/` nur Bearbeitern, Projektleitung und Admin eine 200;
Betrachter und Rückmelder bekommen 404 (kein Hinweis auf Existenz). Fehlt die Datei, ist sie
unlesbar oder anders geformt, gilt sie als nicht freigegeben (fail closed).

Der Status wird je Paket nach (mtime, Größe) zwischengespeichert: `/api/authz` liegt im heißen
Pfad, eine Freigabe oder Rücknahme wirkt trotzdem beim nächsten Abruf.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

IMAGES_FILE = "images.json"
FOTOS_PRAEFIX = "fotos/"
MINDESTROLLE = "bearbeiter"

_cache: dict[Path, tuple[int, int, bool]] = {}
_sperre = threading.Lock()


def ist_fotopfad(rest: str) -> bool:
    """Pfad innerhalb des Pakets (nach dem Dekodieren), Groß-/Kleinschreibung egal.

    Breit statt genau: jedes erste Segment, das mit „fotos“ oder „images“ beginnt, gilt als
    Fotopfad — auch Arbeits- und Sicherungskopien (fotos.neu/, fotos-alt/, images.alt.json), die
    ein abgebrochener Import oder ein Mensch im Paket hinterlassen kann.
    """
    erstes = rest.lower().split("/", 1)[0]
    return erstes.startswith(FOTOS_PRAEFIX.rstrip("/")) or erstes.startswith("images")


def _lesen(datei: Path) -> bool:
    try:
        daten = json.loads(datei.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(daten, dict):
        return False
    anon = daten.get("anonymization")
    return isinstance(anon, dict) and anon.get("status") == "released"


def freigegeben(paket: Path) -> bool:
    datei = paket / IMAGES_FILE
    try:
        st = datei.stat()
    except OSError:
        return False
    with _sperre:
        alt = _cache.get(datei)
        if alt is not None and alt[:2] == (st.st_mtime_ns, st.st_size):
            return alt[2]
    wert = _lesen(datei)
    with _sperre:
        _cache[datei] = (st.st_mtime_ns, st.st_size, wert)
    return wert
