import csv
import io
import json
import re
from typing import Any

from conftest import csrf
from fastapi.testclient import TestClient
from nutzer import person
from test_authz import mit_paketen  # noqa: F401  (Fixture)

from scanview_server import export

P = "/api/v1/projekte/ost"


def _poi(c: TestClient, pid: str, **daten: Any) -> None:
    basis = {"title": "T", "category": "", "note": "", "position": [0, 0, 0], "station": None}
    r = c.post(f"{P}/pois", headers=csrf(), json={"id": pid, "daten": {**basis, **daten}})
    assert r.status_code == 201, r.text


def _zeilen(text: str) -> list[list[str]]:
    assert text.startswith(export.CSV_BOM)
    return list(csv.reader(io.StringIO(text[1:], newline=""), delimiter=";"))


def test_csv_formelinjektion(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    gefaehrlich = ['=HYPERLINK("http://x";"klick")', "+1+1", "-cmd", "@SUMME(A1)"]
    for i, titel in enumerate(gefaehrlich):
        _poi(c, f"P-{i}", title=titel, note=titel, category="=1+2")
    _poi(c, "-id", title="ID mit Minus")
    r = c.post(f"{P}/messungen", headers=csrf(), json={
        "daten": {"kind": "height", "points": [[-1.0, -2.0, 5.5], [-1.0, -2.0, 4.0]]},
    })
    assert r.status_code == 201
    r = c.get(f"{P}/export.csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert 'filename="scanview-ost-' in r.headers["content-disposition"]
    zeilen = _zeilen(r.text)
    # Paket „ost“ hat kein CRS: Koordinatenspalte „[m, lokal]“ (F23).
    assert zeilen[0] == ["Koordinaten X Y Z [m, lokal]" if k == export.KOORDINATEN_KOPF else k
                         for k in export.KOPF]
    for z in zeilen[1:]:
        for zelle in z:
            if re.fullmatch(r"-?\d+,\d{3}", zelle):
                continue  # Zahlzelle, bleibt Zahl
            assert not zelle.startswith(export.FORMEL_START), (z, zelle)
    pois = [z for z in zeilen[1:] if z[0] == "POI"]
    assert {z[2] for z in pois} >= {"'" + t for t in gefaehrlich}
    assert {z[4] for z in pois} >= {"'" + t for t in gefaehrlich}
    assert all(z[3] == "'=1+2" for z in pois if z[1].startswith("P-"))
    assert any(z[1] == "'-id" for z in pois)
    [hoehe] = [z for z in zeilen[1:] if z[0] == "Messung"]
    assert hoehe[2] == "Höhe" and hoehe[9] == "-1,500"  # Zahlzelle bleibt Zahl
    assert hoehe[14].startswith("P1: -1,000 -2,000 5,500 | P2: ")


def test_text_zelle_tab_cr_und_quoting() -> None:
    # Tab/CR am Anfang kommen über die API nicht an (Texte werden getrimmt) — Schutz trotzdem
    # direkt geprüft, falls künftig ungetrimmte Felder exportiert werden.
    assert export.text_zelle("\tTab") == "'\tTab"
    assert export.text_zelle("\rCR") == '"\'\rCR"'
    assert export.text_zelle('a"b') == '"a""b"'
    assert export.text_zelle("normal") == "normal"


def test_csv_zeilenende_und_werte() -> None:
    from scanview_server.inhalte import Eintrag

    def e(i: str, daten: dict[str, Any]) -> Eintrag:
        return Eintrag(id=i, daten=daten, version=1, paket_revision=1, erstellt_von=1,
                       erstellt_am=0, geaendert_von=1, geaendert_von_name="Anna", geaendert_am=0)

    text = export.csv_text(
        [e("P-1", {"title": "A;B", "category": "", "note": 'x "y"\nz', "position": [1, 2, 3],
                   "station": "S001"})],
        [e("M-1", {"kind": "polyline", "points": [[0, 0, 0], [3, 4, 0], [3, 4, 2]]}),
         e("M-2", {"kind": "area", "points": [[0, 0, 0], [4, 0, 0], [4, 3, 0]],
                   "areaM2": 6.0, "planarityM": 0.0})],
    )
    assert text.endswith("\r\n") and "\r\n" in text
    zeilen = _zeilen(text)
    assert zeilen[1][2] == "A;B" and zeilen[1][4] == 'x "y"\nz' and zeilen[1][5] == "S001"
    assert zeilen[1][15] == "1970-01-01 00:00" and zeilen[1][16] == "Anna"
    assert zeilen[2][6] == "7,000" and zeilen[2][13] == "3"
    assert (zeilen[3][7], zeilen[3][8], zeilen[3][12]) == ("6,000", "12,000", "0,000")
    assert export.num(-0.0001) == "0,000" and export.num(1234.5678) == "1234,568"


def test_json_export_ist_als_poi_datei_importierbar(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    _poi(c, "P-1", title="Tür", station="S001")
    _poi(c, "P-2", title="weg")
    c.delete(f"{P}/pois/P-2", headers=csrf(), params={"version": 1})
    c.post(f"{P}/messungen", headers=csrf(),
           json={"daten": {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]]}})
    r = c.get(f"{P}/export.json")
    assert r.status_code == 200 and 'filename="scanview-ost-' in r.headers["content-disposition"]
    d = r.json()
    assert (d["schemaVersion"], d["projectId"], d["revision"], d["frame"], d["unit"]) == (
        1, "ost", 1, "world", "m",
    )
    assert d["pois"] == [{"id": "P-1", "title": "Tür", "category": "", "note": "",
                          "position": [0.0, 0.0, 0.0], "station": "S001"}]
    assert [m["kind"] for m in d["messungen"]] == ["distance"]


def test_export_rechte(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bet = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    for pfad in ("export.csv", "export.json"):
        r = bet.get(f"{P}/{pfad}")
        assert r.status_code == 403 and r.json()["fehler"] == "Export ist Bearbeitern vorbehalten."
    assert mit_paketen.get(f"{P}/export.csv").status_code == 401
    _, fremd = person(mit_paketen, "f@b.de", grants={"kirche": "bearbeiter"})
    assert fremd.get(f"{P}/export.csv").status_code == 404


def test_projekt_protokoll_seitenweise_und_gefiltert(mit_paketen: TestClient) -> None:  # noqa: F811
    _, c = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    for i in range(3):
        _poi(c, f"P-{i}", title=f"T{i}")
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    from scanview_server import db, protokoll

    conn = db.connect(mit_paketen.app.state.settings.db_path)  # type: ignore[attr-defined]
    protokoll.ereignis(conn, kanal="cli", art="recht.entzogen", project_id="ost", objekt_id="9",
                       vorher="betrachter")
    conn.close()
    s1 = c.get(f"{P}/protokoll", params={"limit": 2}).json()
    assert [e["objekt"] for e in s1["ereignisse"]] == ["P-2", "P-1"] and s1["weiter"] is not None
    s2 = c.get(f"{P}/protokoll", params={"limit": 2, "vor": s1["weiter"]}).json()
    assert [e["objekt"] for e in s2["ereignisse"]] == ["P-0"] and s2["weiter"] is None
    assert s1["ereignisse"][0]["nutzer"] == "b" and s1["ereignisse"][0]["kanal"] == "web"
    arten_admin = [e["art"] for e in adm.get(f"{P}/protokoll").json()["ereignisse"]]
    assert arten_admin[0] == "recht.entzogen"
    zeilen = c.get(f"{P}/protokoll.jsonl").text.splitlines()
    assert [json.loads(z)["objekt"] for z in zeilen] == ["P-0", "P-1", "P-2"]
    assert c.get(f"{P}/protokoll.jsonl").headers["content-type"].startswith("application/x-ndjson")


def test_protokoll_rechte(mit_paketen: TestClient) -> None:  # noqa: F811
    _, bet = person(mit_paketen, "t@b.de", grants={"ost": "betrachter"})
    r = bet.get(f"{P}/protokoll")
    assert r.status_code == 403
    assert r.json()["fehler"] == "Den Verlauf sehen Bearbeiter, Projektleitung und Admins."
    _, bea = person(mit_paketen, "b@b.de", grants={"ost": "bearbeiter"})
    assert bea.get("/api/v1/protokoll").status_code == 404
    assert bea.get("/api/v1/protokoll.jsonl").status_code == 404
    _, adm = person(mit_paketen, "a@b.de", admin=True)
    alles = adm.get("/api/v1/protokoll").json()["ereignisse"]
    assert {e["art"] for e in alles} >= {"konto.angelegt"}
    nur_ost = adm.get("/api/v1/protokoll", params={"projekt": "ost"}).json()["ereignisse"]
    assert all(e["projekt"] == "ost" for e in nur_ost)
    assert adm.get("/api/v1/protokoll", params={"limit": 0}).status_code == 422
    assert len(adm.get("/api/v1/protokoll.jsonl").text.splitlines()) == len(alles)


def test_csv_kopf_maskiert_systemnamen() -> None:
    text = export.csv_text([], [], einheit='[m, Foo; "bar", NHN]').lstrip("\ufeff")
    kopf = next(csv.reader(io.StringIO(text), delimiter=";"))
    assert len(kopf) == len(export.KOPF)
    assert 'Koordinaten X Y Z [m, Foo; "bar", NHN]' in kopf
