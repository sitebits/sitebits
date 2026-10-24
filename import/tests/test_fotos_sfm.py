"""Fotos aus COLMAP und OpenDroneMap: Modellabbildung, Leser, voller CLI-Lauf, Abbrüche.

Die Projektionsformeln von OpenCV/COLMAP und OpenSfM stehen hier bewusst noch einmal, unabhängig
von `fotos_sfm.py`: Nur so fällt eine vertauschte Reihenfolge der Tangentialterme auf.
"""

import json
import struct
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from pyproj import Transformer
from scipy.spatial.transform import Rotation

from scanview_import.cli import main
from scanview_import.fotos_sfm import (
    _opensfm_spuren,
    colmap_kalibrierung,
    opensfm_kalibrierung,
    read_colmap,
)
from scanview_import.verzeichnung import VerzeichnungFehler, verzeichnen
from tests.test_fotos import _szene, _wolke_schreiben
from tests.test_kameras_cli import package

W, H = 240, 160
# fx ≠ fy, Hauptpunkt außerhalb der Mitte, große und ungleiche Tangentialterme.
OPENCV = [200.0, 196.0, 127.3, 76.1, -0.12, 0.03, 0.004, -0.007]


def _opencv_projektion(p, x, y):
    fx, fy, cx, cy, k1, k2, p1, p2, *rest = p
    k3 = rest[0] if rest else 0.0
    r2 = x * x + y * y
    radial = 1 + k1 * r2 + k2 * r2**2 + k3 * r2**3
    xd = x * radial + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * radial + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return fx * xd + cx, fy * yd + cy


def _strahlen():
    x, y = np.meshgrid(np.linspace(-0.55, 0.55, 9), np.linspace(-0.35, 0.35, 7))
    return x.ravel(), y.ravel()


def test_opencv_abbildung_trifft_eigene_formel():
    x, y = _strahlen()
    u, v = verzeichnen(colmap_kalibrierung("OPENCV", W, H, OPENCV), x, y)
    eu, ev = _opencv_projektion(OPENCV, x, y)
    assert np.max(np.hypot(u - eu, v - ev)) < 1e-9


def test_vertauschte_tangentialterme_wuerden_auffallen():
    x, y = _strahlen()
    getauscht = OPENCV[:6] + [OPENCV[7], OPENCV[6]]
    u, v = verzeichnen(colmap_kalibrierung("OPENCV", W, H, getauscht), x, y)
    eu, ev = _opencv_projektion(OPENCV, x, y)
    assert np.max(np.hypot(u - eu, v - ev)) > 0.5


@pytest.mark.parametrize(
    ("modell", "params", "als_opencv"),
    [
        ("SIMPLE_PINHOLE", [200.0, 120.0, 80.0], [200, 200, 120, 80, 0, 0, 0, 0]),
        ("PINHOLE", [200.0, 190.0, 121.0, 79.0], [200, 190, 121, 79, 0, 0, 0, 0]),
        ("SIMPLE_RADIAL", [200.0, 120.0, 80.0, -0.1], [200, 200, 120, 80, -0.1, 0, 0, 0]),
        ("RADIAL", [200.0, 120.0, 80.0, -0.1, 0.02], [200, 200, 120, 80, -0.1, 0.02, 0, 0]),
        (
            "FULL_OPENCV",
            [*OPENCV, 0.005, 0.0, 0.0, 0.0],
            [*OPENCV, 0.005],
        ),
    ],
)
def test_colmap_modelle(modell, params, als_opencv):
    x, y = _strahlen()
    u, v = verzeichnen(colmap_kalibrierung(modell, W, H, params), x, y)
    eu, ev = _opencv_projektion(als_opencv, x, y)
    assert np.max(np.hypot(u - eu, v - ev)) < 1e-9


@pytest.mark.parametrize(
    ("modell", "params", "text"),
    [
        ("OPENCV_FISHEYE", [200.0] * 8, "OPENCV_FISHEYE"),
        ("FULL_OPENCV", [*OPENCV, 0.0, 0.01, 0.0, 0.0], "k4 bis k6"),
        ("OPENCV", OPENCV[:7], "7 statt 8"),
    ],
)
def test_colmap_modelle_abgelehnt(modell, params, text):
    with pytest.raises(VerzeichnungFehler, match=text):
        colmap_kalibrierung(modell, W, H, params)


