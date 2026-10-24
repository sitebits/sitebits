"""Release-Manifest: kanonisches JSON, SSH-Signatur
(`ssh-keygen -Y`, Namespace `scanview-release`, Kennung `release@scanview`), Hash-Prüfung der
Artefakte, CLI `manifest pruefen`. Signiert wird ausschließlich mit einem Test-Schlüssel in
tmp_path — nie mit dem echten Release-Schlüssel."""

from __future__ import annotations

import hashlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from pflicht import auf_schema_1, auf_schema_2, berichte_dateien, werkzeuge, werkzeuge_roh

from scanview_nachweise import cli, manifest
from scanview_nachweise.artefakte import Artefakt, Netzschritt
from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.manifest import (
    baue_manifest,
    kanonisch,
    lade_manifest,
    lade_signiertes_manifest,
    pruefe_artefakte,
    pruefe_signatur,
    schreibe_manifest,
    signiere,
)
from scanview_nachweise.scan import DbStand

QUELLE = "a" * 40
EXPORT = "b" * 40
ZEIT = datetime(2026, 9, 27, 12, 0, tzinfo=UTC)
NETZ = Netzschritt(["npm", "ci"], "web", "2026-09-27T11:00:00+00:00")
DB = DbStand(ZEIT, "c" * 64, "UpdatedAt", Netzschritt(["trivy", "image", "--download-db-only"],
                                                      ".", "2026-09-27T10:00:00+00:00"))
WERKZEUGE = werkzeuge()
PRUEFUNGEN = {**{n: "bestanden" for n in manifest.PFLICHT_PRUEFUNGEN},
              "reproduzierbarkeit": "nicht-anwendbar"}
UMGEBUNG = {"node": "v22.20.0", "npm": "10.9.3", "python": "3.12.11", "uv": "uv 0.11.3",
            "docker": "28.4.0", "architektur": "x86_64"}


def _sha256(pfad: Path) -> str:
    return hashlib.sha256(pfad.read_bytes()).hexdigest()


def _artefakt(ordner: Path, name: str, inhalt: bytes) -> Artefakt:
    pfad = ordner / f"scanview-{name}-0.1.0.tar"
    pfad.write_bytes(inhalt)
    return Artefakt(name, pfad, hashlib.sha256(inhalt).hexdigest(),
                    hashlib.sha512(inhalt).hexdigest(), [])


@pytest.fixture
def eingaben(tmp_path: Path) -> dict[str, Any]:
    art = tmp_path / "artefakte"
    art.mkdir()
    sb = tmp_path / "sbom"
    sb.mkdir()
    for n in ("web", "server", "mcp", "import"):
        (sb / f"scanview-{n}-0.1.0.cdx.json").write_text(f'{{"n":"{n}"}}', encoding="utf-8")
    (tmp_path / "scan-ergebnis.json").write_text('{"ergebnis":"grün"}', encoding="utf-8")
    return {
        "version": "0.1.0",
        "quellcommit": QUELLE,
        "exportcommit": EXPORT,
        "artefakte": [_artefakt(art, "web", b"web"), _artefakt(art, "server", b"server"),
                      _artefakt(art, "mcp", b"mcp"), _artefakt(art, "import", b"import")],
        "sboms": [sb / f"scanview-{n}-0.1.0.cdx.json"
                  for n in ("web", "server", "mcp", "import")],
        "berichte": berichte_dateien(tmp_path / "berichte"),
        "werkzeuge": WERKZEUGE,
        "db_stand": DB,
        "pruefungen": dict(PRUEFUNGEN),
        "ausgeliefert": {"web", "server", "mcp", "import"},
        "netzschritte": [NETZ],
        "erstellt": ZEIT,
        "umgebung": dict(UMGEBUNG),
        "trockenlauf": False,
        "veroeffentlichbar": True,
        "sperrgrund": None,
    }


def _bauen(e: dict[str, Any]) -> dict[str, Any]:
    return baue_manifest(e["version"], e["quellcommit"], e["exportcommit"], e["artefakte"],
                         e["sboms"], e["berichte"], e["werkzeuge"], e["db_stand"],
                         e["pruefungen"], ausgeliefert=e["ausgeliefert"],
                         netzschritte=e["netzschritte"], erstellt=e["erstellt"],
                         umgebung=e["umgebung"], trockenlauf=e["trockenlauf"],
                         veroeffentlichbar=e["veroeffentlichbar"], sperrgrund=e["sperrgrund"])


@pytest.fixture
def schluessel(tmp_path: Path) -> tuple[Path, Path]:
    """Wegwerf-Schlüssel + passende allowed_signers-Datei (wie nachweise/allowed_signers)."""
    key = tmp_path / "schluessel" / "test_ed25519"
    key.parent.mkdir()
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "test", "-f", str(key)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    oeffentlich = key.with_suffix(".pub").read_text(encoding="utf-8").split()
    signers = tmp_path / "schluessel" / "allowed_signers"
    signers.write_text(f'release@scanview namespaces="scanview-release" {oeffentlich[0]} '
                       f"{oeffentlich[1]}\n", encoding="utf-8")
    return key, signers


