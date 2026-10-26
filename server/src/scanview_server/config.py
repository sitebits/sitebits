"""Einstellungen je Instanz, ausschließlich aus Umgebungsvariablen (systemd EnvironmentFile)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .barrierefreiheit import Barrierefreiheit
from .rechtliches import Rechtliches

LESEZUGRIFFE_TAGE_MIN = 7
LESEZUGRIFFE_TAGE_MAX = 730
_HTTPS_URL = re.compile(r"https://[^\s/?#]+(?:[/?#]\S*)?")


@dataclass(frozen=True)
class Settings:
    data_dir: Path  # Paketwurzel, nur lesend
    state_dir: Path  # SQLite-Datenbank
    origin: str  # z. B. https://scanview.example.org (ohne Slash am Ende)
    rp_id: str  # WebAuthn Relying-Party-ID = Hostname
    rp_name: str
    cookie_secure: bool = True
    # Globale Anmeldebremse (WB-M2): ab so vielen fehlgeschlagenen Passwort-Anmeldungen je
    # Minute (alle Konten zusammen) weist der Dienst jede Passwort-Anmeldung mit 429 ab.
    anmelde_fehler_pro_minute: int = 30
    # Git-Export der Bearbeitungen ist aus, bis
    # SCANVIEW_GIT_EXPORT=1. Ohne SCANVIEW_GIT_DIR liegen die Repos unter state_dir/git.
    git_export: bool = False
    git_verzeichnis: Path | None = None
    # Vorlage mit {projekt} (ssh://git@forgejo.example/scanview/{projekt}.git); None = kein Push.
    git_remote: str | None = None
    # Quelltext-Link (EUPL-1.2) im Portal. None = kein Link; nur HTTPS, damit keine
    # javascript:-Adresse als Link im Portal landet.
    quelltext_url: str | None = None
    # Aufbewahrung des Lesezugriffsprotokolls in Tagen; 0 = Protokoll aus (auch vorhandene
    # Einträge werden beim Start gelöscht), sonst 7 bis 730 (SCANVIEW_LESEZUGRIFFE_TAGE).
    lesezugriffe_tage: int = 90
    # Erklärung zur Barrierefreiheit und Feedback-Weg, geprüft beim Start.
    barrierefreiheit: Barrierefreiheit = field(default_factory=Barrierefreiheit)
    # Links zu Impressum und Datenschutzhinweisen (den Rechtstext stellt der Betreiber),
    # geprüft beim Start.
    rechtliches: Rechtliches = field(default_factory=Rechtliches)
    # Auswertungsjobs: eigene Auftragsdatenbank (nie scanview.db) und Ablage der
    # Berichte, die der Import-Worker schreibt. Beide None = Auswertungen aus (Routen 404).
    jobs_db: Path | None = None
    berichte_dir: Path | None = None
    # Hochladen und neue Projekte aus Uploads: aus, bis SCANVIEW_IMPORT=1 (dann Routen 404 aus).
    # Braucht den Jobläufer (jobs_db), den Upload-Ordner außerhalb der Paketwurzel und eine
    # Quote ohne Standardwert (der Betreiber legt sie fest).
    import_enabled: bool = False
    uploads_dir: Path | None = None
    upload_quote_bytes: int = 0
    # Freier Platz, der nach einem angekündigten Upload im Upload-Ordner bleiben muss.
    upload_reserve_bytes: int = 10 * 1024**3
    # Fotos und PDFs am Befund-Faden (F7): aus, bis SCANVIEW_ANHAENGE gesetzt ist (Ordner außerhalb
    # der Paketwurzel; braucht den Jobläufer, der Bilder isoliert neu kodiert). Quote je Projekt.
    anhaenge_dir: Path | None = None
    anhaenge_quote_bytes: int = 2 * 1024**3
    # E-Mail-Benachrichtigung (F8): aus, bis SCANVIEW_POST (Spool-Verzeichnis für den eigenen
    # Dienst scanview-post) und SCANVIEW_POST_ABSENDER gesetzt sind. SMTP-Zugang kennt nur
    # scanview-post, nie der Webdienst.
    post_dir: Path | None = None
    post_absender: str | None = None

    @property
    def db_path(self) -> Path:
        return self.state_dir / "scanview.db"

    @property
    def git_dir(self) -> Path:
        return self.git_verzeichnis if self.git_verzeichnis is not None else self.state_dir / "git"

    @staticmethod
    def from_env() -> Settings:
        def need(name: str) -> str:
            v = os.environ.get(name, "").strip()
            if not v:
                raise SystemExit(f"Umgebungsvariable {name} fehlt.")
            return v

        origin = need("SCANVIEW_ORIGIN").rstrip("/")
        roh_bremse = os.environ.get("SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE", "30").strip()
        if not roh_bremse.isdigit() or int(roh_bremse) < 1:
            raise SystemExit(
                "SCANVIEW_ANMELDE_FEHLER_PRO_MINUTE muss eine ganze Zahl ab 1 sein.",
            )
        roh_tage = os.environ.get("SCANVIEW_LESEZUGRIFFE_TAGE", "").strip() or "90"
        if not (roh_tage.isascii() and roh_tage.isdigit()) or not (
            int(roh_tage) == 0 or LESEZUGRIFFE_TAGE_MIN <= int(roh_tage) <= LESEZUGRIFFE_TAGE_MAX
        ):
            raise SystemExit(
                "SCANVIEW_LESEZUGRIFFE_TAGE muss 0 (Protokoll aus) oder eine ganze Zahl von "
                f"{LESEZUGRIFFE_TAGE_MIN} bis {LESEZUGRIFFE_TAGE_MAX} sein.",
            )
        # Rohwert; geprüft wird erst im Export (gitexport.remote_fehler) — eine kaputte Vorlage
        # darf den Webdienst nicht am Start hindern (gemeinsame Umgebungsdatei).
        git_remote = os.environ.get("SCANVIEW_GIT_REMOTE", "").strip() or None
        git_dir = os.environ.get("SCANVIEW_GIT_DIR", "").strip()
        quelltext_url = os.environ.get("SCANVIEW_QUELLTEXT_URL", "").strip() or None
        if quelltext_url is not None and not _HTTPS_URL.fullmatch(quelltext_url):
            raise SystemExit("SCANVIEW_QUELLTEXT_URL muss eine https://-Adresse ohne "
                             "Leerzeichen sein.")
        jobs_db = os.environ.get("SCANVIEW_JOBS_DB", "").strip()
        berichte = os.environ.get("SCANVIEW_BERICHTE", "").strip()
        if bool(jobs_db) != bool(berichte) or not all(
                Path(p).is_absolute() for p in (jobs_db, berichte) if p):
            raise SystemExit("SCANVIEW_JOBS_DB und SCANVIEW_BERICHTE nur gemeinsam und als "
                             "absolute Pfade setzen.")
        import_an = os.environ.get("SCANVIEW_IMPORT", "0").strip() == "1"
        uploads = os.environ.get("SCANVIEW_UPLOADS", "").strip()
        roh_quote = os.environ.get("SCANVIEW_UPLOAD_QUOTE_GB", "").strip()
        if import_an:
            if not jobs_db:
                raise SystemExit("SCANVIEW_IMPORT=1 braucht den Jobläufer (SCANVIEW_JOBS_DB).")
            if not uploads or not Path(uploads).is_absolute():
                raise SystemExit("SCANVIEW_IMPORT=1 braucht SCANVIEW_UPLOADS als absoluten Pfad.")
            if not (roh_quote.isascii() and roh_quote.isdigit() and 0 < int(roh_quote) <= 100000):
                raise SystemExit("SCANVIEW_IMPORT=1 braucht SCANVIEW_UPLOAD_QUOTE_GB als ganze "
                                 "Zahl von 1 bis 100000 (kein Standardwert).")
            if Path(uploads).resolve().is_relative_to(Path(need("SCANVIEW_DATA")).resolve()):
                raise SystemExit("SCANVIEW_UPLOADS darf nicht unter SCANVIEW_DATA liegen.")
        anhaenge, aquote = _anhaenge_aus_env(bool(jobs_db), need("SCANVIEW_DATA"))
        post, absender = _post_aus_env(need("SCANVIEW_DATA"))
        return Settings(
            post_dir=Path(post) if post else None,
            post_absender=absender or None,
            anhaenge_dir=Path(anhaenge) if anhaenge else None,
            anhaenge_quote_bytes=aquote,
            import_enabled=import_an,
            uploads_dir=Path(uploads) if import_an else None,
            upload_quote_bytes=int(roh_quote) * 1024**3 if import_an else 0,
            jobs_db=Path(jobs_db) if jobs_db else None,
            berichte_dir=Path(berichte) if berichte else None,
            data_dir=Path(need("SCANVIEW_DATA")),
            state_dir=Path(need("SCANVIEW_STATE")),
            origin=origin,
            rp_id=need("SCANVIEW_RP_ID"),
            rp_name=os.environ.get("SCANVIEW_RP_NAME", "siteBits"),
            cookie_secure=os.environ.get("SCANVIEW_COOKIE_SECURE", "1") != "0",
            anmelde_fehler_pro_minute=int(roh_bremse),
            lesezugriffe_tage=int(roh_tage),
            git_export=os.environ.get("SCANVIEW_GIT_EXPORT", "0").strip() == "1",
            git_verzeichnis=Path(git_dir) if git_dir else None,
            git_remote=git_remote,
            quelltext_url=quelltext_url,
            barrierefreiheit=Barrierefreiheit.aus_umgebung(os.environ),
            rechtliches=Rechtliches.aus_umgebung(os.environ),
        )



def _ausserhalb(name: str, wert: str, data: str) -> None:
    if not Path(wert).is_absolute():
        raise SystemExit(f"{name} muss ein absoluter Pfad sein.")
    if Path(wert).resolve().is_relative_to(Path(data).resolve()):
        raise SystemExit(f"{name} darf nicht unter SCANVIEW_DATA liegen.")


def _anhaenge_aus_env(jobs: bool, data: str) -> tuple[str, int]:
    """SCANVIEW_ANHAENGE (Ordner außerhalb der Paketwurzel, braucht den Jobläufer) und Quote."""
    anhaenge = os.environ.get("SCANVIEW_ANHAENGE", "").strip()
    roh = os.environ.get("SCANVIEW_ANHAENGE_QUOTE_MB", "").strip() or "2048"
    if anhaenge:
        if not jobs:
            raise SystemExit("SCANVIEW_ANHAENGE braucht den Jobläufer (SCANVIEW_JOBS_DB).")
        _ausserhalb("SCANVIEW_ANHAENGE", anhaenge, data)
    if not (roh.isascii() and roh.isdigit() and 0 < int(roh) <= 10**7):
        raise SystemExit("SCANVIEW_ANHAENGE_QUOTE_MB muss eine ganze Zahl ab 1 sein.")
    return anhaenge, int(roh) * 1024**2


def _post_aus_env(data: str) -> tuple[str, str]:
    """SCANVIEW_POST (Spool) und SCANVIEW_POST_ABSENDER, nur gemeinsam; Absender streng geprüft."""
    from email.errors import HeaderParseError
    from email.headerregistry import Address

    post = os.environ.get("SCANVIEW_POST", "").strip()
    absender = os.environ.get("SCANVIEW_POST_ABSENDER", "").strip()
    if bool(post) != bool(absender):
        raise SystemExit("SCANVIEW_POST und SCANVIEW_POST_ABSENDER nur gemeinsam setzen.")
    if not post:
        return "", ""
    _ausserhalb("SCANVIEW_POST", post, data)
    try:
        a: Address | None = Address(addr_spec=absender)
    except (ValueError, IndexError, HeaderParseError):
        a = None
    if a is None or not a.username or not a.domain or any(
            ord(c) < 33 or ord(c) == 127 for c in absender):
        raise SystemExit("SCANVIEW_POST_ABSENDER muss eine einzelne E-Mail-Adresse sein.")
    return post, absender
