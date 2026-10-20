"""Echte libE57-Dateien: Genauigkeit, lokale Grenze und alle Lesepfade."""

import numpy as np
import pytest
from e57_fixture import write_fixture

from scanview_import.convert import convert, world_bounds
from scanview_import.inventory import read_inventory
from scanview_import.model import Pose
from scanview_import.source_points import e57_chunks


@pytest.mark.parametrize("bounds", [True, False])
@pytest.mark.parametrize("value", [16384., -5806700.])
@pytest.mark.parametrize("reader", ["bounds", "chunks", "convert"])
def test_single_rejected_in_every_reader(tmp_path, bounds, value, reader):
    path = tmp_path / "test.e57"
    write_fixture(path, [(Pose((0., 0., 0.), (1., 0., 0., 0.)),
                         np.array([[value, 0., 0.]]), np.zeros(1, dtype=np.int64))],
                  coord_precision="single", bounds=bounds)
    with pytest.raises(ValueError, match="double_precision=true"):
        if reader == "bounds":
            world_bounds(path, read_inventory(path))
        elif reader == "chunks":
            list(e57_chunks(path))
        else:
            convert(path, tmp_path / "out", None)
    assert not (tmp_path / "out" / "inventory.json").exists()


@pytest.mark.parametrize("precision,value,translation", [
    ("single", 100., 5806700.), ("double", 5806700., 0.),
    ("scaled", 5806700., 0.), ("single", 16383., 0.),
])
def test_safe_coordinates_remain_allowed(tmp_path, precision, value, translation):
    path = tmp_path / "test.e57"
    write_fixture(path, [(Pose((translation, 0., 0.), (1., 0., 0., 0.)),
                         np.array([[value, 0., 0.]]), np.zeros(1, dtype=np.int64))],
                  coord_precision=precision)
    lo, _ = world_bounds(path, read_inventory(path))
    assert lo[0] == pytest.approx(value + translation)
    assert next(e57_chunks(path))[0][0, 0] == pytest.approx(value + translation)


def test_explicit_override_reports_actual_step(tmp_path):
    from scanview_import.inventory import pruefe_e57_genauigkeit

    path = tmp_path / "test.e57"
    write_fixture(path, [(Pose((0., 0., 0.), (1., 0., 0., 0.)),
                         np.array([[5806700., 0., 0.]]), np.zeros(1, dtype=np.int64))],
                  coord_precision="single")
    warning = pruefe_e57_genauigkeit(read_inventory(path), True)
    assert warning["float32Schritt_m"] == 0.5
    assert warning["warnings"]
    assert world_bounds(path, read_inventory(path), float32_zulassen=True)[0][0] == 5806700.
    assert next(e57_chunks(path, float32_zulassen=True))[0][0, 0] == 5806700.


@pytest.mark.docker
def test_override_convert_and_verify_report(tmp_path):
    from scanview_import.verify import verify

    path = tmp_path / "test.e57"
    xyz = np.array([[5806700., 0., 0.], [5806701., 1., 1.]])
    write_fixture(path, [(Pose((0., 0., 0.), (1., 0., 0., 0.)), xyz,
                         np.zeros(2, dtype=np.int64))], coord_precision="single")
    out = tmp_path / "out"
    manifest = convert(path, out, None, float32_zulassen=True)
    assert manifest["conversion"]["float32Schritt_m"] == 0.5
    report = verify(path, out, float32_zulassen=True)
    assert report["float32Schritt_m"] == 0.5
    assert report["warnings"]
    with pytest.raises(ValueError, match="Float32"):
        verify(path, out)
