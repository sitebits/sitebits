"""Tests für das reine Metadaten-Inventar (keine Punkte)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from scanview_import.inventory import inventory_to_json, read_inventory

# Echte Referenz-E57 der Pilotkirche (45 Scans, 270 Bilder), nur lokal vorhanden: Pfad per Umgebung.
_REFERENZ_E57 = os.environ.get("SCANVIEW_REFERENZ_E57")
REFERENZ_E57 = Path(_REFERENZ_E57) if _REFERENZ_E57 else None


def test_scans_with_pose_and_fields(two_scan_e57):
    inv = read_inventory(two_scan_e57.path)
    assert [s.guid for s in inv.scans] == two_scan_e57.scan_guids
    assert inv.scans[1].pose.translation == (1010.0, 2000.0, 10.0)
    assert "cartesianInvalidState" in inv.scans[0].fields
    assert inv.crs_wkt is None
    assert inv.images == []


def test_color_max_captures_declared_8bit_range(two_scan_e57):
    # Fixture deklariert colorRed als IntegerNode(0, 0, 255) — 8-bit.
    inv = read_inventory(two_scan_e57.path)
    assert inv.scans[0].color_max == 255
    assert inv.scans[1].color_max == 255
    # two_scan_e57 hat keine colorLimits-Struktur (wie Pilothalle) — muss None bleiben.
    assert inv.scans[0].color_limits_max is None
    assert inv.scans[1].color_limits_max is None


def test_color_limits_max_captures_declared_range(two_scan_e57_color_limits):
    # colorLimits-Struktur (wie an der Pilotkirche real vorhanden) muss
    # separat vom Prototyp-Feld erkannt werden.
    inv = read_inventory(two_scan_e57_color_limits.path)
    assert inv.scans[0].color_limits_max == 255
    assert inv.scans[1].color_limits_max == 255
    assert inv.scans[0].color_max == 255  # Prototyp bleibt weiterhin 8-bit deklariert


def test_images_kind_scan_and_blob(cube_e57):
    inv = read_inventory(cube_e57.path)
    assert len(inv.images) == 6
    assert {i.kind for i in inv.images} == {"pinhole"}
    assert {i.scan_guid for i in inv.images} == {cube_e57.scan_guids[0]}
    assert inv.images[0].blob_node == "pinholeRepresentation/jpegImage"
    assert inv.images[0].width == 64


def test_json_roundtrip_keys(cube_e57):
    d = inventory_to_json(read_inventory(cube_e57.path))
    assert d["images"][0]["kind"] == "pinhole"
    assert d["scans"][0]["pose"]["rotationWxyz"] == [1.0, 0.0, 0.0, 0.0]


@pytest.mark.data
@pytest.mark.skipif(
    REFERENZ_E57 is None or not REFERENZ_E57.exists(),
    reason="Pilotdaten fehlen (SCANVIEW_REFERENZ_E57)",
)
def test_referenz_inventory():
    assert REFERENZ_E57 is not None
    inv = read_inventory(REFERENZ_E57)
    assert len(inv.scans) == 45
    assert len(inv.images) == 270
    per_scan: dict[str | None, int] = {}
    for im in inv.images:
        per_scan[im.scan_guid] = per_scan.get(im.scan_guid, 0) + 1
    assert set(per_scan.values()) == {6}
