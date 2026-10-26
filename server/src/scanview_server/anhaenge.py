"""Fotos und PDFs am Befund-Faden (F7): Dateiseite im Webdienst.

Der Webdienst liest von einem hochgeladenen Anhang nur die Kopfbytes (Signatur) und zählt die
Größe beim Empfang mit; dekodiert werden Bilder ausschließlich im isolierten Auftrag des
Import-Workers (`anhang_bereinigen`). PDFs werden nie gelesen oder angezeigt, nur unverändert
als Download ausgeliefert (`Content-Disposition: attachment`, Hinweis „PDF aus externer Quelle“).

Ordner unter `SCANVIEW_ANHAENGE` (außerhalb der Paketwurzel, nie von Caddy ausgeliefert):
- `eingang/<id>`: Rohdatei, vom Webdienst geschrieben, vom Worker gelesen; nach der Übernahme weg;
- `geprueft/<id>/`: `bild.jpg` + `info.json` vom Worker (fremd: erneut geprüft);
- `ablage/<projekt>/<sha256>.<jpg|pdf>`: ausgelieferte Dateien, vom Webdienst geschrieben.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import stat
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MAX_BYTES = 20 * 1024 * 1024
BILD_MAX = 32 * 1024 * 1024
RESERVE_ANTEIL = 0.10
ID_RE = re.compile(r"[0-9a-f]{32}")
SHA_RE = re.compile(r"[0-9a-f]{64}")
_AUFNAHME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_NAME_RE = re.compile(r"[^\w .()+,-]", re.UNICODE)
NAME_MAX = 120
_NEU = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
_LESEN = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC

TYPEN = {"image/jpeg": "bild", "image/png": "bild", "application/pdf": "pdf"}
NUR_TYPEN = "Erlaubt sind Fotos (JPEG, PNG) und PDF."
HEIC_TEXT = ("HEIC wird noch nicht angenommen – bitte als JPEG hochladen (iPhone: Einstellungen, "  # noqa: RUF001 (Gedankenstrich im Nutzertext)
             "Kamera, Formate: „Maximale Kompatibilität“).")
SIGNATUR_FALSCH = "Der Dateiinhalt passt nicht zum angegebenen Typ."
ZU_GROSS = "Höchstens 20 MB je Anhang."
LEER = "Die Datei ist leer."
PLATZ = "Auf dem Server ist gerade zu wenig Platz für Anhänge."
QUOTE = "Die Anhang-Quote des Projekts ist erschöpft."


class AnhangFehler(ValueError):
    """Nutzersichtbar, ohne Pfade. `status`: HTTP-Status der Antwort."""

    def __init__(self, text: str, status: int = 422) -> None:
        super().__init__(text)
        self.status = status


def neue_id() -> str:
    return secrets.token_hex(16)


def typ_pruefen(content_type: str, kopf: bytes) -> str:
    """`bild` oder `pdf`, wenn Content-Type und Signatur zusammenpassen; sonst AnhangFehler.
    SVG, HTML und alles andere scheitern an der Positivliste (nie am Inhalt erkannt)."""
    ct = content_type.split(";", 1)[0].strip().lower()
    if kopf[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftyphevc") or ct in (
            "image/heic", "image/heif"):
        raise AnhangFehler(HEIC_TEXT, 415)
    art = TYPEN.get(ct)
    if art is None:
        raise AnhangFehler(NUR_TYPEN, 415)
    passt = {
        "image/jpeg": kopf.startswith(b"\xff\xd8\xff"),
        "image/png": kopf.startswith(b"\x89PNG\r\n\x1a\n"),
        "application/pdf": kopf.startswith(b"%PDF-"),
    }[ct]
    if not passt:
        raise AnhangFehler(SIGNATUR_FALSCH, 415)
    return art


def dateiname(roh: str | None) -> str | None:
    """Anzeigename (nie als Pfad verwendet): ohne Verzeichnisanteil, nur harmlose Zeichen."""
    if not roh:
        return None
    name = roh.replace("\\", "/").rsplit("/", 1)[-1]
    name = _NAME_RE.sub("_", name).strip(" .")[:NAME_MAX]
    return name or None


def ordner(wurzel: Path, teil: str) -> Path:
    """Ordner 0750 — ausdrücklich gesetzt, die Unit des Webdienstes hat UMask=0077 und der Worker
    liest `eingang/` über die Gruppe."""
    d = wurzel / teil
    d.mkdir(mode=0o750, parents=True, exist_ok=True)
    d.chmod(0o750)
    return d


def platz_pruefen(wurzel: Path) -> None:
    u = shutil.disk_usage(wurzel)
    if u.free < u.total * RESERVE_ANTEIL + MAX_BYTES:
        raise AnhangFehler(PLATZ, 507)


async def empfangen(strom: AsyncIterator[bytes], ziel: Path) -> tuple[int, str, bytes]:
    """Körper in eine neue Datei streamen (O_EXCL, ohne Symlink), höchstens MAX_BYTES; liefert
    (Größe, SHA-256, Kopfbytes). Bei Überschreitung wird die Datei entfernt."""
    fd = os.open(ziel, _NEU, 0o640)
    os.fchmod(fd, 0o640)  # UMask=0077 der Unit: der Worker liest über die Gruppe
    h = hashlib.sha256()
    groesse = 0
    kopf = b""
    try:
        async for stueck in strom:
            groesse += len(stueck)
            if groesse > MAX_BYTES:
                raise AnhangFehler(ZU_GROSS, 413)
            if len(kopf) < 16:
                kopf = (kopf + stueck)[:16]
            h.update(stueck)
            os.write(fd, stueck)
        if groesse == 0:
            raise AnhangFehler(LEER)
    except BaseException:
        os.close(fd)
        _weg(str(ziel))
        raise
    os.close(fd)
    return groesse, h.hexdigest(), kopf


def _weg(pfad: str) -> None:
    try:
        os.unlink(pfad)
    except FileNotFoundError:
        pass


def _kopieren(quelle: Path, ziel: Path, grenze: int) -> tuple[int, str]:
    """Reguläre Datei ohne Symlink-Folgen in eine neue Datei kopieren; (Größe, SHA-256)."""
    fq = os.open(quelle, _LESEN)
    try:
        st = os.fstat(fq)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1 or not 0 < st.st_size <= grenze:
            raise ValueError("keine passende reguläre Datei")
        tmp = ziel.with_name(f".{ziel.name}.{secrets.token_hex(4)}")
        fz = os.open(tmp, _NEU, 0o640)
        h = hashlib.sha256()
        n = 0
        try:
            while block := os.read(fq, 1 << 20):
                n += len(block)
                if n > grenze:
                    raise ValueError("Datei wächst")
                h.update(block)
                os.write(fz, block)
        except BaseException:
            os.close(fz)
            tmp.unlink(missing_ok=True)
            raise
        os.close(fz)
    finally:
        os.close(fq)
    os.replace(tmp, ziel)
    return n, h.hexdigest()


@dataclass(frozen=True)
class Abgelegt:
    groesse: int
    sha256: str
    breite: int | None = None
    hoehe: int | None = None
    aufnahme: str | None = None


def pdf_ablegen(wurzel: Path, pid: str, aid: str) -> Abgelegt:
    """PDF unverändert aus dem Eingang in die Ablage (nur Download, nie gelesen)."""
    ziel_dir = ordner(wurzel, f"ablage/{pid}")
    tmp = ziel_dir / f".{aid}.pdf"
    n, sha = _kopieren(wurzel / "eingang" / aid, tmp, MAX_BYTES)
    os.replace(tmp, ziel_dir / f"{sha}.pdf")
    (wurzel / "eingang" / aid).unlink(missing_ok=True)
    return Abgelegt(groesse=n, sha256=sha)


def bild_uebernehmen(wurzel: Path, pid: str, aid: str) -> Abgelegt:
    """Ausgabe des Workers (fremd) prüfen und in die Ablage kopieren. Wirft ValueError."""
    quelle = wurzel / "geprueft" / aid
    if quelle.is_symlink() or not quelle.is_dir():
        raise ValueError("keine Ausgabe")
    roh = (quelle / "info.json").read_bytes()[:4097]
    if len(roh) > 4096:
        raise ValueError("info.json zu groß")
    info: Any = json.loads(roh)
    if not isinstance(info, dict) or set(info) - {"breite", "hoehe", "aufnahme"}:
        raise ValueError("info.json Form")
    b, h, auf = info.get("breite"), info.get("hoehe"), info.get("aufnahme")
    if type(b) is not int or type(h) is not int or not (0 < b <= 8192 and 0 < h <= 8192):
        raise ValueError("Maße")
    if auf is not None and (not isinstance(auf, str) or not _AUFNAHME_RE.fullmatch(auf)):
        raise ValueError("Aufnahmezeit")
    ziel_dir = ordner(wurzel, f"ablage/{pid}")
    tmp = ziel_dir / f".{aid}.jpg"
    n, sha = _kopieren(quelle / "bild.jpg", tmp, BILD_MAX)
    with tmp.open("rb") as f:
        if f.read(3) != b"\xff\xd8\xff":
            tmp.unlink()
            raise ValueError("kein JPEG")
    os.replace(tmp, ziel_dir / f"{sha}.jpg")
    (wurzel / "eingang" / aid).unlink(missing_ok=True)
    return Abgelegt(groesse=n, sha256=sha, breite=b, hoehe=h, aufnahme=auf)


def ablage_pfad(wurzel: Path, pid: str, sha: str, typ: str) -> Path | None:
    """Pfad einer ausgelieferten Datei, wenn sie regulär ist und ihr Name zum Hash passt."""
    if not SHA_RE.fullmatch(sha) or typ not in ("bild", "pdf"):
        return None
    p = wurzel / "ablage" / pid / f"{sha}.{'jpg' if typ == 'bild' else 'pdf'}"
    try:
        st = p.lstat()
    except OSError:
        return None
    return p if stat.S_ISREG(st.st_mode) else None


def belegt(wurzel: Path, pid: str) -> int:
    """Bytes der Ablage eines Projekts (für die Quote)."""
    d = wurzel / "ablage" / pid
    if not d.is_dir():
        return 0
    return sum(p.lstat().st_size for p in d.iterdir() if p.is_file() and not p.is_symlink())


# --- Datenbank: Anhänge als Faden-Einträge -------------------------------------------------------

JE_BEFUND_MAX = 50
FEHLER_VERARBEITUNG = "Die Datei ließ sich nicht verarbeiten."
ZU_VIELE_JE_BEFUND = f"Höchstens {JE_BEFUND_MAX} Anhänge je Befund."
WARTESCHLANGE_VOLL = "Gerade werden zu viele Fotos geprüft – bitte gleich noch einmal."  # noqa: RUF001 (Gedankenstrich im Nutzertext)


def _zeile_json(r: Any) -> dict[str, Any]:
    return {"id": r["id"], "typ": r["typ"], "status": r["status"], "name": r["name"],
            "groesse": int(r["groesse"]), "breite": r["breite"], "hoehe": r["hoehe"],
            "aufnahme": r["aufnahme"], "fehler": r["fehler"]}


def anreichern(conn: Any, eintraege: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Einträge der Art `anhang` um ihren Verarbeitungsstand ergänzen (Feld `anhang`)."""
    schluessel = [e["schluessel"] for e in eintraege if e.get("art") == "anhang"]
    if not schluessel:
        return eintraege
    zeilen = {r["schluessel"]: r for r in conn.execute(
        "SELECT * FROM anhaenge WHERE schluessel IN (SELECT value FROM json_each(?))",
        (json.dumps(schluessel),))}
    for e in eintraege:
        if e.get("art") == "anhang":
            r = zeilen.get(e["schluessel"])
            e["anhang"] = None if r is None or e.get("zurueckgezogen_am") else _zeile_json(r)
    return eintraege


