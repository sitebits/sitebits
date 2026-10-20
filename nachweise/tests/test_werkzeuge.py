"""Gepinnte Werkzeuge und gehärteter Container-Lauf: Digest-Pflicht, Härtungsflags,
Mount-Rechte, Fehlerpfad fail-closed."""

import ast
import json
import re
import sys
import tempfile
import tomllib
from pathlib import Path

import pytest

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.werkzeuge import (
    CAP_AUSNAHMEN,
    HOST_NETZ,
    Werkzeug,
    ausfuehren,
    docker_befehl,
    lade_werkzeuge,
)

WURZEL = Path(__file__).resolve().parents[2]
ECHTE_WERKZEUGE = WURZEL / "nachweise" / "werkzeuge.json"
DIGEST = "sha256:" + "a" * 64
HAERTUNG = ["--rm", "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges"]


def _schreibe(tmp_path: Path, inhalt: object) -> Path:
    pfad = tmp_path / "werkzeuge.json"
    pfad.write_text(json.dumps(inhalt), encoding="utf-8")
    return pfad


def _werkzeug() -> Werkzeug:
    return Werkzeug(name="syft", image=f"anchore/syft@{DIGEST}", version="1.0.0",
                    werkzeugordner=None)


def _enthaelt_folge(liste: list[str], folge: list[str]) -> bool:
    return any(liste[i:i + len(folge)] == folge for i in range(len(liste) - len(folge) + 1))


# --- lade_werkzeuge ---------------------------------------------------------------------------

def test_lade_gueltig(tmp_path: Path) -> None:
    pfad = _schreibe(tmp_path, {
        "syft": {"image": f"anchore/syft@{DIGEST}", "version": "1.0.0"},
        "cyclonedx-npm": {"image": f"node@{DIGEST}", "version": "6.0.1",
                          "werkzeugordner": "werkzeuge/cyclonedx-npm"},
    })
    w = lade_werkzeuge(pfad)
    assert w["syft"] == Werkzeug("syft", f"anchore/syft@{DIGEST}", "1.0.0", None)
    assert w["cyclonedx-npm"].werkzeugordner == "werkzeuge/cyclonedx-npm"


@pytest.mark.parametrize("image", [
    "anchore/syft:v1.0.0",                        # nur Tag
    "anchore/syft",                               # gar nichts
    "anchore/syft@sha256:abc",                    # Digest zu kurz
    f"anchore/syft@{DIGEST.upper()}",             # Großbuchstaben
    f"anchore/syft@{DIGEST}x",                    # Anhang nach dem Digest
    "anchore/syft@sha512:" + "a" * 128,           # falscher Algorithmus
])
def test_kein_digest_ist_fehler(tmp_path: Path, image: str) -> None:
    pfad = _schreibe(tmp_path, {"syft": {"image": image, "version": "1.0.0"}})
    with pytest.raises(NachweisFehler) as e:
        lade_werkzeuge(pfad)
    assert e.value.regel == "werkzeuge"
    assert "syft" in e.value.text


def test_tag_plus_digest_erlaubt(tmp_path: Path) -> None:
    pfad = _schreibe(tmp_path, {"syft": {"image": f"anchore/syft:v1.0.0@{DIGEST}",
                                         "version": "1.0.0"}})
    assert lade_werkzeuge(pfad)["syft"].image.endswith(DIGEST)


@pytest.mark.parametrize("eintrag", [
    {"image": f"a@{DIGEST}"},                                  # version fehlt
    {"version": "1"},                                          # image fehlt
    {"image": f"a@{DIGEST}", "version": ""},                   # leere Version
    {"image": f"a@{DIGEST}", "version": 1},                    # falscher Typ
    {"image": f"a@{DIGEST}", "version": "1", "extra": "x"},    # unbekanntes Feld
    {"image": f"a@{DIGEST}", "version": "1", "tag": ""},       # leerer Tag
    {"image": f"a@{DIGEST}", "version": "1", "tag": "  "},     # Tag nur aus Leerraum
    {"image": f"a@{DIGEST}", "version": "1", "tag": 1},        # falscher Typ
    {"image": f"a@{DIGEST}", "version": "1", "tag": "v1@x"},   # Tag mit Digest-Zeichen
    {"image": f"a@{DIGEST}", "version": "1", "werkzeugordner": "/abs"},   # absolut
    {"image": f"a@{DIGEST}", "version": "1", "werkzeugordner": "../raus"},
    "kein-objekt",
])
def test_ungueltiger_eintrag(tmp_path: Path, eintrag: object) -> None:
    with pytest.raises(NachweisFehler, match="werkzeuge"):
        lade_werkzeuge(_schreibe(tmp_path, {"syft": eintrag}))


def test_datei_fehlt_oder_kaputt(tmp_path: Path) -> None:
    with pytest.raises(NachweisFehler, match="nicht lesbar"):
        lade_werkzeuge(tmp_path / "fehlt.json")
    kaputt = tmp_path / "kaputt.json"
    kaputt.write_text("{", encoding="utf-8")
    with pytest.raises(NachweisFehler, match="kein gültiges JSON"):
        lade_werkzeuge(kaputt)
    with pytest.raises(NachweisFehler, match="Objekt"):
        lade_werkzeuge(_schreibe(tmp_path, []))
    with pytest.raises(NachweisFehler, match="leer"):
        lade_werkzeuge(_schreibe(tmp_path, {}))


