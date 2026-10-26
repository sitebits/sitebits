"""F20: Kugelpanoramen nur aus einem geprüften Anonymisierungslauf (`panos-kameras --anonymisiert`).

Das Manifest bildet das Format des Anonymisierungswerkzeugs nach (synthetisch, keine echten Daten).
"""

import hashlib
import json
from pathlib import Path

import pytest
from PIL import Image

from scanview_import.cli import main
from tests.test_kameras import camera_file
from tests.test_kameras_cli import package


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def anon_lauf(tmp_path: Path, *, aktion="geschwaerzt", status="fertig", verifiziert=True,
              revision=3, zusatz=None):
    """Quelle rot (Person), Ausgabe grau (geschwärzt); Manifest + Entscheidung daneben."""
    source = camera_file(tmp_path)
    quelle = tmp_path / "kugel.png"
    ordner = tmp_path / "anon"
    (ordner / "entscheidungen").mkdir(parents=True)
    (ordner / "ausgabe").mkdir()
    ausgabe = ordner / "ausgabe" / "kugel.tif"
    Image.new("RGB", (128, 64), (128, 128, 128)).save(ausgabe)
    eintrag = {"pano": "kugel", "quelle": str(quelle), "sha256": _sha(quelle),
               "revision": revision, "aktion": aktion,
               "ausgabe": str(ausgabe) if aktion == "geschwaerzt" else None,
               "verifiziert": verifiziert}
    eintrag.update(zusatz or {})
    manifest = ordner / "manifest.json"
    manifest.write_text(json.dumps({"projekt": "test", "originale_unveraendert": True,
                                    "panos": [eintrag]}))
    (ordner / "entscheidungen" / "kugel.json").write_text(json.dumps(
        {"pano": "kugel", "status": status, "revision": 3,
         "quelle": {"sha256": _sha(quelle)}}))
    return source, manifest, ausgabe, quelle


def _lauf(source, pkg, manifest):
    return main(["panos-kameras", "--kameras", str(source), str(pkg), "--levels", "16,32",
                 "--anonymisiert", str(manifest)])


def test_nur_ausgaben_und_hash_je_stufe(tmp_path, capsys):
    source, manifest, _, _ = anon_lauf(tmp_path)
    pkg = package(tmp_path)
    assert _lauf(source, pkg, manifest) == 0
    out = json.loads((pkg / "stations.json").read_text())
    anon = out["anonymization"]
    assert anon["status"] == "reviewed" and anon["tool"] == "pano-anonym"
    assert anon["manifestSha256"] == _sha(manifest)
    stufen = sorted(pkg.rglob("*.jpg"))
    assert len(stufen) == 6 * 2
    assert anon["files"] == {p.relative_to(pkg).as_posix(): _sha(p) for p in stufen}
    # Pixel aus der grauen Ausgabe, nicht aus der roten Quelle.
    with Image.open(stufen[0]) as bild:
        r, g, b = bild.convert("RGB").getpixel((5, 5))
    assert abs(r - 128) < 8 and abs(g - 128) < 8 and abs(b - 128) < 8
    bericht = json.loads(capsys.readouterr().out)
    assert bericht["anonymisierung"]["panos"] == [{"pano": "kugel", "aktion": "geschwaerzt"}]


def test_unveraendert_nimmt_quelle(tmp_path):
    source, manifest, _, _ = anon_lauf(tmp_path, aktion="unveraendert")
    pkg = package(tmp_path)
    assert _lauf(source, pkg, manifest) == 0
    with Image.open(next(pkg.rglob("16.jpg"))) as bild:
        r, g, b = bild.convert("RGB").getpixel((5, 5))
    assert r > 200 and g < 50 and b < 50


def test_ohne_anonymisierung_unreviewed_mit_hashes(tmp_path):
    pkg = package(tmp_path)
    assert main(["panos-kameras", "--kameras", str(camera_file(tmp_path)), str(pkg),
                 "--levels", "16,32"]) == 0
    anon = json.loads((pkg / "stations.json").read_text())["anonymization"]
    assert anon["required"] is True and anon["status"] == "unreviewed"
    assert "tool" not in anon and len(anon["files"]) == 12


@pytest.mark.parametrize("fall,meldung", [
    ("quelle_nach_pruefung", "fehlt im"),
    ("nicht_fertig", "nicht fertig"),
    ("revision", "veraltet"),
    ("nicht_verifiziert", "nicht verifiziert"),
    ("aktion", "unbekannte Aktion"),
    ("ausgabe_symlink", "keine reguläre Datei"),
    ("ausgabe_fehlt", "keine reguläre Datei"),
    ("ausgabe_masse", "andere Maße"),
    ("ausgabe_sha", "weicht vom Manifest ab"),
    ("originale", "unveränderte Originale"),
    ("ohne_entscheidungen", "entscheidungen"),
])
def test_bricht_ab_ohne_zu_schreiben(tmp_path, capsys, fall, meldung):
    zusatz = {"aktion": "verpixelt"} if fall == "aktion" else (
        {"ausgabe_sha256": "0" * 64} if fall == "ausgabe_sha" else None)
    source, manifest, ausgabe, quelle = anon_lauf(
        tmp_path, status="in_arbeit" if fall == "nicht_fertig" else "fertig",
        verifiziert=fall != "nicht_verifiziert", revision=4 if fall == "revision" else 3,
        zusatz=zusatz)
    if fall == "quelle_nach_pruefung":
        Image.new("RGB", (128, 64), "blue").save(quelle)
    elif fall == "ausgabe_symlink":
        echt = ausgabe.with_name("echt.tif")
        ausgabe.rename(echt)
        ausgabe.symlink_to(echt)
    elif fall == "ausgabe_fehlt":
        ausgabe.unlink()
    elif fall == "ausgabe_masse":
        Image.new("RGB", (64, 32), (128, 128, 128)).save(ausgabe)
    elif fall == "originale":
        d = json.loads(manifest.read_text())
        d["originale_unveraendert"] = False
        manifest.write_text(json.dumps(d))
    elif fall == "ohne_entscheidungen":
        for p in (manifest.parent / "entscheidungen").iterdir():
            p.unlink()
        (manifest.parent / "entscheidungen").rmdir()
    pkg = package(tmp_path)
    assert _lauf(source, pkg, manifest) == 1
    assert meldung in capsys.readouterr().err
    assert not (pkg / "stations.json").exists()
    assert not list(pkg.rglob("*.jpg"))


def test_austausch_waehrend_import_laesst_altes_manifest(tmp_path, monkeypatch):
    """Ausgabe wird zwischen Prüfung und Abschluss getauscht → Abbruch, kein reviewed-Manifest."""
    from scanview_import import anonymisiert

    source, manifest, ausgabe, _ = anon_lauf(tmp_path)
    pkg = package(tmp_path)
    echt = anonymisiert.nachpruefen

    def tauschen(uebernahmen):
        Image.new("RGB", (128, 64), "red").save(ausgabe)
        echt(uebernahmen)

    monkeypatch.setattr(anonymisiert, "nachpruefen", tauschen)
    assert _lauf(source, pkg, manifest) == 1
    assert not (pkg / "stations.json").exists()
