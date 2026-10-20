"""Markdown-Darstellung der Register: private und öffentliche Fassung, deterministisch."""

from datetime import date

from scanview_nachweise.darstellung import (
    anwendbarkeit_md,
    anwendbarkeit_oeffentlich_md,
    befunde_md,
    matrix_md,
)
from scanview_nachweise.register import Anwendbarkeit, Befund, Kontrolle, Modell


def _k(**abweichung: object) -> Kontrolle:
    felder: dict[str, object] = dict(
        id="K-TEST-1", quelle="Quelle 1", titel="Titel", art="pflicht", anwendbar=True,
        begruendung="", pruefhandlung="Handlung", sollwert="Soll", release_voraussetzung=True,
        iso=("8.8", "8.9"), oeffentlich=True, nachweis_schluessel="scan",
    )
    felder.update(abweichung)
    return Kontrolle(**felder)  # type: ignore[arg-type]


def test_oeffentlich_ohne_pruefhandlung_interner_zeilen():
    intern = _k(id="K-INTERN", oeffentlich=False, pruefhandlung="GEHEIME-HANDLUNG",
                sollwert="GEHEIMER-SOLLWERT", begruendung="GEHEIME-BEGRÜNDUNG")
    md = matrix_md([_k(), intern], None, oeffentlich=True)
    assert "GEHEIME-HANDLUNG" not in md
    assert "GEHEIMER-SOLLWERT" not in md
    assert "GEHEIME-BEGRÜNDUNG" not in md
    assert "K-INTERN" in md
    assert "intern (Vault), auf Anfrage" in md
    # öffentliche Zeilen bleiben vollständig
    assert "Handlung" in md


def test_oeffentlich_ohne_zeile_fehlt_ganz():
    """`oeffentlich_zeile = false`: schon Kennung und Titel würden die Lücke offenlegen — die
    öffentliche Fassung lässt die Zeile weg und sagt, dass sie Zeilen weglässt."""
    weg = _k(id="K-WEG", titel="VERRATENDER-TITEL", oeffentlich=False, oeffentlich_zeile=False)
    md = matrix_md([_k(), weg], None, oeffentlich=True)
    assert "K-WEG" not in md
    assert "VERRATENDER-TITEL" not in md
    assert "nur in der internen Fassung" in md
    assert "| K-TEST-1 |" in md


def test_oeffentlich_ohne_weggelassene_zeilen_kein_hinweis():
    md = matrix_md([_k()], None, oeffentlich=True)
    assert "nur in der internen Fassung" not in md


def test_privat_enthaelt_weggelassene_zeile():
    weg = _k(id="K-WEG", titel="VERRATENDER-TITEL", oeffentlich=False, oeffentlich_zeile=False)
    md = matrix_md([weg], None, oeffentlich=False)
    assert "| K-WEG |" in md
    assert "VERRATENDER-TITEL" in md
    assert "nur in der internen Fassung" not in md


def test_privat_enthaelt_alles():
    intern = _k(id="K-INTERN", oeffentlich=False, pruefhandlung="GEHEIME-HANDLUNG")
    md = matrix_md([intern], None, oeffentlich=False)
    assert "GEHEIME-HANDLUNG" in md
    assert "intern (Vault), auf Anfrage" not in md


def test_pipe_und_zeilenumbruch_werden_escaped():
    md = matrix_md([_k(titel="a | b", pruefhandlung="x\ny")], None, oeffentlich=False)
    zeile = next(z for z in md.splitlines() if z.startswith("| K-TEST-1"))
    assert "a \\| b" in zeile
    assert "x y" in zeile
    # Spaltenzahl stimmt mit dem Kopf überein (escapte Pipes zählen nicht).
    kopf = next(z for z in md.splitlines() if z.startswith("| ID"))
    zaehle = lambda z: z.replace("\\|", "").count("|")  # noqa: E731
    assert zaehle(zeile) == zaehle(kopf)


def test_ergebnis_spalte_strich_ohne_ergebnis():
    md = matrix_md([_k()], None, oeffentlich=False)
    zeile = next(z for z in md.splitlines() if z.startswith("| K-TEST-1"))
    assert zeile.rstrip().endswith("| — |")