def test_tag_optional_wird_geladen(tmp_path: Path) -> None:
    pfad = _schreibe(tmp_path, {
        "syft": {"image": f"anchore/syft@{DIGEST}", "version": "1.0.0", "tag": "v1.0.0"},
        "trivy": {"image": f"aquasec/trivy@{DIGEST}", "version": "0.1.0"},
    })
    w = lade_werkzeuge(pfad)
    assert w["syft"].tag == "v1.0.0"
    assert w["trivy"].tag is None


#: Werkzeuge, deren Image-Tag die Werkzeugversion trägt und die Renovate deshalb über das Feld
#: `tag` aktualisiert (renovate.json, customManagers). Alle anderen: Basis-Images mit
#: `werkzeugordner` (Werkzeugversion ≠ Image-Version) und Images ohne versionsgenauen Tag.
_MIT_TAG = ("trivy", "syft", "cyclonedx-cli", "sbomqs", "gitleaks", "semgrep", "zap",
            "caddy-test", "playwright", "deploy-probe")


def test_echte_tags_passen_zur_version() -> None:
    """`tag` ist der Docker-Tag, aus dem der Digest stammt: Version, optional mit „v“ davor und
    Variantenzusatz („-alpine“, „-noble“) dahinter. Weicht er ab, aktualisiert Renovate die
    falsche Zeile oder keine."""
    w = lade_werkzeuge(ECHTE_WERKZEUGE)
    assert sorted(n for n, x in w.items() if x.tag is not None) == sorted(_MIT_TAG)
    for name in _MIT_TAG:
        werkzeug = w[name]
        assert werkzeug.werkzeugordner is None, name
        assert werkzeug.tag is not None
        assert re.fullmatch(rf"v?{re.escape(werkzeug.version)}(-[a-z]+)?", werkzeug.tag), name
    # Zeile im Quelltext: Digest, Version und Tag direkt nacheinander (Muster des Renovate-
    # Managers; sonst würde Renovate den Eintrag nicht erkennen).
    text = ECHTE_WERKZEUGE.read_text(encoding="utf-8")
    zeilen = re.findall(r'"image": "[^@"]+@sha256:[0-9a-f]{64}",\s*"version": "[^"]+",\s*'
                        r'"tag": "[^"]+"', text)
    assert len(zeilen) == len(_MIT_TAG)


def test_renovate_regex_erkennt_jeden_tag_eintrag() -> None:
    """Der Regex-Manager in renovate.json erkennt genau die Einträge mit `tag` (Name, Digest und
    Tag stimmen), nicht die ohne; Mindestalter und Vorschlagsbetrieb sind gesetzt."""
    konfig = json.loads((WURZEL / "renovate.json").read_text(encoding="utf-8"))
    assert konfig["minimumReleaseAge"] == "7 days"
    assert konfig["automerge"] is False and konfig["platformAutomerge"] is False
    assert konfig["internalChecksFilter"] == "strict"
    (manager,) = konfig["customManagers"]
    muster = re.compile(manager["matchStrings"][0].replace("(?<", "(?P<"))
    text = ECHTE_WERKZEUGE.read_text(encoding="utf-8")
    erkannt = {m["depName"]: (m["currentDigest"], m["currentValue"])
               for m in (t.groupdict() for t in muster.finditer(text))}
    erwartet = {w.image.split("@")[0]: (w.image.split("@")[1], w.tag)
                for w in lade_werkzeuge(ECHTE_WERKZEUGE).values() if w.tag is not None}
    assert erkannt == erwartet


def test_echte_werkzeuge_gueltig() -> None:
    w = lade_werkzeuge(ECHTE_WERKZEUGE)
    for pflicht in ("trivy", "syft", "cyclonedx-cli", "sbomqs", "gitleaks"):
        assert pflicht in w
    for name, werkzeug in w.items():
        if werkzeug.werkzeugordner is not None:
            ordner = ECHTE_WERKZEUGE.parent / werkzeug.werkzeugordner
            # Jeder Werkzeugordner trägt ein Lockfile mit Hashes (npm, pip --require-hashes, uv).
            locks = [ordner / "package-lock.json", ordner / "requirements.txt",
                     ordner / "uv.lock"]
            assert any(p.is_file() for p in locks), name


def _compose_caddy_digest() -> str:
    text = (WURZEL / "deploy" / "platform" / "docker-compose.caddy.yml").read_text("utf-8")
    treffer = re.findall(r"^\s*image:\s*caddy:[^@\s]+@(sha256:[0-9a-f]{64})\s*$", text, re.M)
    assert len(treffer) == 1
    return treffer[0]


def test_pruefwerkzeuge_teil4_gepinnt() -> None:
    """SAST (semgrep, bandit), Fuzzing (schemathesis, caddy-test), DAST (zap) und der Browser des
    Egress-Tests (playwright): eigene Images bzw. dasselbe uv-Image wie cyclonedx-py mit
    Werkzeugordner; Caddy wie die Compose-Datei."""
    w = lade_werkzeuge(ECHTE_WERKZEUGE)
    assert list(w)[-6:] == ["semgrep", "bandit", "schemathesis", "zap", "caddy-test", "playwright"]
    assert w["semgrep"].image.startswith("semgrep/semgrep@sha256:")
    assert w["zap"].image.startswith("zaproxy/zap-stable@sha256:")
    assert w["caddy-test"].image == f"caddy@{_compose_caddy_digest()}"
    for name in ("bandit", "schemathesis"):
        assert w[name].image == w["cyclonedx-py"].image
        assert w[name].werkzeugordner == f"werkzeuge/{name}"
    for name in ("semgrep", "zap", "caddy-test", "playwright"):
        assert w[name].werkzeugordner is None