# --- kanonisches JSON --------------------------------------------------------------------------

def test_kanonisch_exakt_und_stabil():
    a = {"z": 1, "ä": "ü", "a": [3, {"y": None, "b": True}]}
    b = {"a": [3, {"b": True, "y": None}], "ä": "ü", "z": 1}
    assert kanonisch(a) == kanonisch(b)
    assert kanonisch(a) == '{"a":[3,{"b":true,"y":null}],"z":1,"ä":"ü"}'.encode()
    assert kanonisch(a) == json.dumps(a, sort_keys=True, ensure_ascii=False,
                                      separators=(",", ":")).encode()


# --- baue_manifest ----------------------------------------------------------------------------

def test_manifest_inhalt(eingaben):
    m = _bauen(eingaben)
    assert m["schema"] == manifest.SCHEMA
    assert m["version"] == "0.1.0"
    assert m["quellcommit"] == QUELLE and m["exportcommit"] == EXPORT
    assert m["erstellt"] == "2026-09-27T12:00:00+00:00"
    namen = [a["name"] for a in m["artefakte"]]
    assert namen == ["web", "server", "mcp", "import"]
    web = m["artefakte"][0]
    assert web == {"name": "web", "datei": "scanview-web-0.1.0.tar",
                   "sha256": hashlib.sha256(b"web").hexdigest(),
                   "sha512": hashlib.sha512(b"web").hexdigest(), "ausgeliefert": True}
    assert [a["ausgeliefert"] for a in m["artefakte"]] == [True, True, True, True]
    assert m["sboms"] == {p.name: _sha256(p) for p in eingaben["sboms"]}
    assert m["berichte"] == {n: {"datei": p.name, "sha256": _sha256(p)}
                             for n, p in eingaben["berichte"].items()}
    assert m["werkzeuge"] == werkzeuge_roh()
    assert m["db_stand"] == {"zeit": "2026-09-27T12:00:00+00:00", "feld": "UpdatedAt",
                             "sha256": "c" * 64,
                             "netzschritt": {"befehl": ["trivy", "image", "--download-db-only"],
                                             "cwd": ".", "zeit": "2026-09-27T10:00:00+00:00"}}
    assert m["netzschritte"] == [{"befehl": ["npm", "ci"], "cwd": "web",
                                  "zeit": "2026-09-27T11:00:00+00:00"}]
    assert m["pruefungen"] == PRUEFUNGEN
    assert m["umgebung"] == UMGEBUNG
    assert m["trockenlauf"] is False
    # JSON-fähig ohne Umwandlungshilfe (keine datetime/Path-Reste).
    kanonisch(m)


def test_manifest_ohne_exportcommit(eingaben):
    """Ohne Exportcommit ist ein Manifest unvollständig — nie signiert."""
    eingaben["exportcommit"] = None
    with pytest.raises(NachweisFehler) as e:
        _bauen(eingaben)
    assert e.value.regel == "manifest" and "Exportcommit" in e.value.text


@pytest.mark.parametrize("name", ["web", "server", "mcp", "import"])
def test_manifest_ohne_sbom_eines_ausgelieferten(eingaben, name):
    eingaben["sboms"] = [p for p in eingaben["sboms"] if f"-{name}-" not in p.name]
    with pytest.raises(NachweisFehler, match=f"SBOM fehlt.*{name}"):
        _bauen(eingaben)


def test_manifest_ohne_berichte_oder_werkzeuge(eingaben):
    for feld in ("berichte", "werkzeuge"):
        kopie = dict(eingaben)
        kopie[feld] = {}
        with pytest.raises(NachweisFehler) as e:
            _bauen(kopie)
        assert e.value.regel == "manifest"


@pytest.mark.parametrize("pruefung", manifest.PFLICHT_PRUEFUNGEN)
@pytest.mark.parametrize("wert", [None, "nicht-anwendbar"])
def test_pflichtpruefung_muss_bestanden_sein(eingaben, pruefung, wert):
    if wert is None:
        del eingaben["pruefungen"][pruefung]
    else:
        eingaben["pruefungen"][pruefung] = wert
    with pytest.raises(NachweisFehler, match=f"Pflichtprüfungen.*{pruefung}"):
        _bauen(eingaben)