def test_ergebnis_spalte_mit_ergebnis():
    md = matrix_md([_k(), _k(id="K-TEST-2")], {"K-TEST-1": "bestanden (N-1)"}, oeffentlich=False)
    z1 = next(z for z in md.splitlines() if z.startswith("| K-TEST-1"))
    z2 = next(z for z in md.splitlines() if z.startswith("| K-TEST-2"))
    assert z1.rstrip().endswith("| bestanden (N-1) |")
    assert z2.rstrip().endswith("| — |")


def test_kopfzeile_erzeugt_und_deterministisch():
    ks = [_k(), _k(id="K-TEST-2", anwendbar=False, begruendung="b" * 25)]
    a = matrix_md(ks, None, oeffentlich=False)
    assert a == matrix_md(ks, None, oeffentlich=False)
    assert "nicht von Hand bearbeiten" in a.splitlines()[0] or \
        "nicht von Hand bearbeiten" in a.splitlines()[2]
    assert a.endswith("\n") and not a.endswith("\n\n")


def test_ja_nein_und_iso():
    md = matrix_md([_k(anwendbar=False, begruendung="b" * 25, release_voraussetzung=False,
                       nachweis_schluessel=None)], None, oeffentlich=False)
    zeile = next(z for z in md.splitlines() if z.startswith("| K-TEST-1"))
    assert "nein — " + "b" * 25 in zeile
    assert "8.8, 8.9" in zeile


def test_anwendbarkeit_md():
    reg = Anwendbarkeit(
        entschieden_am="2026-09-27", status="ungeklärt — handeln, als gälte Art. 14",
        hinweise=("Hinweis | eins",),
        modelle=(Modell(name="Plattform", produktgrenze="Server", rechtsperson="UG",
                        seit="27.09.2026", einordnung="ungeklärt",
                        offene_fragen=("Rolle Hersteller?",)),),
    )
    md = anwendbarkeit_md(reg)
    assert "nicht von Hand bearbeiten" in md
    assert "2026-09-27" in md
    assert "ungeklärt — handeln, als gälte Art. 14" in md
    assert "| Plattform | Server | UG | 27.09.2026 | ungeklärt |" in md
    assert "Rolle Hersteller?" in md
    assert md == anwendbarkeit_md(reg)


def test_anwendbarkeit_oeffentlich_ohne_interna():
    """Öffentliche Kurzfassung: Datum, Status, Modelle mit Grenze/Seit/Einordnung — ohne
    Rechtsperson, interne Hinweise und Fragen an den Anwalt."""
    reg = Anwendbarkeit(
        entschieden_am="2026-09-27", status="ungeklärt — handeln, als gälte Art. 14",
        hinweise=("INTERNER-HINWEIS mit Kundenbezug",),
        modelle=(Modell(name="Plattform", produktgrenze="Server", rechtsperson="GEHEIME-PERSON",
                        seit="27.09.2026", einordnung="ungeklärt",
                        offene_fragen=("ANWALTSFRAGE?",)),),
    )
    md = anwendbarkeit_oeffentlich_md(reg)
    assert "öffentliche Fassung" in md and "nicht von Hand bearbeiten" in md
    assert "2026-09-27" in md and "ungeklärt — handeln, als gälte Art. 14" in md
    assert "| Plattform | Server | 27.09.2026 | ungeklärt |" in md
    for intern in ("INTERNER-HINWEIS", "GEHEIME-PERSON", "ANWALTSFRAGE", "Anwalt"):
        assert intern not in md
    assert md == anwendbarkeit_oeffentlich_md(reg)


# --- Befundregister ----------------------------------------------------------------------------

def _b(**abweichung: object) -> Befund:
    felder: dict[str, object] = dict(
        id="B-WB-M4", quelle="WB-M4 (audit-code-whitebox)", titel="GEHEIMER-ANGRIFFSPFAD",
        schwere="mittel", kategorie="rechte", status="offen", staende=("a546418",),
        fix_commit=None, retest=None, akzeptanz_begruendung=None, frist=None,
        entdeckt=date(2026, 9, 27),
    )
    felder.update(abweichung)
    return Befund(**felder)  # type: ignore[arg-type]


