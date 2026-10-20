"""Git-Objekte mit Hashbindung: Inhalt muss zur Objekt-ID passen; fremde Objektspeicher
werden abgelehnt."""

from __future__ import annotations

from pathlib import Path

import pytest
from signierhilfe import commit, git, objekt_ueberschreiben, repo, schluessel

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.gitobjekte import Objektleser, blob_id


@pytest.fixture
def r(tmp_path: Path) -> Path:
    key, _ = schluessel(tmp_path / "k")
    rp = repo(tmp_path / "r", key)
    (rp / "d").mkdir()
    (rp / "d" / "x.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (rp / "d" / "x.sh").chmod(0o755)
    (rp / "d" / "link").symlink_to("x.sh")
    git(rp, "add", "d")
    commit(rp, "a", signiert=False)
    return rp


def test_dateien_wie_ls_tree(r):
    kopf = git(r, "rev-parse", "HEAD")
    with Objektleser(r, "t") as o:
        eigen = o.dateien(kopf)
    soll = {}
    for zeile in git(r, "ls-tree", "-r", "HEAD").splitlines():
        info, pfad = zeile.split("\t")
        modus, _, oid = info.split()
        soll[pfad] = (modus, oid)
    assert eigen == soll
    with Objektleser(r, "t") as o:
        assert o.datei(kopf, "d/x.sh") == ("100755", b"#!/bin/sh\n")
        assert o.datei(kopf, "gibtsnicht") is None
        assert o.commit(kopf)[1] == ()
    assert blob_id(b"#!/bin/sh\n", "sha1") == soll["d/x.sh"][1]


def test_manipulierter_loser_blob(r):
    kopf = git(r, "rev-parse", "HEAD")
    oid = git(r, "rev-parse", "HEAD:a.txt")
    objekt_ueberschreiben(r, oid, "blob", b"untergeschoben\n")
    assert git(r, "cat-file", "blob", oid) == "untergeschoben"   # git selbst merkt es nicht
    with Objektleser(r, "t") as o, pytest.raises(NachweisFehler) as e:
        o.datei(kopf, "a.txt")
    assert e.value.regel == "t" and "passt nicht zur Objekt-ID" in e.value.text


def test_manipulierter_commit(r):
    kopf = git(r, "rev-parse", "HEAD")
    roh = git(r, "cat-file", "commit", kopf).encode() + b"\n"
    objekt_ueberschreiben(r, kopf, "commit", roh.replace(b"\n\n", b"\nparent " + b"1" * 40
                                                         + b"\n\n", 1))
    with Objektleser(r, "t") as o, pytest.raises(NachweisFehler, match="Objekt-ID"):
        o.erreichbar([kopf])


def test_fehlendes_objekt_und_falscher_typ(r):
    with Objektleser(r, "t") as o:
        with pytest.raises(NachweisFehler, match="fehlt"):
            o.lies("0" * 40)
        with pytest.raises(NachweisFehler, match="erwartet commit"):
            o.lies(git(r, "rev-parse", "HEAD:a.txt"), "commit")
        with pytest.raises(NachweisFehler, match="keine volle Objekt-ID"):
            o.lies("HEAD")


@pytest.mark.parametrize("name", ["alternates", "http-alternates"])
def test_alternates_abgelehnt(r, tmp_path, name):
    (r / ".git" / "objects" / "info").mkdir(exist_ok=True)
    (r / ".git" / "objects" / "info" / name).write_text(str(tmp_path) + "\n", encoding="utf-8")
    with pytest.raises(NachweisFehler, match="fremde Objektspeicher"):
        Objektleser(r, "t")


def test_objektleser_nutzt_den_zentralen_git_aufruf(r, monkeypatch):
    from scanview_nachweise import gitobjekte
    aufrufe: list[tuple[str, ...]] = []
    echt = gitobjekte.git_befehl

    def mitschnitt(*args: str, repo: Path | None = None) -> list[str]:
        aufrufe.append(args)
        return echt(*args, repo=repo)
    monkeypatch.setattr(gitobjekte, "git_befehl", mitschnitt)
    with Objektleser(r, "t") as o:
        o.commit(git(r, "rev-parse", "HEAD"))
    assert ("cat-file", "--batch") in aufrufe


@pytest.mark.parametrize("kopf", [
    "tree {t}\nauthor A <a@x> 1 +0000\ncommitter A <a@x> 1 +0000\nparent {p}\n",
    "author A <a@x> 1 +0000\ntree {t}\ncommitter A <a@x> 1 +0000\n",
    "tree {t}\ntree {t}\nauthor A <a@x> 1 +0000\ncommitter A <a@x> 1 +0000\n",
], ids=["parent-nach-author", "tree-nicht-zuerst", "tree-doppelt"])
def test_commit_kopf_nur_git_konform(r, kopf):
    """tree zuerst, parent-Zeilen nur direkt danach (wie git selbst)."""
    import subprocess
    t = git(r, "rev-parse", "HEAD^{tree}")
    p = git(r, "rev-parse", "HEAD")
    roh = kopf.format(t=t, p=p) + "\nnachricht\n"
    oid = subprocess.run(["git", "-C", str(r), "hash-object", "-t", "commit", "-w",
                          "--literally", "--stdin"], input=roh, capture_output=True, text=True,
                         check=True).stdout.strip()
    with Objektleser(r, "t") as o, pytest.raises(NachweisFehler, match="Kopf"):
        o.commit(oid)