@pytest.mark.parametrize("aendern", [
    lambda m: m.update(sboms=None),
    lambda m: m.update(berichte=None),
    lambda m: m.update(db_stand=None),
    lambda m: m.update(exportcommit=None),
    lambda m: m.update(pruefungen={"irgendwas": "nicht-anwendbar"}),
    lambda m: m.pop("veroeffentlichung"),
    lambda m: m["db_stand"].update(sha256="kurz"),
    lambda m: m["berichte"]["scan-ergebnis"].update(datei="../x"),
    lambda m: m.update(netzschritte=[{"befehl": "npm ci", "cwd": "web", "zeit": "t"}]),
    lambda m: m.update(werkzeuge={"trivy": {"image": ""}}),
], ids=["sboms-null", "berichte-null", "db-null", "export-null", "pruefungen-beliebig",
        "ohne-veroeffentlichung", "db-hash", "bericht-pfad", "netzschritt", "werkzeug"])
def test_laden_verlangt_vollstaendigen_inhalt(eingaben, tmp_path, aendern):
    """Feldnamen allein genügen nicht — Nachweisreferenzen müssen vollständig und
    richtig geformt sein."""
    m = _bauen(eingaben)
    aendern(m)
    pfad = tmp_path / "manifest.json"
    pfad.write_bytes(kanonisch(m))
    with pytest.raises(NachweisFehler) as e:
        lade_manifest(pfad)
    assert e.value.regel == "manifest"


@pytest.mark.parametrize("wert", ["gescheitert", "offen", "", "Bestanden"])
def test_pruefung_nur_bestanden_oder_nicht_anwendbar(eingaben, wert):
    eingaben["pruefungen"]["scan"] = wert
    with pytest.raises(NachweisFehler) as e:
        _bauen(eingaben)
    assert e.value.regel == "manifest"


def test_pruefungen_leer_abgelehnt(eingaben):
    eingaben["pruefungen"] = {}
    with pytest.raises(NachweisFehler, match="Prüfung"):
        _bauen(eingaben)


def test_artefakt_hash_weicht_ab(eingaben):
    eingaben["artefakte"][0].pfad.write_bytes("nachträglich verändert".encode())
    with pytest.raises(NachweisFehler) as e:
        _bauen(eingaben)
    assert e.value.regel == "manifest" and "web" in e.value.text


@pytest.mark.parametrize("ausgeliefert", [set(), {"web", "mcp"}, {"web", "server"},
                                          {"web", "server", "mcp"}])
def test_ausgeliefert_muss_passen(eingaben, ausgeliefert):
    eingaben["ausgeliefert"] = ausgeliefert
    with pytest.raises(NachweisFehler, match="ausgeliefert"):
        _bauen(eingaben)


def test_doppelter_artefaktname(eingaben):
    eingaben["artefakte"].append(eingaben["artefakte"][0])
    with pytest.raises(NachweisFehler, match="doppelt"):
        _bauen(eingaben)


def test_fehlende_datei(eingaben, tmp_path):
    eingaben["berichte"]["scan-ergebnis"] = tmp_path / "fehlt.json"
    with pytest.raises(NachweisFehler, match=r"fehlt\.json"):
        _bauen(eingaben)


@pytest.mark.parametrize("feld,wert", [("quellcommit", "abc"), ("exportcommit", "B" * 40),
                                       ("version", ""), ("version", "../0.1.0")])
def test_ungueltige_kennungen(eingaben, feld, wert):
    eingaben[feld] = wert
    with pytest.raises(NachweisFehler):
        _bauen(eingaben)


# --- schreiben / laden ------------------------------------------------------------------------

def test_schreiben_kanonisch_und_laden(eingaben, tmp_path):
    m = _bauen(eingaben)
    pfad = schreibe_manifest(m, tmp_path / "out" / "manifest.json")
    assert pfad.read_bytes() == kanonisch(m)
    assert lade_manifest(pfad) == m
    with pytest.raises(NachweisFehler, match="existiert"):
        schreibe_manifest(m, pfad)


def test_laden_lehnt_nicht_kanonische_datei_ab(eingaben, tmp_path):
    pfad = tmp_path / "manifest.json"
    pfad.write_text(json.dumps(_bauen(eingaben), indent=2), encoding="utf-8")
    with pytest.raises(NachweisFehler, match="kanonisch"):
        lade_manifest(pfad)


def test_laden_lehnt_gescheitert_ab(eingaben, tmp_path):
    m = _bauen(eingaben)
    m["pruefungen"]["scan"] = "gescheitert"
    pfad = tmp_path / "manifest.json"
    pfad.write_bytes(kanonisch(m))
    with pytest.raises(NachweisFehler) as e:
        lade_manifest(pfad)
    assert e.value.regel == "manifest"


# --- Signatur ---------------------------------------------------------------------------------

def _signiertes(eingaben, tmp_path, schluessel) -> tuple[Path, Path]:
    pfad = schreibe_manifest(_bauen(eingaben), tmp_path / "manifest.json")
    return pfad, signiere(pfad, schluessel[0])


def test_signatur_roundtrip(eingaben, tmp_path, schluessel):
    pfad, sig = _signiertes(eingaben, tmp_path, schluessel)
    assert sig == tmp_path / "manifest.json.sig"
    assert sig.read_text(encoding="utf-8").startswith("-----BEGIN SSH SIGNATURE-----")
    pruefe_signatur(pfad, schluessel[1])