def _opensfm_projektion(kamera, x, y):
    size = max(kamera["width"], kamera["height"])
    if kamera["projection_type"] == "perspective":
        r2 = x * x + y * y
        d = 1 + kamera["k1"] * r2 + kamera["k2"] * r2**2
        un, vn = kamera["focal"] * x * d, kamera["focal"] * y * d
    else:
        p = [kamera[n] for n in ("focal_x", "focal_y", "c_x", "c_y", "k1", "k2", "p1", "p2")]
        un, vn = _opencv_projektion([*p, kamera["k3"]], x, y)
    # OpenSfM: normiert = (Pixelindex + 0,5 - w/2) / max(w, h)
    return un * size + kamera["width"] / 2, vn * size + kamera["height"] / 2


BROWN = {
    "projection_type": "brown",
    "width": W,
    "height": H,
    "focal_x": 0.83,
    "focal_y": 0.81,
    "c_x": 0.012,
    "c_y": -0.008,
    "k1": -0.1,
    "k2": 0.02,
    "p1": 0.004,
    "p2": -0.007,
    "k3": 0.003,
}


@pytest.mark.parametrize(
    "kamera",
    [
        BROWN,
        {
            "projection_type": "perspective",
            "width": W,
            "height": H,
            "focal": 0.85,
            "k1": -0.08,
            "k2": 0.01,
        },
    ],
)
def test_opensfm_abbildung_trifft_eigene_formel(kamera):
    x, y = _strahlen()
    u, v = verzeichnen(opensfm_kalibrierung(kamera), x, y)
    eu, ev = _opensfm_projektion(kamera, x, y)
    assert np.max(np.hypot(u - eu, v - ev)) < 1e-9


def test_opensfm_fisheye_abgelehnt():
    with pytest.raises(VerzeichnungFehler, match="fisheye"):
        opensfm_kalibrierung(dict(BROWN, projection_type="fisheye"))


# --- Szene: Nadirkameras über `_szene()` -------------------------------------------------------

# Kamera → Welt: X rechts = Ost, Y unten = Süd, Z Blick = unten.
NADIR = np.diag([1.0, -1.0, -1.0])


def _kameras():
    zentren = [
        (400_012.0, 5_800_012.0, 60.0),
        (400_028.0, 5_800_014.0, 62.0),
        (400_020.0, 5_800_028.0, 61.0),
    ]
    kippen = [(0.02, -0.03, 0.1), (-0.01, 0.02, -0.2), (0.03, 0.01, 0.05)]
    out = []
    for c, k in zip(zentren, kippen, strict=True):
        r_m = Rotation.from_rotvec(k).as_matrix() @ NADIR
        out.append((np.array(c), r_m))
    return out


def _beobachtungen(punkte, zentrum, r_m, projektion):
    lokal = (punkte - zentrum) @ r_m
    vorn = lokal[:, 2] > 0
    x, y = lokal[:, 0] / lokal[:, 2], lokal[:, 1] / lokal[:, 2]
    u, v = projektion(x, y)
    innen = vorn & (u > 1) & (u < W - 1) & (v > 1) & (v < H - 1)
    return np.flatnonzero(innen), u, v


def _bild(pfad: Path, orientation: int = 1):
    pfad.parent.mkdir(parents=True, exist_ok=True)
    exif = Image.Exif()
    if orientation != 1:
        exif[0x0112] = orientation
    Image.new("RGB", (W, H), "gray").save(pfad, exif=exif)


