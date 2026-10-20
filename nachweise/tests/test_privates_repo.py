"""Dateien, deren Tests im öffentlichen Export überspringen, müssen im privaten Repo da sein.

Mehrere Tests überspringen, wenn eine nur privat vorhandene Datei fehlt (`skipif … exists()`).
Das Kriterium ist „Datei fehlt“, nicht „öffentlicher Export“: Benennt jemand eine davon im
privaten Repo um, entfiele die Prüfung still. Dieser Test verlangt sie deshalb, sobald das Repo
als privat erkannt ist.

Privat ist das Repo, sobald das Befundregister (`register.BEFUNDE`) oder eine der Dateien unten
existiert. `.git` taugt nicht als Merkmal, ein Klon des öffentlichen Exports hat es auch. Das
Befundregister liegt unter `docs/compliance/`, das die Positivliste nie aufnimmt, und die Kette
liest es in der Stufe `register` — eine Umbenennung bricht das Release, statt diesen Test still
zu überspringen. Dass keine der Dateien exportiert wird, prüft der zweite Test gegen die
Positivliste selbst (sie ist öffentlich und läuft daher überall).

Bewusst nicht hier: `test_register.py::test_exportierter_quellcode_ohne_interne_kennungen`
überspringt nur ohne `.git`, weil es `git ls-files` braucht. In einem öffentlichen Klon läuft es
also mit — das ist strenger, nicht lückenhaft."""

from __future__ import annotations

from pathlib import Path

import pytest

from scanview_nachweise import dast, egress, export, register, sast
from scanview_nachweise.positivliste import POSITIVLISTE, Positivliste, lies_positivliste

WURZEL = Path(__file__).resolve().parents[2]

#: Je Datei die Tests, die bei ihrem Fehlen überspringen.
NUR_PRIVAT: dict[str, str] = {
    dast.AUSNAHMEN.as_posix(): "test_dast.py::test_echte_ausnahmendatei_laedt",
    egress.AUSNAHMEN.as_posix(): "test_egress.py::test_echte_ausnahmen_laden",
    export.FREIGABEN: "test_egress.py::test_echte_freigaben_decken_jede_binaerdatei_des_pakets",
    "docs/compliance/kontrollmatrix.toml": "test_register.py (_echt, "
                                           "test_oeffentliche_matrixzeilen_ohne_interne_kennungen)",
    export.GITLEAKS_KONFIG: "test_export.py::test_gitleaks_* (zwei Tests)",
    (Path("nachweise") / sast.REGELN_LOCK).as_posix():
        "test_export.py::test_vendierter_semgrep_regelsatz_nie_im_export",
    # Der Regelsatz ist ein Verzeichnis (`skipif not is_dir()`); seine Lizenzdatei steht dafür,
    # damit auch die Positivliste (Dateimuster) ihn prüfen kann.
    (Path("nachweise") / sast.REGELN / "LICENSE").as_posix(): "test_sast.py (_REGELN_DA)",
    "docs/compliance/npm-signaturen-ausnahmen.toml":
        "test_artefakte.py::test_echte_npm_ausnahmedatei_ist_gueltig",
    register.BEFUNDE.as_posix(): "test_befunde.py (echtes Befundregister, zwei Stellen)",
}
MERKMAL = register.BEFUNDE.as_posix()


def ist_privat(wurzel: Path) -> bool:
    return any((wurzel / p).exists() for p in NUR_PRIVAT)


def fehlende(wurzel: Path) -> list[str]:
    return [p for p in NUR_PRIVAT if not (wurzel / p).exists()]


def test_erkennung_ohne_private_dateien_oeffentlich(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    (tmp_path / POSITIVLISTE).parent.mkdir(parents=True)
    (tmp_path / POSITIVLISTE).write_text("README.md\n", encoding="utf-8")
    assert not ist_privat(tmp_path)


def test_erkennung_umbenannte_datei_faellt_auf(tmp_path: Path) -> None:
    # Privat am Merkmal erkannt, die übrigen Dateien fehlen (etwa umbenannt) → gemeldet.
    (tmp_path / MERKMAL).parent.mkdir(parents=True)
    (tmp_path / MERKMAL).write_text("", encoding="utf-8")
    assert ist_privat(tmp_path)
    assert fehlende(tmp_path) == [p for p in NUR_PRIVAT if p != MERKMAL]


@pytest.mark.skipif(not ist_privat(WURZEL), reason="öffentlicher Export: die Dateien fehlen dort "
                                                   "absichtlich")
def test_nur_privat_vorhandene_dateien_existieren() -> None:
    assert fehlende(WURZEL) == [], ("im privaten Repo fehlen Dateien, deren Tests sonst still "
                                    "überspringen (umbenannt? Pfad im Test nachziehen)")


def test_nur_privat_vorhandene_dateien_nie_im_export() -> None:
    liste = Positivliste(lies_positivliste((WURZEL / POSITIVLISTE).read_text(encoding="utf-8")))
    assert [p for p in NUR_PRIVAT if liste.nimmt_auf(p)] == []
