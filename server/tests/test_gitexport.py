"""Git-Export der Bearbeitungen (siehe ADR 0011).

Echtes `git` im Testlauf; jeder Git-Aufruf der Tests läuft mit derselben minimalen Umgebung wie
der Export (`gitexport._git_env`), damit die ~/.gitconfig des Entwicklers (Signieren, Hooks,
log-Formate) nichts verfälscht.
"""

from __future__ import annotations

import dataclasses
import fcntl
import json
import signal
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import cli, db, gitexport, inhalte, konten, verwaltung
from scanview_server.config import Settings

FREITEXT = "Tür „Nord“ – Schlüssel bei Frau Größe 🔑\nZweite Zeile\tmit Tab"  # noqa: RUF001
POI: dict[str, Any] = {"title": "Tür Nord", "category": "Zugang", "note": FREITEXT,
                       "position": [1.5, -2.25, 3.0], "station": "S001"}
STRECKE: dict[str, Any] = {"kind": "distance", "points": [[0, 0, 0], [3, 4, 0]]}


@pytest.fixture
def an(settings: Settings) -> Settings:
    return dataclasses.replace(settings, git_export=True)


@pytest.fixture
def conn(an: Settings) -> Iterator[sqlite3.Connection]:
    c = db.connect(an.db_path)
    db.migrate(c)
    yield c
    c.close()


def konto(conn: sqlite3.Connection, email: str, name: str) -> int:
    t = konten.einladung_anlegen(conn, email=email, name=name, grants={"ost": "bearbeiter"})
    return konten.einladung_einloesen(conn, t, lambda _c, _u: None).id


def git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", *args], cwd=repo, env=gitexport._git_env(repo.parent), check=True,
        capture_output=True, text=True,
    )
    return r.stdout


def commits(repo: Path) -> list[str]:
    return git(repo, "log", "--format=%H").split()


def erwartet(daten: Any) -> bytes:
    return (json.dumps(daten, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def test_poi_lebenszyklus_vier_commits_bytegenau(an: Settings, conn: sqlite3.Connection) -> None:
    uid = konto(conn, "a@a.de", "Anna Beispiel")
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=uid, revision=1, now=1000)
    geaendert = inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "Tür Süd"}, 1,
                                nutzer_id=uid, now=2000)
    inhalte.loeschen(conn, "pois", "ost", "P-1", 2, nutzer_id=uid, now=3000)

    assert gitexport.exportieren(conn, an) == {"ost": 3}
    repo = an.git_dir / "ost"
    assert not (repo / "pois" / "P-1.json").exists()
    assert (repo / "README.md").is_file()
    assert len(commits(repo)) == 3
    # Der Zwischenstand nach dem Ändern: voller zusammengeführter Stand, bytegenau.
    zwischen = subprocess.run(
        ["git", "show", "HEAD~1:pois/P-1.json"], cwd=repo, env=gitexport._git_env(an.git_dir),
        check=True, capture_output=True,
    ).stdout
    assert zwischen == erwartet(geaendert.daten)
    assert geaendert.daten["note"] == FREITEXT and geaendert.daten["title"] == "Tür Süd"

    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=uid, revision=1, now=4000)
    assert gitexport.exportieren(conn, an) == {"ost": 1}
    assert len(commits(repo)) == 4
    assert (repo / "pois" / "P-1.json").read_bytes() == erwartet(POI)
    assert git(repo, "status", "--porcelain") == ""
    arten = git(repo, "log", "--reverse", "--format=%s").splitlines()
    assert arten == ["poi.angelegt P-1", "poi.geaendert P-1", "poi.geloescht P-1",
                     "poi.wiederhergestellt P-1"]
    assert git(repo, "log", "--reverse", "--format=%at").split() == [
        "1000", "2000", "3000", "4000"]
    # README nur einmal, im ersten Commit.
    assert git(repo, "log", "--format=%s", "--", "README.md").splitlines() == ["poi.angelegt P-1"]