def belegt_db(conn: Any, pid: str) -> int:
    """Bytes des Projekts laut Datenbank (bereit + in Prüfung) — Grundlage der Quote."""
    return int(conn.execute("SELECT COALESCE(SUM(groesse), 0) FROM anhaenge WHERE project_id=?"
                            " AND status IN ('pruefung', 'bereit')", (pid,)).fetchone()[0])


def abgleichen(conn: Any, jc: Any, wurzel: Path, pid: str, now: int) -> int:
    """Anhänge „in Prüfung“ mit dem Stand ihres Auftrags abgleichen: fertig → Ausgabe als fremde
    Eingabe übernehmen (bereit), fehlgeschlagen → abgelehnt. Liefert die Zahl der Änderungen."""
    from . import auftraege

    n = 0
    for r in conn.execute("SELECT id, auftrag FROM anhaenge WHERE project_id=? AND"
                          " status='pruefung'", (pid,)).fetchall():
        a = auftraege.anhang_auftrag(jc, str(r["auftrag"] or ""))
        if a is None or a.status in auftraege.OFFEN:
            continue
        if a.status == auftraege.FERTIG:
            try:
                ab = bild_uebernehmen(wurzel, pid, str(r["id"]))
            except (OSError, ValueError):
                conn.execute("UPDATE anhaenge SET status='abgelehnt', fehler=?, geaendert_am=?"
                             " WHERE id=?", (FEHLER_VERARBEITUNG, now, r["id"]))
            else:
                conn.execute("UPDATE anhaenge SET status='bereit', sha256=?, groesse=?, breite=?,"
                             " hoehe=?, aufnahme=?, geaendert_am=? WHERE id=?",
                             (ab.sha256, ab.groesse, ab.breite, ab.hoehe, ab.aufnahme, now,
                              r["id"]))
        else:
            # Meldung des Workers (fremd): nur kurze, pfadfreie Texte übernehmen.
            text = a.fehler if (a.fehler and len(a.fehler) <= 200 and "/" not in a.fehler
                                and a.fehler.isprintable()) else FEHLER_VERARBEITUNG
            conn.execute("UPDATE anhaenge SET status='abgelehnt', fehler=?, geaendert_am=?"
                         " WHERE id=?", (text, now, r["id"]))
            _weg(str(wurzel / "eingang" / str(r["id"])))
        n += 1
    return n


def entfernen(conn: Any, wurzel: Path, schluessel: str, now: int) -> None:
    """Nach dem Zurückziehen: Datei sofort löschen, wenn kein anderer Anhang dieselbe nutzt."""
    r = conn.execute("SELECT * FROM anhaenge WHERE schluessel=?", (schluessel,)).fetchone()
    if r is None:
        return
    conn.execute("UPDATE anhaenge SET status='entfernt', geaendert_am=? WHERE id=?", (now, r["id"]))
    _weg(str(wurzel / "eingang" / str(r["id"])))
    if r["sha256"] is None:
        return
    andere = conn.execute("SELECT 1 FROM anhaenge WHERE project_id=? AND sha256=? AND id<>? AND"
                          " status IN ('pruefung', 'bereit')", (r["project_id"], r["sha256"],
                                                                 r["id"])).fetchone()
    if andere is None:
        p = ablage_pfad(wurzel, str(r["project_id"]), str(r["sha256"]), str(r["typ"]))
        if p is not None:
            _weg(str(p))
