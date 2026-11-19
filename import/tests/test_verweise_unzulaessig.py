"""Unzulässige Verweise im Paket: jeder Verify-Pfad lehnt ab, bevor er eine Datei öffnet.

Muster: absolut, ``..``, kodiert (Prozentkodierung, Backslash, NUL, Unicode-Normalisierung). Je
Verify-Pfad (``verify``, ``verify_points``, ``verify_mesh``) mindestens ein Fall je Muster, dazu
Positivfälle. Die Pakete sind synthetisch; die Prüfung greift vor dem Lesen der Daten, deshalb
genügt für die Ablehnung ein ``project.json``."""

import json
import struct
from pathlib import Path

import pytest

from scanview_import.mesh_verify import verify_mesh
from scanview_import.point_verify import verify_points
from scanview_import.verify import verify

ABGELEHNT = "Verweis unzulässig"

#: (Muster, Verweis) — jedes Muster mindestens einmal.
MUSTER = [
    ("absolut", "/etc/passwd"),
    ("absolut-url", "file:///etc/passwd"),
    ("absolut-laufwerk", "C:/daten/cloud.copc.laz"),
    ("punktpunkt", "../anderes-paket/cloud.copc.laz"),
    ("punktpunkt-mitte", "layers/../../cloud.copc.laz"),
    ("kodiert-prozent-punkt", "%2e%2e/cloud.copc.laz"),
    ("kodiert-prozent-slash", "..%2fcloud.copc.laz"),
    ("kodiert-backslash", "..\\cloud.copc.laz"),
    ("kodiert-nul", "cloud.copc.laz\x00.png"),
    ("kodiert-unicode-vollbreite", "\uff0e\uff0e\uff0fcloud.copc.laz"),
    ("kodiert-unicode-zerlegt", "layers/mesh/mode\u0308l.glb"),
]
IDS = [m[0] for m in MUSTER]


def _paket(tmp_path: Path, manifest: dict) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir(exist_ok=True)
    (pkg / "project.json").write_text(json.dumps(manifest), encoding="utf-8")
    return pkg


def _wolke(url: str, **zusatz) -> dict:
    return {"schemaVersion": 2, "projectId": "p", "layers": [
        {"id": "punkte", "kind": "pointcloud", "url": url, "readerPointCount": 1,
         "invalidDropped": 0, **zusatz}]}


def _mesh(url: str = "layers/mesh/model.glb", preview: str = "layers/mesh/preview.glb",
          **zusatz) -> dict:
    return {"schemaVersion": 2, "projectId": "p", "layers": [
        {"id": "mesh", "kind": "mesh", "format": "gltf", "url": url, "previewUrl": preview,
         "sourceOptions": {"crs": None, "unit": "m", "up": "z"}, **zusatz}]}


def _glb(pfad: Path, gltf: dict) -> None:
    roh = json.dumps(gltf).encode()
    roh += b" " * (-len(roh) % 4)
    pfad.parent.mkdir(parents=True, exist_ok=True)
    pfad.write_bytes(b"glTF" + struct.pack("<II", 2, 20 + len(roh))
                     + struct.pack("<I4s", len(roh), b"JSON") + roh)


# --- verify (E57 gegen COPC) ---------------------------------------------------------------------


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_lehnt_wolken_verweis_ab(tmp_path, ref):
    pkg = _paket(tmp_path, _wolke(ref))
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify(tmp_path / "fehlt.e57", pkg)


@pytest.mark.parametrize("ort", ["topview", "verify.report", "previewUrl"])
@pytest.mark.parametrize("ref", ["/etc/passwd", "../x.json", "%2e%2e/x.json"],
                         ids=["absolut", "punktpunkt", "kodiert"])
def test_verify_lehnt_weitere_manifestverweise_ab(tmp_path, ort, ref):
    m = _wolke("cloud.copc.laz")
    if ort == "topview":
        m["topview"] = {"url": ref}
    elif ort == "verify.report":
        m["layers"][0]["verify"] = {"ok": True, "report": ref}
    else:
        m["layers"].append({"id": "mesh", "kind": "mesh", "url": "m.glb", "previewUrl": ref})
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify(tmp_path / "fehlt.e57", _paket(tmp_path, m))


@pytest.mark.parametrize("ref", ["/abs/S001/front", "../S001/front", "panos/S001%2ffront"],
                         ids=["absolut", "punktpunkt", "kodiert"])
def test_verify_lehnt_panoramaseiten_in_stations_json_ab(tmp_path, ref):
    m = _wolke("cloud.copc.laz")
    m["layers"].append({"id": "standpunkte", "kind": "panoramas", "url": "stations.json"})
    pkg = _paket(tmp_path, m)
    (pkg / "stations.json").write_text(json.dumps({"stations": [
        {"id": "S001", "panorama": {"faces": {"front": ref}}}]}), encoding="utf-8")
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify(tmp_path / "fehlt.e57", pkg)


