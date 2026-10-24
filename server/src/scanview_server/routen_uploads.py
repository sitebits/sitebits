"""/api/v1/uploads und /api/v1/importe: Hochladen in Stücken und neues Projekt aus Mesh/Splats.

Protokoll (eigenes, kleines; keine Bibliothek):
1. `POST /uploads` kündigt die Dateiliste an (relative Pfade, Größen); Quote und Platz werden vor
   der Annahme geprüft, der Server legt Ordner und leere Dateien an.
2. `PUT /uploads/{id}/dateien/{nr}` schreibt ein Stück von genau 64 MiB (das letzte den Rest) an
   die Stelle aus `Content-Range`, mit `Content-Digest: sha-256=:…:` des Stücks. Fortsetzen: `HEAD`
   liefert den Stand (`Upload-Offset`), das nächste Stück beginnt dort. Ein Stück an anderer
   Stelle, zu lang, zu kurz oder mit falscher Prüfsumme wird abgelehnt und zurückgenommen.
3. `POST /uploads/{id}/abschliessen` rechnet die SHA-256 jeder Datei nach (optional gegen die
   des Aufrufers) und prüft Endung und Kopfbytes.
4. `POST /importe` reiht einen Importauftrag ein; den baut der Import-Worker isoliert.

Rechte: nur Admins der Instanz (Nicht-Admins 404 wie Verwaltungswege), auch per Token mit Umfang
„schreiben“ — für Skripte und Agenten; große Dateien gehören nicht durch ein Sprachmodell, das
MCP-Werkzeug startet nur den Import. Mit `SCANVIEW_IMPORT` aus antworten alle Routen 404.

Halbe und nicht mehr genutzte Uploads entfernt der Dienst nach 24 Stunden ohne Fortschritt.
"""

from __future__ import annotations

import shutil
import sqlite3
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import auftraege, konten, protokoll
from . import db as dbmod
from . import uploads as U
from .api_modelle import FEHLER_OHNE_KOERPER
from .db import transaktion
from .web import Ungueltig, csrf, db, fehler, kanal, ki, token_name
from .zugriff import NICHT_GEFUNDEN, angemeldet

router = APIRouter(prefix="/api/v1")

QUOTE = "Die Upload-Quote der Instanz ist erschöpft – erst ältere Uploads löschen."  # noqa: RUF001
PLATZ = "Zu wenig freier Speicherplatz für diesen Upload."
STAND = "Das Stück passt nicht zum Stand der Datei; Stand siehe Upload-Offset."
BELEGT = "An dieser Datei wird gerade ein Stück geschrieben."
NICHT_OFFEN = "Der Upload ist schon abgeschlossen."
UNVOLLSTAENDIG = "Noch nicht alle Dateien vollständig hochgeladen."
HASH = "Prüfsumme des Stücks stimmt nicht – Stück verworfen, bitte erneut senden."  # noqa: RUF001
HASH_DATEI = "SHA-256 einer Datei stimmt nicht mit der angegebenen überein."
IN_ARBEIT = "Der Upload wird gerade von einem Import gelesen."
VERGEBEN = "Diese Projekt-ID ist schon vergeben."
ZU_VIELE = "Es laufen schon zu viele Importe – bitte warten, bis einer fertig ist."  # noqa: RUF001
NICHT_ABGESCHLOSSEN = "Der Upload ist noch nicht abgeschlossen."
NICHT_MEHR_OFFEN = "Der Import ist schon beendet."
DEFEKT = "Die Upload-Ablage ist beschädigt – den Upload löschen und neu beginnen."  # noqa: RUF001
EINE_DATEI = ("Für ein neues Projekt aus {art} genau eine Datei hochladen ({endungen}); "
              "texturierte Meshes und Ordner kommen später.")


class DateiIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pfad: str = Field(min_length=1, max_length=1300)
    groesse: int = Field(gt=0, le=2**50)


class UploadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dateien: list[DateiIn] = Field(min_length=1, max_length=U.MAX_DATEIEN + 1)


class PruefsummeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    nr: int = Field(ge=0, le=U.MAX_DATEIEN)
    sha256: str = Field(min_length=64, max_length=64)


class AbschliessenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dateien: list[PruefsummeIn] | None = Field(default=None, max_length=U.MAX_DATEIEN)


class ImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload: str = Field(min_length=32, max_length=32)
    typ: str = Field(max_length=40)
    optionen: dict[str, Any] = Field(default_factory=dict)


# --- Zugang ----------------------------------------------------------------------------------


