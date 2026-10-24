"""Einordnung eines RMSE in gängige Genauigkeitsklassen — zur Orientierung, kein Urteil.

Das Urteil fällt allein gegen die Schwelle des Katalogs. Die Einordnung übersetzt den Wert in die
Sprache, in der Ausschreibungen Genauigkeit verlangen (Recherche 21.10.2026, Abschnitt Normen):

- BFR-Verm 2.2 (Bremen, in Anlehnung an DIN 18710-1), 1 sigma: Lage OGL1 5 bis 15 cm,
  OGL2 1,5 bis 5 cm, OGL3 5 bis 15 mm, OGL4 0,5 bis 5 mm; Höhe OGH1 2 bis 5 cm, OGH2 5 bis 20 mm.
  RMSE gilt als Schätzer für sigma.
- USIBD LOA 3.x: LOA10 50 bis 150 mm, LOA20 15 bis 50 mm, LOA30 5 bis 15 mm, LOA40 1 bis 5 mm,
  LOA50 0 bis 1 mm. Konfidenz laut Sekundärquelle 95 %, am Primärdokument nicht geprüft;
  verglichen wird deshalb 2 · RMSE [Annahme: 95 % ≈ 2 sigma].
- Eckstein-Stufen der Bauaufnahme: II ±10 cm, III ±2,5 cm, IV ±2 cm (Sekundärquelle); was „±“
  statistisch meint, ist nicht festgelegt, verglichen wird der RMSE [Annahme].
"""

from __future__ import annotations

# (Name, untere Grenze, obere Grenze) in m, feinste zuletzt
OGL = (("OGL1", 0.05, 0.15), ("OGL2", 0.015, 0.05), ("OGL3", 0.005, 0.015), ("OGL4", 0.0005, 0.005))
OGH = (("OGH1", 0.02, 0.05), ("OGH2", 0.005, 0.02))
LOA = (
    ("LOA10", 0.05, 0.15),
    ("LOA20", 0.015, 0.05),
    ("LOA30", 0.005, 0.015),
    ("LOA40", 0.001, 0.005),
    ("LOA50", 0.0, 0.001),
)
ECKSTEIN = (("II", 0.10), ("III", 0.025), ("IV", 0.02))


def _band(wert: float, klassen: tuple[tuple[str, float, float], ...]) -> str:
    if wert > klassen[0][2]:
        return f"gröber als {klassen[0][0]}"
    for name, unten, oben in klassen:
        if unten < wert <= oben or (unten == 0.0 and wert <= oben):
            return name
    return f"feiner als {klassen[-1][0]}"


def _eckstein(rmse: float) -> str:
    erreicht = [name for name, grenze in ECKSTEIN if rmse <= grenze]
    return f"Stufe {erreicht[-1]}" if erreicht else "nur Stufe I (schematisch)"


def einordnung(rmse: float, lage: bool) -> str:
    """Belegzeile für einen RMSE der Lage (``lage``) oder Höhe."""
    din = _band(rmse, OGL if lage else OGH)
    return (
        f"Einordnung zur Orientierung (kein Urteil): BFR-Verm {din} (1\u03c3); USIBD "
        f"{_band(2 * rmse, LOA)} [Annahme 95 % ≈ 2 · RMSE]; Eckstein {_eckstein(rmse)} "
        "[Annahme ± = RMSE]"
    )