def test_signieren_ueberschreibt_keine_signatur(eingaben, tmp_path, schluessel):
    pfad, sig = _signiertes(eingaben, tmp_path, schluessel)
    vorher = sig.read_bytes()
    with pytest.raises(NachweisFehler) as e:
        signiere(pfad, schluessel[0])
    assert e.value.regel == "signatur"
    assert sig.read_bytes() == vorher


def test_manipuliertes_manifest(eingaben, tmp_path, schluessel):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    pfad.write_bytes(pfad.read_bytes().replace(b"bestanden", b"bestandeN", 1))
    with pytest.raises(NachweisFehler) as e:
        pruefe_signatur(pfad, schluessel[1])
    assert e.value.regel == "signatur"


def test_falscher_namespace(eingaben, tmp_path, schluessel):
    pfad = schreibe_manifest(_bauen(eingaben), tmp_path / "manifest.json")
    subprocess.run(["ssh-keygen", "-q", "-Y", "sign", "-f", str(schluessel[0]), "-n", "anderes",
                    str(pfad)], check=True, stdin=subprocess.DEVNULL, capture_output=True)
    with pytest.raises(NachweisFehler) as e:
        pruefe_signatur(pfad, schluessel[1])
    assert e.value.regel == "signatur"


def test_falsche_kennung(eingaben, tmp_path, schluessel):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    signers = schluessel[1]
    signers.write_text(signers.read_text(encoding="utf-8").replace("release@scanview",
                                                                   "jemand@anders"),
                       encoding="utf-8")
    with pytest.raises(NachweisFehler, match="signatur"):
        pruefe_signatur(pfad, signers)


def test_fremder_schluessel(eingaben, tmp_path, schluessel):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    fremd = tmp_path / "fremd_ed25519"
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", "x", "-f", str(fremd)],
                   check=True, stdin=subprocess.DEVNULL, capture_output=True)
    teile = fremd.with_suffix(".pub").read_text(encoding="utf-8").split()
    signers = tmp_path / "fremd_signers"
    signers.write_text(f'release@scanview namespaces="scanview-release" {teile[0]} {teile[1]}\n',
                       encoding="utf-8")
    with pytest.raises(NachweisFehler, match="signatur"):
        pruefe_signatur(pfad, signers)


def test_signatur_fehlt(eingaben, tmp_path, schluessel):
    pfad = schreibe_manifest(_bauen(eingaben), tmp_path / "manifest.json")
    with pytest.raises(NachweisFehler) as e:
        pruefe_signatur(pfad, schluessel[1])
    assert e.value.regel == "signatur" and ".sig" in e.value.text


def test_allowed_signers_fehlt(eingaben, tmp_path, schluessel):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    with pytest.raises(NachweisFehler, match="signatur"):
        pruefe_signatur(pfad, tmp_path / "gibtsnicht")


# --- Artefakt-Hashes --------------------------------------------------------------------------

def test_pruefe_artefakte(eingaben, tmp_path):
    m = _bauen(eingaben)
    pruefe_artefakte(m, tmp_path / "artefakte")


def test_pruefe_artefakte_nur_ausgeliefert(eingaben, tmp_path):
    m = _bauen(eingaben)
    auf_schema_2(m)  # /2: import steht nur informativ im Manifest
    (tmp_path / "artefakte" / "scanview-import-0.1.0.tar").unlink()
    with pytest.raises(NachweisFehler) as e:
        pruefe_artefakte(m, tmp_path / "artefakte")
    assert e.value.regel == "artefakt-hash"
    pruefe_artefakte(m, tmp_path / "artefakte", nur_ausgeliefert=True)


def test_pruefe_artefakte_abweichung(eingaben, tmp_path):
    m = _bauen(eingaben)
    (tmp_path / "artefakte" / "scanview-server-0.1.0.tar").write_bytes(b"anders")
    with pytest.raises(NachweisFehler) as e:
        pruefe_artefakte(m, tmp_path / "artefakte", nur_ausgeliefert=True)
    assert e.value.regel == "artefakt-hash" and "server" in e.value.text


# --- CLI --------------------------------------------------------------------------------------

def test_cli_manifest_pruefen(eingaben, tmp_path, schluessel, capsys):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    assert cli.main(["manifest", "pruefen", str(pfad), "--allowed-signers",
                     str(schluessel[1]), "--artefakte", str(tmp_path / "artefakte")]) == 0
    aus = capsys.readouterr().out
    assert "Signatur gültig" in aus and "4 Artefakt" in aus


def test_cli_manifest_pruefen_ohne_artefakte(eingaben, tmp_path, schluessel, capsys):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    assert cli.main(["manifest", "pruefen", str(pfad), "--allowed-signers",
                     str(schluessel[1])]) == 0
    assert "nicht geprüft" in capsys.readouterr().out