def test_messung_und_meta(mit_paketen: TestClient, an: Settings) -> None:  # noqa: F811
    n, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    assert c.post("/api/v1/projekte/ost/messungen", headers=csrf(),
                  json={"id": "M-1", "daten": STRECKE}).status_code == 201
    assert c.patch("/api/v1/projekte/ost", headers=csrf(), json={
        "titel": "Fassade Ost", "beschreibung": "68 Standpunkte\nNordseite ä", "version": 0,
    }).status_code == 200
    conn = db.connect(an.db_path)
    try:
        assert gitexport.exportieren(conn, an) == {"ost": 2}
    finally:
        conn.close()
    repo = an.git_dir / "ost"
    assert (repo / "meta.json").read_bytes() == erwartet(
        {"titel": "Fassade Ost", "beschreibung": "68 Standpunkte\nNordseite ä"})
    m = json.loads((repo / "messungen" / "M-1.json").read_text(encoding="utf-8"))
    assert m["kind"] == "distance"
    conn = db.connect(an.db_path)
    letzte = conn.execute("SELECT MAX(id) FROM ereignisse").fetchone()[0]
    conn.close()
    assert git(repo, "log", "-1", "--format=%s%n%an <%ae>%n%b") == (
        f"meta.geaendert ost\nNutzer {n.id} <nutzer-{n.id}@scanview.invalid>\nKanal: web\n"
        f"Ereignis: {letzte}\n\n"
    )


def test_autor_pseudonym_und_nur_mit_einwilligung_klarname(
    an: Settings, conn: sqlite3.Connection,
) -> None:
    anna = konto(conn, "anna@firma.de", "Anna Beispiel")
    bert = konto(conn, "bert@firma.de", "Bert Muster")
    conn.execute("UPDATE users SET git_name_zeigen=1 WHERE id=?", (bert,))
    inhalte.anlegen(conn, "pois", "ost", "P-a", POI, nutzer_id=anna, revision=1, now=10,
                    kanal="api", token_name="Claude Desktop")
    inhalte.anlegen(conn, "pois", "ost", "P-b", POI, nutzer_id=bert, revision=1, now=20)
    gitexport.exportieren(conn, an)
    repo = an.git_dir / "ost"
    [a, b] = git(repo, "log", "--reverse", "--format=%an|%ae|%cn|%ce|%B%x00").split("\0")[:2]
    kopf, rumpf = a.strip().split("|poi.angelegt P-a", 1)
    assert kopf == (f"Nutzer {anna}|nutzer-{anna}@scanview.invalid|scanview|"
                    "scanview@scanview.invalid")
    # Befund B-TE-M2: Rumpf nur mit dem Kanal, ohne Token-Namen.
    assert rumpf.strip().splitlines()[0] == "Kanal: api"
    assert b.strip().startswith(f"Bert Muster|nutzer-{bert}@scanview.invalid|")
    assert "Claude Desktop" not in git(repo, "log", "--format=%B")
    alles = git(repo, "log", "--format=%an%ae%cn%ce%B")
    for geheim in ("Anna", "Beispiel", "anna@firma.de", "bert@firma.de", "firma.de",
                   "Claude Desktop", "Token"):
        assert geheim not in alles, geheim


def test_einwilligung_gilt_zur_exportzeit_und_anonymisieren_setzt_zurueck(
    an: Settings, conn: sqlite3.Connection,
) -> None:
    uid = konto(conn, "c@firma.de", "Carla <Chef>\nX")
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=uid, revision=1, now=10)
    # Einwilligung nach dem Ereignis, aber vor dem Export: zählt (Exportzeit).
    conn.execute("UPDATE users SET git_name_zeigen=1 WHERE id=?", (uid,))
    gitexport.exportieren(conn, an)
    repo = an.git_dir / "ost"
    # Spitze Klammern und Zeilenumbrüche fliegen raus — sie würden die Git-Identität zerlegen.
    assert git(repo, "log", "-1", "--format=%an <%ae>").strip() == (
        f"Carla Chef X <nutzer-{uid}@scanview.invalid>")
    verwaltung.anonymisieren(conn, uid, von=None, kanal="cli")
    inhalte.anlegen(conn, "pois", "ost", "P-2", POI, nutzer_id=None, revision=1, now=20)
    inhalte.aendern(conn, "pois", "ost", "P-1", {"title": "neu"}, 1, nutzer_id=uid, now=30)
    gitexport.exportieren(conn, an)
    assert git(repo, "log", "--format=%an <%ae>").splitlines()[:2] == [
        f"Nutzer {uid} <nutzer-{uid}@scanview.invalid>",
        "scanview <scanview@scanview.invalid>",
    ]


