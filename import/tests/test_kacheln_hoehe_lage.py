"""Kachler rechnet Höhe und Lage selbst: dieselbe Kette wie ``add`` (Höhe, dann Lagematrix)."""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from scanview_import import hoehen, lage
from scanview_import.cli import main
from scanview_import.kacheln import HUELLE_MAX_M, kacheln
from scanview_import.kacheln.paket import _rahmen
from scanview_import.manifest_io import read_manifest
from scanview_import.mesh_convert import _mesh_hoehe
from scanview_import.mesh_source import read_mesh

CRS = "EPSG:25833"


def _obj(path: Path, z: float = 100.0) -> Path:
    """Zwei Dreiecke in UTM 33, Höhen ellipsoidisch (rund 40 m über NHN)."""
    path.write_text(
        f"v 368000.125 5807000.250 {z}\n"
        f"v 368002.500 5807000.250 {z + 0.5}\n"
        f"v 368002.500 5807003.000 {z + 1.25}\n"
        f"v 368000.125 5807003.000 {z + 0.75}\n"
        "f 1 2 3\nf 1 3 4\n"
    )
    return path


def _matrix() -> np.ndarray:
    """Starre Bewegung: 0,4° um die Hochachse am Drehpunkt, dazu Versatz in x, y und z."""
    winkel = math.radians(0.4)
    c, s = math.cos(winkel), math.sin(winkel)
    r = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])
    drehpunkt = np.array([368001.0, 5807001.5, 60.0])
    m = np.eye(4)
    m[:3, :3] = r
    m[:3, 3] = drehpunkt - r @ drehpunkt + [0.62, -0.81, 0.07]
    return m


def _soll(obj: Path, matrix: np.ndarray | None) -> np.ndarray:
    """Direkte Kette wie ``add_mesh``: ``positions_hoehe`` und danach ``lage.wende_an``."""
    mesh = _mesh_hoehe(read_mesh(obj, crs=CRS, unit="m", up="z"), hoehen.ELLIPSOIDISCH,
                       hoehen.DHHN2016)
    return mesh.positions if matrix is None else lage.wende_an(mesh.positions, matrix)


def _paket(
    root: Path, *, matrix: np.ndarray | None = None, datum: str = "DHHN2016",
    bounds: list[list[float]] | None = None,
) -> Path:
    """Mini-Paket mit Wolkenebene (Lagematrix, boundsWorld) und frame.verticalDatum."""
    pkg = root / "paket"
    pkg.mkdir(parents=True)
    cloud: dict = {
        "id": "punkte",
        "kind": "pointcloud",
        "boundsWorld": bounds or [[367990.0, 5806990.0, 45.0], [368010.0, 5807010.0, 70.0]],
        "sourceOptions": {"unit": "m"},
    }
    if matrix is not None:
        cloud["sourceOptions"]["lage"] = {
            "art": "transformation", "matrix": matrix.tolist(), "einheit": "m",
            "drehpunkt": [368001.0, 5807001.5, 60.0], "datei": "ausrichtung.json",
        }
    manifest = {
        "schemaVersion": 2,
        "frame": {"crs": {"wkt": CRS, "verticalDatum": datum}, "originWorld": [368000, 5807000, 0],
                  "unit": "m"},
        "layers": [cloud],
    }
    (pkg / "project.json").write_text(json.dumps(manifest))
    return pkg


def _huelle(tileset: Path) -> np.ndarray:
    box = np.asarray(json.loads(tileset.read_text())["root"]["boundingVolume"]["box"])
    extent = np.abs(box[3:].reshape(3, 3)).sum(axis=0)
    return np.array([box[:3] - extent, box[:3] + extent])


def _args(obj: Path, out: Path, *extra: str) -> list[str]:
    return ["kacheln", str(obj), str(out), "--jobs", "1", "--crs", CRS, *extra]