def test_cli_manifest_pruefen_manipuliert(eingaben, tmp_path, schluessel, capsys):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    (tmp_path / "artefakte" / "scanview-web-0.1.0.tar").write_bytes(b"x")
    assert cli.main(["manifest", "pruefen", str(pfad), "--allowed-signers",
                     str(schluessel[1]), "--artefakte", str(tmp_path / "artefakte")]) == 2
    assert "NACHWEIS-FEHLER artefakt-hash" in capsys.readouterr().err


def test_cli_standard_allowed_signers(tmp_path, monkeypatch):
    # Ohne --allowed-signers gilt <wurzel>/nachweise/allowed_signers.
    (tmp_path / "nachweise").mkdir()
    gesehen: list[Path] = []
    def _laden(p: Path, s: Path) -> dict[str, Any]:
        gesehen.append(s)
        return {"version": "0.1.0", "artefakte": []}

    monkeypatch.setattr(manifest, "lade_signiertes_manifest", _laden)
    assert cli.main(["manifest", "pruefen", str(tmp_path / "m.json"), "--wurzel",
                     str(tmp_path)]) == 0
    assert gesehen == [tmp_path / "nachweise" / "allowed_signers"]


def test_repo_allowed_signers_passt_zum_oeffentlichen_schluessel():
    nachweise = Path(__file__).parents[1]
    pub = (nachweise / "signierschluessel.pub").read_text(encoding="utf-8").split()
    zeilen = [z for z in (nachweise / "allowed_signers").read_text(encoding="utf-8").splitlines()
              if z.strip() and not z.startswith("#")]
    assert pub[0] == "ssh-ed25519" and pub[2] == "scanview-release"
    assert (f'release@scanview namespaces="scanview-release,scanview-abschluss" {pub[0]} '
            f'{pub[1]}') in zeilen


# --- Ausgelieferte Artefakte (Konstante) --------------------------------------------------------

def test_ausgeliefert_konstante_r14():
    assert manifest.AUSGELIEFERT == frozenset({"web", "server", "mcp", "import"})
    assert manifest.AUSGELIEFERT_JE_SCHEMA == {
        SCHEMA_1: frozenset({"web", "server", "mcp"}),
        SCHEMA_2: frozenset({"web", "server", "mcp"}),
        SCHEMA_3: frozenset({"web", "server", "mcp", "import"}),
    }


def test_ausgeliefertes_artefakt_fehlt_im_bau(eingaben):
    eingaben["artefakte"] = [a for a in eingaben["artefakte"] if a.name != "mcp"]
    with pytest.raises(NachweisFehler, match="mcp"):
        _bauen(eingaben)


def test_lade_signiertes_manifest(eingaben, tmp_path, schluessel):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    assert lade_signiertes_manifest(pfad, schluessel[1]) == _bauen(eingaben)


def test_austausch_nach_signaturpruefung_wirkt_nicht(eingaben, tmp_path, schluessel,
                                                     monkeypatch):
    # TOCTOU: Nach der Signaturprüfung wird die Datei gegen ein (kanonisches, aber unsigniertes)
    # Manifest mit anderen Hashes getauscht. Ausgewertet werden müssen die geprüften Bytes.
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    echt = lade_manifest(pfad)
    falsch = json.loads(json.dumps(echt))
    falsch["artefakte"][0]["sha256"] = "0" * 64
    original = manifest._ssh_keygen

    def _pruefen_dann_tauschen(befehl: list[str], eingabe: bytes | None):
        ergebnis = original(befehl, eingabe)
        pfad.write_bytes(kanonisch(falsch))
        return ergebnis

    monkeypatch.setattr(manifest, "_ssh_keygen", _pruefen_dann_tauschen)
    assert lade_signiertes_manifest(pfad, schluessel[1]) == echt


def test_cli_nutzt_einmal_gelesene_bytes(eingaben, tmp_path, schluessel, monkeypatch, capsys):
    pfad, _ = _signiertes(eingaben, tmp_path, schluessel)
    falsch = lade_manifest(pfad)
    falsch["artefakte"][0]["sha256"] = "0" * 64
    original = manifest._ssh_keygen

    def _pruefen_dann_tauschen(befehl: list[str], eingabe: bytes | None):
        ergebnis = original(befehl, eingabe)
        pfad.write_bytes(kanonisch(falsch))
        return ergebnis

    monkeypatch.setattr(manifest, "_ssh_keygen", _pruefen_dann_tauschen)
    assert cli.main(["manifest", "pruefen", str(pfad), "--allowed-signers",
                     str(schluessel[1]), "--artefakte", str(tmp_path / "artefakte")]) == 0


@pytest.mark.parametrize("feld,wert", [("ausgeliefert", "true"), ("ausgeliefert", 1),
                                       ("sha256", 5), ("sha512", "abc"), ("datei", None)])
