"""Quellen je Eckpunkt: strikt, rückwärtskompatibel und bis zum Git-Export."""

import pytest
from conftest import csrf
from nutzer import person
from test_authz import mit_paketen  # noqa: F401

from scanview_server import inhalte
from scanview_server.web import Ungueltig

SOURCE = {"source": "mesh", "layerId": "modell"}
POINTS = [[0, 0, 0], [1, 0, 0]]


def test_legacy_and_valid():
    old = {"kind": "distance", "points": POINTS}
    assert "pointSources" not in inhalte.messung_pruefen(old)
    new = {**old, "pointSources": [SOURCE, None]}
    assert inhalte.messung_pruefen(new)["pointSources"] == [SOURCE, None]


@pytest.mark.parametrize("sources", [[], [SOURCE], [SOURCE, SOURCE, None], None,
    [{"source": "guess", "layerId": "m"}, None],
    [{**SOURCE, "extra": True}, None], [{"source": "mesh", "layerId": "../m"}, None]])
def test_bad_sources(sources):
    with pytest.raises(Ungueltig):
        inhalte.messung_pruefen({"kind": "distance", "points": POINTS, "pointSources": sources})


def test_api_patch_clear_and_reload(mit_paketen):  # noqa: F811
    _, c = person(mit_paketen, "quellen@scanview.test", grants={"ost": "bearbeiter"})
    basis = "/api/v1/projekte/ost/messungen"
    data = {"kind": "distance", "points": POINTS, "pointSources": [SOURCE, None]}
    r = c.post(basis, headers=csrf(), json={"id": "M-quellen", "daten": data})
    assert r.status_code == 201
    assert c.get(basis).json()["messungen"][0]["daten"]["pointSources"] == [SOURCE, None]
    r = c.patch(basis + "/M-quellen", headers=csrf(),
                json={"version": 1, "daten": {"points": [[0, 0, 0], [2, 0, 0]]}})
    assert r.status_code == 200
    assert "pointSources" not in r.json()["daten"]
    basis = "/api/v1/projekte/ost/pois"
    poi = {"title": "Quelle", "position": [1, 2, 3], "positionSource": SOURCE}
    r = c.post(basis, headers=csrf(), json={"id": "P-quellen", "daten": poi})
    assert r.status_code == 201
    assert c.get(basis).json()["pois"][0]["daten"]["positionSource"] == SOURCE
    r = c.patch(basis + "/P-quellen", headers=csrf(),
                json={"version": 1, "daten": {"position": [2, 2, 3]}})
    assert r.status_code == 200
    assert "positionSource" not in r.json()["daten"]
