"""Tests für scripts/overlay_offset.py (Überblendtest) mit synthetischen Bildern."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import numpy as np
import pytest
from PIL import Image

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "overlay_offset.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("overlay_offset", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


oo = _load()


def _texture(h: int, w: int, seed: int) -> np.ndarray:
    """Glatte Zufallstextur 40…240 (nie Hintergrundgrau 17)."""
    rng = np.random.default_rng(seed)
    img = rng.random((h, w))
    for _ in range(3):
        img = (np.roll(img, 1, 0) + img + np.roll(img, -1, 0)) / 3
        img = (np.roll(img, 1, 1) + img + np.roll(img, -1, 1)) / 3
    img = (img - img.min()) / (img.max() - img.min())
    return 40 + 200 * img


def _rgb(gray: np.ndarray) -> np.ndarray:
    return np.repeat(np.clip(gray, 0, 255).astype(np.uint8)[:, :, None], 3, axis=2)


def _pair(
    dx: int, dy: int, h: int = 480, w: int = 640, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    """Inhalt der Wolke bei (x, y) liegt im Panorama bei (x + dx, y + dy)."""
    base = _texture(h + 40, w + 40, seed)
    cloud = base[20 : 20 + h, 20 : 20 + w]
    pano = base[20 - dy : 20 - dy + h, 20 - dx : 20 - dx + w]
    return _rgb(cloud), _rgb(pano)


def _periodic_texture(h: int, w: int, period: int = 8, seed: int = 1) -> np.ndarray:
    """Sinusförmig periodisches Muster (Periode ``period``) mit leichtem Zeilenrauschen für
    Struktur. Der Betrag der Ableitung eines Sinus ist selbst periodisch mit der halben Periode
    (``|cos|`` statt ``cos``) — dadurch liegt bei dieser Konstruktion ein zweites Maximum im
    ±6-px-Suchfenster, das die C7-Guards fassen können. Das ist NICHT für jede periodische
    Textur garantiert (siehe Kommentar bei den Tests unten und den Task-6-Report)."""
    x = np.arange(w)
    base_row = 130 + 100 * np.sin(2 * np.pi * x / period)
    rng = np.random.default_rng(seed)
    y_var = rng.normal(0, 8, (h, 1))
    return np.clip(np.tile(base_row, (h, 1)) + y_var, 0, 255)


def _periodic_pair(
    dx: int, dy: int, h: int = 480, w: int = 640, period: int = 8, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    base = _periodic_texture(h + 40, w + 40, period, seed)
    cloud = base[20 : 20 + h, 20 : 20 + w]
    pano = base[20 - dy : 20 - dy + h, 20 - dx : 20 - dx + w]
    return _rgb(cloud), _rgb(pano)


def test_periodic_pattern_full_period_shift_is_rejected_not_reported_as_zero():
    """Mit ±24-px-Kontroll-Fenster: 8-px-periodisches
    Muster um genau eine Periode (8 px) verschoben — ohne den Mehrdeutigkeits-Guard würde das
    als ≈0 px mit NCC-Spitze ~1 gemessen (falscher Bestanden-Fall). Prüft zusätzlich den
    Ablehnungsgrund (``"ambiguous"``) in tileDetails, damit der Test am alten Stand (Commit
    e0d71ea, keine ``reason``/``valid``-Felder) auch wirklich rot wäre."""
    cloud, pano = _periodic_pair(8, 0, period=8)
    r = oo.measure_offsets(cloud, pano)
    assert r["ok"] is False
    assert r["medianPx"] is None or r["medianPx"] > oo.MAX_MEDIAN_PX
    assert r["tiles"] < oo.MIN_TILES
    assert r["tileDetails"] and all(t["reason"] == "ambiguous" for t in r["tileDetails"])


def test_periodic_pattern_aliased_shift_is_rejected():
    """10 px Versatz aliast bei Periode 8 auf ≈2 px. Achtung: schon der Median allein (6 px, da
    das Alias auf dieser Textur zufällig exakt am Fensterrand liegt) hätte das auch ohne Guard
    als ``ok=False`` erkannt — die Kachel-Gründe sind daher die eigentliche Regressionsprüfung
    für den Guard, nicht das ``ok``-Feld."""
    cloud, pano = _periodic_pair(10, 0, period=8)
    r = oo.measure_offsets(cloud, pano)
    assert r["ok"] is False
    assert r["tiles"] < oo.MIN_TILES
    assert r["tileDetails"] and all(t["reason"] == "border" for t in r["tileDetails"])


def test_non_periodic_texture_shift_still_measured_correctly():
    """Gegenprobe zu den periodischen Fällen: eine echte, nicht periodische Textur mit 3 px
    Versatz darf von den neuen Mehrdeutigkeits-Guards nicht verfälscht werden."""
    cloud, pano = _pair(3, 0)
    r = oo.measure_offsets(cloud, pano)
    assert r["tiles"] >= oo.MIN_TILES
    assert abs(r["medianDx"] - 3) < 0.15
    assert abs(r["medianPx"] - 3) < 0.15


def _hard_tiled_texture(h: int, w: int, period: int = 8, seed: int = 1) -> np.ndarray:
    """Streng periodische Textur ohne Glättung quer zur Kachelgrenze (unabhängiges Rauschen je
    ``period``-px-Streifen, per ``np.tile`` wiederholt). Ein nur enges ±6-px-Fenster
    ließ hierauf einen 8-px-Perioden-Versatz unentdeckt durch: die Gradienten-Autokorrelation
    dieser Textur zeigt anders als bei einer Sinustextur
    KEIN konkurrierendes Maximum innerhalb von ±6 px. Der Guard prüft stattdessen ein deutlich
    breiteres Kontroll-Fenster (±24 px), in dem der echte Perioden-Alias liegt."""
    rng = np.random.default_rng(seed)
    unit = rng.random((h + 40, period))
    for _ in range(3):
        unit = (np.roll(unit, 1, 0) + unit + np.roll(unit, -1, 0)) / 3
        unit = (np.roll(unit, 1, 1) + unit + np.roll(unit, -1, 1)) / 3
    reps = (w + 40) // period + 2
    tiled = np.tile(unit, (1, reps))[:, : w + 40]
    tiled = (tiled - tiled.min()) / (tiled.max() - tiled.min())
    return 40 + 200 * tiled


def _hard_tiled_pair(
    dx: int, dy: int, h: int = 480, w: int = 640, period: int = 8, seed: int = 1
) -> tuple[np.ndarray, np.ndarray]:
    base = _hard_tiled_texture(h + 40, w + 40, period, seed)
    cloud = base[20 : 20 + h, 20 : 20 + w]
    pano = base[20 - dy : 20 - dy + h, 20 - dx : 20 - dx + w]
    return _rgb(cloud), _rgb(pano)


def test_hard_tiled_period_8_shift_by_one_period_is_rejected():
    """Die harte (nicht sinusförmige) Perioden-8-Textur ist um genau eine Periode (8 px)
    verschoben — mit dem engen Fenster allein blieb
    das unentdeckt (tiles=24, medianPx≈0, ok=True), weil das enge ±6-px-Fenster kein
    konkurrierendes Maximum zeigt. Mit dem ±24-px-Kontroll-Fenster liegt der echte
    Alias bei ds=±8 innerhalb des Kontroll-Fensters und wird erkannt."""
    cloud, pano = _hard_tiled_pair(8, 0, period=8)
    r = oo.measure_offsets(cloud, pano)
    assert r["ok"] is False
    assert r["tiles"] < oo.MIN_TILES
    assert r["medianPx"] is None or r["medianPx"] > oo.MAX_MEDIAN_PX
    assert r["tileDetails"] and all(t["reason"] == "ambiguous" for t in r["tileDetails"])


def test_hard_tiled_period_8_shift_by_two_periods_is_rejected():
    """Wie oben, aber um zwei Perioden (16 px) verschoben — der Alias bei ds=0 läge im engen
    Fenster, der echte Treffer bei ds=±16 nur im ±24-px-Kontroll-Fenster."""
    cloud, pano = _hard_tiled_pair(16, 0, period=8)
    r = oo.measure_offsets(cloud, pano)
    assert r["ok"] is False
    assert r["tiles"] < oo.MIN_TILES
    assert r["medianPx"] is None or r["medianPx"] > oo.MAX_MEDIAN_PX
    assert r["tileDetails"] and all(t["reason"] == "ambiguous" for t in r["tileDetails"])


def test_integer_shift_is_measured_with_sign():
    cloud, pano = _pair(3, -2)
    r = oo.measure_offsets(cloud, pano)
    assert r["tiles"] >= oo.MIN_TILES
    assert abs(r["medianDx"] - 3) < 0.15
    assert abs(r["medianDy"] + 2) < 0.15
    assert abs(r["medianPx"] - np.hypot(3, 2)) < 0.2
    assert r["ok"] is False  # 3,6 px > 2 px


def test_unique_search_is_recorded_and_configurable():
    """``unique_search`` (Vorgabe 24) landet in ``params`` und ist konfigurierbar."""
    cloud, pano = _pair(0, 0)
    r = oo.measure_offsets(cloud, pano)
    assert r["params"]["uniqueSearch"] == 24 == oo.UNIQUE_SEARCH
    r2 = oo.measure_offsets(cloud, pano, unique_search=10)
    assert r2["params"]["uniqueSearch"] == 10


def test_zero_shift_passes():
    cloud, pano = _pair(0, 0)
    r = oo.measure_offsets(cloud, pano)
    assert r["medianPx"] < 0.1 and r["ok"] is True


def test_subpixel_shift():
    c1, p1 = _pair(1, 0)
    _, p2 = _pair(2, 0)
    pano = ((p1.astype(np.float64) + p2) / 2).round().astype(np.uint8)
    r = oo.measure_offsets(c1, pano)
    assert abs(r["medianDx"] - 1.5) < 0.3


def test_subpixel_shift_quarter():
    """0,75/0,25-Mischung ergibt 1,25 px — Gegenprobe zu 1,5 px, wo Parabel-Symmetrie zufällig
    passt."""
    c1, p1 = _pair(1, 0)
    _, p2 = _pair(2, 0)
    pano = (0.75 * p1.astype(np.float64) + 0.25 * p2).round().astype(np.uint8)
    r = oo.measure_offsets(c1, pano)
    assert abs(r["medianDx"] - 1.25) < 0.3


def test_tiles_without_points_are_ignored():
    _, pano = _pair(0, 0)
    empty = np.full_like(pano, 17)
    r = oo.measure_offsets(empty, pano)
    assert r["tiles"] == 0 and r["medianPx"] is None and r["ok"] is False


def test_flat_images_have_no_valid_tiles():
    flat = np.full((480, 640, 3), 128, dtype=np.uint8)
    assert oo.measure_offsets(flat, flat)["tiles"] == 0


def test_partial_coverage_drops_tiles_below_min_tiles():
    """Linke Bildhälfte der Wolke ohne Punkte → weniger als MIN_TILES gültige Kacheln, ok=False."""
    cloud, pano = _pair(0, 0)
    cloud = cloud.copy()
    cloud[:, : cloud.shape[1] // 2] = 17
    r = oo.measure_offsets(cloud, pano)
    assert r["tiles"] < oo.MIN_TILES
    assert r["ok"] is False


def test_noise_does_not_break_measurement():
    cloud, pano = _pair(2, 1)
    rng = np.random.default_rng(42)
    cloud_n = np.clip(cloud.astype(np.float64) + rng.normal(0, 4.0, cloud.shape), 0, 255).astype(
        np.uint8
    )
    pano_n = np.clip(pano.astype(np.float64) + rng.normal(0, 4.0, pano.shape), 0, 255).astype(
        np.uint8
    )
    r = oo.measure_offsets(cloud_n, pano_n)
    assert r["tiles"] >= oo.MIN_TILES
    assert abs(r["medianDx"] - 2) < 0.3
    assert abs(r["medianDy"] - 1) < 0.3


def test_batch_needs_three_passing_stations(tmp_path):
    for sid, shift in (("S001", 0), ("S002", 0), ("S003", 0), ("S004", 4)):
        cloud, pano = _pair(shift, 0, seed=int(sid[1:]))
        Image.fromarray(cloud).save(tmp_path / f"{sid}-cloud.png")
        Image.fromarray(pano).save(tmp_path / f"{sid}-pano.png")
    out = tmp_path / "r.json"
    assert oo.main(["batch", str(tmp_path), "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["passed"] == 3 and res["ok"] is True
    assert res["stations"]["S004"]["ok"] is False
    assert "tileDetails" in res["stations"]["S001"]


def test_cli_pair_exit_code_reflects_ok(tmp_path):
    cloud, pano = _pair(0, 0)
    cloud_path = tmp_path / "S001-cloud.png"
    pano_path = tmp_path / "S001-pano.png"
    Image.fromarray(cloud).save(cloud_path)
    Image.fromarray(pano).save(pano_path)
    out = tmp_path / "r.json"
    assert oo.main(["pair", str(cloud_path), str(pano_path), "--out", str(out)]) == 0
    res = json.loads(out.read_text())
    assert res["ok"] is True

    cloud2, pano2 = _pair(4, 0)
    cloud2_path = tmp_path / "S002-cloud.png"
    pano2_path = tmp_path / "S002-pano.png"
    Image.fromarray(cloud2).save(cloud2_path)
    Image.fromarray(pano2).save(pano2_path)
    assert oo.main(["pair", str(cloud2_path), str(pano2_path)]) == 1


def test_cli_missing_png_gives_clean_error_not_traceback(tmp_path):
    pano_path = tmp_path / "S001-pano.png"
    Image.fromarray(_pair(0, 0)[1]).save(pano_path)
    with pytest.raises(SystemExit) as exc_info:
        oo.main(["pair", str(tmp_path / "missing-cloud.png"), str(pano_path)])
    assert "Traceback" not in str(exc_info.value)
    assert "nicht lesbar" in str(exc_info.value)


def test_cli_corrupt_png_gives_clean_error_not_traceback(tmp_path):
    cloud_path = tmp_path / "S001-cloud.png"
    pano_path = tmp_path / "S001-pano.png"
    cloud_path.write_bytes(b"not a png")
    Image.fromarray(_pair(0, 0)[1]).save(pano_path)
    with pytest.raises(SystemExit) as exc_info:
        oo.main(["pair", str(cloud_path), str(pano_path)])
    assert "Traceback" not in str(exc_info.value)
    assert "nicht lesbar" in str(exc_info.value)


def test_batch_pools_views_of_a_station(tmp_path):
    """Mehrere Blickrichtungen je Standpunkt (``<id>-<n>-cloud.png``)
    werden je Standpunkt zusammengefasst — Kacheln addiert, Median/p90 über alle gültigen
    Kacheln aller Blicke, dazu eine Aufschlüsselung je Blick."""
    # S001: zwei Blicke mit je halber Deckung (je < 12 Kacheln), zusammen ≥ 12.
    for n, seed in ((1, 11), (2, 12)):
        cloud, pano = _pair(0, 0, seed=seed)
        cloud = cloud.copy()
        cloud[:, : cloud.shape[1] // 2] = 17
        Image.fromarray(cloud).save(tmp_path / f"S001-{n}-cloud.png")
        Image.fromarray(pano).save(tmp_path / f"S001-{n}-pano.png")
    # S002: ein Blick im alten Namensschema (ohne Blicknummer) bleibt lesbar.
    cloud, pano = _pair(0, 0, seed=2)
    Image.fromarray(cloud).save(tmp_path / "S002-cloud.png")
    Image.fromarray(pano).save(tmp_path / "S002-pano.png")
    out = tmp_path / "r.json"
    oo.main(["batch", str(tmp_path), "--out", str(out)])
    res = json.loads(out.read_text())
    assert set(res["stations"]) == {"S001", "S002"}
    s1 = res["stations"]["S001"]
    single = [
        oo.measure_offsets(
            np.asarray(Image.open(tmp_path / f"S001-{n}-cloud.png").convert("RGB")),
            np.asarray(Image.open(tmp_path / f"S001-{n}-pano.png").convert("RGB")),
        )
        for n in (1, 2)
    ]
    assert all(r["tiles"] < oo.MIN_TILES for r in single)
    assert s1["tiles"] == sum(r["tiles"] for r in single) >= oo.MIN_TILES
    pooled = [t["px"] for r in single for t in r["tileDetails"] if t["valid"]]
    assert s1["medianPx"] == pytest.approx(float(np.median(pooled)))
    assert s1["p90Px"] == pytest.approx(float(np.percentile(pooled, 90)))
    assert s1["ok"] is True
    assert [v["view"] for v in s1["views"]] == ["1", "2"]
    assert [v["tiles"] for v in s1["views"]] == [r["tiles"] for r in single]
    assert res["stations"]["S002"]["views"][0]["view"] is None
    assert res["stations"]["S002"]["tiles"] >= oo.MIN_TILES


def test_cli_passes_tile_and_min_peak_options(tmp_path):
    """Kachelgröße und minPeak sind CLI-Optionen; Standard bleibt 96 px / 0,3."""
    cloud, pano = _pair(0, 0)
    cloud_path = tmp_path / "S001-cloud.png"
    pano_path = tmp_path / "S001-pano.png"
    Image.fromarray(cloud).save(cloud_path)
    Image.fromarray(pano).save(pano_path)
    out = tmp_path / "r.json"
    oo.main(["pair", str(cloud_path), str(pano_path), "--out", str(out)])
    assert json.loads(out.read_text())["params"]["tile"] == 96
    assert json.loads(out.read_text())["params"]["minPeak"] == 0.3
    oo.main(
        [
            "pair",
            str(cloud_path),
            str(pano_path),
            "--tile",
            "64",
            "--min-peak",
            "0.2",
            "--out",
            str(out),
        ]
    )
    params = json.loads(out.read_text())["params"]
    assert params["tile"] == 64 and params["minPeak"] == 0.2
    oo.main(["batch", str(tmp_path), "--tile", "64", "--min-peak", "0.2", "--out", str(out)])
    params = json.loads(out.read_text())["stations"]["S001"]["params"]
    assert params["tile"] == 64 and params["minPeak"] == 0.2


# Kachelkriterien-Kandidaten, vor dem Einsatz an Echtdaten an Synthetik
# belegt. Vorgabe (96 px / 0,3) läuft als Gegenprobe mit.
CRITERIA = [(96, 0.3), (64, 0.3), (64, 0.2), (96, 0.2)]
CHOSEN = (64, 0.2)  # erster Kandidat, der Synthetik und S005 besteht


@pytest.mark.parametrize(("tile", "min_peak"), CRITERIA)
@pytest.mark.parametrize("shift", [1, 3, 5])
def test_criteria_hit_known_shift(tile, min_peak, shift):
    """(i) Bekannter Versatz 1/3/5 px wird auf ±0,3 px getroffen (x und diagonal)."""
    for dx, dy in ((shift, 0), (shift, -shift)):
        cloud, pano = _pair(dx, dy, seed=3)
        r = oo.measure_offsets(cloud, pano, tile=tile, min_peak=min_peak)
        assert r["tiles"] >= oo.MIN_TILES
        assert abs(r["medianDx"] - dx) <= 0.3
        assert abs(r["medianDy"] - dy) <= 0.3
        assert abs(r["medianPx"] - np.hypot(dx, dy)) <= 0.3


@pytest.mark.parametrize(("tile", "min_peak"), CRITERIA)
@pytest.mark.parametrize(
    ("maker", "shift"),
    [(_periodic_pair, 8), (_periodic_pair, 10), (_hard_tiled_pair, 8), (_hard_tiled_pair, 16)],
)
def test_criteria_reject_periodic_patterns(tile, min_peak, maker, shift):
    """(ii) Periodische Muster der C7/C8-Testfälle werden weiterhin verworfen: kein ``ok``, keine
    gültige Kachel mit kleinem Scheinversatz."""
    cloud, pano = maker(shift, 0)
    r = oo.measure_offsets(cloud, pano, tile=tile, min_peak=min_peak)
    assert r["ok"] is False
    assert r["tiles"] < oo.MIN_TILES
    assert all(not t["valid"] for t in r["tileDetails"])


def _speckled_cloud(cloud: np.ndarray, seed: int, dropout: float = 0.4) -> np.ndarray:
    """Wolkensimulation wie im Echtbild: gesprenkelt — ein Anteil ``dropout`` der Pixel
    zeigt eine fremde Fläche „dahinter“ (andere Textur), dazu Rauschen je Punkt."""
    h, w = cloud.shape[:2]
    rng = np.random.default_rng(seed)
    behind = _texture(h, w, seed + 100)
    gray = cloud[:, :, 0].astype(np.float64)
    mask = rng.random((h, w)) < dropout
    gray = np.where(mask, behind, gray) + rng.normal(0, 6.0, (h, w))
    return _rgb(np.clip(gray, 40, 255))


@pytest.mark.parametrize(("tile", "min_peak"), CRITERIA)
@pytest.mark.parametrize("shift", [0, 2])
def test_criteria_speckled_cloud_gives_no_false_offset(tile, min_peak, shift):
    """(iii) Gesprenkelte, verrauschte Wolke gegen glattes Panorama: keine gültige Kachel mit
    einem falschen Versatz > 2 px (Fehler gegen den wahren Versatz). Für die gewählten
    Kriterien (64 px / 0,2) muss es dabei genug gültige Kacheln geben, sonst bestünde der Test leer
    (beobachtet 35 bis 56 von 63); die übrigen Kandidaten verwerfen hier fast alles (lowPeak)."""
    for seed in (5, 6, 7):
        cloud, pano = _pair(shift, 0, seed=seed)
        r = oo.measure_offsets(_speckled_cloud(cloud, seed), pano, tile=tile, min_peak=min_peak)
        if (tile, min_peak) == CHOSEN:
            assert r["tiles"] >= oo.MIN_TILES
        for t in r["tileDetails"]:
            if t["valid"]:
                assert np.hypot(t["dx"] - shift, t["dy"]) <= 2.0


@pytest.mark.parametrize(("tile", "min_peak"), CRITERIA)
def test_criteria_unrelated_images_do_not_pass(tile, min_peak):
    """(iii) Gefährlichster Fall eines niedrigeren minPeak: Wolke und Panorama zeigen
    Verschiedenes (andere Textur, gesprenkelt) — darf nie ``ok`` werden und keine einzige
    Kachel als gültig liefern (die Eindeutigkeitsprüfung C8 verwirft die zufälligen Spitzen)."""
    for seed in (5, 6, 7):
        cloud, _ = _pair(0, 0, seed=seed)
        _, pano = _pair(0, 0, seed=seed + 50)
        r = oo.measure_offsets(_speckled_cloud(cloud, seed), pano, tile=tile, min_peak=min_peak)
        assert r["ok"] is False
        assert r["tiles"] == 0