def test_befunde_oeffentlich_nur_id_schwere_kategorie_status():
    bs = [_b(), _b(id="B-WB-M1", titel="BEHOBENER-TITEL", status="behoben", kategorie="auth",
                   fix_commit="3ee2ca6", retest="server/tests/test_s.py::test_m1",
                   artefakte=("import",))]
    md = befunde_md(bs, oeffentlich=True)
    for geheim in ("GEHEIMER-ANGRIFFSPFAD", "BEHOBENER-TITEL", "audit-code-whitebox", "a546418",
                   "3ee2ca6", "test_m1"):
        assert geheim not in md, geheim
    assert "| B-WB-M4 | mittel | rechte | offen | alle |" in md
    assert "| B-WB-M1 | mittel | auth | behoben | import |" in md
    kopf = next(z for z in md.splitlines() if z.startswith("| ID"))
    assert kopf == "| ID | Schwere | Kategorie | Status | Betrifft |"
    assert "nicht von Hand bearbeiten" in md


def test_befunde_privat_vollstaendig_und_deterministisch():
    b = _b(status="akzeptiert", akzeptanz_begruendung="Begründung lang genug hier",
           frist=date(2026, 12, 31))
    md = befunde_md([b], oeffentlich=False)
    assert md == befunde_md([b], oeffentlich=False)
    for teil in ("GEHEIMER-ANGRIFFSPFAD", "WB-M4 (audit-code-whitebox)", "a546418",
                 "2026-09-27", "Begründung lang genug hier", "2026-12-31"):
        assert teil in md, teil
    assert "`docs/compliance/befunde.toml`" in md and "nicht von Hand bearbeiten" in md
    assert md.endswith("\n") and not md.endswith("\n\n")


def test_befunde_zusammenfassung_nach_status():
    md = befunde_md([_b(), _b(id="B-X", status="behoben", fix_commit="3ee2ca6",
                              retest="server/tests/test_s.py::test_m1")], oeffentlich=True)
    assert "offen: 1" in md and "behoben: 1" in md


# --- Betriebsnachweise aus dem Archiv -------------------------------------------------------------

def _nachweis(archiv, name, daten=None):
    import json
    ordner = archiv / "pruefungen" / name
    ordner.mkdir(parents=True)
    (ordner / "ergebnis.json").write_text(json.dumps(daten or {}), encoding="utf-8")


def test_betrieb_juengster_nachweis_je_instanz(tmp_path):
    from scanview_nachweise.darstellung import betriebsergebnisse
    _nachweis(tmp_path, "2026-09-20-dast-L", {"gate": "gruen"})
    _nachweis(tmp_path, "2026-10-01-dast-L", {"gate": "blockiert"})
    _nachweis(tmp_path, "2026-09-25-dast-S", {"gate": "gruen"})
    k = _k(id="K-DAST", nachweis_schluessel="betrieb:dast", release_voraussetzung=False)
    text = betriebsergebnisse([k], tmp_path)["K-DAST"]
    assert "L: 2026-10-01 blockiert (`pruefungen/2026-10-01-dast-L`)" in text
    assert "S: 2026-09-25 gruen (`pruefungen/2026-09-25-dast-S`)" in text
    assert "2026-09-20" not in text


def test_betrieb_juengster_lauf_numerisch(tmp_path):
    from scanview_nachweise.darstellung import betriebsergebnisse
    _nachweis(tmp_path, "2026-10-01-dast-L", {"gate": "blockiert"})
    _nachweis(tmp_path, "2026-10-01.9-dast-L", {"gate": "blockiert"})
    _nachweis(tmp_path, "2026-10-01.10-dast-L", {"gate": "bestanden"})
    _nachweis(tmp_path, "2026-09-30.11-dast-L", {"gate": "blockiert"})
    _nachweis(tmp_path, "2026-10-01.2-dast-S", {"gate": "bestanden"})
    _nachweis(tmp_path, "2026-10-01-dast-S", {"gate": "blockiert"})
    _nachweis(tmp_path, "2026-10-02.1-dast-L", {"gate": "blockiert"})  # kein gültiger Name
    k = _k(id="K-DAST", nachweis_schluessel="betrieb:dast", release_voraussetzung=False)
    text = betriebsergebnisse([k], tmp_path)["K-DAST"]
    assert text == ("L: 2026-10-01 bestanden (`pruefungen/2026-10-01.10-dast-L`); "
                    "S: 2026-10-01 bestanden (`pruefungen/2026-10-01.2-dast-S`)")