@pytest.mark.parametrize("name", ["bandit", "schemathesis"])
def test_uv_werkzeugordner_lieferkette(name: str) -> None:
    """Exakter Pin = Version in werkzeuge.json, kein eigenes Paket, fester Stichtag (auch im
    Lock), jedes Paket des Locks nur als Wheel (no-build-package, drift-getestet)."""
    w = lade_werkzeuge(ECHTE_WERKZEUGE)[name]
    ordner = ECHTE_WERKZEUGE.parent / (w.werkzeugordner or "")
    projekt = tomllib.loads((ordner / "pyproject.toml").read_text("utf-8"))
    lock = tomllib.loads((ordner / "uv.lock").read_text("utf-8"))
    assert projekt["project"]["dependencies"] == [f"{name}=={w.version}"]
    uv = projekt["tool"]["uv"]
    assert uv["package"] is False
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T00:00:00Z", uv["exclude-newer"])
    assert lock["options"]["exclude-newer"] == uv["exclude-newer"]
    pakete = {p["name"] for p in lock["package"] if "virtual" not in p.get("source", {})}
    assert name in pakete
    assert set(uv["no-build-package"]) == pakete
    assert all(p.get("wheels") for p in lock["package"]
               if "virtual" not in p.get("source", {})), "Paket ohne Wheel im Lock"


# --- docker_befehl ----------------------------------------------------------------------------

def test_docker_befehl_haertung(tmp_path: Path) -> None:
    befehl = docker_befehl(_werkzeug(), ["scan", "dir:/quelle"], {tmp_path: "/quelle"},
                           uid=1234, gid=5678)
    assert befehl[:2] == ["docker", "run"]
    for flag in HAERTUNG:
        assert flag in befehl
    assert _enthaelt_folge(befehl, ["--network", "none"])
    assert _enthaelt_folge(befehl, ["--pull", "never"])
    assert _enthaelt_folge(befehl, ["--cap-drop", "ALL"])
    assert _enthaelt_folge(befehl, ["--security-opt", "no-new-privileges"])
    assert _enthaelt_folge(befehl, ["--user", "1234:5678"])
    i = befehl.index("--tmpfs")
    assert befehl[i + 1].startswith("/tmp:")
    assert "mode=1777" in befehl[i + 1] and "noexec" in befehl[i + 1]
    assert _enthaelt_folge(befehl, ["-v", f"{tmp_path}:/quelle:ro"])
    # Image steht direkt vor den Werkzeug-Argumenten, danach nichts mehr von docker.
    assert befehl[-3:] == [f"anchore/syft@{DIGEST}", "scan", "dir:/quelle"]


def test_docker_befehl_schreibend_nur_rw_fuer_ziel(tmp_path: Path) -> None:
    quelle = tmp_path / "repo"
    quelle.mkdir()
    datei = tmp_path / "eingabe.json"
    datei.write_text("{}", encoding="utf-8")
    aus = tmp_path / "aus"
    aus.mkdir()
    befehl = docker_befehl(_werkzeug(), [], {quelle: "/quelle", datei: "/eingabe.json"},
                           {aus: "/aus"}, uid=1, gid=1)
    mounts = [befehl[i + 1] for i, a in enumerate(befehl) if a == "-v"]
    assert mounts == [f"{quelle}:/quelle:ro", f"{datei}:/eingabe.json:ro", f"{aus}:/aus:rw"]


def test_fehlender_eingabepfad_ist_fehler(tmp_path: Path) -> None:
    # docker würde einen fehlenden Host-Pfad still als leeres Verzeichnis anlegen — ein
    # Scan darüber endet mit Exit 0 und leerem Ergebnis. Deshalb vorher abbrechen.
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(_werkzeug(), [], {tmp_path / "fehlt": "/quelle"}, uid=1, gid=1)
    assert e.value.regel == "werkzeug-mount"
    assert "existiert nicht" in e.value.text
    assert not (tmp_path / "fehlt").exists()


def test_schreibziel_muss_verzeichnis_sein(tmp_path: Path) -> None:
    datei = tmp_path / "datei"
    datei.write_text("x", encoding="utf-8")
    for ziel in (datei, tmp_path / "fehlt"):
        with pytest.raises(NachweisFehler) as e:
            docker_befehl(_werkzeug(), [], {}, {ziel: "/aus"}, uid=1, gid=1)
        assert e.value.regel == "werkzeug-mount"


def test_docker_befehl_netz_nur_auf_wunsch() -> None:
    mit_netz = docker_befehl(_werkzeug(), [], {}, netz=True, uid=1, gid=1)
    assert "--network" not in mit_netz
    # Die übrige Härtung bleibt auch mit Netz bestehen.
    for flag in ["--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges"]:
        assert flag in mit_netz
    assert _enthaelt_folge(mit_netz, ["--pull", "never"])
    ohne = docker_befehl(_werkzeug(), [], {}, uid=1, gid=1)
    assert _enthaelt_folge(ohne, ["--network", "none"])