@pytest.mark.parametrize("jobs", ["1", "2"])
def test_lage_von_gleich_der_direkten_kette_und_rahmen_aus_dem_paket(
    tmp_path: Path, jobs: str
) -> None:
    """(a) Hülle mm-gleich zur direkten Kette; (b) heightSystem = verticalDatum, _rahmen besteht.

    Auch mit Prozesspool: Arbeiter lesen nur die Prüffassung, nie das unumgerechnete OBJ.
    """
    matrix = _matrix()
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=matrix)
    out = tmp_path / "aus"
    args = _args(obj, out, "--packen", "--quell-hoehensystem", "ellipsoidisch",
                 "--lage-von", str(pkg), "--jobs", jobs)
    assert main(args) == 0
    soll = _soll(obj, matrix)
    erwartet = np.array([soll.min(axis=0), soll.max(axis=0)])
    # Die Umrechnung fand tatsächlich statt (Quasigeoid rund 40 m), sonst wäre der Vergleich leer.
    roh = read_mesh(obj, crs=CRS, unit="m", up="z").positions
    assert 35 < float(roh[:, 2].min() - soll[:, 2].min()) < 45
    np.testing.assert_allclose(_huelle(out / "prueffassung/tileset.json"), erwartet, atol=1e-3)

    m = read_manifest(out / "paket")
    layer = m["layers"][0]
    np.testing.assert_allclose(np.asarray(layer["boundsWorld"]), erwartet, atol=1e-3)
    datum = read_manifest(pkg)["frame"]["crs"]["verticalDatum"]
    assert layer["heightSystem"] == datum == "DHHN2016"
    assert m["frame"]["crs"]["verticalDatum"] == datum
    _rahmen(m, layer)  # wirft bei Widerspruch zwischen Ebene und Rahmen
    ts = json.loads((out / "paket/layers/kacheln/tileset.json").read_text())
    assert ts["extras"]["heightSystem"] == datum
    assert layer["sourceOptions"]["hoehe"] == hoehen.quellblock(hoehen.ELLIPSOIDISCH,
                                                               hoehen.DHHN2016)
    assert layer["sourceOptions"]["lage"]["matrix"] == matrix.tolist()
    assert main(["verify", str(obj), str(out / "paket")]) == 0


def test_ohne_paket_rechnet_wie_convert_ins_standardziel(tmp_path: Path) -> None:
    obj = _obj(tmp_path / "netz.obj")
    out = tmp_path / "aus"
    report = kacheln(obj, out, crs=CRS, umrechnen=("ellipsoidisch", None), jobs=1)
    soll = _soll(obj, None)
    np.testing.assert_allclose(
        _huelle(out / "tileset.json"), [soll.min(axis=0), soll.max(axis=0)], atol=1e-3
    )
    assert json.loads((out / "tileset.json").read_text())["extras"]["heightSystem"] == "dhhn2016"
    assert report["sourceOptions"] == {
        "hoehe": hoehen.quellblock(hoehen.ELLIPSOIDISCH, hoehen.DHHN2016)
    }


def test_mesh_40_m_ueber_der_wolke_bricht_ohne_ausgabe_ab(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(c) Ellipsoidische Höhen als NHN ausgegeben: Hüllentor rot, nichts geschrieben."""
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=_matrix())
    out = tmp_path / "aus"
    args = _args(obj, out, "--packen", "--quell-hoehensystem", "dhhn2016", "--lage-von", str(pkg))
    assert main(args) == 1
    assert "Hüllentor" in capsys.readouterr().err
    assert not out.exists()
    assert not list(tmp_path.rglob("tileset.json"))


@pytest.mark.parametrize(("abstand", "rc"), [(HUELLE_MAX_M + 1, 1), (HUELLE_MAX_M - 1, 0)])
def test_huellentor_mitte_ausserhalb(tmp_path: Path, abstand: float, rc: int) -> None:
    """z überlappt, die Mesh-Mitte liegt neben der Wolke: rot über 10 m, grün darunter."""
    matrix = _matrix()
    obj = _obj(tmp_path / "netz.obj")
    soll = _soll(obj, matrix)
    mitte = (soll.min(axis=0) + soll.max(axis=0)) / 2
    rand = float(mitte[0] - abstand)
    bounds = [[rand - 20, mitte[1] - 10, 40.0], [rand, mitte[1] + 10, 70.0]]
    pkg = _paket(tmp_path, matrix=matrix, bounds=bounds)
    out = tmp_path / "aus"
    args = _args(obj, out, "--quell-hoehensystem", "ellipsoidisch", "--lage-von", str(pkg))
    assert main(args) == rc
    assert (out / "tileset.json").exists() is (rc == 0)


def test_hoehe_und_quellhoehensystem_zusammen_ist_ein_fehler(tmp_path: Path) -> None:
    """(d)"""
    obj = _obj(tmp_path / "netz.obj")
    out = tmp_path / "aus"
    args = _args(obj, out, "--hoehe", "DHHN2016", "--quell-hoehensystem", "ellipsoidisch")
    assert main(args) == 1
    assert not out.exists()


def test_zielhoehensystem_gegen_das_paket(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """(e) Mit --lage-von bestimmt das Paket das Ziel (Entscheidung 0015)."""
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=_matrix())
    out = tmp_path / "aus"
    args = _args(obj, out, "--quell-hoehensystem", "dhhn2016", "--ziel-hoehensystem",
                 "ellipsoidisch", "--lage-von", str(pkg))
    assert main(args) == 1
    assert "widerspricht dem Paket" in capsys.readouterr().err
    assert not out.exists()


def test_dhhn92_in_dhhn2016_paket_wie_add(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Gleiche Ablehnung wie ``add_mesh`` (eine DHHN92-Umrechnung gibt es nicht)."""
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=_matrix())
    out = tmp_path / "aus"
    args = _args(obj, out, "--quell-hoehensystem", "dhhn92", "--lage-von", str(pkg))
    assert main(args) == 1
    err = capsys.readouterr().err
    assert "Dieses Paket steht auf dhhn2016" in err
    assert "nur in ein DHHN92-Paket" in err
    assert not out.exists()