def _an(request: Request) -> tuple[Path, Path]:
    s = request.app.state.settings
    if not s.import_enabled or s.uploads_dir is None or s.jobs_db is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return s.uploads_dir, s.jobs_db


def _admin(request: Request, conn: sqlite3.Connection) -> konten.Nutzer:
    """401 ohne Anmeldung, 404 ohne Import oder für Nicht-Admins, 403 für Lese-Tokens beim
    Schreiben (Vorrang wie bei den Verwaltungswegen, aber Tokens sind hier erlaubt)."""
    _an(request)
    n = angemeldet(request, conn, umfang=False)
    if not n.is_admin:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    angemeldet(request, conn)  # Umfang: Lese-Token schreibend → 403
    return n


def jobs(request: Request) -> Iterator[sqlite3.Connection | None]:
    pfad: Path | None = request.app.state.settings.jobs_db
    if pfad is None:
        yield None
        return
    jc = auftraege.verbinden(pfad)
    try:
        yield jc
    finally:
        jc.close()


@dataclass
class Sperren:
    """Je (Upload, Datei) schreibt höchstens ein PUT gleichzeitig."""

    lock: threading.Lock
    belegt: set[tuple[str, int]]

    @staticmethod
    def neu() -> Sperren:
        return Sperren(threading.Lock(), set())

    def nehmen(self, schluessel: tuple[str, int]) -> bool:
        with self.lock:
            if schluessel in self.belegt:
                return False
            self.belegt.add(schluessel)
            return True

    def geben(self, schluessel: tuple[str, int]) -> None:
        with self.lock:
            self.belegt.discard(schluessel)


def _sperren(request: Request) -> Sperren:
    s: Sperren | None = getattr(request.app.state, "upload_sperren", None)
    if s is None:
        s = request.app.state.upload_sperren = Sperren.neu()
    return s


# --- Aufräumen -------------------------------------------------------------------------------


def aufraeumen(wurzel: Path, conn: sqlite3.Connection, jc: sqlite3.Connection,
               now: int | None = None) -> int:
    """Uploads ohne Fortschritt seit 24 h entfernen (außer ein offener Import liest sie) und
    Ordner ohne Eintrag (abgebrochenes Anlegen). Liefert die Zahl entfernter Uploads."""
    in_arbeit = auftraege.uploads_in_arbeit(jc)
    n = 0
    for uid in U.abgelaufen(conn, now):
        if uid in in_arbeit:
            continue
        with transaktion(conn):
            U.austragen(conn, uid)
            protokoll.ereignis(conn, kanal="cli", art="upload.abgelaufen", objekt_id=uid, now=now)
        U.entfernen(wurzel, uid)
        n += 1
    bekannt = {r[0] for r in conn.execute("SELECT id FROM uploads")}
    if wurzel.is_dir():
        for d in wurzel.iterdir():
            if U.UPLOAD_ID_RE.fullmatch(d.name) and d.name not in bekannt:
                U.entfernen(wurzel, d.name)
    return n


def _upload(conn: sqlite3.Connection, uid: str) -> U.Upload:
    u = U.lesen(conn, uid)
    if u is None:
        raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)
    return u


def _datei(u: U.Upload, nr: int) -> U.Datei:
    for d in u.dateien:
        if d.nr == nr:
            return d
    raise StarletteHTTPException(status_code=404, detail=NICHT_GEFUNDEN)


# --- Uploads ---------------------------------------------------------------------------------


@router.post("/uploads", dependencies=[Depends(csrf)], status_code=201,
             summary="Upload ankündigen (Admin): Dateiliste mit relativen Pfaden und Größen")
