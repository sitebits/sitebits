"""K6-Kontrollvertices ignorieren verwaiste Quellpunkte."""

import numpy as np
from spike.source_mesh import SourceMesh
from spike.tiles_contract import _control_vertices


def test_kontrollvertices_nur_aus_dreiecken() -> None:
    flaeche = np.array([[0, 0, 0], [1, 0, 0], [0, 1, 0], [1, 1, 1]], dtype=np.float64)
    verwaist = np.array([[50, -50, 9]], dtype=np.float64)
    source = SourceMesh(
        vertices=np.vstack([flaeche, verwaist]),
        faces=np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64),
        uv=None,
        texture=None,
    )
    kontrolle = _control_vertices(source)
    assert not np.any(np.all(kontrolle == verwaist[0], axis=1))
    assert {tuple(p) for p in kontrolle} <= {tuple(p) for p in flaeche}