def test_zweiter_lauf_ohne_neues_und_stand(an: Settings, conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    assert gitexport.exportieren(conn, an) == {"ost": 1}
    assert gitexport.exportieren(conn, an) == {"ost": 0}
    repo = an.git_dir / "ost"
    assert len(commits(repo)) == 1
    letzte = conn.execute("SELECT MAX(id) FROM ereignisse").fetchone()[0]
    assert conn.execute(
        "SELECT letzte_ereignis_id FROM git_export_stand WHERE project_id='ost'",
    ).fetchone()[0] == letzte


def test_nur_ein_projekt(an: Settings, conn: sqlite3.Connection) -> None:
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    inhalte.anlegen(conn, "pois", "kirche", "P-1", POI, nutzer_id=None, revision=1)
    assert gitexport.exportieren(conn, an, projekt="kirche") == {"kirche": 1}
    assert not (an.git_dir / "ost").exists()
    assert gitexport.exportieren(conn, an) == {"ost": 1, "kirche": 0}


def test_fremde_id_wird_uebersprungen_und_gemeldet(
    an: Settings, conn: sqlite3.Connection, tmp_path: Path,
) -> None:
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    # Altbestand/Handeintrag: an inhalte vorbei, darum Roh-INSERT.
    for oid in ("../x", "../../../../ausbruch", "", "a" * 65):
        conn.execute(
            "INSERT INTO ereignisse (zeit, kanal, project_id, art, objekt_id, nachher)"
            " VALUES (1, 'web', 'ost', 'poi.angelegt', ?, '{\"title\":\"x\"}')", (oid,),
        )
    conn.execute(
        "INSERT INTO ereignisse (zeit, kanal, project_id, art, objekt_id, nachher)"
        " VALUES (1, 'web', 'ost', 'messung.angelegt', NULL, '{}')",
    )
    conn.execute(  # kaputter Inhalt (Handeintrag)
        "INSERT INTO ereignisse (zeit, kanal, project_id, art, objekt_id, nachher)"
        " VALUES (1, 'web', 'ost', 'poi.geaendert', 'P-1', '{kaputt')",
    )
    conn.execute(  # fremde Projekt-ID wird nie ein Verzeichnis
        "INSERT INTO ereignisse (zeit, kanal, project_id, art, objekt_id, nachher)"
        " VALUES (1, 'web', '../ost', 'poi.angelegt', 'P-9', '{}')",
    )
    vorher = sorted(p for p in tmp_path.rglob("*") if ".git" not in p.parts)
    ergebnis = gitexport.exportieren(conn, an)
    ids = [r[0] for r in conn.execute("SELECT id FROM ereignisse ORDER BY id").fetchall()]
    assert ergebnis == {"ost": 1, **{f"übersprungen:{i}": 1 for i in ids[1:]}}
    repo = an.git_dir / "ost"
    neu = {p for p in tmp_path.rglob("*") if ".git" not in p.parts} - set(vorher)
    erlaubt = (an.git_dir, an.git_dir / ".sperre")  # Verzeichnis selbst + Laufsperre
    assert all(p.is_relative_to(repo) or p in erlaubt for p in neu), neu
    assert sorted(p.name for p in (repo / "pois").iterdir()) == ["P-1.json"]
    assert not (repo / "messungen").exists()
    assert len(commits(repo)) == 1
    assert conn.execute(
        "SELECT letzte_ereignis_id FROM git_export_stand WHERE project_id='ost'",
    ).fetchone()[0] == ids[-2]
    # Stand rückt vor: zweiter Lauf meldet nichts erneut.
    assert gitexport.exportieren(conn, an) == {"ost": 0}


def test_konto_und_rechte_ereignisse_nie_im_repo(
    an: Settings, conn: sqlite3.Connection,
) -> None:
    uid = konto(conn, "d@firma.de", "Dora")  # einladung.*, konto.* ohne project_id
    verwaltung.recht_setzen(conn, uid, "ost", "betrachter", von=None, kanal="cli")
    verwaltung.sperren(conn, uid, von=None, kanal="cli")
    assert conn.execute(
        "SELECT COUNT(*) FROM ereignisse WHERE art LIKE 'recht.%' AND project_id='ost'",
    ).fetchone()[0] >= 1
    assert gitexport.exportieren(conn, an) == {"ost": 0}
    repo = an.git_dir / "ost"
    assert not repo.exists() or commits_oder_leer(repo) == []
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    assert gitexport.exportieren(conn, an) == {"ost": 1}
    assert git(repo, "log", "--format=%s").splitlines() == ["poi.angelegt P-1"]


def test_zweiter_lauf_wartet_auf_den_ersten(an: Settings, conn: sqlite3.Connection) -> None:
    """Handaufruf parallel zum Timer: kein doppelter Commit für dasselbe Ereignis."""
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    an.git_dir.mkdir(parents=True)
    ergebnisse: list[dict[str, int]] = []

    def lauf() -> None:
        c = db.connect(an.db_path)
        try:
            ergebnisse.append(gitexport.exportieren(c, an))
        finally:
            c.close()

    with open(an.git_dir / ".sperre", "a") as sperre:
        fcntl.flock(sperre, fcntl.LOCK_EX)
        t = threading.Thread(target=lauf)
        t.start()
        t.join(0.3)
        assert t.is_alive() and not ergebnisse  # wartet auf die Sperre
        assert gitexport._exportieren(conn, an, None, False, "git") == {"ost": 1}
    t.join(5)
    assert ergebnisse == [{"ost": 0}]
    assert len(commits(an.git_dir / "ost")) == 1


@pytest.mark.parametrize("fall", ["symlink_datei", "ordner_nach_git"])
def test_ziel_symlink_wird_uebersprungen(
    an: Settings, conn: sqlite3.Connection, tmp_path: Path, fall: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    assert gitexport.exportieren(conn, an) == {"ost": 1}
    repo = an.git_dir / "ost"
    if fall == "symlink_datei":  # zeigt ins Repo, aber auf eine andere Datei
        (repo / "pois" / "P-2.json").symlink_to(repo / "README.md")
        grund = "Ziel ist ein Symlink"
    else:  # Ordner zeigt in .git: Schreiben würde Git-Interna überschreiben
        (repo / "messungen").symlink_to(repo / ".git")
        grund = "Ziel liegt unter .git"
    readme = (repo / "README.md").read_bytes()
    git_config = (repo / ".git" / "config").read_bytes()
    inhalte.anlegen(conn, "pois", "ost", "P-2", POI, nutzer_id=None, revision=1)
    inhalte.anlegen(conn, "messungen", "ost", "config", STRECKE, nutzer_id=None, revision=1)
    ergebnis = gitexport.exportieren(conn, an)
    assert (repo / "README.md").read_bytes() == readme
    assert (repo / ".git" / "config").read_bytes() == git_config
    assert grund in caplog.text
    assert any(k.startswith(gitexport.UEBERSPRUNGEN) for k in ergebnis)


def commits_oder_leer(repo: Path) -> list[str]:
    r = subprocess.run(["git", "log", "--format=%H"], cwd=repo,
                       env=gitexport._git_env(repo.parent), capture_output=True, text=True)
    return r.stdout.split() if r.returncode == 0 else []


def test_fremde_git_konfiguration_wirkt_nicht(
    an: Settings, conn: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hooks/Signieren aus der Umgebung des Aufrufers dürfen den Export nicht beeinflussen."""
    heim = tmp_path / "heim"
    heim.mkdir()
    (heim / ".gitconfig").write_text(
        "[commit]\n\tgpgsign = true\n[user]\n\tsigningkey = gibtsnicht\n"
        "[core]\n\thooksPath = /gibtsnicht\n", encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(heim))
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Leck")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(heim / ".gitconfig"))
    inhalte.anlegen(conn, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    assert gitexport.exportieren(conn, an) == {"ost": 1}
    assert "Leck" not in git(an.git_dir / "ost", "log", "--format=%an")


# --- CLI ------------------------------------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch, s: Settings, **extra: str) -> None:
    monkeypatch.setenv("SCANVIEW_DATA", str(s.data_dir))
    monkeypatch.setenv("SCANVIEW_STATE", str(s.state_dir))
    monkeypatch.setenv("SCANVIEW_ORIGIN", "https://x.test")
    monkeypatch.setenv("SCANVIEW_RP_ID", "x.test")
    for k in ("SCANVIEW_GIT_EXPORT", "SCANVIEW_GIT_DIR", "SCANVIEW_GIT_REMOTE"):
        monkeypatch.delenv(k, raising=False)
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_cli_aus_ohne_wirkung(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch, settings)
    assert cli.main(["git-export", "--push"]) == 0
    assert "Git-Export ist aus (SCANVIEW_GIT_EXPORT=1)" in capsys.readouterr().out
    assert not settings.db_path.exists()
    assert not (settings.state_dir / "git").exists()


def test_cli_push_ohne_remote_exit_1(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _env(monkeypatch, settings, SCANVIEW_GIT_EXPORT="1")
    assert cli.main(["git-export", "--push"]) == 1
    assert "SCANVIEW_GIT_REMOTE" in capsys.readouterr().err
    assert not (settings.state_dir / "git").exists()


def test_cli_export_und_push_in_bare_repo(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    ziel = tmp_path / "remote"
    ziel.mkdir()
    subprocess.run(["git", "init", "-q", "--bare", str(ziel / "ost.git")], check=True,
                   env=gitexport._git_env(tmp_path))
    gitdir = tmp_path / "anderswo"
    _env(monkeypatch, settings, SCANVIEW_GIT_EXPORT="1", SCANVIEW_GIT_DIR=str(gitdir),
         SCANVIEW_GIT_REMOTE=str(ziel / "{projekt}.git"))
    c = db.connect(settings.db_path)
    db.migrate(c)
    inhalte.anlegen(c, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    inhalte.anlegen(c, "pois", "ost", "P-2", POI, nutzer_id=None, revision=1)
    c.close()
    assert cli.main(["git-export", "--push"]) == 0
    assert "ost: 2 neue Commits" in capsys.readouterr().out
    log = git(ziel / "ost.git", "log", "--format=%s", "main").splitlines()
    assert log == ["poi.angelegt P-2", "poi.angelegt P-1"]
    # Kein Remote-Eintrag im Arbeitsrepo (URL bleibt in der Umgebung, nicht in .git/config).
    assert git(gitdir / "ost", "remote").strip() == ""
    # Nichts Neues, trotzdem Push (holt einen früher gescheiterten Push nach) → Exit 0.
    assert cli.main(["git-export", "--push", "--projekt", "ost"]) == 0
    assert cli.main(["git-export", "--projekt", "../x"]) == 2


def test_cli_push_fehler_ohne_zugangsdaten(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    fehlt = tmp_path / "gibtsnicht"
    remote = f"file://bot:geheim123@localhost{fehlt}/{{projekt}}.git"
    _env(monkeypatch, settings, SCANVIEW_GIT_EXPORT="1", SCANVIEW_GIT_REMOTE=remote)
    c = db.connect(settings.db_path)
    db.migrate(c)
    inhalte.anlegen(c, "pois", "ost", "P-1", POI, nutzer_id=None, revision=1)
    c.close()
    assert cli.main(["git-export", "--push"]) == 1
    out = capsys.readouterr()
    assert "geheim123" not in out.err + out.out and "bot" not in out.err + out.out
    assert "Push" in out.err and "ost" in out.err


def test_url_bereinigt() -> None:
    s = gitexport.url_ohne_zugang
    assert s("https://bot:tok@forgejo.example/scan/ost.git") == "https://forgejo.example/scan/ost.git"
    assert s("https://tok@forgejo.example:3000/x.git") == "https://forgejo.example:3000/x.git"
    assert s("ssh://git@forgejo.example:2222/x.git") == "ssh://forgejo.example:2222/x.git"
    assert s("git@forgejo.example:scan/ost.git") == "forgejo.example:scan/ost.git"
    assert s("/srv/remote/ost.git") == "/srv/remote/ost.git"
    # Auch was Git selbst in stderr schreibt, wird geschwärzt (bekannte und fremde URL-Form).
    url = "https://bot:geheim@h.example/x.git"
    text = gitexport._schwaerzen(f"fatal: '{url}' und 'ssh://bot@h.example/y' kaputt", url)
    assert "geheim" not in text and "bot" not in text and "h.example/x.git" in text


def test_settings_git(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    s = Settings(data_dir=tmp_path, state_dir=tmp_path / "st", origin="o", rp_id="r",
                 rp_name="n")
    assert (s.git_export, s.git_dir, s.git_remote) == (False, tmp_path / "st" / "git", None)
    monkeypatch.setenv("SCANVIEW_DATA", str(tmp_path))
    monkeypatch.setenv("SCANVIEW_STATE", str(tmp_path))
    monkeypatch.setenv("SCANVIEW_ORIGIN", "https://x.test")
    monkeypatch.setenv("SCANVIEW_RP_ID", "x.test")
    monkeypatch.setenv("SCANVIEW_GIT_EXPORT", "1")
    monkeypatch.setenv("SCANVIEW_GIT_REMOTE", "ssh://git@h/scan/{projekt}.git")
    s = Settings.from_env()
    assert (s.git_export, s.git_dir) == (True, tmp_path / "git")
    assert s.git_remote == "ssh://git@h/scan/{projekt}.git"
    # Kaputte Vorlage: Settings bleiben ladbar (auch für `serve`) — geprüft wird erst beim Export.
    for kaputt in ("ssh://git@h/scan/alle.git", "ssh://h/{projekt}/{x}.git", "ssh://h/{"):
        monkeypatch.setenv("SCANVIEW_GIT_REMOTE", kaputt)
        assert Settings.from_env().git_remote == kaputt
        assert gitexport.remote_fehler(kaputt) == gitexport.REMOTE_UNGUELTIG
    assert gitexport.remote_fehler("ssh://git@h/scan/{projekt}.git") is None


@pytest.mark.parametrize("kaputt", ["ssh://geheim@h/alle.git", "ssh://h/{projekt}/{x}.git"])
def test_cli_kaputte_remote_vorlage(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    kaputt: str,
) -> None:
    _env(monkeypatch, settings, SCANVIEW_GIT_EXPORT="1", SCANVIEW_GIT_REMOTE=kaputt)
    apps: list[Any] = []
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kw: apps.append(app))
    alter = signal.getsignal(signal.SIGHUP)
    try:
        assert cli.main(["serve"]) == 0 and apps  # Webdienst startet trotzdem
    finally:
        signal.signal(signal.SIGHUP, alter)
    capsys.readouterr()
    assert cli.main(["git-export", "--push"]) == 1
    err = capsys.readouterr().err
    assert gitexport.REMOTE_UNGUELTIG in err and "geheim" not in err
    assert not (settings.state_dir / "git").exists()
    s = dataclasses.replace(settings, git_export=True, git_remote=kaputt)
    c = db.connect(s.db_path)
    try:
        with pytest.raises(gitexport.GitFehler, match="Platzhalter"):
            gitexport.exportieren(c, s, push=True)
    finally:
        c.close()
    assert not (settings.state_dir / "git").exists()  # vor der Sperre abgelehnt


def test_quellen_im_git_export(an: Settings, conn: sqlite3.Connection) -> None:
    source = {"source": "mesh", "layerId": "modell"}
    messung = {**STRECKE, "pointSources": [source, None]}
    poi = {**POI, "positionSource": source}
    inhalte.anlegen(conn, "messungen", "ost", "M-quellen", messung,
                    nutzer_id=None, revision=1)
    inhalte.anlegen(conn, "pois", "ost", "P-quellen", poi, nutzer_id=None, revision=1)
    assert gitexport.exportieren(conn, an) == {"ost": 2}
    repo = an.git_dir / "ost"
    assert json.loads((repo / "messungen/M-quellen.json").read_text())["pointSources"] == [
        source, None,
    ]
    assert json.loads((repo / "pois/P-quellen.json").read_text())["positionSource"] == source
