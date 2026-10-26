"""Fotos und PDFs am Befund-Faden (F7): hochladen, Stand lesen, ausliefern — eigenes Zugriffstor.

Ohne `SCANVIEW_ANHAENGE` (braucht den Jobläufer) antworten alle Wege mit 404.

- **Hochladen** `POST …/pois/{eid}/faden/anhaenge?name=<Anzeigename>`: roher Körper (kein
  multipart), `Content-Type` image/jpeg, image/png oder application/pdf; höchstens 20 MB (Caddy
  begrenzt den Körper dieser Route eigens). Recht wie Kommentieren (Rückmelder und mehr, Sitzung
  mit CSRF oder Schreib-Token). Der Webdienst prüft nur Kopfbytes und Größe; Bilder dekodiert und
  kodiert der Jobläufer isoliert neu (Auftrag `anhang_bereinigen`), bis dahin „in Prüfung“. PDFs
  werden unverändert abgelegt und nur als Download ausgeliefert. Anhänge sind nie intern.
- **Ausliefern** `GET …/faden/anhaenge/{aid}`: Rechte wie Faden lesen (auch Rückmelder und
  Betrachter), nur wenn der Eintrag sichtbar, nicht zurückgezogen und „bereit“ ist; Befund
  gelöscht → 404. Nie an Gäste (Nur-Lese-Links kennen die API nicht). Kopfzeilen: nosniff,
  CSP `sandbox`, `private, no-store`; PDF immer `Content-Disposition: attachment`.
- Rückmelder ändern nur Eigenes: zurückziehen kann nur der Verfasser (Faden-Regel).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import anhaenge, auftraege, faden, lesezugriff
from . import db as dbmod
from .db import transaktion
from .routen_faden import _poi_id, _zugriff, sicht
from .web import csrf, db, kanal, token_name
from .zugriff import NICHT_GEFUNDEN

router = APIRouter(prefix="/api/v1/projekte/{pid}/pois/{eid}/faden/anhaenge")
NUR_KOMMENTIEREN = "Anhänge dürfen nur Beteiligte mit Recht zum Kommentieren anfügen."


def _wurzel(request: Request) -> tuple[Path, Path]:
    s = request.app.state.settings
    if s.anhaenge_dir is None or s.jobs_db is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return Path(s.anhaenge_dir), Path(s.jobs_db)


def abgleichen(request: Request, conn: sqlite3.Connection, pid: str) -> None:
    """Stand der Bildprüfung übernehmen (beim Lesen des Fadens und eines Anhangs)."""
    s = request.app.state.settings
    if s.anhaenge_dir is None or s.jobs_db is None:
        return
    jc = auftraege.verbinden(s.jobs_db)
    try:
        with transaktion(conn):
            anhaenge.abgleichen(conn, jc, Path(s.anhaenge_dir), pid, int(time.time()))
    finally:
        jc.close()


def _fehler(e: anhaenge.AnhangFehler) -> StarletteHTTPException:
    return StarletteHTTPException(status_code=e.status, detail=str(e))


@router.post("", status_code=201, dependencies=[Depends(csrf)], operation_id="anhang_hochladen",
             summary="Foto (JPEG, PNG) oder PDF an den Befund-Faden hängen: roher Körper, "
             "Content-Type, höchstens 20 MB; Bilder werden isoliert ohne Metadaten neu kodiert")
async def hochladen(pid: str, eid: str, request: Request, name: str | None = None) -> JSONResponse:
    wurzel, jobs_db = _wurzel(request)

    def vorab() -> tuple[Any, faden.Sicht, str]:
        conn = dbmod.connect(request.app.state.settings.db_path)
        try:
            z = _zugriff(request, conn, pid, schreiben=True)
            poi = _poi_id(eid)
            s = sicht(request, conn, z)
            r = faden._poi(conn, pid, poi, s)
            st = faden._stand(r)
            if not faden.rechte(s, st)["kommentieren"]:
                raise StarletteHTTPException(status_code=403, detail=NUR_KOMMENTIEREN)
            faden._gueltig(r)
            anzahl = conn.execute("SELECT COUNT(*) FROM anhaenge WHERE project_id=? AND"
                                  " poi_id=? AND status IN ('pruefung', 'bereit')",
                                  (pid, poi)).fetchone()[0]
            if anzahl >= anhaenge.JE_BEFUND_MAX:
                raise StarletteHTTPException(status_code=422, detail=anhaenge.ZU_VIELE_JE_BEFUND)
            quote = request.app.state.settings.anhaenge_quote_bytes
            if anhaenge.belegt_db(conn, pid) + anhaenge.MAX_BYTES > quote:
                raise StarletteHTTPException(status_code=507, detail=anhaenge.QUOTE)
            return z, s, poi
        finally:
            conn.close()

    z, s, poi = await run_in_threadpool(vorab)
    try:
        anhaenge.platz_pruefen(wurzel)
    except anhaenge.AnhangFehler as e:
        raise _fehler(e) from None
    ct = request.headers.get("content-type", "")
    laenge = request.headers.get("content-length", "")
    if laenge.isdigit() and int(laenge) > anhaenge.MAX_BYTES:
        raise _fehler(anhaenge.AnhangFehler(anhaenge.ZU_GROSS, 413))
    aid = anhaenge.neue_id()
    eingang = anhaenge.ordner(wurzel, "eingang") / aid
    try:
        groesse, _sha, kopf = await anhaenge.empfangen(request.stream(), eingang)
        typ = anhaenge.typ_pruefen(ct, kopf)
    except anhaenge.AnhangFehler as e:
        anhaenge._weg(str(eingang))
        raise _fehler(e) from None
    anzeigename = anhaenge.dateiname(name)

    def ablegen() -> dict[str, Any]:
        conn = dbmod.connect(request.app.state.settings.db_path)
        n = int(time.time())
        auftrag: str | None = None
        try:
            if typ == "bild":
                jc = auftraege.verbinden(jobs_db)
                try:
                    auftrag = auftraege.einreihen(
                        jc, projekt=pid, typ="anhang_bereinigen", optionen={"anhang": aid},
                        nutzer_id=z.nutzer.id, kanal=kanal(request))
                except auftraege.ZuViele:
                    raise StarletteHTTPException(
                        status_code=429, detail=anhaenge.WARTESCHLANGE_VOLL) from None
                finally:
                    jc.close()
                status, sha, abgelegt = "pruefung", None, None
            else:
                abgelegt = anhaenge.pdf_ablegen(wurzel, pid, aid)
                status, sha = "bereit", abgelegt.sha256
            with transaktion(conn):
                e = faden.einfuegen(
                    conn, pid, poi, art="anhang", text=None,
                    wert={"anhang": aid, "typ": typ, "name": anzeigename}, intern=False,
                    nutzer=s.nutzer, kanal=kanal(request), token_name=token_name(request), now=n)
                conn.execute(
                    "INSERT INTO anhaenge (id, schluessel, project_id, poi_id, typ, status,"
                    " auftrag, name, groesse, sha256, erstellt_von, erstellt_am, geaendert_am)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (aid, e["schluessel"], pid, poi, typ, status, auftrag, anzeigename,
                     abgelegt.groesse if abgelegt else groesse, sha, s.nutzer, n, n))
                anhaenge.anreichern(conn, [e])
            lesezugriff.erfasse_anfrage(request, conn, z, "api", "faden")
            return e
        finally:
            conn.close()

    try:
        eintrag = await run_in_threadpool(ablegen)
    except BaseException:
        anhaenge._weg(str(eingang))
        raise
    return JSONResponse({"eintrag": eintrag}, status_code=201)


def _zeile(conn: sqlite3.Connection, request: Request, pid: str, eid: str,
           aid: str) -> tuple[sqlite3.Row, Any]:
    z = _zugriff(request, conn, pid)
    poi = _poi_id(eid)
    s = sicht(request, conn, z)
    faden._poi(conn, pid, poi, s)  # gelöschter Befund → 404
    if not anhaenge.ID_RE.fullmatch(aid):
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    abgleichen(request, conn, pid)
    r = conn.execute(
        "SELECT a.*, e.zurueckgezogen_am, e.intern FROM anhaenge a JOIN befund_eintraege e"
        " ON e.schluessel = a.schluessel WHERE a.id=? AND a.project_id=? AND a.poi_id=?",
        (aid, pid, poi)).fetchone()
    if r is None or r["zurueckgezogen_am"] is not None or (r["intern"] and not s.intern):
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return r, z


@router.get("/{aid}/stand", operation_id="anhang_stand",
            summary="Stand eines Anhangs: in Prüfung, bereit oder abgelehnt (mit Grund)")
def stand(pid: str, eid: str, aid: str, request: Request,
          conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    _wurzel(request)
    r, z = _zeile(conn, request, pid, eid, aid)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "faden")
    return JSONResponse(anhaenge._zeile_json(r))


@router.get("/{aid}", operation_id="anhang_laden",
            summary="Datei eines Anhangs (Bild als JPEG, PDF immer als Download)",
            response_class=FileResponse,
            responses={200: {"content": {"image/jpeg": {}, "application/pdf": {}}}})
def laden(pid: str, eid: str, aid: str, request: Request,
          conn: sqlite3.Connection = Depends(db)) -> FileResponse:
    wurzel, _ = _wurzel(request)
    r, z = _zeile(conn, request, pid, eid, aid)
    pfad = (anhaenge.ablage_pfad(wurzel, pid, str(r["sha256"]), str(r["typ"]))
            if r["status"] == "bereit" and r["sha256"] else None)
    if pfad is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    lesezugriff.erfasse_anfrage(request, conn, z, "api", "anhang")
    pdf = r["typ"] == "pdf"
    kopf = {"X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox; default-src 'none'",
            "Cache-Control": "private, no-store",
            "Cross-Origin-Resource-Policy": "same-origin"}
    return FileResponse(pfad, media_type="application/pdf" if pdf else "image/jpeg",
                        headers=kopf, content_disposition_type="attachment" if pdf else "inline",
                        filename=f"anhang-{aid[:8]}.{'pdf' if pdf else 'jpg'}")
