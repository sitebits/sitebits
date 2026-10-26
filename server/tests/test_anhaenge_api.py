"""F7: Fotos und PDFs am Befund-Faden über die API — Abnahme und Negativtests.

Abnahme lokal: Rückmelder bekommt eine Mail (Test-SMTP), folgt dem Link zum Faden und antwortet
im Browser mit einem Foto; der Jobläufer (Test-Läufer statt Unit, Decoder-Ausgabe synthetisch)
bereinigt es, danach ist es für Projektbeteiligte abrufbar. Die echte Dekodierung prüfen
import/tests/test_anhang.py und die Läufer-Tests in test_worker_anhang.py."""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import ORIGIN, csrf
from fadenwelt import FADEN, Welt, welt  # noqa: F401
from fastapi.testclient import TestClient
from smtpfake import SmtpFake

from scanview_server import auftraege, post
from scanview_server.config import Settings
from scanview_worker import worker as W
from scanview_worker.laeufer import Ergebnis, Lauf, TestLaeufer

ANH = FADEN + "/anhaenge"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00\x10JFIF" + b"x" * 200 + b"\xff\xd9"
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 64
PDF = b"%PDF-1.7\n%synthetisch\n"
NEU = b"\xff\xd8\xff\xdbBEREINIGT" + b"y" * 50 + b"\xff\xd9"
ABSENDER = "benachrichtigung@example.test"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    for d in ("data", "state", "anh", "jobs", "berichte", "spool"):
        (tmp_path / d).mkdir()
    return Settings(data_dir=tmp_path / "data", state_dir=tmp_path / "state", origin=ORIGIN,
                    rp_id="scanview.test", rp_name="scanview", cookie_secure=True,
                    jobs_db=tmp_path / "jobs" / "jobs.db", berichte_dir=tmp_path / "berichte",
                    anhaenge_dir=tmp_path / "anh", anhaenge_quote_bytes=100 * 1024**2,
                    post_dir=tmp_path / "spool", post_absender=ABSENDER)


@pytest.fixture
def smtp() -> Iterator[SmtpFake]:
    with SmtpFake() as s:
        yield s


def _hoch(c: TestClient, inhalt: bytes, ct: str = "image/jpeg", name: str = "Foto 1.jpg",
          pfad: str = ANH) -> tuple[int, dict]:  # type: ignore[type-arg]
    r = c.post(pfad, params={"name": name}, content=inhalt,
               headers={**csrf(), "Content-Type": ct})
    return r.status_code, (r.json() if r.content else {})


def _worker(w: Welt, ausgabe: bytes = NEU, info: dict | None = None,  # type: ignore[type-arg]
            ergebnis: Ergebnis | None = None) -> TestLaeufer:
    s = w.client.app.state.settings  # type: ignore[attr-defined]

    def decoder(lauf: Lauf) -> Ergebnis:
        if ergebnis is not None:
            lauf.protokoll.write_text(
                "Abgelehnt: Bild hat zu viele Pixel (höchstens 40 Megapixel).\n")
            return ergebnis
        aus = Path(lauf.argv[-1])
        aus.mkdir(parents=True)
        (aus / "bild.jpg").write_bytes(ausgabe)
        (aus / "info.json").write_text(json.dumps(info or {"breite": 60, "hoehe": 30,
                                                           "aufnahme": "2026-10-21T14:05:09"}))
        return Ergebnis("ok", 0, 0.1)

    tl = TestLaeufer(decoder)
    wk = W.Worker(W.Einstellungen(jobs_db=s.jobs_db, berichte=s.berichte_dir, data_dir=s.data_dir,
                                  import_bin=Path("/opt/import/bin"), anhaenge=s.anhaenge_dir), tl)
    while wk.schritt():
        pass
    return tl