def upload_anlegen(body: UploadIn, request: Request, conn: sqlite3.Connection = Depends(db),
                   jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    n = _admin(request, conn)
    wurzel, _ = _an(request)
    assert jc is not None
    s = request.app.state.settings
    try:
        pfade = U.pruefe_pfade([d.pfad for d in body.dateien])
        U.pruefe_endungen(pfade)
    except U.UploadFehler as e:
        raise Ungueltig(str(e)) from None
    aufraeumen(wurzel, conn, jc)
    summe = sum(d.groesse for d in body.dateien)
    if U.belegt(conn) + summe > s.upload_quote_bytes:
        return fehler(413, QUOTE)
    if _frei(wurzel) - summe < s.upload_reserve_bytes:
        return fehler(507, PLATZ)
    uid = U.neue_id()
    jetzt = int(time.time())
    weg = kanal(request)
    with transaktion(conn):
        U.eintragen(conn, uid, n.id, weg, [(d.pfad, d.groesse) for d in body.dateien], jetzt)
        protokoll.ereignis(conn, kanal=weg, nutzer_id=n.id, art="upload.angelegt",
                           objekt_id=uid, nachher={"dateien": len(pfade), "groesse": summe},
                           token_name=token_name(request), now=jetzt)
    try:
        U.anlegen(wurzel, uid, pfade)
    except BaseException:
        with transaktion(conn):
            U.austragen(conn, uid)
        U.entfernen(wurzel, uid)
        raise
    return JSONResponse(_upload(conn, uid).als_json(), status_code=201)


def _frei(wurzel: Path) -> int:
    return shutil.disk_usage(wurzel).free


@router.get("/uploads", summary="Uploads der Instanz (Admin), Quote und Belegung")
def uploads_auflisten(request: Request, conn: sqlite3.Connection = Depends(db),
                      jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    _admin(request, conn)
    wurzel, _ = _an(request)
    assert jc is not None
    aufraeumen(wurzel, conn, jc)
    s = request.app.state.settings
    return JSONResponse({"uploads": [u.als_json() for u in U.liste(conn)],
                         "quoteBytes": s.upload_quote_bytes, "belegtBytes": U.belegt(conn),
                         "stueckBytes": U.STUECK})


@router.get("/uploads/{uid}", summary="Ein Upload: Dateien, Stand, Prüfsummen")
def upload_lesen(uid: str, request: Request,
                 conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    _admin(request, conn)
    return JSONResponse(_upload(conn, uid).als_json())


@router.head("/uploads/{uid}/dateien/{nr}", status_code=204,
             summary="Stand einer Datei (Header Upload-Offset) zum Fortsetzen",
             responses=FEHLER_OHNE_KOERPER)
def stand(uid: str, nr: int, request: Request, conn: sqlite3.Connection = Depends(db),
          ) -> Response:
    _admin(request, conn)
    d = _datei(_upload(conn, uid), nr)
    return Response(status_code=204, headers={"Upload-Offset": str(d.stand),
                                              "Upload-Length": str(d.groesse)})


def _put_vorbereiten(request: Request, uid: str, nr: int) -> tuple[U.Datei, int, int, bytes]:
    conn = dbmod.connect(request.app.state.settings.db_path)
    try:
        _admin(request, conn)
        u = _upload(conn, uid)
        d = _datei(u, nr)
        if u.status != "offen":
            raise StarletteHTTPException(status_code=409, detail=NICHT_OFFEN)
        try:
            a, laenge = U.content_range(request.headers.get("content-range"), d.groesse)
            digest = U.content_digest(request.headers.get("content-digest"))
        except U.UploadFehler as e:
            raise StarletteHTTPException(status_code=422, detail=str(e)) from None
        if a != d.stand:
            raise StarletteHTTPException(status_code=409, detail=STAND,
                                         headers={"Upload-Offset": str(d.stand)})
        laenge_kopf = request.headers.get("content-length")
        if laenge_kopf is not None and laenge_kopf.isdigit() and int(laenge_kopf) != laenge:
            raise StarletteHTTPException(status_code=422 if int(laenge_kopf) < laenge else 413,
                                         detail="Länge des Stücks passt nicht zu Content-Range.")
        return d, a, laenge, digest
    finally:
        conn.close()


def _put_eintragen(request: Request, uid: str, d: U.Datei, a: int, laenge: int) -> bool:
    conn = dbmod.connect(request.app.state.settings.db_path)
    try:
        return U.stand_setzen(conn, uid, d.nr, a, a + laenge, int(time.time()))
    finally:
        conn.close()


@router.put("/uploads/{uid}/dateien/{nr}", dependencies=[Depends(csrf)],
            summary="Ein Stück schreiben (Content-Range, Content-Digest sha-256)")
async def stueck(uid: str, nr: int, request: Request) -> JSONResponse:
    d, a, laenge, digest = await run_in_threadpool(_put_vorbereiten, request, uid, nr)
    wurzel, _ = _an(request)
    schluessel = (uid, nr)
    sperren = _sperren(request)
    if not sperren.nehmen(schluessel):
        return fehler(409, BELEGT)
    try:
        try:
            w = await run_in_threadpool(U.Schreiber, wurzel, uid, d.teile, a)
        except (OSError, U.UploadFehler):
            return fehler(409, DEFEKT)
        gelesen = 0
        try:
            async for block in request.stream():
                gelesen += len(block)
                if gelesen > laenge:
                    raise StarletteHTTPException(status_code=413,
                                                 detail="Stück länger als angekündigt.")
                if block:
                    await run_in_threadpool(w.schreiben, block)
        except BaseException:
            await run_in_threadpool(w.abbrechen)
            raise
        if gelesen != laenge:
            await run_in_threadpool(w.abbrechen)
            return fehler(422, "Stück kürzer als angekündigt.")
        if not await run_in_threadpool(w.fertig, digest):
            return fehler(422, HASH)
        if not await run_in_threadpool(_put_eintragen, request, uid, d, a, laenge):
            return fehler(409, STAND)
    finally:
        sperren.geben(schluessel)
    return JSONResponse({"nr": nr, "stand": a + laenge, "groesse": d.groesse},
                        headers={"Upload-Offset": str(a + laenge)})


@router.post("/uploads/{uid}/abschliessen", dependencies=[Depends(csrf)],
             summary="Upload abschließen: SHA-256 nachrechnen, Endung und Kopfbytes prüfen")
def abschliessen(uid: str, request: Request, body: AbschliessenIn | None = None,
                 conn: sqlite3.Connection = Depends(db)) -> JSONResponse:
    n = _admin(request, conn)
    wurzel, _ = _an(request)
    u = _upload(conn, uid)
    if u.status != "offen":
        return JSONResponse(u.als_json())
    if any(d.stand != d.groesse for d in u.dateien):
        return fehler(409, UNVOLLSTAENDIG)
    erwartet: dict[int, str] = {}
    for p in (body.dateien if body is not None and body.dateien else []):
        if not U.ist_sha256(p.sha256):
            raise Ungueltig("sha256: 64 Hexzeichen in Kleinschreibung.")
        erwartet[p.nr] = p.sha256
    pruef: dict[int, tuple[str, str]] = {}
    for d in u.dateien:
        try:
            sha, kopf = U.sha256_und_kopf(wurzel, uid, d.teile, d.groesse)
            art = U.signatur(d.teile[-1], kopf, d.groesse)
        except U.UploadFehler as e:
            return fehler(422, f"{d.pfad}: {e}")
        except OSError:
            return fehler(409, DEFEKT)
        if d.nr in erwartet and erwartet[d.nr] != sha:
            return fehler(422, f"{d.pfad}: {HASH_DATEI}")
        pruef[d.nr] = (sha, art)
    with transaktion(conn):
        U.abschliessen(conn, uid, pruef, int(time.time()))
        protokoll.ereignis(conn, kanal=kanal(request), nutzer_id=n.id,
                           art="upload.abgeschlossen", objekt_id=uid,
                           nachher={"dateien": len(pruef)}, token_name=token_name(request))
    return JSONResponse(_upload(conn, uid).als_json())


@router.delete("/uploads/{uid}", dependencies=[Depends(csrf)], status_code=204,
               summary="Upload abbrechen bzw. löschen (räumt die Dateien weg)")
def upload_loeschen(uid: str, request: Request, conn: sqlite3.Connection = Depends(db),
                    jc: sqlite3.Connection | None = Depends(jobs)) -> Response:
    n = _admin(request, conn)
    wurzel, _ = _an(request)
    assert jc is not None
    _upload(conn, uid)
    if uid in auftraege.uploads_in_arbeit(jc):
        return fehler(409, IN_ARBEIT)
    with transaktion(conn):
        U.austragen(conn, uid)
        protokoll.ereignis(conn, kanal=kanal(request), nutzer_id=n.id, art="upload.geloescht",
                           objekt_id=uid, token_name=token_name(request))
    U.entfernen(wurzel, uid)
    return Response(status_code=204)


# --- Importe ---------------------------------------------------------------------------------


def _import_json(a: auftraege.Auftrag, jc: sqlite3.Connection) -> dict[str, Any]:
    fertig = a.status == auftraege.FERTIG
    return {
        "id": a.id, "typ": a.typ, "projekt": a.projekt, "titel": a.optionen.get("titel"),
        "upload": a.optionen.get("upload"), "datei": a.optionen.get("datei"),
        "crs": a.optionen.get("crs"), "status": a.status, "stufe": a.stufe,
        "warteplatz": auftraege.position(jc, a), "fehler": a.fehler,
        "erstelltAm": a.erstellt_am, "erstelltVon": a.erstellt_von, "kanal": a.kanal,
        "begonnenAm": a.begonnen_am, "beendetAm": a.beendet_am,
        "projektUrl": f"/api/v1/projekte/{a.projekt}" if fertig else None,
    }


@router.post("/importe", dependencies=[Depends(csrf)], status_code=202,
             summary="Neues Projekt aus einem abgeschlossenen Upload (Admin; neu_mesh, neu_splats)")
def import_starten(body: ImportIn, request: Request, conn: sqlite3.Connection = Depends(db),
                   jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    n = _admin(request, conn)
    assert jc is not None
    u = U.lesen(conn, body.upload)
    if u is None:
        raise Ungueltig("upload: unbekannt.")
    if u.status != "abgeschlossen":
        return fehler(409, NICHT_ABGESCHLOSSEN)
    if "datei" in body.optionen or "upload" in body.optionen:
        # Die Hauptdatei wählt der Dienst aus dem geprüften Upload, nie der Aufrufer.
        raise Ungueltig("Unbekannte Option: datei bzw. upload gehören nicht in optionen.")
    endungen = auftraege.IMPORT_ENDUNGEN.get(body.typ)
    if endungen is not None:
        art = "Mesh" if body.typ == "neu_mesh" else "Splats"
        passend = [d for d in u.dateien if d.pfad.lower().endswith(endungen)]
        if len(u.dateien) != 1 or len(passend) != 1:
            raise Ungueltig(EINE_DATEI.format(art=art, endungen=", ".join(endungen)))
        optionen = {**body.optionen, "upload": u.id, "datei": passend[0].pfad}
    else:
        optionen = {**body.optionen, "upload": u.id}
    try:
        geprueft = auftraege.pruefe(body.typ, optionen, erlaubt=auftraege.IMPORTE)
    except auftraege.AuftragUngueltig as e:
        raise Ungueltig(str(e)) from None
    pid = geprueft["projekt"]
    if request.app.state.katalog.get(pid) is not None or auftraege.projekt_in_arbeit(jc, pid):
        return fehler(409, VERGEBEN)
    weg = "agent" if ki(request) else kanal(request)
    try:
        aid = auftraege.einreihen(jc, projekt=pid, typ=body.typ, optionen=geprueft,
                                  nutzer_id=n.id, kanal=weg)
    except auftraege.ZuViele:
        return fehler(409, ZU_VIELE)
    with transaktion(conn):
        protokoll.ereignis(
            conn, kanal=kanal(request), nutzer_id=n.id, project_id=pid, art="import.gestartet",
            objekt_id=aid, nachher={"typ": body.typ, "upload": u.id,
                                    "crs": geprueft.get("crs")},
            token_name=token_name(request))
    a = auftraege.import_lesen(jc, aid)
    if a is None:
        return fehler(500, "Auftrag verschwunden")
    return JSONResponse({**_import_json(a, jc), "worker": auftraege.worker_zustand(jc)},
                        status_code=202)


@router.get("/importe", summary="Importaufträge der Instanz (Admin) und Zustand des Workers")
def importe_auflisten(request: Request, conn: sqlite3.Connection = Depends(db),
                      jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    _admin(request, conn)
    assert jc is not None
    return JSONResponse({"importe": [_import_json(a, jc) for a in auftraege.importe(jc)],
                         "worker": auftraege.worker_zustand(jc)})


@router.get("/importe/{aid}", summary="Ein Importauftrag: Status, Stufe, Fehler")
def import_lesen(aid: str, request: Request, conn: sqlite3.Connection = Depends(db),
                 jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    _admin(request, conn)
    assert jc is not None
    a = auftraege.import_lesen(jc, aid)
    if a is None:
        return fehler(404, NICHT_GEFUNDEN)
    return JSONResponse({**_import_json(a, jc), "worker": auftraege.worker_zustand(jc)})


@router.delete("/importe/{aid}", dependencies=[Depends(csrf)], status_code=202,
               summary="Import abbrechen (wartend sofort, laufend: der Worker stoppt den Lauf)")
def import_abbrechen(aid: str, request: Request, conn: sqlite3.Connection = Depends(db),
                     jc: sqlite3.Connection | None = Depends(jobs)) -> JSONResponse:
    n = _admin(request, conn)
    assert jc is not None
    a = auftraege.import_lesen(jc, aid)
    if a is None:
        return fehler(404, NICHT_GEFUNDEN)
    danach = auftraege.abbrechen_anfordern(jc, aid)
    if danach is None:
        return fehler(409, NICHT_MEHR_OFFEN)
    with transaktion(conn):
        protokoll.ereignis(conn, kanal=kanal(request), nutzer_id=n.id, project_id=a.projekt,
                           art="import.abgebrochen", objekt_id=aid,
                           token_name=token_name(request))
    b = auftraege.import_lesen(jc, aid)
    return JSONResponse(_import_json(b if b is not None else a, jc), status_code=202)
