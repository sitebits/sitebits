"""OpenVEX laden und Scan-Gate: freigebend nur `fixed`/`not_affected`
mit gültiger Begründung, passendem Lockfile-Hash und höchstens 90 Tage alt — sonst gilt die
Aussage als nicht vorhanden und der Treffer blockiert."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from scanview_nachweise.fehler import NachweisFehler
from scanview_nachweise.register import Befund
from scanview_nachweise.scan import Treffer
from scanview_nachweise.vex import (
    LOCKDATEIEN,
    OPENVEX_BEGRUENDUNGEN,
    VexAussage,
    befund_vorschlaege,
    lade_vex,
    scan_gate,
    treffer_gate,
    vex_meldungen,
)

HEUTE = date(2026, 9, 27)
LOCK_WEB = "sha256:" + "a" * 64
LOCKS = {"web/package-lock.json": LOCK_WEB, "server/uv.lock": "sha256:" + "b" * 64,
         "mcp-server/uv.lock": "sha256:" + "c" * 64, "import/uv.lock": "sha256:" + "d" * 64}
BEGRUENDUNG = "template() wird in web/src nirgends aufgerufen (grep, Stand 27.09.)"


def vex(schwachstelle: str, **abweichung: object) -> VexAussage:
    felder: dict[str, object] = dict(
        schwachstelle=schwachstelle, produkte=("pkg:npm/demo@1.0.0",), status="not_affected",
        justification="vulnerable_code_not_in_execute_path", impact_statement=BEGRUENDUNG,
        zeit=datetime(2026, 9, 20, tzinfo=UTC), lock_hash=LOCK_WEB, pruefer="Andre",
        lockdatei="web/package-lock.json",
    )
    felder.update(abweichung)
    return VexAussage(**felder)  # type: ignore[arg-type]


def treffer(schwere: str = "hoch", **abweichung: object) -> Treffer:
    felder: dict[str, object] = dict(id="CVE-2099-0001", paket="pkg:npm/demo@1.0.0",
                                     version="1.0.0", schwere=schwere, status_vex=None,
                                     artefakt="web", bom_ref="pkg:npm/demo@1.0.0")
    felder.update(abweichung)
    return Treffer(**felder)  # type: ignore[arg-type]


def befund(**abweichung: object) -> Befund:
    felder: dict[str, object] = dict(
        id="B-001", quelle="Testquelle", titel="Testbefund", schwere="niedrig",
        kategorie="sonstiges", status="offen", staende=("abc1234",), fix_commit=None,
        retest=None, akzeptanz_begruendung=None, frist=None, entdeckt=HEUTE,
    )
    felder.update(abweichung)
    return Befund(**felder)  # type: ignore[arg-type]


BLOCK = "CVE-2099-0001 (hoch) pkg:npm/demo@1.0.0: keine freigebende VEX-Aussage"


# --- Gate: Beispiele und Grenzfälle -------------------------------------------------------------


def test_under_investigation_blockiert():
    t = Treffer("CVE-2099-0001", "pkg:npm/demo@1.0.0", "1.0.0", "hoch", None)
    v = [vex("CVE-2099-0001", status="under_investigation")]
    assert scan_gate([t], v, [], date(2026,9,27)) == ["CVE-2099-0001 (hoch) pkg:npm/demo@1.0.0: keine freigebende VEX-Aussage"]  # noqa: E501


def test_affected_blockiert():
    assert scan_gate([treffer()], [vex("CVE-2099-0001", status="affected")], [], HEUTE,
                     lock_hashes=LOCKS) == [BLOCK]


def test_gueltige_not_affected_gibt_frei():
    assert scan_gate([treffer()], [vex("CVE-2099-0001")], [], HEUTE, lock_hashes=LOCKS) == []


def test_gueltige_fixed_gibt_frei_ohne_begruendung():
    v = vex("CVE-2099-0001", status="fixed", justification=None, impact_statement=None)
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == []


def test_not_affected_ohne_begruendungstext_blockiert():
    v = vex("CVE-2099-0001", impact_statement="zu kurz")
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_not_affected_begruendung_genau_20_zeichen_reicht():
    v = vex("CVE-2099-0001", impact_statement="x" * 20)
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == []


def test_not_affected_mit_leerraum_zaehlt_nicht_als_text():
    v = vex("CVE-2099-0001", impact_statement=" " * 25)
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


@pytest.mark.parametrize("code", [None, "build_only", "requires_configuration"])
def test_not_affected_ohne_openvex_code_blockiert(code):
    v = vex("CVE-2099-0001", justification=code)
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_abgelaufene_aussage_91_tage_blockiert():
    v = vex("CVE-2099-0001", zeit=datetime(2026, 6, 28, 12, tzinfo=UTC))  # 91 Tage
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_aussage_90_tage_alt_gilt_noch():
    v = vex("CVE-2099-0001", zeit=datetime(2026, 6, 29, tzinfo=UTC))
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == []


def test_fixed_verfaellt_ebenfalls():
    v = vex("CVE-2099-0001", status="fixed", zeit=datetime(2026, 1, 1, tzinfo=UTC))
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_aussage_aus_der_zukunft_blockiert():
    v = vex("CVE-2099-0001", zeit=datetime(2026, 10, 1, tzinfo=UTC))
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_falscher_lock_hash_blockiert():
    v = vex("CVE-2099-0001", lock_hash="sha256:" + "f" * 64)
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_fixed_mit_falschem_lock_hash_blockiert():
    v = vex("CVE-2099-0001", status="fixed", lock_hash="sha256:" + "f" * 64)
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_ohne_lock_hashes_gibt_nichts_frei():
    # Fail-closed: ohne aktuelle Lockfile-Hashes kann keine Aussage geprüft werden.
    assert scan_gate([treffer()], [vex("CVE-2099-0001")], [], HEUTE) == [BLOCK]


def test_lockdatei_muss_zum_artefakt_passen():
    # Dieselbe PyPI-PURL kann in server und mcp stecken: eine Aussage mit server/uv.lock gibt den
    # Treffer im MCP-Artefakt nicht frei.
    t = treffer(paket="pkg:pypi/demo@1.0.0", artefakt="mcp", bom_ref="pkg:pypi/demo@1.0.0")
    v = vex("CVE-2099-0001", produkte=("pkg:pypi/demo@1.0.0",), lockdatei="server/uv.lock",
            lock_hash=LOCKS["server/uv.lock"])
    assert scan_gate([t], [v], [], HEUTE, lock_hashes=LOCKS) == \
        ["CVE-2099-0001 (hoch) pkg:pypi/demo@1.0.0: keine freigebende VEX-Aussage"]
    v2 = vex("CVE-2099-0001", produkte=("pkg:pypi/demo@1.0.0",), lockdatei="mcp-server/uv.lock",
             lock_hash=LOCKS["mcp-server/uv.lock"])
    assert scan_gate([t], [v2], [], HEUTE, lock_hashes=LOCKS) == []


def test_image_komponente_nicht_per_vex_freigebbar():
    # Komponenten aus den Container-Images haben keine Lockdatei → keine VEX-Freigabe möglich.
    t = treffer(paket="pkg:cargo/pyo3@0.25.1", artefakt="import",
                bom_ref="pdal|pkg:cargo/pyo3@0.25.1?package-id=1")
    v = vex("CVE-2099-0001", produkte=("pkg:cargo/pyo3@0.25.1",), lockdatei="import/uv.lock",
            lock_hash=LOCKS["import/uv.lock"])
    assert scan_gate([t], [v], [], HEUTE, lock_hashes=LOCKS) == \
        ["CVE-2099-0001 (hoch) pkg:cargo/pyo3@0.25.1: keine freigebende VEX-Aussage"]


def test_treffer_ohne_artefakt_nicht_freigebbar():
    t = Treffer("CVE-2099-0001", "pkg:npm/demo@1.0.0", "1.0.0", "hoch", None)
    assert scan_gate([t], [vex("CVE-2099-0001")], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_produkt_muss_exakt_passen():
    v = vex("CVE-2099-0001", produkte=("pkg:npm/demo@1.0.1",))
    assert scan_gate([treffer()], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_produkt_vergleich_purl_dekodiert():
    t = treffer(paket="pkg:npm/demo@1:1.0.0")
    v = vex("CVE-2099-0001", produkte=("pkg:npm/demo@1%3A1.0.0",))
    assert scan_gate([t], [v], [], HEUTE, lock_hashes=LOCKS) == []


def test_andere_schwachstelle_gibt_nicht_frei():
    assert scan_gate([treffer()], [vex("CVE-2099-0009")], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_trivy_unterdrueckung_allein_gibt_nicht_frei():
    # trivy wendet die VEX-Datei selbst an; das Gate entscheidet nur nach eigenen Regeln.
    t = treffer(status_vex="not_affected")
    v = vex("CVE-2099-0001", zeit=datetime(2026, 1, 1, tzinfo=UTC))
    assert scan_gate([t], [v], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_neueste_aussage_gilt_aeltere_under_investigation_ueberholt():
    # OpenVEX: die jüngste Aussage je (Schwachstelle, Produkt) gilt.
    v = [vex("CVE-2099-0001", status="under_investigation",
             zeit=datetime(2026, 9, 1, tzinfo=UTC)), vex("CVE-2099-0001")]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == []


def test_neuere_under_investigation_zieht_aeltere_not_affected_zurueck():
    v = [vex("CVE-2099-0001", zeit=datetime(2026, 9, 1, tzinfo=UTC)),
         vex("CVE-2099-0001", status="under_investigation",
             zeit=datetime(2026, 9, 20, tzinfo=UTC))]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_neuere_ungueltige_aussage_zieht_aeltere_gueltige_zurueck():
    v = [vex("CVE-2099-0001", zeit=datetime(2026, 9, 1, tzinfo=UTC)),
         vex("CVE-2099-0001", zeit=datetime(2026, 9, 20, tzinfo=UTC), impact_statement="kurz")]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_gleich_alte_widerspruechliche_aussagen_blockieren():
    v = [vex("CVE-2099-0001"), vex("CVE-2099-0001", status="affected")]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == [BLOCK]
    assert scan_gate([treffer()], list(reversed(v)), [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_gleich_alte_gleichlautende_aussagen_geben_frei():
    v = [vex("CVE-2099-0001"), vex("CVE-2099-0001", status="fixed")]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == []


def test_neuere_aussage_fuer_andere_lockdatei_ueberholt_nicht():
    # Aussagen gelten je (Schwachstelle, Produkt, Lockdatei).
    v = [vex("CVE-2099-0001", zeit=datetime(2026, 9, 1, tzinfo=UTC)),
         vex("CVE-2099-0001", status="affected", lockdatei="server/uv.lock",
             lock_hash=LOCKS["server/uv.lock"], zeit=datetime(2026, 9, 20, tzinfo=UTC))]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == []


def test_neuere_aussage_ohne_lockdatei_ueberholt_trotzdem():
    # Ohne Lockdatei-Angabe gilt eine Aussage für jedes Artefakt — also zieht sie auch zurück.
    v = [vex("CVE-2099-0001", zeit=datetime(2026, 9, 1, tzinfo=UTC)),
         vex("CVE-2099-0001", status="under_investigation", lockdatei=None, lock_hash=None,
             zeit=datetime(2026, 9, 20, tzinfo=UTC))]
    assert scan_gate([treffer()], v, [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_kritisch_blockiert_mittel_und_niedrig_nicht():
    t = [treffer("kritisch"), treffer("mittel", id="CVE-2099-0002"),
         treffer("niedrig", id="CVE-2099-0003")]
    assert scan_gate(t, [], [], HEUTE, lock_hashes=LOCKS) == \
        ["CVE-2099-0001 (kritisch) pkg:npm/demo@1.0.0: keine freigebende VEX-Aussage"]


def test_unbekannte_schwere_im_gate_ist_fehler():
    with pytest.raises(NachweisFehler) as e:
        scan_gate([treffer("unbekannt")], [], [], HEUTE)
    assert e.value.regel == "scan-schwere"


def test_doppelte_zeilen_einmal():
    t = [treffer(), treffer(bom_ref="anderer-ref")]
    assert scan_gate(t, [], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


def test_befund_gate_kommt_dazu():
    b = befund(id="B-X", schwere="hoch")
    assert scan_gate([treffer()], [], [b], HEUTE, lock_hashes=LOCKS) == [BLOCK, "B-X: hoch offen"]
    assert treffer_gate([treffer()], [], HEUTE, lock_hashes=LOCKS) == [BLOCK]


# --- Befund-Vorschläge und Meldungen -----------------------------------------------------------


def test_mittel_ohne_vex_wird_befund_vorschlag():
    t = [treffer("mittel"), treffer("mittel"), treffer("niedrig", id="CVE-2099-0005")]
    assert befund_vorschlaege(t, [], [], HEUTE, lock_hashes=LOCKS) == [
        "B-SCAN-CVE-2099-0001: CVE-2099-0001 (mittel) pkg:npm/demo@1.0.0 im Artefakt web — "
        "keine freigebende VEX-Aussage, als Befund eintragen"]


def test_mittel_mit_gueltiger_vex_kein_vorschlag():
    assert befund_vorschlaege([treffer("mittel")], [vex("CVE-2099-0001")], [], HEUTE,
                              lock_hashes=LOCKS) == []


def test_mittel_schon_im_register_kein_vorschlag():
    b = befund(id="B-SCAN-CVE-2099-0001", schwere="mittel", kategorie="lieferkette")
    assert befund_vorschlaege([treffer("mittel")], [], [b], HEUTE, lock_hashes=LOCKS) == []


def test_ungueltige_aussagen_werden_gemeldet():
    v = [vex("CVE-2099-0001", impact_statement="kurz"),
         vex("CVE-2099-0002", lock_hash="sha256:" + "0" * 64),
         vex("CVE-2099-0003", zeit=datetime(2026, 1, 1, tzinfo=UTC)),
         vex("CVE-2099-0004", status="under_investigation"),
         vex("CVE-2099-0005")]
    meldungen = vex_meldungen(v, HEUTE, lock_hashes=LOCKS)
    assert [m.split(":")[0] for m in meldungen] == \
        ["CVE-2099-0001", "CVE-2099-0002", "CVE-2099-0003", "CVE-2099-0004"]
    assert "impact_statement" in meldungen[0]
    assert "lock_hash" in meldungen[1]
    assert "90 Tage" in meldungen[2]
    assert "under_investigation" in meldungen[3]


def test_aussage_ohne_passenden_treffer_wird_gemeldet():
    v = [vex("CVE-2099-0001"), vex("CVE-2099-0007")]
    meldungen = vex_meldungen(v, HEUTE, lock_hashes=LOCKS, treffer=[treffer()])
    assert len(meldungen) == 1
    assert meldungen[0].startswith("CVE-2099-0007:") and "keinem Treffer" in meldungen[0]


def test_aussage_mit_fremder_lockdatei_wird_gemeldet():
    t = treffer(paket="pkg:pypi/demo@1.0.0", artefakt="mcp", bom_ref="pkg:pypi/demo@1.0.0")
    v = vex("CVE-2099-0001", produkte=("pkg:pypi/demo@1.0.0",), lockdatei="server/uv.lock",
            lock_hash=LOCKS["server/uv.lock"])
    [m] = vex_meldungen([v], HEUTE, lock_hashes=LOCKS, treffer=[t])
    assert "server/uv.lock" in m and "mcp" in m


def test_aussage_fuer_image_inhalt_wird_gemeldet():
    t = treffer(paket="pkg:cargo/pyo3@0.25.1", artefakt="import", bom_ref="pdal|pyo3")
    v = vex("CVE-2099-0001", produkte=("pkg:cargo/pyo3@0.25.1",), lockdatei="import/uv.lock",
            lock_hash=LOCKS["import/uv.lock"])
    [m] = vex_meldungen([v], HEUTE, lock_hashes=LOCKS, treffer=[t])
    assert "Image-Inhalt" in m


def test_passende_gueltige_aussage_keine_meldung():
    assert vex_meldungen([vex("CVE-2099-0001")], HEUTE, lock_hashes=LOCKS,
                         treffer=[treffer()]) == []


# --- lade_vex ----------------------------------------------------------------------------------


def test_openvex_codes_genau_die_fuenf_der_spezifikation():
    assert OPENVEX_BEGRUENDUNGEN == (
        "component_not_present", "vulnerable_code_not_present",
        "vulnerable_code_not_in_execute_path",
        "vulnerable_code_cannot_be_controlled_by_adversary", "inline_mitigations_already_exist")


def test_lockdateien_je_artefakt():
    assert LOCKDATEIEN == {"web": "web/package-lock.json", "server": "server/uv.lock",
                           "mcp": "mcp-server/uv.lock", "import": "import/uv.lock"}


def _dokument(*aussagen: dict[str, object], **kopf: object) -> dict[str, object]:
    d: dict[str, object] = {"@context": "https://openvex.dev/ns/v0.2.0",
                            "@id": "urn:scanview:vex", "author": "Andre Stiebitz",
                            "timestamp": "2026-09-20T10:00:00Z", "version": 1,
                            "statements": list(aussagen)}
    d.update(kopf)
    return d


def _aussage(**abweichung: object) -> dict[str, object]:
    a: dict[str, object] = {
        "vulnerability": {"name": "CVE-2099-0001"},
        "products": [{"@id": "pkg:npm/demo@1.0.0"}],
        "status": "not_affected", "justification": "vulnerable_code_not_present",
        "impact_statement": BEGRUENDUNG, "timestamp": "2026-09-21T08:00:00+02:00",
        "scanview": {"lock_hash": LOCK_WEB, "lockdatei": "web/package-lock.json",
                     "pruefer": "Andre"},
    }
    a.update(abweichung)
    return a


def _schreibe(tmp_path: Path, daten: object) -> Path:
    p = tmp_path / "vex.openvex.json"
    p.write_text(json.dumps(daten), encoding="utf-8")
    return p


def test_lade_vex_liest_aussage_mit_metadaten(tmp_path):
    [a] = lade_vex(_schreibe(tmp_path, _dokument(_aussage())))
    assert a == VexAussage(
        schwachstelle="CVE-2099-0001", produkte=("pkg:npm/demo@1.0.0",), status="not_affected",
        justification="vulnerable_code_not_present", impact_statement=BEGRUENDUNG,
        zeit=datetime(2026, 9, 21, 6, tzinfo=UTC), lock_hash=LOCK_WEB, pruefer="Andre",
        lockdatei="web/package-lock.json")


def test_lade_vex_leeres_dokument(tmp_path):
    assert lade_vex(_schreibe(tmp_path, _dokument())) == []


def test_zeit_erbt_vom_dokument(tmp_path):
    a = _aussage()
    del a["timestamp"]
    [v] = lade_vex(_schreibe(tmp_path, _dokument(a)))
    assert v.zeit == datetime(2026, 9, 20, 10, tzinfo=UTC)


def test_last_updated_zaehlt_als_zeit(tmp_path):
    [v] = lade_vex(_schreibe(tmp_path, _dokument(_aussage(last_updated="2026-09-25T00:00:00Z"))))
    assert v.zeit == datetime(2026, 9, 25, tzinfo=UTC)


def test_produkt_ueber_identifiers_purl(tmp_path):
    a = _aussage(products=[{"identifiers": {"purl": "pkg:npm/demo@1.0.0"}}])
    [v] = lade_vex(_schreibe(tmp_path, _dokument(a)))
    assert v.produkte == ("pkg:npm/demo@1.0.0",)


def test_ohne_metadaten_bleiben_felder_leer(tmp_path):
    a = _aussage()
    del a["scanview"]
    [v] = lade_vex(_schreibe(tmp_path, _dokument(a)))
    assert (v.lock_hash, v.lockdatei, v.pruefer) == (None, None, None)


@pytest.mark.parametrize("kaputt", [
    {"@context": "https://example.org/ns"},
    {"author": ""},
    {"timestamp": "gestern"},
    {"timestamp": "2026-09-20T10:00:00"},       # ohne Zeitzone
    {"statements": {}},
    {"version": "1"},
])
def test_kaputter_kopf_ist_fehler(tmp_path, kaputt):
    with pytest.raises(NachweisFehler) as e:
        lade_vex(_schreibe(tmp_path, _dokument(**kaputt)))
    assert e.value.regel == "vex"


@pytest.mark.parametrize("kaputt", [
    {"status": "vielleicht"},
    {"vulnerability": {}},
    {"products": []},
    {"products": [{"@id": "pkg:npm/demo"}]},          # PURL ohne Version
    {"products": [{"@id": "demo@1.0.0"}]},            # keine PURL
    {"scanview": {"lock_hash": "abc"}},                # kein sha256:<hex>
    {"scanview": {"lockdatei": "etc/passwd"}},         # keine bekannte Lockdatei
    {"scanview": {"unbekannt": 1}},
])
def test_kaputte_aussage_ist_fehler(tmp_path, kaputt):
    with pytest.raises(NachweisFehler) as e:
        lade_vex(_schreibe(tmp_path, _dokument(_aussage(**kaputt))))
    assert e.value.regel == "vex"


def test_fehlende_datei_ist_fehler(tmp_path):
    with pytest.raises(NachweisFehler) as e:
        lade_vex(tmp_path / "fehlt.json")
    assert e.value.regel == "vex"


def test_kein_json_ist_fehler(tmp_path):
    p = tmp_path / "v.json"
    p.write_text("{", encoding="utf-8")
    with pytest.raises(NachweisFehler):
        lade_vex(p)


_REPO_VEX = Path(__file__).resolve().parents[2] / "docs/compliance/vex.openvex.json"


@pytest.mark.skipif(not _REPO_VEX.exists(), reason="VEX-Datei liegt nur im privaten Repo (nicht "
                                                  "Teil des öffentlichen Exports)")
def test_repo_vex_datei_ist_gueltig():
    assert lade_vex(_REPO_VEX) is not None


def test_lock_hashes_aus_liest_ueber_funktion_und_meldet_fehlende():
    from scanview_nachweise.vex import lock_hashes_aus

    inhalt = {pfad: pfad.encode() for pfad in LOCKDATEIEN.values()}
    hashes = lock_hashes_aus(inhalt.get)
    assert hashes[LOCKDATEIEN["web"]] == "sha256:" + hashlib.sha256(
        LOCKDATEIEN["web"].encode()).hexdigest()
    del inhalt[LOCKDATEIEN["mcp"]]
    with pytest.raises(NachweisFehler) as e:
        lock_hashes_aus(inhalt.get)
    assert e.value.regel == "scan-lock"
