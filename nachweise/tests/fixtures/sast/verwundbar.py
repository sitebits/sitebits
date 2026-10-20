"""Absichtlich verwundbare Fixture der Negativkontrolle `sast-fund-unregistriert`: Semgrep und
Bandit müssen hier Funde ab mittel melden, die in keinem Befundregister stehen. Wird nie
importiert oder ausgeführt und liegt außerhalb der gescannten Quellbäume des Produkts."""

import pickle
import subprocess


def befehl_ausfuehren(eingabe: str) -> int:
    return subprocess.call(eingabe, shell=True)


def laden(daten: bytes) -> object:
    return pickle.loads(daten)