def _colmap_modell(tmp_path, *, binaer=False, versatz_px=0.0, namen=None, modell="OPENCV"):
    """Szene, Paket und COLMAP-Modell (Bilder unter tmp/bilder/flug1/)."""
    szene = _szene()
    rng = np.random.default_rng(3)
    punkte = szene[rng.choice(len(szene), 3000, replace=False)]
    namen = namen or [f"flug1/IMG_{i}.jpg" for i in range(3)]
    bilder = tmp_path / "bilder"
    bilder.mkdir()
    for n in namen:
        if ".." not in n:
            _bild(bilder / n)
    zeilen = []
    for i, ((c, r_m), name) in enumerate(zip(_kameras(), namen, strict=True), start=1):
        idx, u, v = _beobachtungen(punkte, c, r_m, lambda x, y: _opencv_projektion(OPENCV, x, y))
        r = r_m.T  # Welt → Kamera
        t = -r @ c
        q = Rotation.from_matrix(r).as_quat()  # x, y, z, w
        obs = [(float(u[j]) + versatz_px, float(v[j]), int(j) + 1) for j in idx]
        zeilen.append(
            (i, [float(a) for a in (q[3], q[0], q[1], q[2])], [float(a) for a in t], name, obs)
        )
    modell_dir = tmp_path / "sparse" / "0"
    modell_dir.mkdir(parents=True)
    ids = np.arange(1, len(punkte) + 1)
    if binaer:
        with (modell_dir / "cameras.bin").open("wb") as f:
            mid = {"OPENCV": 4, "OPENCV_FISHEYE": 5}[modell]
            f.write(struct.pack("<Q", 1) + struct.pack("<iiQQ", 1, mid, W, H))
            f.write(struct.pack("<8d", *OPENCV))
        with (modell_dir / "images.bin").open("wb") as f:
            f.write(struct.pack("<Q", len(zeilen)))
            for i, q, t, name, obs in zeilen:
                f.write(struct.pack("<i4d3di", i, *q, *t, 1) + name.encode() + b"\0")
                f.write(struct.pack("<Q", len(obs) + 1))
                for x, y, pid in obs:
                    f.write(struct.pack("<ddq", x, y, pid))
                f.write(struct.pack("<ddq", 5.0, 5.0, -1))  # Merkmal ohne 3D-Punkt
        with (modell_dir / "points3D.bin").open("wb") as f:
            f.write(struct.pack("<Q", len(punkte)))
            for pid, p in zip(ids, punkte, strict=True):
                f.write(struct.pack("<Q3d3BdQ", int(pid), *p, 1, 2, 3, 0.5, 2))
                f.write(struct.pack("<iiii", 1, 0, 2, 0))
    else:
        (modell_dir / "cameras.txt").write_text(
            "# Camera list\n1 " + modell + f" {W} {H} " + " ".join(map(repr, OPENCV)) + "\n"
        )
        text = ["# Image list"]
        for i, q, t, name, obs in zeilen:
            text.append(" ".join(map(repr, [i, *q, *t, 1])) + " " + name)
            text.append(" ".join(f"{x!r} {y!r} {pid}" for x, y, pid in obs) + " 5.0 5.0 -1")
        (modell_dir / "images.txt").write_text("\n".join(text) + "\n")
        (modell_dir / "points3D.txt").write_text(
            "# 3D point list\n"
            + "".join(
                f"{pid} {float(p[0])!r} {float(p[1])!r} {float(p[2])!r} 1 2 3 0.5 1 0 2 0\n"
                for pid, p in zip(ids, punkte, strict=True)
            )
        )
    pkg = package(tmp_path, wkt="EPSG:32633", datum="DHHN2016")
    _wolke_schreiben(pkg, szene)
    return modell_dir, bilder, pkg


def _colmap_cli(modell_dir, bilder, pkg):
    return main(
        [
            "fotos-colmap",
            "--modell",
            str(modell_dir),
            "--bilder",
            str(bilder),
            "--epsg",
            "32633",
            "--hoehensystem",
            "DHHN2016",
            str(pkg),
        ]
    )


@pytest.mark.parametrize("binaer", [False, True])
def test_colmap_cli_rundlauf(tmp_path, capsys, binaer):
    modell_dir, bilder, pkg = _colmap_modell(tmp_path, binaer=binaer)
    assert _colmap_cli(modell_dir, bilder, pkg) == 0, capsys.readouterr().err
    bericht = json.loads(capsys.readouterr().out)
    assert bericht["exportiert"] == 3
    assert bericht["modellbeleg"]["medianPx"] < 1e-6
    assert bericht["rueckprojektion"]["medianPx"] < 1e-3
    images = json.loads((pkg / "images.json").read_text())
    prov = images["provenance"]
    assert prov["source"] == "colmap"
    assert "maxReferenceErrorPx" not in prov
    assert prov["modelCheck"]["source"] == "colmap-points2D"
    assert {g["id"] for g in images["groups"]} == {"flug1"}
    erstes = images["images"][0]
    assert erstes["label"] == "flug1/IMG_0.jpg"
    assert np.allclose(erstes["pose"]["translation"], _kameras()[0][0], atol=1e-6)


def test_colmap_halbpixel_versatz_bricht_ab(tmp_path, capsys):
    modell_dir, bilder, pkg = _colmap_modell(tmp_path, versatz_px=0.5)
    assert _colmap_cli(modell_dir, bilder, pkg) == 1
    assert "Median-Restvektor (+0.500" in capsys.readouterr().err
    assert not (pkg / "images.json").exists()


