"""Mindestversionen der Web-Abhängigkeiten mit bekannten Lücken (Befund B-WB-N7).

Der Wochenscan findet neue Lücken; dieser Test hält die behobenen fest: Eine Lockdatei, die
eines der Pakete unter die Fix-Version zurückfallen lässt, fällt hier auf, auch ohne Netz.
"""

import json
from pathlib import Path

WURZEL = Path(__file__).resolve().parents[2]

# Paket → geprüfter Stand ohne die Lücken aus B-WB-N7 (npm audit 0); darunter liegen sie.
MINDESTVERSIONEN = {
    "vitest": (4, 1, 11),
    "vite": (6, 4, 3),
    # Event-Loop-DoS über Offsets indizierter Source-Map-Abschnitte (npm audit „high“)
    "source-map-js": (1, 2, 2),
}


def _version(text: str) -> tuple[int, ...]:
    return tuple(int(teil) for teil in text.split("-", 1)[0].split("."))


def test_b_wb_n7_keine_versionen_mit_bekannter_luecke_in_der_web_lockdatei():
    lock = WURZEL / "web" / "package-lock.json"
    pakete = json.loads(lock.read_text(encoding="utf-8"))["packages"]
    gefunden: dict[str, list[str]] = {}
    for pfad, eintrag in pakete.items():
        name = pfad.rsplit("node_modules/", 1)[-1]
        if name in MINDESTVERSIONEN:
            gefunden.setdefault(name, []).append(eintrag["version"])
    assert set(gefunden) == set(MINDESTVERSIONEN), "Paket fehlt in der Lockdatei — Liste anpassen"
    zu_alt = {name: v for name, versionen in gefunden.items() for v in versionen
              if _version(v) < MINDESTVERSIONEN[name]}
    assert not zu_alt, f"Versionen mit bekannter Lücke (B-WB-N7): {zu_alt}"
