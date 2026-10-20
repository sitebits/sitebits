"""Echte libE57-Dateien: Genauigkeit, lokale Grenze und alle Lesepfade."""

import numpy as np
import pytest
from e57_fixture import write_fixture

from scanview_import.inventory import inventory_to_json, read_inventory
from scanview_import.model import Pose


@pytest.mark.parametrize("precision", ["single", "double", "scaled"])
def test_inventory_precision(tmp_path, precision):
    path = tmp_path / "test.e57"
    write_fixture(path, [(Pose((0., 0., 0.), (1., 0., 0., 0.)),
                         np.array([[100., -10., 2.]]), np.zeros(1, dtype=np.int64))],
                  coord_precision=precision, bounds=True)
    scan = read_inventory(path).scans[0]
    assert scan.coord_precision == precision
    assert scan.local_abs_max_m == 100.
    assert inventory_to_json(read_inventory(path))["scans"][0]["coordPrecision"] == precision