def test_colmap_bildpfad_ausserhalb(tmp_path, capsys):
    namen = ["flug1/IMG_0.jpg", "flug1/IMG_1.jpg", "../IMG_2.jpg"]
    _bild(tmp_path / "IMG_2.jpg")
    modell_dir, bilder, pkg = _colmap_modell(tmp_path, namen=namen)
    assert _colmap_cli(modell_dir, bilder, pkg) == 1
    assert "außerhalb des Bildordners" in capsys.readouterr().err


def test_colmap_fisheye_abgelehnt(tmp_path, capsys):
    modell_dir, bilder, pkg = _colmap_modell(tmp_path, binaer=True, modell="OPENCV_FISHEYE")
    assert _colmap_cli(modell_dir, bilder, pkg) == 1
    assert "Kameramodell-ID 5" in capsys.readouterr().err


def test_colmap_bild_fehlt_und_exif_drehung(tmp_path):
    modell_dir, bilder, _ = _colmap_modell(tmp_path)
    _bild(bilder / "flug1/IMG_1.jpg", orientation=6)
    with pytest.raises(ValueError, match="EXIF-Orientation 6"):
        read_colmap(modell_dir, bilder, epsg=32633, vertical_datum="DHHN2016")
    (bilder / "flug1/IMG_1.jpg").unlink()
    with pytest.raises(ValueError, match="Bild fehlt"):
        read_colmap(modell_dir, bilder, epsg=32633, vertical_datum="DHHN2016")


def test_colmap_falsches_crs(tmp_path, capsys):
    modell_dir, bilder, pkg = _colmap_modell(tmp_path)
    assert (
        main(
            [
                "fotos-colmap",
                "--modell",
                str(modell_dir),
                "--bilder",
                str(bilder),
                "--epsg",
                "32632",
                "--hoehensystem",
                "DHHN2016",
                str(pkg),
            ]
        )
        == 1
    )
    assert "CRS" in capsys.readouterr().err


# --- OpenDroneMap ------------------------------------------------------------------------------

LAT, LON, ALT = 52.3, 15.0, 30.0  # UTM 33N


def _topo_nach_utm():
    """Unabhängig von fotos_sfm: PROJ-Pipeline topozentrisch → geografisch → UTM 33N."""
    return Transformer.from_pipeline(
        f"+proj=pipeline +step +inv +proj=topocentric +ellps=WGS84 +lat_0={LAT} +lon_0={LON} "
        f"+h_0={ALT} +step +inv +proj=cart +ellps=WGS84 "
        "+step +proj=utm +zone=33 +ellps=WGS84"
    )


def _tracks_v102(beob):
    daten = bytearray(b"OPENSFM_TRACKS_VERSION_v102\n")
    for bild, spur, x, y in beob:
        b, s = bild.encode(), spur.encode()
        daten += struct.pack("<HH", len(b), len(s)) + b + s
        daten += struct.pack("<ifff3Bx", 7, x, y, 0.001, 10, 20, 30)
    return bytes(daten)


def _odm_projekt(tmp_path):
    # Boden und zwei Wände (Rahmenprüfung braucht Flächen in allen Richtungen), lokal um 0.
    mitte = np.array([400_020.0, 5_800_020.0, 30.0])
    lokal_punkte = _szene(3000, seed=1) - mitte
    tr = _topo_nach_utm()
    utm = np.column_stack(tr.transform(*lokal_punkte.T))
    projekt = tmp_path / "odm"
    (projekt / "opensfm").mkdir(parents=True)
    (projekt / "odm_georeferencing").mkdir()
    (projekt / "odm_georeferencing" / "coords.txt").write_text("WGS84 UTM 33N\n123 456\n")
    (projekt / "opensfm" / "reference_lla.json").write_text(
        json.dumps({"latitude": LAT, "longitude": LON, "altitude": ALT})
    )
    size = max(W, H)
    shots, beob, zentren = {}, [], {}
    for i, (c, kipp) in enumerate(
        [((-6.0, -4.0, 40.0), (0.02, 0.0, 0.1)), ((5.0, 3.0, 41.0), (-0.02, 0.01, -0.1))]
    ):
        name = f"DJI_{i:04d}.JPG"
        _bild(projekt / "images" / name)
        r_m = Rotation.from_rotvec(kipp).as_matrix() @ NADIR
        r = r_m.T
        shots[name] = {
            "camera": "v2 test brown",
            "rotation": Rotation.from_matrix(r).as_rotvec().tolist(),
            "translation": (-r @ np.array(c)).tolist(),
        }
        zentren[name] = np.array(c)
        idx, u, v = _beobachtungen(
            lokal_punkte, np.array(c), r_m, lambda x, y: _opensfm_projektion(BROWN, x, y)
        )
        for j in idx:
            beob.append((name, str(j), (u[j] - W / 2) / size, (v[j] - H / 2) / size))
    beob.append(("DJI_0000.JPG", "999999", 0.0, 0.0))  # Spur ohne 3D-Punkt
    rek = [
        {
            "cameras": {"v2 test brown": BROWN},
            "shots": shots,
            "points": {
                str(j): {"coordinates": p.tolist(), "color": [1, 2, 3]}
                for j, p in enumerate(lokal_punkte)
            },
            "reference_lla": {"latitude": LAT, "longitude": LON, "altitude": ALT},
        }
    ]
    (projekt / "opensfm" / "reconstruction.topocentric.json").write_text(json.dumps(rek))
    (projekt / "opensfm" / "tracks.csv").write_bytes(_tracks_v102(beob))
    pkg = package(tmp_path, wkt="EPSG:32633", datum="ellipsoidisch")
    dichte = _szene(30_000, seed=2) - mitte
    _wolke_schreiben(pkg, np.column_stack(tr.transform(*dichte.T)))
    erwartet = {n: np.array(tr.transform(*z)) for n, z in zentren.items()}
    return projekt, pkg, utm, erwartet