def test_verify_laesst_zulaessige_verweise_durch(tmp_path):
    """Positiv: zulässige Verweise passieren die Prüfung; verify scheitert danach erst an der
    fehlenden Punktwolke, nicht an einem Verweis."""
    m = _wolke("cloud.copc.laz", verify={"ok": True, "report": "conversion-report.json"})
    m["topview"] = {"url": "topview.webp"}
    m["layers"].append({"id": "standpunkte", "kind": "panoramas", "url": "stations.json"})
    pkg = _paket(tmp_path, m)
    (pkg / "stations.json").write_text(json.dumps({"stations": [
        {"id": "S001", "panorama": {"faces": {"front": "panos/S001/front"}}}]}),
        encoding="utf-8")
    with pytest.raises(FileNotFoundError):
        verify(tmp_path / "fehlt.e57", pkg)


# --- verify_points (scanlose Formate, ausgedünnte E57) -------------------------------------------


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_points_lehnt_wolken_verweis_ab(tmp_path, ref):
    pkg = _paket(tmp_path, _wolke(ref, sourceOptions={"maxPoints": None}))
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_points(tmp_path / "fehlt.las", pkg)


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_leitet_auf_verify_points_und_lehnt_ab(tmp_path, ref):
    """Auch der Weg verify → verify_points (Wolke mit sourceOptions) lehnt ab."""
    pkg = _paket(tmp_path, _wolke(ref, sourceOptions={"maxPoints": None}))
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify(tmp_path / "fehlt.las", pkg)


# --- verify_mesh (Mesh, Mesh-Kacheln) -------------------------------------------------------------


@pytest.mark.parametrize("feld", ["url", "previewUrl", "mobileUrl"])
@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_mesh_lehnt_ebenenverweis_ab(tmp_path, feld, ref):
    m = _mesh()
    m["layers"][0][feld] = ref
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", _paket(tmp_path, m))


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_mesh_lehnt_gltf_dateiverweis_ab(tmp_path, ref):
    pkg = _paket(tmp_path, _mesh())
    _glb(pkg / "layers/mesh/model.glb", {"asset": {"version": "2.0"},
                                         "buffers": [{"byteLength": 4, "uri": "daten.bin"}],
                                         "images": [{"uri": ref}]})
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", pkg)


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_mesh_lehnt_kachel_verweis_ab(tmp_path, ref):
    m = {"schemaVersion": 2, "projectId": "p", "layers": [
        {"id": "kacheln", "kind": "mesh-tiles", "format": "3dtiles",
         "url": "layers/mesh-tiles/tileset.json"}]}
    pkg = _paket(tmp_path, m)
    ts = pkg / "layers/mesh-tiles/tileset.json"
    ts.parent.mkdir(parents=True)
    ts.write_text(json.dumps({"asset": {"version": "1.1"}, "root": {
        "content": {"uri": "0.glb"}, "children": [{"content": {"uri": ref}}]}}),
        encoding="utf-8")
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", pkg)


@pytest.mark.parametrize("ref", ["/abs/tileset.json", "../tileset.json", "%2e%2e/tileset.json"],
                         ids=["absolut", "punktpunkt", "kodiert"])
def test_verify_mesh_lehnt_tileset_verweis_ab(tmp_path, ref):
    m = {"schemaVersion": 2, "projectId": "p", "layers": [
        {"id": "kacheln", "kind": "mesh-tiles", "format": "3dtiles", "url": ref}]}
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", _paket(tmp_path, m))


def test_verify_mesh_lehnt_auch_mit_uebergebenem_manifest_ab(tmp_path):
    pkg = _paket(tmp_path, _mesh())
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", pkg, manifest=_mesh(url="../model.glb"))


def test_verify_mesh_echtes_paket_besteht(tmp_path):
    """Positiv: ein frisch konvertiertes Mesh-Paket hat nur zulässige Verweise und besteht."""
    from test_mesh_convert import asymmetric_mesh

    from scanview_import.mesh_convert import convert_mesh

    src = asymmetric_mesh(tmp_path / "a.obj")
    pkg = tmp_path / "echt"
    convert_mesh(src, pkg, unit="m", dreiecke=0)
    assert verify_mesh(src, pkg)["ok"] is True


