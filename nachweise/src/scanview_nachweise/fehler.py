"""Einheitliches Fehlerformat für die Nachweiskette: fail-closed, jede Regelverletzung meldet
sich mit ihrer Regel-Kennung und einem Klartext (`NACHWEIS-FEHLER <regel>: <text>` auf stderr,
Exit ungleich 0)."""

from __future__ import annotations


class NachweisFehler(Exception):
    """Wird von Handlern der Nachweiskette geworfen, wenn eine Regel verletzt ist (z. B. fehlende
    SBOM-Abdeckung, veraltete Scan-Datenbank, ungültige Signatur). `regel` benennt die verletzte
    Regel (z. B. "sbom-abdeckung"), `text` den Klartext für Menschen."""

    def __init__(self, regel: str, text: str) -> None:
        super().__init__(regel, text)
        self.regel = regel
        self.text = text

    def __str__(self) -> str:
        return f"NACHWEIS-FEHLER {self.regel}: {self.text}"
