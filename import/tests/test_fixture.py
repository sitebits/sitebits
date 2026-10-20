"""Tests für die synthetischen E57-Fixtures (zwei Scans, Würfel mit sechs Bildern)."""

from __future__ import annotations

import numpy as np
import pye57
from e57_fixture import FACE_COLORS, FACE_ROT

from scanview_import.geometry import cube_face_for


def test_two_scan_fixture_readable(two_scan_e57):
    e = pye57.E57(str(two_scan_e57.path))
    assert e.scan_count == 2
    assert e.get_header(1).point_count == 1000
    assert two_scan_e57.invalid_count == 14


def test_two_scan_fixture_integer_fields_round_trip_on_disk(two_scan_e57):
    # Regressionstest für einen SourceDestBuffer-Stride-Fehler in pye57 0.4.19:
    # stride=0 (Default) nimmt für IntegerNode-Puffer eine feste 4-Byte-
    # Elementbreite an. Bei int64-Puffern (8 Byte) betrifft das sowohl Schreiben
    # als auch Lesen (siehe Bericht) — ohne das stride=v.itemsize-Fix in
    # e57_fixture.write_fixture wird die Datei bereits beim Schreiben mit
    # Wert/Null-Paaren statt echter Werte befüllt, lautlos, ohne Fehler oder
    # Warnung. Dieser Test liest tatsächlich von der Platte und würde bei einer
    # Regression fehlschlagen (im Gegensatz zu FixtureInfo.invalid_count, das nur
    # das Python-seitige Eingabe-Array zählt, nie die E57-Datei selbst).
    e = pye57.E57(str(two_scan_e57.path))
    raw = e.read_scan_raw(0, ignore_unsupported_fields=True)
    e.close()
    assert int((raw["cartesianInvalidState"] == 2).sum()) == 7
    expected_rgb = np.arange(1000) % 256
    np.testing.assert_array_equal(raw["colorRed"], expected_rgb)
    np.testing.assert_array_equal(raw["colorGreen"], expected_rgb)
    np.testing.assert_array_equal(raw["colorBlue"], expected_rgb)


def test_cube_fixture_has_six_images(cube_e57):
    e = pye57.E57(str(cube_e57.path))
    root = e.image_file.root()
    assert root["images2D"].childCount() == 6


def test_face_rot_is_consistent_with_cube_face_for():
    # FACE_ROT bildet -Z des Bild-KS auf die jeweilige Würfelseite ab.
    # Kreuzprobe gegen cube_face_for (scanview_import.geometry), damit Fixture und
    # Geometrie-Konvention nie wieder auseinanderlaufen (Preflight-Konflikt P1).
    for face, rot in FACE_ROT.items():
        assert cube_face_for(rot) == face


def test_face_colors_cover_all_six_faces():
    assert set(FACE_COLORS) == set(FACE_ROT)