def test_laden_prueft_typen(eingaben, tmp_path, feld, wert):
    m = _bauen(eingaben)
    m["artefakte"][0][feld] = wert
    pfad = tmp_path / "manifest.json"
    pfad.write_bytes(kanonisch(m))
    with pytest.raises(NachweisFehler) as e:
        lade_manifest(pfad)
    assert e.value.regel == "manifest"


def test_pruefe_artefakte_verlangt_ausgelieferte_kennzeichnung(eingaben, tmp_path):
    m = _bauen(eingaben)
    m["artefakte"][2]["ausgeliefert"] = False  # mcp still ausgenommen
    with pytest.raises(NachweisFehler) as e:
        pruefe_artefakte(m, tmp_path / "artefakte", nur_ausgeliefert=True)
    assert e.value.regel == "artefakt-hash" and "mcp" in e.value.text
    m = _bauen(eingaben)
    m["artefakte"] = [a for a in m["artefakte"] if a["name"] != "server"]
    with pytest.raises(NachweisFehler, match="server"):
        pruefe_artefakte(m, tmp_path / "artefakte", nur_ausgeliefert=True)


def test_pruefe_artefakte_sha512(eingaben, tmp_path):
    m = _bauen(eingaben)
    m["artefakte"][0]["sha512"] = "0" * 128
    with pytest.raises(NachweisFehler, match="SHA-512"):
        pruefe_artefakte(m, tmp_path / "artefakte", nur_ausgeliefert=True)


def test_pruefe_artefakte_lesefehler(eingaben, tmp_path):
    m = _bauen(eingaben)
    datei = tmp_path / "artefakte" / "scanview-web-0.1.0.tar"
    datei.chmod(0o000)
    try:
        with pytest.raises(NachweisFehler) as e:
            pruefe_artefakte(m, tmp_path / "artefakte", nur_ausgeliefert=True)
        assert e.value.regel == "artefakt-hash"
    finally:
        datei.chmod(0o644)


# --- Umgebung (Toolchain/Architektur) und Trockenlauf-Kennzeichen ------------------------------

def test_trockenlauf_kennzeichen(eingaben, tmp_path):
    eingaben["trockenlauf"] = True
    m = _bauen(eingaben)
    assert m["trockenlauf"] is True
    assert lade_manifest(schreibe_manifest(m, tmp_path / "manifest.json"))["trockenlauf"] is True


@pytest.mark.parametrize("aenderung", [
    lambda u: u.pop("docker"), lambda u: u.update(node=""), lambda u: u.update(uv=3),
    lambda u: u.pop("architektur"),
])
def test_umgebung_pflichtangaben(eingaben, aenderung):
    aenderung(eingaben["umgebung"])
    with pytest.raises(NachweisFehler) as e:
        _bauen(eingaben)
    assert e.value.regel == "manifest" and "Umgebung" in e.value.text


@pytest.mark.parametrize("feld,wert", [("trockenlauf", "nein"), ("trockenlauf", None),
                                       ("umgebung", {}), ("umgebung", "x86_64")])
def test_laden_prueft_umgebung_und_trockenlauf(eingaben, tmp_path, feld, wert):
    m = _bauen(eingaben)
    m[feld] = wert
    pfad = tmp_path / "manifest.json"
    pfad.write_bytes(kanonisch(m))
    with pytest.raises(NachweisFehler) as e:
        lade_manifest(pfad)
    assert e.value.regel == "manifest"


@pytest.mark.parametrize("feld", ["umgebung", "trockenlauf"])
def test_laden_verlangt_umgebung_und_trockenlauf(eingaben, tmp_path, feld):
    m = _bauen(eingaben)
    del m[feld]
    pfad = tmp_path / "manifest.json"
    pfad.write_bytes(kanonisch(m))
    with pytest.raises(NachweisFehler, match=feld):
        lade_manifest(pfad)


def test_manifest_vermerkt_veroeffentlichung(eingaben: dict[str, Any], tmp_path: Path) -> None:
    """Das Release-Manifest trägt, ob der Export veröffentlicht werden darf."""
    e = eingaben
    assert _bauen(e)["veroeffentlichung"] == {"veroeffentlichbar": True, "grund": None}
    e2 = {**e, "veroeffentlichbar": False, "sperrgrund": "SECURITY.md:3 Marker POSTFACH-OFFEN"}
    m = _bauen(e2)
    assert m["veroeffentlichung"] == {"veroeffentlichbar": False,
                                      "grund": "SECURITY.md:3 Marker POSTFACH-OFFEN"}
    pfad = manifest.schreibe_manifest(m, tmp_path / "m" / "manifest.json")
    assert manifest.lade_manifest(pfad)["veroeffentlichung"]["veroeffentlichbar"] is False
    for falsch in ({"veroeffentlichbar": False, "sperrgrund": None},
                   {"veroeffentlichbar": False, "sperrgrund": " "},
                   {"veroeffentlichbar": True, "sperrgrund": "x"}):
        with pytest.raises(NachweisFehler):
            _bauen({**e, **falsch})