def test_verify_mesh_gltf_mit_data_uri_und_unterordner_zulaessig(tmp_path):
    """Positiv: data:-URIs und Verweise in Unterordner passieren die Prüfung; verify_mesh scheitert
    danach erst an der fehlenden Quelle."""
    pkg = _paket(tmp_path, _mesh())
    _glb(pkg / "layers/mesh/model.glb", {"asset": {"version": "2.0"}, "buffers": [
        {"byteLength": 4, "uri": "data:application/octet-stream;base64,AAAAAA=="}],
        "images": [{"uri": "texturen/farbe_1.ktx2"}]})
    with pytest.raises(FileNotFoundError):
        verify_mesh(tmp_path / "fehlt.obj", pkg)


# --- Nachtrag: levelFile, Kachel-GLB, reine Splat-Pakete -----------------------------------------

#: levelFile-Muster: wie MUSTER, je mit dem Platzhalter ``{level}`` (so liest die Plattform es).
LEVEL_MUSTER = [
    ("absolut", "/etc/{level}.jpg"),
    ("absolut-url", "file:///etc/{level}.jpg"),
    ("punktpunkt", "../../../../etc/{level}.jpg"),
    ("punktpunkt-mitte", "stufe/../../{level}.jpg"),
    ("kodiert-prozent", "%2e%2e/{level}.jpg"),
    ("kodiert-backslash", "..\\{level}.jpg"),
    ("kodiert-nul", "{level}.jpg\x00.png"),
    ("kodiert-unicode-vollbreite", "\uff0e\uff0e\uff0f{level}.jpg"),
    ("kodiert-unicode-zerlegt", "stufe\u0308/{level}.jpg"),
]


def _panorama_paket(tmp_path: Path, level_file: object, schema1: bool = False) -> Path:
    m = _wolke("cloud.copc.laz")
    if schema1:
        m["stations"] = {"url": "stations.json"}
    else:
        m["layers"].append({"id": "standpunkte", "kind": "panoramas", "url": "stations.json"})
    pkg = _paket(tmp_path, m)
    (pkg / "stations.json").write_text(json.dumps({"stations": [
        {"id": "S001", "panorama": {"kind": "cube", "faces": {"front": "panos/S001/front"},
                                    "levels": [512], "levelFile": level_file}}]}),
        encoding="utf-8")
    return pkg


@pytest.mark.parametrize("ref", [m[1] for m in LEVEL_MUSTER], ids=[m[0] for m in LEVEL_MUSTER])
@pytest.mark.parametrize("schema1", [False, True], ids=["schema2", "schema1"])
def test_verify_lehnt_level_file_ab(tmp_path, ref, schema1):
    pkg = _panorama_paket(tmp_path, ref, schema1)
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify(tmp_path / "fehlt.e57", pkg)


def test_verify_lehnt_level_file_ohne_text_ab(tmp_path):
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify(tmp_path / "fehlt.e57", _panorama_paket(tmp_path, ["{level}.jpg"]))


@pytest.mark.parametrize("ref", ["{level}.jpg", "stufe_{level}.webp"])
def test_verify_laesst_zulaessiges_level_file_durch(tmp_path, ref):
    """Positiv: das levelFile des Imports und ein Präfix passieren; verify scheitert danach erst
    an der fehlenden Punktwolke."""
    with pytest.raises(FileNotFoundError):
        verify(tmp_path / "fehlt.e57", _panorama_paket(tmp_path, ref))


def _kachel_paket(tmp_path: Path, bild_uri: str) -> Path:
    m = {"schemaVersion": 2, "projectId": "p", "layers": [
        {"id": "kacheln", "kind": "mesh-tiles", "format": "3dtiles",
         "url": "layers/mesh-tiles/tileset.json"}]}
    pkg = _paket(tmp_path, m)
    ts = pkg / "layers/mesh-tiles/tileset.json"
    ts.parent.mkdir(parents=True)
    ts.write_text(json.dumps({"asset": {"version": "1.1"}, "root": {
        "content": {"uri": "0.glb"}, "children": [{"content": {"uri": "kinder/1.glb"}}]}}),
        encoding="utf-8")
    _glb(pkg / "layers/mesh-tiles/0.glb", {"asset": {"version": "2.0"}})
    _glb(pkg / "layers/mesh-tiles/kinder/1.glb", {"asset": {"version": "2.0"},
                                                  "images": [{"uri": bild_uri}]})
    return pkg


def test_verify_mesh_lehnt_bildverweis_in_kachel_glb_ab(tmp_path):
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", _kachel_paket(tmp_path, "../../geheim.png"))


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_mesh_lehnt_jeden_verweis_in_kachel_glb_ab(tmp_path, ref):
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_mesh(tmp_path / "fehlt.obj", _kachel_paket(tmp_path, ref))


