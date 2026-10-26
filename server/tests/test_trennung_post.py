"""Trennung Webdienst ↔ Postdienst (F8).

- `scanview_post` lädt nichts aus `scanview_server` (keine Datenbank, keine Konten) und nichts
  außer der Standardbibliothek.
- Der Webdienst öffnet keine Netzverbindung für Mail: kein `smtplib`, kein `socket` in
  `scanview_server`.
- Einstellungen des Postdienstes: TLS ab Werk, ohne TLS nur localhost, Passwortdatei nur mit
  Rechten 600, Benutzer und Passwort nur gemeinsam.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

from scanview_post import versand

SRC = Path(__file__).resolve().parent.parent / "src"


def _importe(paket: Path) -> set[str]:
    namen: set[str] = set()
    for datei in paket.rglob("*.py"):
        for k in ast.walk(ast.parse(datei.read_text(encoding="utf-8"))):
            if isinstance(k, ast.Import):
                namen |= {a.name.split(".")[0] for a in k.names}
            elif isinstance(k, ast.ImportFrom) and k.level == 0 and k.module:
                namen.add(k.module.split(".")[0])
    return namen


def test_postdienst_nur_standardbibliothek() -> None:
    fremd = {n for n in _importe(SRC / "scanview_post")
             if n not in sys.stdlib_module_names and n != "scanview_post"}
    assert fremd == set()


def test_postdienst_laedt_keinen_server() -> None:
    code = ("import sys, scanview_post.versand; "
            "print(sorted(m for m in sys.modules if m.startswith('scanview_server')))")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert r.stdout.strip() == "[]"


def test_webdienst_ohne_smtp_und_socket() -> None:
    assert not _importe(SRC / "scanview_server") & {"smtplib", "socket", "ssl"}


BASIS = {"SCANVIEW_POST": "/tmp/spool", "SCANVIEW_SMTP_HOST": "smtp.example.test",
         "SCANVIEW_POST_ABSENDER": "a@example.test"}


def test_vorgabe_tls_465() -> None:
    s = versand.Einstellungen.aus_umgebung(dict(BASIS))
    assert (s.sicherheit, s.port) == ("tls", 465)
    s = versand.Einstellungen.aus_umgebung({**BASIS, "SCANVIEW_SMTP_SICHERHEIT": "starttls"})
    assert (s.sicherheit, s.port) == ("starttls", 587)


def test_ohne_tls_nur_localhost() -> None:
    with pytest.raises(SystemExit, match="localhost"):
        versand.Einstellungen.aus_umgebung({**BASIS, "SCANVIEW_SMTP_SICHERHEIT": "keine"})
    s = versand.Einstellungen.aus_umgebung({**BASIS, "SCANVIEW_SMTP_SICHERHEIT": "keine",
                                            "SCANVIEW_SMTP_HOST": "127.0.0.1"})
    assert s.port == 25


def test_passwortdatei_nur_600(tmp_path: Path) -> None:
    datei = tmp_path / "pw"
    datei.write_text("geheim\n")
    datei.chmod(0o640)
    env = {**BASIS, "SCANVIEW_SMTP_BENUTZER": "u", "SCANVIEW_SMTP_PASSWORT_DATEI": str(datei)}
    with pytest.raises(SystemExit, match="600"):
        versand.Einstellungen.aus_umgebung(env)
    datei.chmod(0o600)
    assert versand.Einstellungen.aus_umgebung(env).passwort == "geheim"
    with pytest.raises(SystemExit, match="gemeinsam"):
        versand.Einstellungen.aus_umgebung({**BASIS, "SCANVIEW_SMTP_BENUTZER": "u"})


@pytest.mark.parametrize("absender", ["a@b.de\r\nBcc: x@y", "a@b.de, c@d.de", "kein-at"])
def test_absender_streng(absender: str) -> None:
    with pytest.raises((ValueError, SystemExit, IndexError)):
        versand.Einstellungen.aus_umgebung({**BASIS, "SCANVIEW_POST_ABSENDER": absender})


def _ausgang(tmp_path: Path, inhalt: str, mid: str = "a" * 32) -> Path:
    d = tmp_path / "ausgang"
    d.mkdir(exist_ok=True)
    (d / f"{mid}.json").write_text(inhalt)
    return d / f"{mid}.json"


def _roh(kopf: str) -> str:
    import base64

    return base64.b64encode(f"{kopf}\r\nSubject: x\r\n\r\nText\r\n".encode()).decode()


@pytest.mark.parametrize("inhalt", [
    '{"id": "' + "a" * 32 + '", "an": "x@example.test", "nachricht": "'
    + _roh("To: x@example.test\r\nBcc: y@boese.test") + '"}',
    '{"id": "' + "a" * 32 + '", "an": "x@example.test", "nachricht": "'
    + _roh("To: y@boese.test") + '"}',
    '{"id": "' + "b" * 32 + '", "an": "x@example.test", "nachricht": "'
    + _roh("To: x@example.test") + '"}',
    '{"id": "' + "a" * 32 + '", "an": "x@example.test\\r\\nRCPT TO:<y@boese.test>",'
    ' "nachricht": "' + _roh("To: x@example.test") + '"}',
])
def test_spooldatei_wird_streng_gelesen(tmp_path: Path, inhalt: str) -> None:
    with pytest.raises(ValueError):
        versand.lesen(_ausgang(tmp_path, inhalt))