# --- Pflicht-Berichte und -Werkzeuge --------------------------------------------------------------

@pytest.mark.parametrize("feld", ["berichte", "werkzeuge"])
def test_vollstaendige_pflichtliste_verlangt(eingaben, tmp_path, feld):
    """Nicht „irgendein“ Bericht/Werkzeug, sondern genau die Pflichtliste —
    ein fehlender oder ein zusätzlicher Eintrag bricht ab, beim Bauen wie beim Laden."""
    m = _bauen(eingaben)
    for aendern in (lambda d: d.pop(next(iter(d))),
                    lambda d: d.update(fremd=next(iter(d.values())))):
        kopie = json.loads(json.dumps(m))
        aendern(kopie[feld])
        pfad = tmp_path / f"m-{feld}.json"
        pfad.write_bytes(kanonisch(kopie))
        with pytest.raises(NachweisFehler, match="nicht vollständig"):
            lade_manifest(pfad)
    eingaben[feld] = dict(list(eingaben[feld].items())[1:])
    with pytest.raises(NachweisFehler, match="nicht vollständig"):
        _bauen(eingaben)


def test_pflichtlisten_eine_quelle():
    """Drift: Stufenberichte wie release.STUFEN bis export, Werkzeuge wie werkzeuge.json."""
    from scanview_nachweise import release
    stufen = release.STUFEN[:release.STUFEN.index("manifest")]
    assert manifest.PFLICHT_BERICHTE == (*(f"stufe-{s}" for s in stufen), "scan-ergebnis",
                                         "werkzeugbefunde", "exportmanifest", "npm-signaturen",
                                         "sast-semgrep", "sast-bandit", "fuzzing-admin",
                                         "fuzzing-betrachter", "egress-statisch",
                                         "egress-browser")
    echt = json.loads((Path(__file__).parents[1] / "werkzeuge.json").read_text("utf-8"))
    assert manifest.PFLICHT_WERKZEUGE == tuple(echt)


# --- Manifest-Schema je Stufe: /1 (bis 0.1.0) und /2 (+ commit-signatur) ------------------------

SCHEMA_1 = "scanview-release-manifest/1"
SCHEMA_2 = "scanview-release-manifest/2"
#: Schema /3: gleiche Pflichtlisten wie /2, dazu wird import ausgeliefert.
SCHEMA_3 = "scanview-release-manifest/3"
#: Stand von Schema /1 — eingefroren: archivierte 0.1.0-Manifeste bleiben lesbar.
BERICHTE_1 = ("stufe-einfrieren", "stufe-register", "stufe-qualitaetstor", "stufe-sbom",
              "stufe-scan", "stufe-negativkontrollen", "stufe-lizenzen", "stufe-matrix",
              "stufe-export", "scan-ergebnis", "werkzeugbefunde", "exportmanifest")
WERKZEUGE_1 = ("trivy", "syft", "cyclonedx-npm", "cyclonedx-py", "cyclonedx-py-py312",
               "cyclonedx-cli", "sbomqs", "gitleaks", "shellcheck", "deploy-probe")
PRUEFUNGEN_1 = ("register", "befunde", "negativtests", "retests", "qualitaetstor", "sbom",
                "scan", "negativkontrollen", "lizenzen", "export")
#: Schema /2 (Release 0.2.0): Prüfwerkzeuge aus Teil 4 kommen hinzu (SAST, Fuzzing, DAST,
#: Headless-Browser des Egress-Tests).
WERKZEUGE_2 = (*WERKZEUGE_1, "semgrep", "bandit", "schemathesis", "zap", "caddy-test",
               "playwright")


def test_pflichtlisten_je_schema():
    assert manifest.SCHEMA == SCHEMA_3
    assert set(manifest.PFLICHT_JE_SCHEMA) == {SCHEMA_1, SCHEMA_2, SCHEMA_3}
    assert manifest.PFLICHT_JE_SCHEMA[SCHEMA_3] == manifest.PFLICHT_JE_SCHEMA[SCHEMA_2]
    assert manifest.PFLICHT_JE_SCHEMA[SCHEMA_1] == (BERICHTE_1, WERKZEUGE_1, PRUEFUNGEN_1)
    assert manifest.PFLICHT_JE_SCHEMA[SCHEMA_2] == (
        manifest.PFLICHT_BERICHTE, manifest.PFLICHT_WERKZEUGE, manifest.PFLICHT_PRUEFUNGEN)
    assert manifest.PFLICHT_PRUEFUNGEN == (*PRUEFUNGEN_1, "commit-signatur", "npm-signaturen",
                                           "sast", "fuzzing", "egress")
    # Schema /2: die Stufenberichte von SAST (nach dem Qualitätstor), Fuzzing und Egress (nach dem
    # Bau) stehen an ihrer Stelle in der Stufenreihenfolge, die Rohberichte der Werkzeuge am Ende.
    assert manifest.PFLICHT_BERICHTE == (
        "stufe-einfrieren", "stufe-register", "stufe-qualitaetstor", "stufe-sast", "stufe-sbom",
        "stufe-fuzzing", "stufe-egress", "stufe-scan", "stufe-negativkontrollen",
        "stufe-lizenzen", "stufe-matrix", "stufe-export", "scan-ergebnis", "werkzeugbefunde",
        "exportmanifest", "npm-signaturen", "sast-semgrep", "sast-bandit", "fuzzing-admin",
        "fuzzing-betrachter", "egress-statisch", "egress-browser")
    assert manifest.PFLICHT_WERKZEUGE == WERKZEUGE_2


