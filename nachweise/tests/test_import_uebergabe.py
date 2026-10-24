"""Root-Übergabe `deploy/platform/scanview-import-uebergabe` mit dem echten Freischalt-Skript.

Ende zu Ende als root im Wegwerf-Container (gehärtet wie `test_paket_freischalten.py`, dazu
`SETUID`/`SETGID` für das echte `runuser`): Der Worker (uid 993) hat geprüfte Pakete in die
Übergabe gelegt, das Übergabe-Skript kopiert sie als scanview-uebergabe (uid 995, nur in
scanview-deploy) in den Eingang und ruft das **unveränderte** `scanview-paket-freischalten` auf.
Geprüft wird: nur gültige projectIds erreichen das Freischalt-Skript, Punkt-Namen, Symlinks und
Dateien bleiben unbeachtet, jede Antwort steht in den Meldungen, root liest keine Dateiinhalte.

Das Kopierprogramm ist das Modul `scanview_worker.uebergabe` aus `server/src` (unter dem festen
Pfad /opt/scanview/server/.venv/bin/scanview-uebergabe eingehängt) — der echte Code, keine
Attrappe.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scanview_nachweise import deploy_probe
from scanview_nachweise.werkzeuge import lade_werkzeuge

REPO_WURZEL = Path(__file__).resolve().parents[2]
PLATFORM = REPO_WURZEL / "deploy" / "platform"
FREISCHALTEN = PLATFORM / "scanview-paket-freischalten"
UEBERGABE = PLATFORM / "scanview-import-uebergabe"
SERVER_SRC = REPO_WURZEL / "server" / "src"
WERKZEUGE_JSON = REPO_WURZEL / "nachweise" / "werkzeuge.json"

KONTEN = ("root:x:0:0:root:/root:/bin/bash\n"
          "nobody:x:65534:65534:nobody:/nonexistent:/usr/sbin/nologin\n"
          "scanview:x:990:990::/nonexistent:/usr/sbin/nologin\n"
          "caddy:x:991:991::/nonexistent:/usr/sbin/nologin\n"
          "scanview-import:x:993:993::/var/lib/scanview-import:/usr/sbin/nologin\n"
          "scanview-uebergabe:x:995:995::/nonexistent:/usr/sbin/nologin\n")
GRUPPEN = ("root:x:0:\nnogroup:x:65534:\nscanview:x:990:scanview-import\ncaddy:x:991:\n"
           "scanview-deploy:x:992:scanview-uebergabe\nscanview-import:x:993:\n"
           "scanview-uebergabe:x:995:\n")
#: Protokolliert jeden Aufruf und reicht ihn an das echte, unveränderte Skript weiter.
FREISCHALTEN_HUELLE = ("#!/bin/bash\nprintf 'freischalten %s\\n' \"$*\" >>/tmp/aufrufe\n"
                       "exec /probe/scanview-paket-freischalten \"$@\"\n")
KOPIERER = ("#!/bin/sh\nprintf 'kopierer uid=%s %s\\n' \"$(id -u)\" \"$*\" >>/tmp/aufrufe\n"
            "exec /usr/local/bin/python3 -I -c 'import sys; sys.path.insert(0, \"/probe/src\"); "
            "from scanview_worker.uebergabe import main; sys.exit(main())' \"$@\"\n")
SYSTEMCTL = "#!/bin/sh\nprintf 'systemctl %s\\n' \"$*\" >>/tmp/aufrufe\n"
PYTHON_SHIM = "#!/bin/sh\nexec /usr/local/bin/python3 \"$@\"\n"

TREIBER = r'''
import json, os, stat, subprocess, sys

GRUND = """
set -eu
install -m 0666 /dev/null /tmp/aufrufe
install -d -o root -g scanview -m 0750 /srv/scanview/data
install -d -o root -g scanview-deploy -m 1770 /srv/scanview/eingang
install -d -o 993 -g 995 -m 2750 /srv/scanview/uebergabe
install -d -o 995 -g 993 -m 2750 /srv/scanview/uebergabe-meldungen
paket() {  # paket <name> <projectId>: geprüftes Paket des Workers in der Übergabe
    d=/srv/scanview/uebergabe/$1
    install -d -o 993 -g 995 -m 2750 "$d" "$d/layers"
    printf '{"schemaVersion": 2, "projectId": "%s"}\n' "$2" >"$d/project.json"
    printf 'splat\n' >"$d/layers/model.spz"
    chown 993:995 "$d/project.json" "$d/layers/model.spz"
    chmod 0640 "$d/project.json" "$d/layers/model.spz"
}
"""
subprocess.run(["bash", "-c", GRUND + sys.argv[1]], check=True)
r = subprocess.run(["/usr/local/sbin/scanview-import-uebergabe"], capture_output=True, text=True,
                   env={"PATH": "/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"})
zustand = {}
for wurzel, ordner, dateien in os.walk("/srv/scanview"):
    for name in ordner + dateien:
        p = os.path.join(wurzel, name)
        s = os.lstat(p)
        typ = "l" if stat.S_ISLNK(s.st_mode) else "d" if stat.S_ISDIR(s.st_mode) else "f"
        zustand[p] = [typ, s.st_uid, s.st_gid, oct(stat.S_IMODE(s.st_mode))]
meldungen = {}
for name in os.listdir("/srv/scanview/uebergabe-meldungen"):
    with open(os.path.join("/srv/scanview/uebergabe-meldungen", name)) as f:
        meldungen[name] = f.read().strip()
with open("/tmp/aufrufe") as f:
    aufrufe = f.read().splitlines()
print(json.dumps({"rc": r.returncode, "stdout": r.stdout, "stderr": r.stderr,
                  "zustand": zustand, "meldungen": meldungen, "aufrufe": aufrufe}))
'''


def _image() -> str:
    return lade_werkzeuge(WERKZEUGE_JSON)["deploy-probe"].image


def _probe_da() -> bool:
    if shutil.which("docker") is None or deploy_probe.bereit() is None:
        return False
    try:
        image = _image()
    except KeyError:
        return False
    return subprocess.run(["docker", "image", "inspect", image], capture_output=True,
                          check=False).returncode == 0


probe_da = pytest.mark.skipif(not _probe_da(),
                              reason="docker, deploy-probe-Image oder setfacl/getfacl fehlen")


@dataclass
class Lauf:
    rc: int
    stdout: str
    stderr: str
    zustand: dict[str, Any]
    meldungen: dict[str, str]
    aufrufe: list[str]


def _probe(tmp_path: Path, vorbereitung: str) -> Lauf:
    acl_bin = deploy_probe.bereit()
    assert acl_bin is not None
    dateien = {"passwd": KONTEN, "group": GRUPPEN, "treiber.py": TREIBER,
               "freischalten": FREISCHALTEN_HUELLE, "kopierer": KOPIERER,
               "systemctl": SYSTEMCTL, "python3": PYTHON_SHIM}
    for name, inhalt in dateien.items():
        (tmp_path / name).write_text(inhalt, encoding="utf-8")
        (tmp_path / name).chmod(0o755)
    assert (SERVER_SRC / "scanview_worker" / "uebergabe.py").is_file()
    befehl = ["docker", "run", "--rm", "--pull", "never", "--network", "none", "--read-only",
              "--cap-drop", "ALL", "--cap-add", "CHOWN", "--cap-add", "FOWNER",
              "--cap-add", "DAC_OVERRIDE", "--cap-add", "SETUID", "--cap-add", "SETGID",
              "--cap-add", "FSETID", "--security-opt", "no-new-privileges", "--user", "0:0",
              "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,mode=1777",
              "--tmpfs", "/srv/scanview:rw,nosuid,nodev,mode=0755,size=20g"]
    einhaengen = {
        UEBERGABE: "/usr/local/sbin/scanview-import-uebergabe",
        tmp_path / "freischalten": "/usr/local/sbin/scanview-paket-freischalten",
        FREISCHALTEN: "/probe/scanview-paket-freischalten",
        tmp_path / "kopierer": "/opt/scanview/server/.venv/bin/scanview-uebergabe",
        SERVER_SRC: "/probe/src",
        tmp_path / "systemctl": "/usr/bin/systemctl",
        tmp_path / "python3": "/usr/bin/python3",
        tmp_path / "passwd": "/etc/passwd",
        tmp_path / "group": "/etc/group",
        tmp_path / "treiber.py": "/probe/treiber.py",
        acl_bin / "setfacl": "/usr/bin/setfacl",
        acl_bin / "getfacl": "/usr/bin/getfacl",
    }
    for quelle, ziel in einhaengen.items():
        befehl += ["--mount", f"type=bind,source={quelle},target={ziel},readonly"]
    ergebnis = subprocess.run([*befehl, _image(), "/usr/local/bin/python3", "/probe/treiber.py",
                               vorbereitung], capture_output=True, text=True, check=False,
                              stdin=subprocess.DEVNULL, timeout=120)
    assert ergebnis.returncode == 0, ergebnis.stdout + ergebnis.stderr
    d = json.loads(ergebnis.stdout)
    return Lauf(d["rc"], d["stdout"], d["stderr"], d["zustand"], d["meldungen"], d["aufrufe"])


@pytest.mark.docker
@probe_da
def test_gepruefte_pakete_gehen_ueber_das_freischalt_skript_live(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "paket neu-eins neu-eins\npaket neu-zwei neu-zwei\n")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.meldungen == {"neu-eins": "OK", "neu-zwei": "OK"}
    for pid in ("neu-eins", "neu-zwei"):
        assert lauf.zustand[f"/srv/scanview/data/{pid}"] == ["d", 0, 990, "0o750"]
        assert lauf.zustand[f"/srv/scanview/data/{pid}/layers/model.spz"] == ["f", 0, 990,
                                                                              "0o640"]
        assert f"/srv/scanview/eingang/{pid}" not in lauf.zustand
    # Kopiert hat der Übergabe-Nutzer (uid 995), nie root; das Freischalt-Skript lief als root
    # (ohne sudo) und hat den Katalog neu geladen.
    kopien = [a for a in lauf.aufrufe if a.startswith("kopierer ")]
    assert kopien and all(a.startswith("kopierer uid=995 ") for a in kopien), kopien
    assert lauf.aufrufe.count("systemctl reload scanview-server") == 2
    assert [a for a in lauf.aufrufe if a.startswith("freischalten ")] == [
        "freischalten pruefen neu-eins", "freischalten neu-eins",
        "freischalten pruefen neu-zwei", "freischalten neu-zwei"]


@pytest.mark.docker
@probe_da
def test_nur_gueltige_ids_erreichen_das_freischalt_skript(tmp_path: Path) -> None:
    """Punkt-Namen (Arbeitsordner), ungültige Namen, Symlinks und Dateien bleiben unbeachtet —
    das Freischalt-Skript sieht nur geprüfte projectIds."""
    lauf = _probe(tmp_path, "\n".join([
        "paket .arbeit-0123456789abcdef x",
        "paket Gross-Name gross-name",
        "paket 'mit leer' mit-leer",
        "ln -s /srv/scanview/data /srv/scanview/uebergabe/verweis",
        "install -o 993 -g 995 -m 0640 /dev/null /srv/scanview/uebergabe/datei",
        "paket ok-eins ok-eins",
    ]) + "\n")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.meldungen == {"ok-eins": "OK"}
    aufrufe = [a for a in lauf.aufrufe if a.startswith("freischalten ")]
    assert aufrufe == ["freischalten pruefen ok-eins", "freischalten ok-eins"], aufrufe
    assert set(p for p in lauf.zustand if p.startswith("/srv/scanview/data/")
               and p.count("/") == 4) == {"/srv/scanview/data/ok-eins"}


@pytest.mark.docker
@probe_da
def test_vorhandenes_projekt_wird_gemeldet_nicht_ueberschrieben(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "paket schon-da schon-da\n"
                            "install -d -o root -g scanview -m 0750 /srv/scanview/data/schon-da\n"
                            "echo alt >/srv/scanview/data/schon-da/alt\n")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.meldungen == {"schon-da": "VORHANDEN"}
    assert "/srv/scanview/data/schon-da/alt" in lauf.zustand
    assert "/srv/scanview/data/schon-da/project.json" not in lauf.zustand
    assert not [a for a in lauf.aufrufe if a.startswith("kopierer") and "kopieren" in a]
    assert [a for a in lauf.aufrufe if a.startswith("freischalten ")] == [
        "freischalten pruefen schon-da"]


@pytest.mark.docker
@probe_da
def test_beantwortetes_paket_wird_nicht_erneut_angefasst(tmp_path: Path) -> None:
    lauf = _probe(tmp_path, "paket fertig-eins fertig-eins\n"
                            "printf 'OK\\n' >/srv/scanview/uebergabe-meldungen/fertig-eins\n"
                            "chown 995:993 /srv/scanview/uebergabe-meldungen/fertig-eins\n")
    assert lauf.rc == 0, lauf.stderr
    assert not [a for a in lauf.aufrufe if a.startswith("freischalten ")]
    assert "/srv/scanview/data/fertig-eins" not in lauf.zustand


@pytest.mark.docker
@probe_da
def test_symlink_im_paket_wird_nicht_freigeschaltet(tmp_path: Path) -> None:
    """Ein Symlink im Paket (z. B. auf eine fremde Datei) kommt nicht in den Eingang; die
    Antwort nennt den Grund ohne Pfad (KOPIE)."""
    lauf = _probe(tmp_path, "paket mit-link mit-link\n"
                            "ln -s /etc/shadow /srv/scanview/uebergabe/mit-link/layers/x.spz\n"
                            "chown -h 993:995 /srv/scanview/uebergabe/mit-link/layers/x.spz\n")
    assert lauf.rc == 0, lauf.stderr
    assert lauf.meldungen == {"mit-link": "KOPIE"}
    assert "/srv/scanview/data/mit-link" not in lauf.zustand
    assert [a for a in lauf.aufrufe if a.startswith("freischalten ")] == [
        "freischalten pruefen mit-link"]


def test_ohne_root_verweigert(tmp_path: Path) -> None:
    r = subprocess.run(["bash", str(UEBERGABE)], capture_output=True, text=True, check=False)
    assert r.returncode != 0
    r = subprocess.run(["bash", str(UEBERGABE), "neu-eins"], capture_output=True, text=True,
                       check=False)
    assert r.returncode == 2