def test_verify_mesh_kachel_gltf_relativ_zur_kachel(tmp_path):
    """Eine .gltf-Kachel im Unterordner verweist auf einen Buffer neben sich (zulässig) und der auf
    eine weitere .gltf mit ``..``: abgelehnt — die Prüfung folgt relativ zur Kachel."""
    pkg = _kachel_paket(tmp_path, "farbe.png")
    ts = pkg / "layers/mesh-tiles/tileset.json"
    ts.write_text(json.dumps({"asset": {"version": "1.1"}, "root": {
        "content": {"uri": "kinder/2.gltf"}}}), encoding="utf-8")
    (pkg / "layers/mesh-tiles/kinder/2.gltf").write_text(json.dumps({
        "asset": {"version": "2.0"}, "buffers": [{"uri": "tief/3.gltf", "byteLength": 1}]}),
        encoding="utf-8")
    (pkg / "layers/mesh-tiles/kinder/tief").mkdir()
    (pkg / "layers/mesh-tiles/kinder/tief/3.gltf").write_text(json.dumps({
        "asset": {"version": "2.0"}, "images": [{"uri": "../../../../geheim.png"}]}),
        encoding="utf-8")
    with pytest.raises(ValueError, match=r"Verweis unzulässig.*kinder/tief/3\.gltf"):
        verify_mesh(tmp_path / "fehlt.obj", pkg)


def test_verify_mesh_kachel_glb_mit_zulaessigem_bild_besteht_pruefung(tmp_path):
    """Positiv: zulässige Bildverweise in Kachel-GLBs passieren die Verweisprüfung; verify_mesh
    scheitert danach an etwas anderem als einem Verweis."""
    with pytest.raises(Exception) as fehler:
        verify_mesh(tmp_path / "fehlt.obj", _kachel_paket(tmp_path, "farbe.png"))
    assert ABGELEHNT not in str(fehler.value)


def _splat_paket(tmp_path: Path, **ebene) -> Path:
    return _paket(tmp_path, {"schemaVersion": 2, "projectId": "p", "frame": {
        "originWorld": [0, 0, 0]}, "layers": [
        {"id": "splats", "kind": "splats", "url": "layers/splats/model.spz", "splatCount": 1,
         "source": {"sha256": "0" * 64}, "verify": {"ok": False, "report": "verify.json"},
         **ebene}]})


@pytest.mark.parametrize("ref", [m[1] for m in MUSTER], ids=IDS)
def test_verify_splats_lehnt_ebenenverweis_ab(tmp_path, ref):
    from scanview_import.neu_ohne_wolke import verify_splats

    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_splats(tmp_path / "fehlt.spz", _splat_paket(tmp_path, url=ref))


@pytest.mark.parametrize("ref", ["../ausserhalb.json", "/tmp/ausserhalb.json", "%2e%2e/a.json"],
                         ids=["punktpunkt", "absolut", "kodiert"])
def test_verify_splats_lehnt_berichtsverweis_ab(tmp_path, ref):
    from scanview_import.neu_ohne_wolke import verify_splats

    pkg = _splat_paket(tmp_path, verify={"ok": False, "report": ref})
    with pytest.raises(ValueError, match=ABGELEHNT):
        verify_splats(tmp_path / "fehlt.spz", pkg)


def test_cli_verify_reines_splat_paket_schreibt_nicht_ausserhalb(tmp_path, capsys):
    """Echtes Splat-Paket, Berichtsverweis nachträglich auf ``../`` gesetzt: ``scanview-import
    verify`` (auch der Weg der Plattform für ``neu_splats``) bricht ab, bevor der Bericht außerhalb
    des Pakets landet."""
    import numpy as np

    from scanview_import.cli import main
    from scanview_import.splat_source import Gaussians
    from scanview_import.splat_spz import write_spz

    n = 50
    rng = np.random.default_rng(3)
    quelle = tmp_path / "quelle.spz"
    write_spz(quelle, Gaussians(rng.uniform(-1, 1, (n, 3)), np.full((n, 3), 0.02),
                                np.tile([0.0, 0, 0, 1], (n, 1)), np.full(n, 0.8),
                                rng.uniform(0.1, 0.9, (n, 3))), np.zeros(3))
    pkg = tmp_path / "splats"
    assert main(["neu-ohne-wolke", str(quelle), str(pkg), "--art", "splats"]) == 0
    m = json.loads((pkg / "project.json").read_text())
    m["layers"][0]["verify"]["report"] = "../ausserhalb.json"
    (pkg / "project.json").write_text(json.dumps(m), encoding="utf-8")
    capsys.readouterr()
    assert main(["verify", str(quelle), str(pkg)]) == 2
    assert ABGELEHNT in capsys.readouterr().err
    assert not (tmp_path / "ausserhalb.json").exists()