def test_docker_befehl_standard_uid_gid(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("os.getuid", lambda: 4321)
    monkeypatch.setattr("os.getgid", lambda: 8765)
    befehl = docker_befehl(_werkzeug(), [], {})
    assert _enthaelt_folge(befehl, ["--user", "4321:8765"])


def test_root_nutzer_verweigert(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(_werkzeug(), [], {}, uid=0, gid=1)
    assert e.value.regel == "werkzeug-nutzer"
    # Auch der Standard (aufrufender Nutzer) darf nicht root sein.
    monkeypatch.setattr("os.getuid", lambda: 0)
    monkeypatch.setattr("os.getgid", lambda: 0)
    with pytest.raises(NachweisFehler, match="root"):
        docker_befehl(_werkzeug(), [], {})


def test_pfade_mit_komma_und_leerzeichen_bleiben_ein_element(tmp_path: Path) -> None:
    host = tmp_path / "mit leer,zeichen"
    host.mkdir()
    befehl = docker_befehl(_werkzeug(), ["--datei", "/q/a b,c.json"], {host: "/q"},
                           uid=1, gid=1)
    assert f"{host}:/q:ro" in befehl
    assert "/q/a b,c.json" in befehl


@pytest.mark.parametrize("host,ziel", [
    (Path("relativ"), "/q"),                  # Host-Pfad nicht absolut
    (Path("/"), "relativ"),                   # Containerpfad nicht absolut
    (Path("/a:b"), "/q"),                     # Doppelpunkt würde -v zerlegen
    (Path("/"), "/q:rw"),                     # Rechte einschmuggeln
])
def test_ungueltige_mounts(host: Path, ziel: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(_werkzeug(), [], {host: ziel}, uid=1, gid=1)
    assert e.value.regel == "werkzeug-mount"


def test_docker_befehl_umgebung() -> None:
    befehl = docker_befehl(_werkzeug(), ["scan"], {},
                           umgebung={"SYFT_FILE_METADATA_DIGESTS": "sha256,sha512",
                                     "npm_execpath": "/w/npm cli.js"}, uid=1, gid=1)
    assert _enthaelt_folge(befehl, ["-e", "SYFT_FILE_METADATA_DIGESTS=sha256,sha512"])
    assert _enthaelt_folge(befehl, ["-e", "npm_execpath=/w/npm cli.js"])
    # Umgebung gehört zu den docker-Optionen, also vor das Image.
    assert befehl.index("npm_execpath=/w/npm cli.js") < befehl.index(f"anchore/syft@{DIGEST}")
    assert _enthaelt_folge(befehl, ["-e", "HOME=/tmp"])


@pytest.mark.parametrize("name", ["", "A=B", "1X", "MIT LEER", "-e", "HOME"])
def test_docker_befehl_umgebung_ungueltiger_name(name: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(_werkzeug(), [], {}, umgebung={name: "x"}, uid=1, gid=1)
    assert e.value.regel == "werkzeug-umgebung"


def test_docker_befehl_ohne_digest_verweigert() -> None:
    ohne = Werkzeug("syft", "anchore/syft:latest", "1", None)
    with pytest.raises(NachweisFehler, match="Digest"):
        docker_befehl(ohne, [], {}, uid=1, gid=1)


# --- Host-Netz, tmpfs, Laufzeit (Docker/Podman) -----------------------------------------------

TRIVY = Werkzeug("trivy", f"aquasec/trivy@{DIGEST}", "0.74.0", None)
SCHEMATHESIS = Werkzeug("schemathesis", f"ghcr.io/astral-sh/uv@{DIGEST}", "4.0.0",
                        "werkzeuge/schemathesis")
CADDY = Werkzeug("caddy-test", f"caddy@{DIGEST}", "2.11.0", None)


@pytest.fixture(autouse=True)
def _laufzeit_standard(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne ausdrückliche Wahl gilt docker — eine SCANVIEW_CONTAINER-Variable des Aufrufers darf
    die übrigen Tests nicht umschalten."""
    monkeypatch.delenv("SCANVIEW_CONTAINER", raising=False)


def test_host_netz_allowlist() -> None:
    assert HOST_NETZ == frozenset({"schemathesis", "caddy-test", "playwright"})


def test_host_netz_fuer_fremdes_werkzeug_verweigert() -> None:
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(TRIVY, [], {}, netz="host", uid=1, gid=1)
    assert e.value.regel == "werkzeug-netz"
    assert "trivy" in e.value.text


PLAYWRIGHT = Werkzeug("playwright", f"mcr.microsoft.com/playwright@{DIGEST}", "1.63.0", None)


@pytest.mark.parametrize("w", [SCHEMATHESIS, CADDY, PLAYWRIGHT])
def test_host_netz_fuer_allowlist(w: Werkzeug) -> None:
    befehl = docker_befehl(w, [], {}, netz="host", uid=1, gid=1)
    assert _enthaelt_folge(befehl, ["--network", "host"])
    assert not _enthaelt_folge(befehl, ["--network", "none"])
    assert befehl.count("--network") == 1


@pytest.mark.parametrize("netz", ["bridge", "none", "", 1, 0, None])
def test_netz_unbekannter_wert_verweigert(netz: object) -> None:
    # "host" ist truthy und 1 == True: nur echte bool-Werte und genau "host" sind erlaubt.
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(SCHEMATHESIS, [], {}, netz=netz, uid=1, gid=1)  # type: ignore[arg-type]
    assert e.value.regel == "werkzeug-netz"


def test_tmpfs_standardoptionen() -> None:
    befehl = docker_befehl(_werkzeug(), [], {}, tmpfs={"/zap/home": ""}, uid=1, gid=1)
    assert _enthaelt_folge(befehl, ["--tmpfs", "/zap/home:rw,noexec,nosuid,nodev,mode=1777"])
    # Das feste /tmp bleibt, das zusätzliche kommt dazu, beides vor dem Image.
    assert _enthaelt_folge(befehl, ["--tmpfs", "/tmp:rw,noexec,nosuid,nodev,mode=1777"])
    assert befehl.index("/zap/home:rw,noexec,nosuid,nodev,mode=1777") < befehl.index(
        f"anchore/syft@{DIGEST}")


def test_tmpfs_eigene_optionen() -> None:
    befehl = docker_befehl(CADDY, [], {}, tmpfs={"/data": "rw,noexec,nosuid,nodev,size=64m",
                                                  "/config": ""}, uid=1, gid=1)
    assert _enthaelt_folge(befehl, ["--tmpfs", "/data:rw,noexec,nosuid,nodev,size=64m"])
    assert _enthaelt_folge(befehl, ["--tmpfs", "/config:rw,noexec,nosuid,nodev,mode=1777"])


@pytest.mark.parametrize("pfad,optionen", [
    ("relativ", ""),                                  # nicht absolut
    ("/a:b", ""),                                     # Doppelpunkt zerlegte --tmpfs
    ("/tmp", ""),                                     # überdeckte das gehärtete /tmp (HOME)
    ("/tmp/x", ""),                                   # unter HOME
    ("/", ""),                                        # Wurzel überdecken
    ("/var/../tmp", ""),                              # Umweg
    ("/.", ""),                                       # nur Punkt (IndexError vor dem Fix)
    ("/a/./b", ""),                                   # Punkt-Segment (PurePosixPath normalisiert)
    ("/./tmp", ""),                                   # Punkt-Umweg zu /tmp
    ("/..", ""),                                      # nur zwei Punkte
    ("/a//b", ""),                                    # leeres Segment
    ("/a/", ""),                                      # leeres Schlusssegment
    ("/daten", "rw,nosuid,nodev"),                    # ohne noexec
    ("/daten", "rw,noexec,nodev"),                    # ohne nosuid
    ("/daten", "rw,noexec,nosuid"),                   # ohne nodev
    ("/daten", "rw,noexec,nosuid,nodev,exec"),        # noexec wieder aufgehoben
    ("/daten", "rw,noexec,nosuid,nodev,suid"),        # nosuid wieder aufgehoben
    ("/daten", "rw,noexec,nosuid,nodev,dev"),         # nodev wieder aufgehoben
    ("/daten", "rw,noexec,nosuid,nodev,mode=1777 x"),  # Leerzeichen
    ("/daten", "rw,noexec,nosuid,nodev:/x"),          # Doppelpunkt in den Optionen
])
def test_tmpfs_ungueltig(pfad: str, optionen: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(_werkzeug(), [], {}, tmpfs={pfad: optionen}, uid=1, gid=1)
    assert e.value.regel == "werkzeug-mount"


def test_name_und_abgesetzt() -> None:
    befehl = docker_befehl(CADDY, [], {}, netz="host", uid=1, gid=1,
                           name="scanview-fuzzing-42", abgesetzt=True)
    assert befehl[:6] == ["docker", "run", "--rm", "-d", "--name", "scanview-fuzzing-42"]
    ohne = docker_befehl(CADDY, [], {}, uid=1, gid=1)
    assert "-d" not in ohne and "--name" not in ohne


@pytest.mark.parametrize("name", ["", "-x", "Gross", "a b", "a/b", "a:b", "x" * 64])
def test_name_ungueltig(name: str) -> None:
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(CADDY, [], {}, uid=1, gid=1, name=name)
    assert e.value.regel == "werkzeug-name"


def test_podman_laufzeit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SCANVIEW_CONTAINER", "podman")
    befehl = docker_befehl(TRIVY, ["--version"], {}, uid=1, gid=1)
    assert befehl[:2] == ["podman", "run"]
    assert "--userns=keep-id" in befehl
    # Podman legt bei --read-only sonst still beschreibbare tmpfs an (/run, /var/tmp, /dev/shm).
    assert "--read-only-tmpfs=false" in befehl
    assert befehl[-2:] == [f"docker.io/aquasec/trivy@{DIGEST}", "--version"]


def test_docker_ist_standard_und_ausdruecklich(monkeypatch: pytest.MonkeyPatch) -> None:
    assert docker_befehl(TRIVY, [], {}, uid=1, gid=1)[0] == "docker"
    monkeypatch.setenv("SCANVIEW_CONTAINER", "docker")
    befehl = docker_befehl(TRIVY, [], {}, uid=1, gid=1)
    assert befehl[0] == "docker"
    assert "--userns=keep-id" not in befehl
    assert befehl[-1] == f"aquasec/trivy@{DIGEST}"


@pytest.mark.parametrize("wert", ["lxc", "", "Docker", "podman ", "/usr/bin/podman"])
def test_unbekannte_laufzeit_verweigert(monkeypatch: pytest.MonkeyPatch, wert: str) -> None:
    monkeypatch.setenv("SCANVIEW_CONTAINER", wert)
    with pytest.raises(NachweisFehler) as e:
        docker_befehl(TRIVY, [], {}, uid=1, gid=1)
    assert e.value.regel == "werkzeug-laufzeit"


@pytest.mark.parametrize("image,voll", [
    (f"node@{DIGEST}", f"docker.io/library/node@{DIGEST}"),
    (f"caddy:2.11-alpine@{DIGEST}", f"docker.io/library/caddy:2.11-alpine@{DIGEST}"),
    (f"aquasec/trivy@{DIGEST}", f"docker.io/aquasec/trivy@{DIGEST}"),
    (f"semgrep/semgrep@{DIGEST}", f"docker.io/semgrep/semgrep@{DIGEST}"),
    (f"ghcr.io/astral-sh/uv@{DIGEST}", f"ghcr.io/astral-sh/uv@{DIGEST}"),
    (f"docker.io/library/node@{DIGEST}", f"docker.io/library/node@{DIGEST}"),
    (f"localhost/x@{DIGEST}", f"localhost/x@{DIGEST}"),
    (f"localhost:5000/x@{DIGEST}", f"localhost:5000/x@{DIGEST}"),
    (f"registry:5000/org/x@{DIGEST}", f"registry:5000/org/x@{DIGEST}"),
])
def test_podman_voll_qualifizierte_namen(monkeypatch: pytest.MonkeyPatch, image: str,
                                         voll: str) -> None:
    monkeypatch.setenv("SCANVIEW_CONTAINER", "podman")
    w = Werkzeug("x", image, "1", None)
    assert docker_befehl(w, [], {}, uid=1, gid=1)[-1] == voll


def test_cap_ausnahme_nur_fuer_caddy() -> None:
    """Das Caddy-Binary trägt die File-Capability cap_net_bind_service=ep; ohne sie in der
    Bounding-Menge verweigert der Kernel exec (Probe: „exec /usr/bin/caddy: operation not
    permitted“). Genau diese eine Ausnahme, fest je Werkzeug, nicht vom Aufrufer wählbar."""
    assert dict(CAP_AUSNAHMEN) == {"caddy-test": ("NET_BIND_SERVICE",)}
    befehl = docker_befehl(CADDY, [], {}, netz="host", uid=1, gid=1)
    assert befehl.count("--cap-add") == 1
    i = befehl.index("--cap-drop")
    assert befehl[i:i + 4] == ["--cap-drop", "ALL", "--cap-add", "NET_BIND_SERVICE"]
    for w in (TRIVY, SCHEMATHESIS, _werkzeug()):
        assert "--cap-add" not in docker_befehl(w, [], {}, uid=1, gid=1)


VARIANTEN = [(laufzeit, w, netz) for laufzeit in ("docker", "podman")
             for w, netz in ((TRIVY, False), (TRIVY, True), (SCHEMATHESIS, "host"),
                             (CADDY, "host"))]


@pytest.mark.parametrize("laufzeit,w,netz", VARIANTEN)
def test_haertung_in_jeder_variante(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                    laufzeit: str, w: Werkzeug, netz: bool | str) -> None:
    monkeypatch.setenv("SCANVIEW_CONTAINER", laufzeit)
    aus = tmp_path / "aus"
    aus.mkdir()
    befehl = docker_befehl(w, ["x"], {tmp_path: "/q"}, {aus: "/aus"}, netz,  # type: ignore[arg-type]
                           tmpfs={"/daten": ""}, uid=1234, gid=5678)
    assert befehl[:2] == [laufzeit, "run"]
    for folge in (["--rm"], ["--pull", "never"], ["--read-only"], ["--cap-drop", "ALL"],
                  ["--security-opt", "no-new-privileges"], ["--user", "1234:5678"],
                  ["--tmpfs", "/tmp:rw,noexec,nosuid,nodev,mode=1777"], ["-e", "HOME=/tmp"],
                  ["-v", f"{tmp_path}:/q:ro"], ["-v", f"{aus}:/aus:rw"]):
        assert _enthaelt_folge(befehl, folge), (laufzeit, netz, folge)
    assert befehl.count("--cap-add") == len(CAP_AUSNAHMEN.get(w.name, ()))
    erwartet_netz = {False: "none", True: None, "host": "host"}[netz]
    if erwartet_netz is None:
        assert "--network" not in befehl
    else:
        assert _enthaelt_folge(befehl, ["--network", erwartet_netz])
    # Alle Optionen stehen vor dem Image, danach nur die Werkzeug-Argumente.
    assert befehl[-1] == "x"
    assert befehl[-2].endswith(w.image)


# --- ausfuehren -------------------------------------------------------------------------------

def test_ausfuehren_erfolg() -> None:
    r = ausfuehren([sys.executable, "-c", "print('ok')"], "probe")
    assert r.returncode == 0
    assert r.stdout.strip() == "ok"


def test_ausfuehren_fehler_mit_stderr_auszug() -> None:
    skript = "import sys; sys.stderr.write('x' * 5000 + 'LETZTE-ZEILE'); sys.exit(3)"
    with pytest.raises(NachweisFehler) as e:
        ausfuehren([sys.executable, "-c", skript], "sbom-erzeugen")
    assert e.value.regel == "sbom-erzeugen"
    assert "Exit 3" in e.value.text
    assert "LETZTE-ZEILE" in e.value.text
    assert len(e.value.text) < 2500


def test_ausfuehren_programm_fehlt() -> None:
    with pytest.raises(NachweisFehler) as e:
        ausfuehren(["/gibt/es/nicht"], "scan")
    assert e.value.regel == "scan"


# --- Umgebung -------------------------------------------------------------------------------------

_EINGESCHLEUST = {"VITE_STANDARDPAKET": "privat-kennung", "NODE_OPTIONS": "--require=/x.js",
                  "GIT_DIR": "/nirgends", "npm_config_registry": "http://fremd",
                  "UV_INDEX_URL": "http://fremd", "PYTEST_ADDOPTS": "--co"}


def _env_ausgabe(stdout: str) -> dict[str, str]:
    return dict(z.split("=", 1) for z in stdout.splitlines() if "=" in z)


def test_ausfuehren_filtert_aufruferumgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, wert in _EINGESCHLEUST.items():
        monkeypatch.setenv(name, wert)
    umgebung = _env_ausgabe(ausfuehren(["env"], "probe").stdout)
    assert not set(_EINGESCHLEUST) & set(umgebung), umgebung


@pytest.mark.parametrize("name", ["DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "LD_PRELOAD",
                                  "LD_LIBRARY_PATH", "PYTHONOPTIMIZE", "PYTHONWARNINGS",
                                  "NODE_EXTRA_CA_CERTS", "UV_PYTHON_INSTALL_DIR", "SSH_AUTH_SOCK",
                                  "SCANVIEW_BEGRIFFE", "GIT_SSH_COMMAND", "BELIEBIG_NEU"])
def test_umgebung_ist_positivliste(monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    """Nur ausdrücklich erlaubte Variablen erreichen einen Lauf — auch eine
    bisher unbekannte nicht."""
    from scanview_nachweise.werkzeuge import (
        UMGEBUNG_ERLAUBT,
        UMGEBUNG_FEST,
        gefilterte_umgebung,
    )
    monkeypatch.setenv(name, "x")
    monkeypatch.setenv("LC_TIME", "C")
    umgebung = gefilterte_umgebung()
    assert name not in umgebung
    assert set(umgebung) <= (set(UMGEBUNG_ERLAUBT) | set(UMGEBUNG_FEST)
                             | {k for k in umgebung if k.startswith("LC_")})
    assert umgebung["LC_TIME"] == "C" and "PATH" in umgebung and "HOME" in umgebung


def test_umgebung_ohne_ersatzobjekte(monkeypatch: pytest.MonkeyPatch) -> None:
    """Git-Ersatzobjekte (`git replace`) gelten in keinem Lauf der Kette — auch
    nicht, wenn der Aufrufer sie ausdrücklich einschaltet."""
    from scanview_nachweise.werkzeuge import gefilterte_umgebung
    monkeypatch.setenv("GIT_NO_REPLACE_OBJECTS", "0")
    assert gefilterte_umgebung()["GIT_NO_REPLACE_OBJECTS"] == "1"


def test_umgebung_ohne_grafts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Eine grafts-Datei (auch über GIT_GRAFT_FILE) verändert keine Elternbeziehung
    in einem Lauf der Kette."""
    from scanview_nachweise.werkzeuge import bau_umgebung, gefilterte_umgebung
    monkeypatch.setenv("GIT_GRAFT_FILE", str(tmp_path / "grafts"))
    assert gefilterte_umgebung()["GIT_GRAFT_FILE"] == "/dev/null"
    assert bau_umgebung(tmp_path / "u")["GIT_GRAFT_FILE"] == "/dev/null"


def test_bau_umgebung_uebernimmt_kein_uv_aus_der_umgebung(tmp_path: Path,
                                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_nachweise.werkzeuge import bau_umgebung
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", "/fremd/pythons")
    assert bau_umgebung(tmp_path / "u")["UV_PYTHON_INSTALL_DIR"] == str(
        Path.home() / ".local" / "share" / "uv" / "python")


@pytest.mark.parametrize("modul", ["manifest", "abschluss", "negativkontrollen"])
def test_ssh_keygen_mit_eigener_umgebung(modul: str) -> None:
    """ssh-keygen läuft nie mit der vollen Aufruferumgebung (env= an jedem Aufruf)."""
    import re
    quelle = (WURZEL / "nachweise" / "src" / "scanview_nachweise" / f"{modul}.py").read_text(
        encoding="utf-8")
    for aufruf in re.findall(r"subprocess\.run\(\[\"ssh-keygen\".*?\)\n", quelle, flags=re.S):
        assert "env=" in aufruf, aufruf


def test_ausfuehren_explizite_umgebung(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VITE_STANDARDPAKET", "privat-kennung")
    umgebung = _env_ausgabe(ausfuehren(["/usr/bin/env"], "probe",
                                       umgebung={"NUR": "das"}).stdout)
    assert umgebung == {"NUR": "das"}


def test_bau_umgebung_ist_positivliste(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from scanview_nachweise.werkzeuge import bau_umgebung
    for name, wert in _EINGESCHLEUST.items():
        monkeypatch.setenv(name, wert)
    umgebung = bau_umgebung(tmp_path / "u")
    assert not set(_EINGESCHLEUST) & set(umgebung)
    assert set(umgebung) == {"PATH", "HOME", "NPM_CONFIG_CACHE", "UV_CACHE_DIR",
                             "XDG_CACHE_HOME", "XDG_DATA_HOME", "UV_PYTHON_INSTALL_DIR", "LANG",
                             "TMPDIR", "NPM_CONFIG_USERCONFIG", "NPM_CONFIG_OMIT",
                             "XDG_CONFIG_HOME", "UV_PYTHON_DOWNLOADS", "CI",
                             "GIT_NO_REPLACE_OBJECTS", "GIT_GRAFT_FILE", "GIT_NO_LAZY_FETCH",
                             "GIT_CONFIG_NOSYSTEM", "GIT_CONFIG_GLOBAL", "GIT_TERMINAL_PROMPT"}
    assert umgebung["HOME"].startswith(str(tmp_path))


def test_bau_umgebung_pinnt_npm_omit_leer(tmp_path: Path) -> None:
    """`NPM_CONFIG_OMIT` explizit leer statt dem `NODE_ENV`-abhängigen npm-Standard (`dev` bei
    `NODE_ENV=production`, sonst leer) — ein künftiger npm-Default-Wechsel oder ein durchsickerndes
    `NODE_ENV=production` ließe sonst devDependencies aus der Installation und damit
    stillschweigend aus der npm-Signaturprüfung fallen: nicht installierte Pakete werden gar
    nicht erst auditiert, `invalid`/`missing` zeigen das nie."""
    from scanview_nachweise.werkzeuge import bau_umgebung
    assert bau_umgebung(tmp_path / "u")["NPM_CONFIG_OMIT"] == ""


def test_mypy_ueber_das_ganze_teilprojekt() -> None:
    """`uv run mypy .` (Qualitätstor) ist grün: die installierten Werkzeuge unter werkzeuge/
    (gitignoriert, fremder Code) sind ausgenommen, Tests werden mitgeprüft."""
    import subprocess
    r = subprocess.run([sys.executable, "-m", "mypy", "."], cwd=WURZEL / "nachweise",
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stdout[-3000:]


# --- Ein zentraler git-Aufruf für die ganze Kette -------------------------------------------------

def test_git_konfig_fester_satz() -> None:
    from scanview_nachweise.werkzeuge import GIT_KONFIG, git_befehl
    assert dict(GIT_KONFIG) == {
        "core.hooksPath": "/dev/null", "core.fsmonitor": "false",
        "core.untrackedCache": "false", "core.commitGraph": "false", "core.pager": "cat",
        "core.sshCommand": "", "diff.external": "", "credential.helper": "",
        "log.showSignature": "false", "gpg.program": "/usr/bin/false",
        "gpg.openpgp.program": "/usr/bin/false", "gpg.x509.program": "/usr/bin/false",
        "gpg.ssh.program": "/usr/bin/ssh-keygen", "protocol.ext.allow": "never",
        "protocol.file.allow": "user", "submodule.recurse": "false"}
    befehl = git_befehl("status")
    assert befehl[0] == "git" and befehl[-1] == "status" and "-C" not in befehl
    assert befehl.count("-c") == len(GIT_KONFIG)


def test_git_umgebung_ohne_system_und_nutzerkonfiguration(monkeypatch) -> None:
    from scanview_nachweise.werkzeuge import bau_umgebung, gefilterte_umgebung
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/nutzer/x/.gitconfig")
    for umgebung in (gefilterte_umgebung(), bau_umgebung(Path(tempfile.mkdtemp()))):
        assert umgebung["GIT_CONFIG_NOSYSTEM"] == "1"
        assert umgebung["GIT_CONFIG_GLOBAL"] == "/dev/null"
        assert umgebung["GIT_TERMINAL_PROMPT"] == "0"


def _git_listen(baum: ast.AST) -> list[tuple[int, str]]:
    """(Zeile, umgebende Funktion) jeder Liste/jedes Tupels, das mit dem Literal "git" beginnt."""
    treffer: list[tuple[int, str]] = []

    def besuche(knoten: ast.AST, funktion: str) -> None:
        for kind in ast.iter_child_nodes(knoten):
            name = kind.name if isinstance(kind, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                else funktion
            if isinstance(kind, (ast.List, ast.Tuple)) and kind.elts and isinstance(
                    kind.elts[0], ast.Constant) and kind.elts[0].value == "git":
                treffer.append((kind.lineno, name))
            besuche(kind, name)
    besuche(baum, "")
    return treffer


def test_kein_git_aufruf_ausserhalb_des_zentralen_aufrufs() -> None:
    """Statisch (AST): im Quelltext der Kette beginnt nur in `werkzeuge.git_befehl` eine
    Befehlsliste mit "git" — jeder andere git-Aufruf geht über den festen Konfigurationssatz."""
    quellen = sorted((WURZEL / "nachweise" / "src" / "scanview_nachweise").glob("*.py"))
    assert len(quellen) > 10
    verstoesse = []
    for pfad in quellen:
        for zeile, funktion in _git_listen(ast.parse(pfad.read_text(encoding="utf-8"))):
            if not (pfad.name == "werkzeuge.py" and funktion == "git_befehl"):
                verstoesse.append(f"{pfad.name}:{zeile} ({funktion or 'Modul'})")
    assert verstoesse == []


def test_ast_pruefung_findet_git_listen() -> None:
    """Gegenprobe: die Prüfung erkennt Liste und Tupel mit "git" vorn."""
    baum = ast.parse('import subprocess\ndef f():\n    subprocess.run(["git", "log"])\n'
                     'G = ("git", "-C")\n')
    assert _git_listen(baum) == [(3, "f"), (4, "")]