def _geschrieben(m: dict[str, Any], pfad: Path) -> Path:
    pfad.write_bytes(kanonisch(m))
    return pfad


def test_schema_1_ohne_commit_signatur_bleibt_lesbar(eingaben, tmp_path):
    m = _bauen(eingaben)
    auf_schema_1(m)
    assert lade_manifest(_geschrieben(m, tmp_path / "m.json"))["schema"] == SCHEMA_1


def test_schema_1_mit_werkzeugen_von_schema_2_abgelehnt(eingaben, tmp_path):
    """/1 ist eingefroren: die erst mit /2 verlangten Werkzeuge sind dort unbekannt."""
    m = _bauen(eingaben)
    auf_schema_2(m)
    m["schema"] = SCHEMA_1
    del m["pruefungen"]["commit-signatur"]
    del m["berichte"]["npm-signaturen"]
    with pytest.raises(NachweisFehler, match=r"nicht vollständig.*unbekannt.*semgrep"):
        lade_manifest(_geschrieben(m, tmp_path / "m.json"))


def test_schema_2_ohne_commit_signatur_abgelehnt(eingaben, tmp_path):
    m = _bauen(eingaben)
    del m["pruefungen"]["commit-signatur"]
    with pytest.raises(NachweisFehler, match=r"Pflichtprüfungen.*commit-signatur"):
        lade_manifest(_geschrieben(m, tmp_path / "m.json"))


def test_schema_1_mit_liste_von_schema_2_geprueft(eingaben, tmp_path):
    """Jedes Schema wird gegen **seine** Listen geprüft: fehlt einem /1-Manifest eine
    /1-Pflichtprüfung, hilft es nicht, dass /2 mehr verlangt."""
    m = _bauen(eingaben)
    auf_schema_1(m)
    del m["pruefungen"]["scan"]
    with pytest.raises(NachweisFehler, match=r"Pflichtprüfungen.*scan"):
        lade_manifest(_geschrieben(m, tmp_path / "m.json"))


# --- Auslieferungsmenge je Schema: /3 mit import, /1 und /2 ohne ---------------------------------

def test_schema_2_ohne_import_bleibt_lesbar(eingaben, tmp_path):
    m = _bauen(eingaben)
    auf_schema_2(m)
    assert lade_manifest(_geschrieben(m, tmp_path / "m.json"))["schema"] == SCHEMA_2


def test_schema_3_ohne_ausgeliefertes_import_abgelehnt(eingaben, tmp_path):
    m = _bauen(eingaben)
    next(a for a in m["artefakte"] if a["name"] == "import")["ausgeliefert"] = False
    with pytest.raises(NachweisFehler, match="ausgeliefert"):
        lade_manifest(_geschrieben(m, tmp_path / "m.json"))
    with pytest.raises(NachweisFehler, match="erwartet import, mcp, server, web"):
        pruefe_artefakte(m, tmp_path)


@pytest.mark.parametrize("schema", [SCHEMA_1, SCHEMA_2])
def test_altes_schema_mit_ausgeliefertem_import_abgelehnt(eingaben, tmp_path, schema):
    """Ein /1- oder /2-Manifest darf import nicht als ausgeliefert führen — sonst nähme ein
    Prüfer einen ungeprüften Artefaktsatz unter altem Schema an."""
    m = _bauen(eingaben)
    auf_schema_1(m) if schema == SCHEMA_1 else auf_schema_2(m)
    next(a for a in m["artefakte"] if a["name"] == "import")["ausgeliefert"] = True
    with pytest.raises(NachweisFehler, match="ausgeliefert"):
        lade_manifest(_geschrieben(m, tmp_path / "m.json"))
    with pytest.raises(NachweisFehler, match="erwartet mcp, server, web"):
        pruefe_artefakte(m, tmp_path)


def test_unbekanntes_schema_hat_keine_auslieferungsmenge():
    with pytest.raises(NachweisFehler, match="unbekanntes Schema"):
        manifest.ausgeliefert_fuer({"schema": "scanview-release-manifest/9"})