def test_betrieb_ohne_nachweis_strich_und_fremde_art_zaehlt_nicht(tmp_path):
    from scanview_nachweise.darstellung import betriebsergebnisse
    _nachweis(tmp_path, "2026-10-01-updates-L", {"ergebnis": "bestanden"})
    ks = [_k(id="K-DAST", nachweis_schluessel="betrieb:dast", release_voraussetzung=False),
          _k(id="K-UPD", nachweis_schluessel="betrieb:updates", release_voraussetzung=False),
          _k(id="K-SCAN", nachweis_schluessel="scan")]
    e = betriebsergebnisse(ks, tmp_path / "gibt-es-nicht")
    assert e == {"K-DAST": "—", "K-UPD": "—"}
    e = betriebsergebnisse(ks, tmp_path)
    assert e["K-DAST"] == "—"
    assert e["K-UPD"] == "L: 2026-10-01 bestanden (`pruefungen/2026-10-01-updates-L`)"


def test_betrieb_ohne_instanz_und_ohne_ergebnisfeld(tmp_path):
    from scanview_nachweise.darstellung import betriebsergebnisse
    _nachweis(tmp_path, "2026-09-30-renovate", {"x": 1})
    k = _k(id="K-R", nachweis_schluessel="betrieb:renovate", release_voraussetzung=False)
    assert betriebsergebnisse([k], tmp_path)["K-R"] == (
        "2026-09-30 vorhanden (`pruefungen/2026-09-30-renovate`)")


def test_betrieb_unlesbarer_juengster_nachweis_wird_nicht_uebersprungen(tmp_path):
    """Ein kaputter jüngster Nachweis darf kein älteres Ergebnis als jüngsten Stand vorschieben."""
    from scanview_nachweise.darstellung import betriebsergebnisse
    _nachweis(tmp_path, "2026-09-20-dast-L", {"gate": "gruen"})
    _nachweis(tmp_path, "2026-10-01-dast-L")
    (tmp_path / "pruefungen" / "2026-10-01-dast-L" / "ergebnis.json").write_text(
        "{kaputt", encoding="utf-8")
    _nachweis(tmp_path, "2026-10-01-dast-S")
    (tmp_path / "pruefungen" / "2026-10-01-dast-S" / "ergebnis.json").write_text(
        "[1, 2]", encoding="utf-8")
    _nachweis(tmp_path, "2026-09-20-dast-X", {"gate": "gruen"})
    (tmp_path / "pruefungen" / "2026-10-01-dast-X").mkdir()  # ohne ergebnis.json
    k = _k(id="K-DAST", nachweis_schluessel="betrieb:dast", release_voraussetzung=False)
    text = betriebsergebnisse([k], tmp_path)["K-DAST"]
    assert "X: 2026-10-01 unlesbar (`pruefungen/2026-10-01-dast-X`)" in text
    assert "L: 2026-10-01 unlesbar (`pruefungen/2026-10-01-dast-L`)" in text
    assert "S: 2026-10-01 unlesbar (`pruefungen/2026-10-01-dast-S`)" in text
    assert "gruen" not in text


def test_betrieb_md_nur_betriebszeilen():
    from scanview_nachweise.darstellung import betrieb_md
    ks = [_k(id="K-DAST", titel="DAST-Titel", nachweis_schluessel="betrieb:dast",
             release_voraussetzung=False), _k(id="K-SCAN")]
    md = betrieb_md(ks, {"K-DAST": "L: 2026-10-01 blockiert (`pruefungen/x`)"})
    assert "K-DAST" in md and "DAST-Titel" in md and "blockiert" in md
    assert "K-SCAN" not in md