def test_abnahme_mail_link_antwort_mit_foto(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    from test_post import _einwilligen, _links, _pumpen, _versand

    _einwilligen(welt, "rueck", smtp)
    st = welt.stand()
    r = welt.post("bh", FADEN, {"art": "zustaendig", "wert": {"gewerk_id": welt.gewerk["Dach"],
                                                              "nutzer_id": welt.id["rueck"]},
                                "version": st["version"]})
    assert r.status_code == 201
    _pumpen(welt)
    _versand(welt, smtp).schritt()
    links = _links(smtp.zustand.empfangen[-1].nachricht.get_content())
    r = welt.c["rueck"].get(links["zum"], follow_redirects=False)
    assert r.headers["location"] == "/befunde?p=ost&b=P-1"
    # Antwort im Browser: Kommentar und Foto.
    assert welt.post("rueck", FADEN, {"art": "kommentar", "text": "Foto anbei."}).status_code \
        == 201
    status, d = _hoch(welt.c["rueck"], JPEG)
    assert status == 201, d
    e = d["eintrag"]
    assert e["art"] == "anhang" and e["anhang"]["status"] == "pruefung"
    aid = e["anhang"]["id"]
    # Vor der Prüfung nicht abrufbar.
    assert welt.c["bh"].get(f"{ANH}/{aid}").status_code == 404
    tl = _worker(welt)
    assert [list(lauf.argv)[1] for lauf in tl.laeufe] == ["anhang-bereinigen"]
    faden = welt.c["bh"].get(FADEN).json()
    anh = next(x for x in faden["eintraege"] if x["art"] == "anhang")["anhang"]
    assert anh["status"] == "bereit" and (anh["breite"], anh["hoehe"]) == (60, 30)
    assert anh["aufnahme"] == "2026-10-21T14:05:09" and anh["name"] == "Foto 1.jpg"
    for wer in ("bh", "rueck", "betr", "leitung"):
        r = welt.c[wer].get(f"{ANH}/{aid}")
        assert r.status_code == 200 and r.content == NEU, wer
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"].startswith("sandbox")
    assert "no-store" in r.headers["cache-control"]
    # Nie die Rohdatei: die ausgelieferte Datei ist die bereinigte.
    assert JPEG not in r.content


def test_pdf_nur_als_download(welt: Welt) -> None:  # noqa: F811
    status, d = _hoch(welt.c["bear"], PDF, "application/pdf", "Plan.pdf")
    assert status == 201 and d["eintrag"]["anhang"]["status"] == "bereit"
    r = welt.c["rueck"].get(f"{ANH}/{d['eintrag']['anhang']['id']}")
    assert r.status_code == 200 and r.content == PDF
    assert r.headers["content-disposition"].startswith("attachment")
    assert r.headers["content-type"] == "application/pdf"


@pytest.mark.parametrize("inhalt,ct,erwartet", [
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script/></svg>', "image/svg+xml", 415),
    (b"<!DOCTYPE html><html></html>", "text/html", 415),
    (b"<svg/>", "image/jpeg", 415),
    (JPEG, "image/png", 415),
    (b"\x00\x00\x00\x18ftypheic" + b"x" * 20, "image/heic", 415),
    (b"", "image/jpeg", 422),
])
def test_typen_abgelehnt(welt: Welt, inhalt: bytes, ct: str, erwartet: int) -> None:  # noqa: F811
    status, _ = _hoch(welt.c["bear"], inhalt, ct)
    assert status == erwartet
    assert welt.sql("SELECT COUNT(*) FROM anhaenge")[0][0] == 0
    s = welt.client.app.state.settings  # type: ignore[attr-defined]
    assert list((s.anhaenge_dir / "eingang").glob("*")) == []


def test_uebergross_413(welt: Welt) -> None:  # noqa: F811
    status, _ = _hoch(welt.c["bear"], b"\xff\xd8\xff" + b"0" * (20 * 1024 * 1024))
    assert status == 413


@pytest.mark.parametrize("wer,erwartet", [("betr", 403), ("fremd", 404)])
def test_rechte_hochladen(welt: Welt, wer: str, erwartet: int) -> None:  # noqa: F811
    status, _ = _hoch(welt.c[wer], JPEG)
    assert status == erwartet


def test_lese_token_laedt_nicht_hoch_schreib_token_schon(welt: Welt) -> None:  # noqa: F811
    lesen = welt.token("rueck", "lesen")
    r = lesen.post(ANH, content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 403
    schreiben = welt.token("rueck", "schreiben")
    r = schreiben.post(ANH, content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 201


def test_ohne_csrf_403(welt: Welt) -> None:  # noqa: F811
    r = welt.c["bear"].post(ANH, content=JPEG, headers={"Content-Type": "image/jpeg"})
    assert r.status_code == 403


def test_abgelehnt_vom_decoder(welt: Welt) -> None:  # noqa: F811
    _, d = _hoch(welt.c["bear"], JPEG)
    _worker(welt, ergebnis=Ergebnis("exit", 2, 0.1))
    anh = next(x for x in welt.c["bear"].get(FADEN).json()["eintraege"]
               if x["art"] == "anhang")["anhang"]
    assert anh["status"] == "abgelehnt" and "40 Megapixel" in anh["fehler"]
    assert welt.c["bear"].get(f"{ANH}/{d['eintrag']['anhang']['id']}").status_code == 404


def test_fremde_decoder_ausgabe_abgelehnt(welt: Welt) -> None:  # noqa: F811
    _hoch(welt.c["bear"], JPEG)
    _worker(welt, ausgabe=b"<html>kein jpeg</html>")
    anh = next(x for x in welt.c["bear"].get(FADEN).json()["eintraege"]
               if x["art"] == "anhang")["anhang"]
    assert anh["status"] == "abgelehnt"


def test_zurueckziehen_nur_verfasser_und_datei_weg(welt: Welt) -> None:  # noqa: F811
    _, d = _hoch(welt.c["rueck"], JPEG)
    _worker(welt)
    aid, sl = d["eintrag"]["anhang"]["id"], d["eintrag"]["schluessel"]
    assert welt.c["bh"].get(f"{ANH}/{aid}").status_code == 200  # übernimmt das Ergebnis
    s = welt.client.app.state.settings  # type: ignore[attr-defined]
    assert len(list((s.anhaenge_dir / "ablage" / "ost").glob("*.jpg"))) == 1
    r = welt.post("bear", f"{FADEN}/{sl}/zurueckziehen")
    assert r.status_code == 403
    r = welt.post("rueck", f"{FADEN}/{sl}/zurueckziehen")
    assert r.status_code == 200 and r.json()["eintrag"]["wert"] is None
    assert welt.c["bh"].get(f"{ANH}/{aid}").status_code == 404
    assert list((s.anhaenge_dir / "ablage" / "ost").glob("*.jpg")) == []
    e = next(x for x in welt.c["bh"].get(FADEN).json()["eintraege"] if x["schluessel"] == sl)
    assert e["anhang"] is None and e["wert"] is None


def test_geloeschter_befund_und_fremdes_projekt_404(welt: Welt) -> None:  # noqa: F811
    _, d = _hoch(welt.c["bear"], PDF, "application/pdf")
    aid = d["eintrag"]["anhang"]["id"]
    assert welt.c["fremd"].get(f"{ANH}/{aid}").status_code == 404
    assert welt.c["fremd"].get(f"/api/v1/projekte/kirche/pois/P-1/faden/anhaenge/{aid}"
                               ).status_code == 404
    welt.sql("UPDATE pois SET geloescht_am=1 WHERE id='P-1'")
    assert welt.c["bear"].get(f"{ANH}/{aid}").status_code == 404


def test_gast_und_anonym_nie(welt: Welt) -> None:  # noqa: F811
    _, d = _hoch(welt.c["bear"], PDF, "application/pdf")
    anonym = TestClient(welt.client.app, base_url=ORIGIN)
    assert anonym.get(f"{ANH}/{d['eintrag']['anhang']['id']}").status_code == 401


def test_grenze_je_befund_und_quote(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    from scanview_server import anhaenge

    monkeypatch.setattr(anhaenge, "JE_BEFUND_MAX", 2)
    assert _hoch(welt.c["bear"], PDF, "application/pdf")[0] == 201
    assert _hoch(welt.c["bear"], PDF, "application/pdf")[0] == 201
    assert _hoch(welt.c["bear"], PDF, "application/pdf")[0] == 422
    monkeypatch.setattr(anhaenge, "JE_BEFUND_MAX", 50)
    welt.sql("UPDATE anhaenge SET groesse=?", 45 * 1024**2)
    assert _hoch(welt.c["bear"], PDF, "application/pdf")[0] == 507


def test_warteschlange_voll_429(welt: Welt, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: F811
    monkeypatch.setattr(auftraege, "ANHAENGE_OFFEN_JE_PROJEKT_MAX", 1)
    assert _hoch(welt.c["bear"], JPEG)[0] == 201
    assert _hoch(welt.c["bear"], JPEG)[0] == 429
    assert welt.sql("SELECT COUNT(*) FROM anhaenge")[0][0] == 1


def test_anhang_loest_mail_aus(welt: Welt, smtp: SmtpFake) -> None:  # noqa: F811
    from test_post import _einwilligen, _pumpen

    _einwilligen(welt, "bh", smtp)
    _hoch(welt.c["bear"], PDF, "application/pdf")
    _pumpen(welt)
    assert welt.sql("SELECT status FROM benachrichtigung_ausgang WHERE ereignis='anhang'"
                    )[0][0] == "gerendert"


def test_name_nur_anzeigename(welt: Welt) -> None:  # noqa: F811
    _, d = _hoch(welt.c["bear"], PDF, "application/pdf", "../../etc/<b>passwd</b>.pdf")
    # Nur der letzte Pfadteil, harmlose Zeichen: nie ein Pfad, nie Markup.
    assert d["eintrag"]["anhang"]["name"] == "b_.pdf"


def test_ohne_anhaenge_ordner_404(client: TestClient) -> None:
    r = client.post("/api/v1/projekte/ost/pois/P-1/faden/anhaenge", content=JPEG,
                    headers={**csrf(), "Content-Type": "image/jpeg"})
    assert r.status_code in (401, 404)


def test_export_enthaelt_nur_metadaten(welt: Welt) -> None:  # noqa: F811
    _hoch(welt.c["bear"], PDF, "application/pdf")
    r = welt.c["leitung"].get("/api/v1/projekte/ost/export.json")
    assert r.status_code == 200
    roh = r.text
    assert "%PDF" not in roh and re.search(r'"art":\s*"anhang"', roh)


def test_mail_vormerken_bleibt_ohne_einwilligung_leer(welt: Welt) -> None:  # noqa: F811
    _hoch(welt.c["bear"], PDF, "application/pdf")
    assert welt.sql("SELECT COUNT(*) FROM benachrichtigung_ausgang")[0][0] == 0
    assert post.EREIGNIS_TEXT["anhang"].startswith("Es gibt einen neuen Anhang")


def test_lesezugriff_erfasst(welt: Welt) -> None:  # noqa: F811
    _, d = _hoch(welt.c["bear"], PDF, "application/pdf")
    welt.sql("DELETE FROM lesezugriffe")
    aid = d["eintrag"]["anhang"]["id"]
    assert welt.c["rueck"].get(f"{ANH}/{aid}").status_code == 200
    assert welt.c["rueck"].get(f"{ANH}/{aid}/stand").json()["status"] == "bereit"
    objekte = {r[0] for r in welt.sql("SELECT objekt FROM lesezugriffe WHERE nutzer_id=?",
                                      welt.id["rueck"])}
    assert objekte == {"anhang", "faden"}