def test_fehlende_lagematrix_nur_mit_ohne_lage(tmp_path: Path) -> None:
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path)
    basis = _args(obj, tmp_path / "a", "--quell-hoehensystem", "ellipsoidisch", "--lage-von",
                  str(pkg))
    assert main(basis) == 1
    assert not (tmp_path / "a").exists()
    assert main([*basis[:2], str(tmp_path / "b"), *basis[3:], "--ohne-lage"]) == 0
    soll = _soll(obj, None)
    np.testing.assert_allclose(
        _huelle(tmp_path / "b/tileset.json"), [soll.min(axis=0), soll.max(axis=0)], atol=1e-3
    )


def test_ohne_lage_trotz_lagematrix_ist_ein_fehler(tmp_path: Path) -> None:
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=_matrix())
    args = _args(obj, tmp_path / "aus", "--quell-hoehensystem", "ellipsoidisch", "--lage-von",
                 str(pkg), "--ohne-lage")
    assert main(args) == 1
    assert main(["kacheln", str(obj), str(tmp_path / "aus"), "--ohne-lage"]) == 1


@pytest.mark.parametrize(
    ("extra", "meldung"),
    [
        (("--hoehe", "ellipsoidisch"), "--hoehe widerspricht dem Paket"),
        ((), "benötigt --quell-hoehensystem oder --hoehe"),
    ],
)
def test_etikett_gegen_das_paket(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], meldung: str
) -> None:
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=_matrix())
    assert main(_args(obj, tmp_path / "aus", *extra, "--lage-von", str(pkg))) == 1
    assert meldung in capsys.readouterr().err


def test_lage_von_verlangt_dasselbe_crs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    obj = _obj(tmp_path / "netz.obj")
    pkg = _paket(tmp_path, matrix=_matrix())
    args = ["kacheln", str(obj), str(tmp_path / "aus"), "--crs", "EPSG:25832",
            "--quell-hoehensystem", "ellipsoidisch", "--lage-von", str(pkg)]
    assert main(args) == 1
    assert "Paketrahmen" in capsys.readouterr().err


def test_packen_verlangt_hoehenangabe(tmp_path: Path) -> None:
    obj = _obj(tmp_path / "netz.obj")
    with pytest.raises(ValueError, match="--hoehe oder --quell-hoehensystem"):
        kacheln(obj, tmp_path / "aus", crs=CRS, packen=True)
    assert not (tmp_path / "aus").exists()


