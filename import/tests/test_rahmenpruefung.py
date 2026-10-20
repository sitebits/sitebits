"""Rahmenprüfung: Tie Points des Exports gegen die Paketwolke."""

import numpy as np
import pytest

from scanview_import.rahmenpruefung import RahmenFehler, pruefe_rahmen


def _wolke(n=40_000, seed=0):
    """Boden, eine Wand in x- und eine in y-Richtung: jede Verschiebung hat eine Normale."""
    rng = np.random.default_rng(seed)
    d = n // 3
    boden = np.column_stack([rng.uniform(0, 50, (d, 2)), rng.normal(0, 0.005, d)])
    wand_x = np.column_stack(
        [rng.uniform(0, 50, d), 25 + rng.normal(0, 0.005, d), rng.uniform(0, 8, d)]
    )
    wand_y = np.column_stack(
        [25 + rng.normal(0, 0.005, d), rng.uniform(0, 50, d), rng.uniform(0, 8, d)]
    )
    return np.vstack([boden, wand_x, wand_y]) + [400_000.0, 5_800_000.0, 30.0]


def _tie(wolke, n=500, versatz=(0.0, 0.0, 0.0), seed=1):
    rng = np.random.default_rng(seed)
    return wolke[rng.choice(len(wolke), n, replace=False)] + versatz + rng.normal(0, 0.01, (n, 3))


def test_gleicher_stand():
    wolke = _wolke()
    ergebnis = pruefe_rahmen(_tie(wolke), wolke)
    assert ergebnis.median_m <= 0.05
    assert ergebnis.n == 500


@pytest.mark.parametrize("versatz", [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)])
def test_ein_meter_versatz_bricht_ab(versatz):
    wolke = _wolke()
    with pytest.raises(RahmenFehler, match="anderen Ausrichtungsstand"):
        pruefe_rahmen(_tie(wolke, versatz=versatz), wolke)


def test_grauzone_verlangt_toleranz():
    wolke = _wolke()
    tie = _tie(wolke, versatz=(0.0, 0.0, 0.1))
    with pytest.raises(RahmenFehler, match="--rahmen-toleranz"):
        pruefe_rahmen(tie, wolke)
    assert pruefe_rahmen(tie, wolke, toleranz_m=0.15).median_m < 0.15


def test_punkte_ausserhalb_der_wolke_zaehlen_nicht():
    wolke = _wolke()
    draussen = np.array([[401_000.0, 5_801_000.0, 30.0]] * 400)
    ergebnis = pruefe_rahmen(np.vstack([_tie(wolke), draussen]), wolke)
    assert ergebnis.n == 500 and ergebnis.median_m <= 0.05


def test_zu_wenige_tie_points():
    wolke = _wolke()
    with pytest.raises(RahmenFehler, match="zu wenige"):
        pruefe_rahmen(_tie(wolke, n=50), wolke)


def test_versatz_wird_geschaetzt():
    wolke = _wolke()
    ergebnis = pruefe_rahmen(_tie(wolke, versatz=(0.03, -0.02, 0.01)), wolke)
    np.testing.assert_allclose(ergebnis.versatz_m, [0.03, -0.02, 0.01], atol=0.01)


def test_zu_ebene_szene_wird_nicht_geraten():
    rng = np.random.default_rng(0)
    boden = np.column_stack([rng.uniform(0, 50, (20_000, 2)), rng.normal(0, 0.005, 20_000)])
    with pytest.raises(RahmenFehler, match="zu eben"):
        pruefe_rahmen(_tie(boden), boden)


def test_duenne_wolke_grosser_szene_besteht_bei_gleichem_stand():
    """Baustelle 200 x 200 m, 30 000 Punkte (≈ 1 m Punktabstand): gleicher Stand muss bestehen."""
    rng = np.random.default_rng(3)
    d = 10_000
    boden = np.column_stack([rng.uniform(0, 200, (d, 2)), rng.normal(0, 0.005, d)])
    wand_x = np.column_stack(
        [rng.uniform(0, 200, d), 100 + rng.normal(0, 0.005, d), rng.uniform(0, 12, d)]
    )
    wand_y = np.column_stack(
        [100 + rng.normal(0, 0.005, d), rng.uniform(0, 200, d), rng.uniform(0, 12, d)]
    )
    wolke = np.vstack([boden, wand_x, wand_y]) + [400_000.0, 5_800_000.0, 30.0]
    # Tie Points liegen auf denselben Flächen, aber nicht auf Wolkenpunkten.
    tie = np.vstack(
        [
            np.column_stack([rng.uniform(0, 200, (300, 2)), np.zeros(300)]),
            np.column_stack(
                [rng.uniform(0, 200, 200), np.full(200, 100.0), rng.uniform(0, 12, 200)]
            ),
            np.column_stack(
                [np.full(200, 100.0), rng.uniform(0, 200, 200), rng.uniform(0, 12, 200)]
            ),
        ]
    ) + [400_000.0, 5_800_000.0, 30.0]
    ergebnis = pruefe_rahmen(tie, wolke)
    assert ergebnis.median_m <= 0.05
    with pytest.raises(RahmenFehler):
        pruefe_rahmen(tie + [0.0, 0.0, 0.5], wolke)
