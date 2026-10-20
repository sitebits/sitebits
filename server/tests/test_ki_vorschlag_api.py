"""G7 über HTTP: Agent-Token schreibt Vorschläge, Menschen prüfen."""

from fadenwelt import BASIS, FADEN, POI, Welt, welt  # noqa: F401

PUNKT = {**POI, "title": "KI-Fund"}
STRECKE = {"kind": "distance", "points": [[0, 0, 0], [1, 0, 0]]}


def _ids(w: Welt, wer: str, *, vorschlaege: bool = True) -> list[str]:
    q = "?vorschlaege=1" if vorschlaege else ""
    return [p["id"] for p in w.c[wer].get(BASIS + "/pois" + q).json()["pois"]]


def _version(w: Welt, eid: str = "P-1") -> int:
    [p] = [p for p in w.c["bh"].get(BASIS + "/pois?vorschlaege=1").json()["pois"]
           if p["id"] == eid]
    return int(p["version"])


def test_agent_vorschlag_annehmen(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.post(BASIS + "/pois", json={"id": "P-KI", "daten": PUNKT})
    assert r.status_code == 201 and r.json()["pruefstand"] == "vorschlag"
    assert r.json()["herkunft"] == "ki"
    assert "P-KI" in _ids(welt, "bh") and "P-KI" in _ids(welt, "bear")
    # ohne Opt-in wie heute: fremde Vorschläge unsichtbar, eigene (Konto „bear“) sichtbar
    assert "P-KI" not in _ids(welt, "bh", vorschlaege=False)
    assert "P-KI" in _ids(welt, "bear", vorschlaege=False)
    assert "P-KI" not in _ids(welt, "betr") and "P-KI" not in _ids(welt, "rueck")
    lese = welt.token("betr", "lesen")
    assert "P-KI" not in [p["id"] for p in
                          lese.get(BASIS + "/pois?vorschlaege=1").json()["pois"]]
    # Agent bestätigt sich nicht selbst, Betrachter prüft nicht
    assert agent.post(BASIS + "/pois/P-KI/annehmen", json={"version": 1}).status_code == 403
    assert welt.post("betr", BASIS + "/pois/P-KI/annehmen", {"version": 1}).status_code == 403
    assert welt.post("bh", BASIS + "/pois/P-KI/annehmen", {"version": 9}).status_code == 409
    r = welt.post("bh", BASIS + "/pois/P-KI/annehmen", {"version": 1})
    assert r.status_code == 200 and r.json()["pruefstand"] == "gueltig"
    assert "P-KI" in _ids(welt, "betr", vorschlaege=False)


def test_annehmen_ohne_csrf_403(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    agent.post(BASIS + "/pois", json={"id": "P-KI", "daten": PUNKT})
    for aktion in ("annehmen", "verwerfen"):
        r = welt.c["bh"].post(BASIS + f"/pois/P-KI/{aktion}", json={"version": 1})
        assert r.status_code == 403, aktion
    assert "P-KI" in _ids(welt, "bh")


def test_agent_aendert_gueltiges_403(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.patch(BASIS + "/pois/P-1", json={"daten": {"title": "x"}, "version": 1})
    assert r.status_code == 403


def test_agent_loeschen_wird_vorschlag(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.delete(BASIS + f"/pois/P-1?version={_version(welt)}")
    assert r.status_code == 202 and r.json()["loeschenVorgeschlagen"] is not None
    p = welt.c["bh"].get(BASIS + "/pois?vorschlaege=1").json()["pois"][0]
    assert p["loeschenVorgeschlagen"] is not None
    assert welt.post("bh", BASIS + "/pois/P-1/verwerfen",
                     {"version": p["version"]}).status_code == 200
    # eigenen Vorschlag löscht der Agent direkt
    agent.post(BASIS + "/pois", json={"id": "P-KI", "daten": PUNKT})
    assert agent.delete(BASIS + "/pois/P-KI?version=1").status_code == 204
    assert "P-KI" not in _ids(welt, "bh")


def test_loeschvorschlag_annehmen_204(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.delete(BASIS + f"/pois/P-1?version={_version(welt)}")
    assert welt.post("bh", BASIS + "/pois/P-1/annehmen",
                     {"version": r.json()["version"]}).status_code == 204
    assert "P-1" not in _ids(welt, "bh")


def test_fremdes_projekt_404_vor_403(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.post("/api/v1/projekte/kirche/pois/X/annehmen", json={"version": 1})
    assert r.status_code == 404


def test_annehmen_unbekannt_404_und_ohne_vorschlag_422(welt: Welt) -> None:  # noqa: F811
    assert welt.post("bh", BASIS + "/pois/P-X/annehmen", {"version": 1}).status_code == 404
    r = welt.post("bh", BASIS + "/pois/P-1/annehmen", {"version": _version(welt)})
    assert r.status_code == 422
    assert welt.post("bh", BASIS + "/pois/P-1/annehmen", {"version": 0}).status_code == 422


def test_rueckmelder_prueft_eigene_rueckmeldung(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("rueck", "schreiben", agent=True)
    rm = {**PUNKT, "category": "Rückmeldung"}
    assert agent.post(BASIS + "/pois", json={"id": "P-R", "daten": rm}).status_code == 201
    assert "P-R" in _ids(welt, "rueck", vorschlaege=False)
    assert welt.post("rueck_bh", BASIS + "/pois/P-R/annehmen", {"version": 1}).status_code == 403
    assert welt.post("rueck", BASIS + "/pois/P-R/annehmen", {"version": 1}).status_code == 200


def test_messung_vorschlag_verwerfen(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.post(BASIS + "/messungen", json={"id": "M-KI", "daten": STRECKE})
    assert r.status_code == 201
    assert welt.c["bh"].get(BASIS + "/messungen").json()["messungen"] == []
    assert len(welt.c["bh"].get(BASIS + "/messungen?vorschlaege=1").json()["messungen"]) == 1
    assert welt.post("rueck", BASIS + "/messungen/M-KI/verwerfen",
                     {"version": 1}).status_code == 403
    assert welt.post("bear", BASIS + "/messungen/M-KI/verwerfen",
                     {"version": 1}).status_code == 204
    assert welt.c["bh"].get(BASIS + "/messungen?vorschlaege=1").json()["messungen"] == []


def test_vorschlaege_nur_als_wahrheitswert(welt: Welt) -> None:  # noqa: F811
    assert welt.c["bh"].get(BASIS + "/pois?vorschlaege=vielleicht").status_code == 422


# --- Befund-Faden ---------------------------------------------------------------------------


def test_faden_status_per_agent_202_und_annehmen(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    st = welt.stand()
    r = agent.post(FADEN, json={"art": "status", "wert": "in_arbeit", "version": st["version"]})
    assert r.status_code == 202, r.text
    s = r.json()["eintrag"]["schluessel"]
    assert r.json()["eintrag"]["offen"] is True
    assert welt.stand()["status"] == "offen"
    assert agent.post(f"{FADEN}/{s}/annehmen", json={"version": st["version"]}).status_code == 403
    r = welt.post("bh", f"{FADEN}/{s}/annehmen", {"version": st["version"]})
    assert r.status_code == 201 and r.json()["stand"]["status"] == "in_arbeit"
    assert r.json()["eintrag"]["bezug"] == s


def test_faden_vorschlag_verwerfen(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.post(FADEN, json={"art": "frist", "wert": "2026-12-24"})
    assert r.status_code == 202, r.text
    s = r.json()["eintrag"]["schluessel"]
    r = welt.post("bh", f"{FADEN}/{s}/verwerfen", {"text": "Frist passt nicht"})
    assert r.status_code == 201 and r.json()["eintrag"]["bezug"] == s
    assert welt.stand()["frist"] is None
    assert welt.post("bh", f"{FADEN}/{s}/verwerfen", {}).status_code == 409


def test_faden_kommentar_per_agent_201_mit_herkunft(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    r = agent.post(FADEN, json={"art": "kommentar", "text": "Hinweis der KI"})
    assert r.status_code == 201 and r.json()["eintrag"]["herkunft"] == "ki"


def test_faden_vorschlag_fuer_betrachter_unsichtbar(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    s = agent.post(FADEN, json={"art": "frist", "wert": "2026-12-24"}).json()["eintrag"]
    lese = welt.token("betr", "lesen")
    for c in (lese, welt.c["betr"], welt.c["rueck"]):
        arten = [e["art"] for e in c.get(FADEN).json()["eintraege"]]
        assert "vorschlag" not in arten
    assert welt.post("rueck", f"{FADEN}/{s['schluessel']}/annehmen",
                     {"version": welt.stand()["version"]}).status_code == 404
    assert any(e["art"] == "vorschlag" for e in welt.c["bear"].get(FADEN).json()["eintraege"])


def test_faden_am_poi_vorschlag(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    agent.post(BASIS + "/pois", json={"id": "P-KI", "daten": PUNKT})
    pfad = BASIS + "/pois/P-KI/faden"
    assert welt.token("betr", "lesen").get(pfad).status_code == 404
    assert welt.c["betr"].get(pfad).status_code == 404
    assert welt.c["bh"].get(pfad).status_code == 200
    r = welt.post("bh", pfad, {"art": "kommentar", "text": "x"})
    assert r.status_code == 409
    r = welt.post("betr", pfad, {"art": "kommentar", "text": "x"})
    assert r.status_code in (403, 404)


def test_faden_pruefen_csrf_und_schluessel(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    s = agent.post(FADEN, json={"art": "frist", "wert": "2026-12-24"}).json()["eintrag"]
    v = welt.stand()["version"]
    assert welt.c["bh"].post(f"{FADEN}/{s['schluessel']}/annehmen",
                             json={"version": v}).status_code == 403
    assert welt.post("bh", f"{FADEN}/zzzz/annehmen", {"version": v}).status_code == 404
    assert welt.post("bh", f"{FADEN}/{s['schluessel']}/annehmen", {}).status_code == 422
    assert welt.post("bh", f"{FADEN}/{s['schluessel']}/verwerfen",
                     {"text": 5}).status_code == 422


# --- zu prüfen ------------------------------------------------------------------------------


def test_zu_pruefen_sammelt_alles(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    agent.post(BASIS + "/pois", json={"id": "P-KI", "daten": PUNKT})
    agent.post(BASIS + "/messungen", json={"id": "M-KI", "daten": STRECKE})
    r = agent.post(FADEN, json={"art": "frist", "wert": "2026-12-24"})
    entschieden = agent.post(FADEN, json={"art": "frist", "wert": "2026-12-25"})
    welt.post("bh", f"{FADEN}/{entschieden.json()['eintrag']['schluessel']}/verwerfen", {})
    agent.delete(BASIS + f"/pois/P-1?version={_version(welt)}")
    r = welt.c["bh"].get(BASIS + "/zu-pruefen")
    assert r.status_code == 200
    d = r.json()
    assert {p["id"] for p in d["pois"]} == {"P-KI", "P-1"}
    assert [m["id"] for m in d["messungen"]] == ["M-KI"]
    assert [(x["poi"], x["eintrag"]["wert"]["wert"]) for x in d["faden"]] == [
        ("P-1", "2026-12-24")]
    assert welt.token("bear", "lesen").get(BASIS + "/zu-pruefen").status_code == 200
    for wer in ("betr", "rueck"):
        assert welt.c[wer].get(BASIS + "/zu-pruefen").status_code == 403, wer
    assert welt.token("betr", "lesen").get(BASIS + "/zu-pruefen").status_code == 403
    assert welt.c["bh"].get("/api/v1/projekte/kirche/zu-pruefen").status_code == 404



def test_loeschvorschlag_fuer_nicht_pruefer_unsichtbar(welt: Welt) -> None:  # noqa: F811
    agent = welt.token("bear", "schreiben", agent=True)
    assert agent.delete(BASIS + f"/pois/P-1?version={_version(welt)}").status_code == 202
    lese = welt.token("betr", "lesen")
    for c in (lese, welt.c["betr"], welt.c["rueck"]):
        [p] = c.get(BASIS + "/pois?vorschlaege=1").json()["pois"]
        assert p["loeschenVorgeschlagen"] is None
        for b in c.get(BASIS + "/befunde").json()["befunde"]:
            assert b["poi"]["loeschenVorgeschlagen"] is None
    # Auch Prüfer sehen ihn nur mit Opt-in; Exporte und Befundliste nie.
    bh = welt.c["bh"]
    assert all(p.get("loeschenVorgeschlagen") is None
               for p in bh.get(BASIS + "/export.json").json()["pois"])
    assert all(b["poi"]["loeschenVorgeschlagen"] is None
               for b in bh.get(BASIS + "/befunde").json()["befunde"])
    assert bh.get(BASIS + "/pois").json()["pois"][0]["loeschenVorgeschlagen"] is None