def test_bestandsaufruf_nur_mit_etikett_bleibt_unveraendert(tmp_path: Path) -> None:
    """(f) Nur --hoehe: keine Umrechnung, Etikett wörtlich, keine sourceOptions."""
    obj = _obj(tmp_path / "netz.obj")
    out = tmp_path / "aus"
    report = kacheln(obj, out, crs=CRS, hoehe="DHHN2016", jobs=1)
    roh = read_mesh(obj, crs=CRS, unit="m", up="z").positions
    np.testing.assert_allclose(
        _huelle(out / "tileset.json"), [roh.min(axis=0), roh.max(axis=0)], atol=1e-3
    )
    extras = json.loads((out / "tileset.json").read_text())["extras"]
    assert extras == {"coordinateReferenceSystem": CRS, "heightSystem": "DHHN2016", "axis": "Z",
                      "unit": "m"}
    assert report["sourceOptions"] == {}


def test_unbekanntes_etikett_passt_nie(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    obj = _obj(tmp_path / "netz.obj", z=60.0)
    pkg = _paket(tmp_path, matrix=_matrix())
    assert main(_args(obj, tmp_path / "aus", "--hoehe", "NN", "--lage-von", str(pkg))) == 1
    assert "--hoehe widerspricht dem Paket" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("aendern", "meldung"),
    [
        (lambda m: m["frame"]["crs"].update(verticalDatum="NN"), "Paket-Höhensystem unbekannt"),
        (lambda m: m["frame"]["crs"].update(verticalDatum=None), "Paket-Höhensystem unbekannt"),
        (lambda m: m["frame"]["crs"].pop("wkt"), "ohne Koordinatensystem"),
        (lambda m: m["frame"].update(crs=None), "ohne Koordinatensystem"),
        (lambda m: m.update(frame=None), "ohne Rahmen"),
        (lambda m: m["layers"][0].update(sourceOptions=None), "ungültigen sourceOptions"),
        (lambda m: m["frame"].update(unit="ft"), "metrisch"),
        (lambda m: m["layers"][0]["sourceOptions"].update(unit="mm"), "metrisch"),
        (lambda m: m["layers"][0].pop("boundsWorld"), "boundsWorld"),
        (lambda m: m.update(layers=[]), "Wolke"),
    ],
)
def test_unvollstaendiges_paket_bricht_vor_dem_lesen_ab(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], aendern, meldung: str
) -> None:
    obj = _obj(tmp_path / "netz.obj", z=60.0)
    pkg = _paket(tmp_path, matrix=_matrix())
    m = json.loads((pkg / "project.json").read_text())
    aendern(m)
    (pkg / "project.json").write_text(json.dumps(m))
    for extra in (("--hoehe", "DHHN2016"), ("--quell-hoehensystem", "ellipsoidisch")):
        out = tmp_path / "aus"
        assert main(_args(obj, out, *extra, "--lage-von", str(pkg))) == 1
        assert meldung in capsys.readouterr().err
        assert not out.exists()


@pytest.mark.parametrize(("abstand", "rc"), [(HUELLE_MAX_M + 1, 1), (HUELLE_MAX_M - 1, 0)])
def test_huellentor_mitte_ueber_der_wolke(tmp_path: Path, abstand: float, rc: int) -> None:
    """z überlappt, die Mesh-Mitte liegt in z über der Wolke: rot über 10 m, grün darunter.

    Fängt ein falsches Höhensystem auch dann, wenn die Wolke so hoch reicht, dass sich die
    z-Bereiche noch überlappen (etwa Mesh 40 m zu hoch über einer Wolke bis Dachhöhe).
    """
    matrix = _matrix()
    obj = tmp_path / "turm.obj"
    obj.write_text(
        "v 368000 5807000 98\nv 368002 5807000 98\nv 368001 5807002 98\nv 368001 5807001 160\n"
        "f 1 2 4\nf 2 3 4\n"
    )
    soll = _soll(obj, matrix)
    mitte = (soll.min(axis=0) + soll.max(axis=0)) / 2
    oben = float(mitte[2] - abstand)
    assert oben > soll[:, 2].min()  # z-Bereiche überlappen, nur die Mitte entscheidet
    bounds = [[mitte[0] - 10, mitte[1] - 10, oben - 30], [mitte[0] + 10, mitte[1] + 10, oben]]
    pkg = _paket(tmp_path, matrix=matrix, bounds=bounds)
    out = tmp_path / "aus"
    args = _args(obj, out, "--quell-hoehensystem", "ellipsoidisch", "--lage-von", str(pkg))
    assert main(args) == rc
    assert (out / "tileset.json").exists() is (rc == 0)