def test_odm_cli_rundlauf(tmp_path, capsys):
    projekt, pkg, _, erwartet = _odm_projekt(tmp_path)
    args = ["fotos-odm", "--projekt", str(projekt), "--hoehensystem", "ellipsoidisch", str(pkg)]
    assert main(args) == 0, capsys.readouterr().err
    bericht = json.loads(capsys.readouterr().out)
    assert bericht["exportiert"] == 2
    assert bericht["modellbeleg"]["source"] == "opensfm-tracks"
    assert bericht["modellbeleg"]["medianPx"] < 1e-3
    # Lineare Näherung über 40 m gegen die exakte PROJ-Abbildung: weit unter einem Zehntelpixel.
    assert bericht["rueckprojektion"]["medianPx"] < 0.05
    images = json.loads((pkg / "images.json").read_text())
    for eintrag in images["images"]:
        assert np.allclose(eintrag["pose"]["translation"], erwartet[eintrag["label"]], atol=2e-3)
    assert images["provenance"]["source"] == "opendronemap"


def test_odm_ohne_georeferenz(tmp_path, capsys):
    projekt, pkg, _, _ = _odm_projekt(tmp_path)
    (projekt / "odm_georeferencing" / "coords.txt").unlink()
    args = ["fotos-odm", "--projekt", str(projekt), "--hoehensystem", "ellipsoidisch", str(pkg)]
    assert main(args) == 1
    assert "nicht georeferenziert" in capsys.readouterr().err


def test_opensfm_tracks_text_und_binaer():
    beob = [("a.jpg", "1", 0.125, -0.25), ("b c.jpg", "22", -0.5, 0.0625)]
    assert _opensfm_spuren(_tracks_v102(beob)) == beob
    text = "OPENSFM_TRACKS_VERSION_v2\n" + "".join(
        f"{b}\t{s}\t3\t{x}\t{y}\t0.001\t1\t2\t3\t-1\t-1\n" for b, s, x, y in beob
    )
    assert _opensfm_spuren(text.encode()) == beob
    with pytest.raises(ValueError, match="v103"):
        _opensfm_spuren(b"OPENSFM_TRACKS_VERSION_v103\n")


def test_paket_crs_ohne_autoritaet_nur_bei_gleichwertigkeit():
    from pyproj import CRS

    from scanview_import.kameras_import import _gleiches_crs

    # So schreibt ODM/PDAL UTM in die LAZ: ohne EPSG-Autorität auf äußerster Ebene.
    wkt = CRS.from_epsg(32617).to_wkt("WKT1_GDAL").replace(
        'PROJCS["WGS 84 / UTM zone 17N"', 'PROJCS["unknown"'
    )
    wkt = wkt[: wkt.rindex(',AUTHORITY["EPSG","32617"]')] + "]"
    assert "32617" not in wkt
    assert _gleiches_crs(wkt, 32617)
    assert not _gleiches_crs(wkt, 32616)
    assert not _gleiches_crs("kein CRS", 32617)
    assert _gleiches_crs("EPSG:32617", 32617) and not _gleiches_crs("EPSG:32617", 32616)