def _obj_aus(path: Path, positions: np.ndarray, triangles: np.ndarray) -> Path:
    zeilen = [f"v {x:.4f} {y:.4f} {z:.4f}" for x, y, z in positions]
    zeilen += [f"f {a + 1} {b + 1} {c + 1}" for a, b, c in triangles]
    path.write_text("\n".join(zeilen) + "\n")
    return path


def test_etikett_mit_lagematrix_wirkt_nie_doppelt(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Schon umgerechnetes OBJ (Höhe und Lage vorab angewandt) mit --hoehe --lage-von: Abbruch.

    Früher wirkte die Lagematrix still ein zweites Mal (rc 0, rund 1 m Versatz, Hüllentor blind).
    Die beiden gemeinten Wege bleiben offen und treffen die einfache Kette mm-genau.
    """
    matrix = _matrix()
    roh = _obj(tmp_path / "roh.obj")
    pkg = _paket(tmp_path, matrix=matrix)
    soll = _soll(roh, matrix)
    erwartet = np.array([soll.min(axis=0), soll.max(axis=0)])
    fertig = _obj_aus(tmp_path / "fertig.obj", soll,
                      read_mesh(roh, crs=CRS, unit="m", up="z").triangles)

    out = tmp_path / "doppelt"
    assert main(_args(fertig, out, "--packen", "--hoehe", "DHHN2016",
                      "--lage-von", str(pkg))) == 1
    assert "ob sie schon angewandt ist" in capsys.readouterr().err
    assert not out.exists()

    # Höhe und Lage schon angewandt: ohne --lage-von, das Etikett allein
    out = tmp_path / "etikett"
    assert main(_args(fertig, out, "--hoehe", "DHHN2016")) == 0
    np.testing.assert_allclose(_huelle(out / "tileset.json"), erwartet, atol=1e-3)

    # Nur die Höhe schon umgerechnet: Quelle gleich dem Paket, die Lage wirkt genau einmal
    nur_hoehe = _obj_aus(tmp_path / "nur-hoehe.obj", _soll(roh, None),
                         read_mesh(roh, crs=CRS, unit="m", up="z").triangles)
    out = tmp_path / "einmal"
    assert main(_args(nur_hoehe, out, "--quell-hoehensystem", "dhhn2016",
                      "--lage-von", str(pkg))) == 0
    np.testing.assert_allclose(_huelle(out / "tileset.json"), erwartet, atol=1e-3)


def test_etikett_mit_paket_ohne_lagematrix_bleibt_zulaessig(tmp_path: Path) -> None:
    """Ohne Lagematrix kann nichts doppelt wirken: --hoehe --lage-von --ohne-lage besteht."""
    roh = _obj(tmp_path / "roh.obj")
    soll = _soll(roh, None)
    fertig = _obj_aus(tmp_path / "fertig.obj", soll,
                      read_mesh(roh, crs=CRS, unit="m", up="z").triangles)
    pkg = _paket(tmp_path)
    out = tmp_path / "aus"
    assert main(_args(fertig, out, "--hoehe", "DHHN2016", "--lage-von", str(pkg),
                      "--ohne-lage")) == 0
    np.testing.assert_allclose(_huelle(out / "tileset.json"),
                               [soll.min(axis=0), soll.max(axis=0)], atol=1e-3)


def _gitter_obj(path: Path, z0: float) -> Path:
    """Asymmetrisches Netz 20 x 12 m mit Relief, damit frameCheck von add_mesh greift."""
    zeilen = []
    nx, ny = 11, 7
    for j in range(ny):
        for i in range(nx):
            x = 368000.0 + 2.0 * i + 0.37 * j
            y = 5807000.0 + 2.0 * j
            z = z0 + 0.3 * i + 0.11 * j * j + (1.7 if (i, j) == (3, 2) else 0.0)
            zeilen.append(f"v {x:.4f} {y:.4f} {z:.4f}")
    for j in range(ny - 1):
        for i in range(nx - 1):
            a = j * nx + i + 1
            zeilen += [f"f {a} {a + 1} {a + nx + 1}", f"f {a} {a + nx + 1} {a + nx}"]
    path.write_text("\n".join(zeilen) + "\n")
    return path


def _las_paket(root: Path, src: Path, quelle: str | None, datum: str, matrix: np.ndarray) -> Path:
    """Paket mit echter LAS-Wolke auf der richtig umgerechneten und bewegten Fläche."""
    import laspy

    from scanview_import.mesh_surface import SourceMesh

    mesh = read_mesh(src, crs=CRS, unit="m", up="z")
    pos = mesh.positions
    if quelle is not None:
        pos = _mesh_hoehe(mesh, quelle, hoehen.normiere(datum)).positions
    pos = lage.wende_an(pos, matrix)
    pts = SourceMesh(pos, mesh.triangles, None, None).sample_surface(3000, 7)[0]
    root.mkdir(parents=True)
    h = laspy.LasHeader(point_format=7, version="1.4")
    h.scales = [0.0001] * 3
    h.offsets = [368000, 5807000, 0]
    las = laspy.LasData(h)
    las.x, las.y, las.z = pts.T
    las.write(root / "cloud.las")
    layer = {
        "id": "punkte", "kind": "pointcloud", "url": "cloud.las",
        "boundsWorld": [pts.min(axis=0).tolist(), pts.max(axis=0).tolist()],
        "sourceOptions": {"unit": "m", "lage": {
            "art": "transformation", "matrix": matrix.tolist(), "einheit": "m",
            "drehpunkt": [368010.0, 5807006.0, 60.0],
        }},
    }
    manifest = {
        "schemaVersion": 2, "revision": 1, "projectId": "gegenprobe",
        "frame": {"unit": "m", "crs": {"wkt": CRS, "verticalDatum": datum},
                  "originWorld": [368000.0, 5807000.0, 0.0]},
        "layers": [layer],
    }
    (root / "project.json").write_text(json.dumps(manifest))
    return root


@pytest.mark.parametrize(
    ("quelle_cli", "quelle", "datum", "z0"),
    [
        ("ellipsoidisch", hoehen.ELLIPSOIDISCH, "DHHN2016", 100.0),  # echte Umrechnung, rund 40 m
        ("dhhn92", None, "dhhn92", 60.0),  # DHHN92 nur ins DHHN92-Paket: Identität
    ],
)
def test_gleich_add_mesh_auf_demselben_obj_und_paket(
    tmp_path: Path, quelle_cli: str, quelle: str | None, datum: str, z0: float
) -> None:
    """Gegenprobe mit add_mesh selbst: Hülle ±1 mm, sourceOptions.hoehe/.lage wörtlich gleich.

    Fällt auf, sobald add und kacheln auseinanderlaufen (Reihenfolge, Gitter, Matrix).
    """
    from scanview_import.mesh_convert import add_mesh

    src = _gitter_obj(tmp_path / "netz.obj", z0)
    matrix = _matrix()
    pa = _las_paket(tmp_path / "paket-add", src, quelle, datum, matrix)
    pk = _las_paket(tmp_path / "paket-kacheln", src, quelle, datum, matrix)
    after = add_mesh(src, pa, same_frame=True, crs=CRS, unit="m", dreiecke=0,
                     hoehe=(quelle_cli, None))
    ml = after["layers"][1]
    assert ml["frameCheck"]["ok"] is True
    origin = np.asarray(after["frame"]["originWorld"], dtype=np.float64)
    add_pos = read_mesh(pa / ml["url"]).positions + origin

    out = tmp_path / "aus"
    assert main(_args(src, out, "--packen", "--quell-hoehensystem", quelle_cli,
                      "--lage-von", str(pk))) == 0
    kl = read_manifest(out / "paket")["layers"][0]
    np.testing.assert_allclose(np.asarray(kl["boundsWorld"]),
                               [add_pos.min(axis=0), add_pos.max(axis=0)], atol=1e-3)
    assert kl["sourceOptions"]["hoehe"] == ml["sourceOptions"]["hoehe"]
    assert kl["sourceOptions"]["lage"] == ml["sourceOptions"]["lage"]
    assert kl["heightSystem"] == after["frame"]["crs"]["verticalDatum"]
