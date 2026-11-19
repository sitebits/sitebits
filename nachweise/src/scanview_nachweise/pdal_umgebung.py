"""PDAL-Umgebung des Imports (`import/pdal-umgebung/`): Lock lesen, Lizenzbeilage erzeugen und
prüfen, SBOM-Komponenten bilden.

Die Umgebung (PDAL, untwine und ihre conda-forge-Abhängigkeiten) wird auf dem Host installiert und
zählt damit als ausgeliefert (ADR 2026-10-22 „PDAL-Umgebung: Lizenzen“). Ausgeliefert wird von uns
nur das Lock (`pixi.lock`, URL + SHA-256 je Paket); die Pakete lädt `einrichten.sh` beim Betreiber
von conda-forge. Pflichten, die das Release dafür erfüllt:

- `quellen.json` (im Repo): je Paket Version, Build, Paket-URL, SHA-256 aus dem Lock,
  conda-forge-Rezept (Feedstock + Commit), Upstream-Quelle und die Lizenzdateien im Paket bzw.
  belegte Ergänzungen, gebunden an den SHA-256 des Locks;
- `LIZENZEN.txt` (nur auf dem Host, `/opt/scanview/pdal/LIZENZEN.txt`): die vollständigen
  Lizenztexte, erzeugt von `scanview-import pdal-lizenzen` in `einrichten.sh` aus dem Paket-Cache
  des Baus (ADR 2026-10-22, Nachtrag Weg 3: fremde Lizenztexte nicht im Repo).

`scanview-nachweise pdal-umgebung erzeugen --cache <pixi-cache>` schreibt `quellen.json` aus den
entpackten Paketen (Ordner `info/` je Paket); `pruefe` stellt offline sicher, dass sie genau zum
Lock passt — ein geändertes Lock ohne neue Beilage ist ein Fehler.

Nur Standardbibliothek: das Lock ist YAML, gelesen wird aber nur die feste Form, die pixi schreibt
(Lock-Version 7); alles Unerwartete ist ein Fehler (fail-closed)."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .fehler import NachweisFehler

_REGEL = "pdal-umgebung"
ORDNER = Path("import/pdal-umgebung")
LOCK = ORDNER / "pixi.lock"
QUELLEN = ORDNER / "quellen.json"
LIZENZTEXTE = ORDNER / "LIZENZEN.txt"
#: Nur dieser Kanal und diese Plattform (Server: linux-64).
_URL = re.compile(r"https://conda\.anaconda\.org/conda-forge/(linux-64|noarch)/"
                  r"([A-Za-z0-9_.+-]+)-([A-Za-z0-9_.+!]+)-([A-Za-z0-9_.+]+)\.conda")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_MD5 = re.compile(r"[0-9a-f]{32}")
#: Ausführbare Programme der Umgebung (BSI: Anwendung statt Bibliothek).
PROGRAMME = frozenset({"untwine"})
UMGEBUNG = "pdal"
#: bom-ref-Präfix der Komponenten; bewusst nicht `pdal|` (so heißt der Image-Inhalt im
#: Nachweis-SBOM importbilder, für den der Scan eine Ausnahme kennt).
REF_PRAEFIX = "umgebung-pdal|"


@dataclass(frozen=True)
class Paket:
    name: str
    version: str
    build: str
    subdir: str
    url: str
    sha256: str
    md5: str
    lizenz: str
    #: Namen der Abhängigkeiten laut Lock (ohne virtuelle Pakete `__…`).
    haengt_ab: tuple[str, ...] = ()

    @property
    def dateiname(self) -> str:
        return self.url.rsplit("/", 1)[1]

    @property
    def purl(self) -> str:
        return (f"pkg:conda/{self.name}@{self.version}?build={self.build}"
                f"&channel=conda-forge&subdir={self.subdir}&type=conda")


def lock_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _fehler(text: str) -> NachweisFehler:
    return NachweisFehler(_REGEL, f"{LOCK}: {text}")


def lies_lock(text: str) -> list[Paket]:
    """Pakete aus `pixi.lock` (Version 7, eine Umgebung `default`, nur conda-Pakete). Prüft, dass
    die Paketliste der Umgebung und die Paketblöcke genau übereinstimmen."""
    zeilen = text.splitlines()
    if not zeilen or zeilen[0] != "version: 7":
        raise _fehler("Lock-Version 7 erwartet")
    try:
        start = zeilen.index("packages:")
    except ValueError:
        raise _fehler("Abschnitt packages fehlt") from None
    kopf = zeilen[:start]
    if any(z.strip().startswith("- pypi:") for z in zeilen):
        raise _fehler("PyPI-Pakete sind in der PDAL-Umgebung nicht vorgesehen")
    if "environments:" not in kopf or [z for z in kopf if z.startswith("- name: ")] != [
            "- name: linux-64"]:
        raise _fehler("nur Plattform linux-64 und ein Abschnitt environments erwartet")
    umgebungen = [z for z in kopf[kopf.index("environments:"):]
                  if re.fullmatch(r"  [A-Za-z0-9_-]+:", z)]
    if umgebungen != ["  default:"]:
        raise _fehler("genau eine Umgebung default erwartet")
    gelistet = [z.split("- conda: ", 1)[1] for z in kopf if z.startswith("      - conda: ")]
    bloecke: list[list[str]] = []
    for z in zeilen[start + 1:]:
        if z.startswith("- conda: "):
            bloecke.append([z])
        elif z.startswith("  ") and bloecke:
            bloecke[-1].append(z)
        elif z.strip():
            raise _fehler(f"unerwartete Zeile {z[:60]!r}")
    pakete = [_paket(b) for b in bloecke]
    if sorted(gelistet) != sorted(p.url for p in pakete) or len(set(gelistet)) != len(gelistet):
        raise _fehler("Paketliste der Umgebung ≠ Paketblöcke")
    if not pakete:
        raise _fehler("keine Pakete")
    return pakete


def _paket(block: list[str]) -> Paket:
    url = block[0].split("- conda: ", 1)[1].strip()
    m = _URL.fullmatch(url)
    if m is None:
        raise _fehler(f"Paket-URL nicht von conda-forge oder unlesbar: {url}")
    felder: dict[str, str] = {}
    abh: list[str] = []
    liste = ""
    for z in block[1:]:
        if z.startswith("  - "):
            if liste == "depends":
                name = z[4:].split()[0]
                if not name.startswith("__"):
                    abh.append(name)
            continue
        if z.startswith("    "):
            continue  # verschachtelte Abschnitte (run_exports …) — nicht Teil der Prüfung
        liste = z[2:].split(":", 1)[0]
        schluessel, sep, wert = z[2:].partition(":")
        if not sep:
            raise _fehler(f"unlesbare Zeile im Block {m.group(2)}: {z[:60]!r}")
        felder[schluessel] = wert.strip()
    sha, md5, lizenz = felder.get("sha256", ""), felder.get("md5", ""), felder.get("license", "")
    if not _SHA256.fullmatch(sha) or not _MD5.fullmatch(md5):
        raise _fehler(f"{m.group(2)}: SHA-256 oder MD5 fehlt")
    if not lizenz:
        raise _fehler(f"{m.group(2)}: keine Lizenzangabe im Lock")
    return Paket(name=m.group(2), version=m.group(3), build=m.group(4), subdir=m.group(1), url=url,
                 sha256=sha, md5=md5, lizenz=lizenz, haengt_ab=tuple(sorted(set(abh))))


# --- Beilage erzeugen (aus dem pixi-Cache) -------------------------------------------------------

def _rezeptquelle(info: Path) -> dict[str, str]:
    """Erste Upstream-Quelle (URL + SHA-256 bzw. Git-Revision) aus dem Rezept, sonst leer.
    Gelesen wird nur das Fenster nach `source:` (Rezeptformen von conda-build und rattler-build:
    URL einzeln oder als Liste von Spiegeln)."""
    for name in ("recipe/rendered_recipe.yaml", "recipe/meta.yaml", "recipe/recipe.yaml"):
        pfad = info / name
        if not pfad.is_file():
            continue
        zeilen = pfad.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, z in enumerate(zeilen):
            if z.strip() != "source:":
                continue
            fenster = "\n".join(zeilen[i + 1:i + 14])
            url = re.search(r"(?:url|git_url):\s*(?:\n\s*-\s*)?(\S+://\S+)", fenster)
            sha = re.search(r"sha256:\s*([0-9a-f]{64})", fenster)
            rev = re.search(r"git_rev:\s*(\S+)", fenster)
            if url and (sha or rev):
                return ({"url": url.group(1), "sha256": sha.group(1)} if sha
                        else {"url": url.group(1), "rev": rev.group(1) if rev else ""})
    return {}


def _lizenzdateien(paket_ordner: Path) -> list[Path]:
    for wurzel in (paket_ordner / "info" / "licenses", paket_ordner / "share" / "licenses"):
        if wurzel.is_dir():
            dateien = sorted(p for p in wurzel.rglob("*") if p.is_file() and not p.is_symlink())
            if dateien:
                return dateien
    return []


#: Ergänzende Lizenztexte, wo das Paket selbst keinen oder nur einen Teil mitbringt: Paket →
#: [(Quellpaket im selben Lock, Datei darin, Erklärung)]. GCC-Laufzeit: Pakete bringen nur die
#: Laufzeitausnahme mit, der GPL-3.0-Text kommt aus readline (wörtlich dieselbe Lizenz); die
#: Metapakete *-ng haben keine Dateien. SQLite: der Segenstext steht im Kopf von sqlite3.h.
#: Nur Verweise: den Text zieht `scanview-import pdal-lizenzen` auf dem Host aus dem Paket-Cache.
_GPL3 = ("readline", "info/licenses/COPYING",
         "GPL-3.0-Text, auf den sich die Laufzeitausnahme bezieht (wortgleich aus readline)")
_ERGAENZUNG: dict[str, list[tuple[str, str, str]]] = {
    "libgcc": [_GPL3],
    "libgomp": [_GPL3],
    "libstdcxx": [_GPL3],
    "libgcc-ng": [("libgcc", "share/licenses/gcc-libs/RUNTIME.LIBRARY.EXCEPTION",
                   "Metapaket ohne eigene Dateien, Lizenz wie libgcc"), _GPL3],
    "libstdcxx-ng": [("libstdcxx", "share/licenses/libstdc++/RUNTIME.LIBRARY.EXCEPTION",
                      "Metapaket ohne eigene Dateien, Lizenz wie libstdcxx"), _GPL3],
    "libsqlite": [("libsqlite", "include/sqlite3.h", "Segenstext (Kopf von sqlite3.h)")],
    "sqlite": [("libsqlite", "include/sqlite3.h", "Segenstext (Kopf von sqlite3.h)")],
}
#: Ein Lizenztext muss mindestens so lang sein (gegen leere oder abgeschnittene Dateien); gleiche
#: Schwelle wie `scanview_import.pdal_lizenzen.MINDESTLAENGE`.
_MINDESTLAENGE = 40


def _kopfkommentar(text: str) -> str:
    """Erster C-Kommentarblock (Segenstext von SQLite)."""
    ende = text.find("*/")
    return text[: ende + 2] if text.startswith("/*") and ende > 0 else ""


def _ergaenzungen(p: Paket, pakete: list[Paket], cache: Path) -> list[dict[str, str]]:
    """Belegte Ergänzungen eines Pakets: Quelle muss im Lock stehen und Text liefern."""
    out = []
    for quelle, datei, erklaerung in _ERGAENZUNG.get(p.name, []):
        q = next((x for x in pakete if x.name == quelle), None)
        if q is None:
            raise NachweisFehler(_REGEL, f"{p.name}: Ergänzung aus {quelle} — nicht im Lock")
        pfad = cache / "pkgs" / q.dateiname.removesuffix(".conda") / datei
        try:
            text = pfad.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise NachweisFehler(_REGEL, f"{pfad}: {e.strerror}") from None
        if datei.endswith(".h"):
            text = _kopfkommentar(text)
        if len(text.strip()) < _MINDESTLAENGE:
            raise NachweisFehler(_REGEL, f"{pfad}: kein Lizenztext")
        out.append({"paket": quelle, "datei": datei, "erklaerung": erklaerung})
    return out


def erzeuge(lock_text: str, cache: Path) -> dict[str, Any]:
    """`quellen.json` aus den entpackten Paketen unter `<cache>/pkgs/`: je Paket Rezept,
    Upstream, die Lizenzdateien im Paket und belegte Ergänzungen. Die Texte selbst stehen nicht
    im Repo; `scanview-import pdal-lizenzen` erzeugt sie auf dem Host (ADR-Nachtrag Weg 3).
    Geprüft wird hier schon, dass jedes Paket wenigstens einen Text liefert."""
    pakete = lies_lock(lock_text)
    eintraege: list[dict[str, Any]] = []
    for p in pakete:
        ordner = cache / "pkgs" / p.dateiname.removesuffix(".conda")
        info = ordner / "info"
        try:
            index = json.loads((info / "index.json").read_text(encoding="utf-8"))
            about = json.loads((info / "about.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            raise NachweisFehler(_REGEL, f"{ordner}: Paketinfo fehlt ({e})") from None
        if (index.get("name"), index.get("version"), index.get("build")) != \
                (p.name, p.version, p.build):
            raise NachweisFehler(_REGEL, f"{ordner}: index.json passt nicht zum Lock")
        extra = about.get("extra") if isinstance(about.get("extra"), dict) else {}
        rezept = {"feedstock": str(extra.get("remote_url") or ""),
                  "commit": str(extra.get("sha") or "")}
        upstream = {"home": str(about.get("home") or ""), "dev": str(about.get("dev_url") or ""),
                    **_rezeptquelle(info)}
        dateien = [d for d in _lizenzdateien(ordner)
                   if len(d.read_text(encoding="utf-8", errors="replace").strip())
                   >= _MINDESTLAENGE]
        ergaenzt = _ergaenzungen(p, pakete, cache)
        if not dateien and not ergaenzt:
            raise NachweisFehler(_REGEL, f"{p.name}: kein Lizenztext im Paket und keine "
                                         "Ergänzung")
        eintraege.append({**{k: v for k, v in asdict(p).items() if k != "haengt_ab"},
                          "rezept": rezept, "upstream": upstream,
                          "lizenzdateien": [str(d.relative_to(ordner)) for d in dateien],
                          "ergaenzungen": ergaenzt})
    return {"lockSha256": lock_sha256(lock_text), "pakete": eintraege}


# --- Prüfen (offline) ----------------------------------------------------------------------------

def _sicherer_pfad(pfad: object) -> bool:
    return isinstance(pfad, str) and bool(pfad) and not pfad.startswith("/") \
        and ".." not in pfad.split("/") and "\\" not in pfad


def _pruefe_lizenzbezug(eintrag: dict[str, Any], namen: set[str]) -> None:
    """Je Paket mindestens eine Lizenzdatei im Paket oder eine Ergänzung aus dem Lock; alle
    Pfade relativ und innerhalb des Paketordners."""
    name = eintrag.get("name")
    dateien = eintrag.get("lizenzdateien")
    ergaenzungen = eintrag.get("ergaenzungen")
    if not isinstance(dateien, list) or not isinstance(ergaenzungen, list):
        raise NachweisFehler(_REGEL, f"{name}: lizenzdateien/ergaenzungen fehlen")
    if not dateien and not ergaenzungen:
        raise NachweisFehler(_REGEL, f"{name}: weder Lizenzdatei noch Ergänzung")
    if not all(_sicherer_pfad(d) for d in dateien):
        raise NachweisFehler(_REGEL, f"{name}: Lizenzdatei mit unzulässigem Pfad")
    for e in ergaenzungen:
        if not isinstance(e, dict) or e.get("paket") not in namen \
                or not _sicherer_pfad(e.get("datei")) \
                or len(str(e.get("erklaerung", "")).strip()) < 10:
            raise NachweisFehler(_REGEL, f"{name}: Ergänzung unvollständig oder aus einem Paket "
                                         "außerhalb des Locks")


def pruefe(worktree: Path) -> dict[str, Any]:
    """`quellen.json` passt genau zum Lock: gleicher Lock-SHA-256, gleiche Pakete mit gleichen
    Hashes und Lizenzen, je Paket Rezept, Upstream-Quelle und ein Lizenzbezug (Datei im Paket oder
    belegte Ergänzung). Die Texte selbst erzeugt `einrichten.sh` auf dem Host."""
    try:
        lock_text = (worktree / LOCK).read_text(encoding="utf-8")
        quellen = json.loads((worktree / QUELLEN).read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise NachweisFehler(_REGEL, f"Beilage der PDAL-Umgebung nicht lesbar: {e}") from None
    pakete = lies_lock(lock_text)
    soll = lock_sha256(lock_text)
    if not isinstance(quellen, dict) or quellen.get("lockSha256") != soll:
        raise NachweisFehler(_REGEL, f"{QUELLEN}: gehört nicht zu diesem Lock (SHA-256 {soll}) — "
                                     "neu erzeugen: scanview-nachweise pdal-umgebung erzeugen")
    if (worktree / LIZENZTEXTE).exists():
        raise NachweisFehler(_REGEL, f"{LIZENZTEXTE}: gehört nicht ins Repo — einrichten.sh "
                                     "erzeugt die Datei auf dem Host")
    ist = {(e.get("name"), e.get("version"), e.get("build"), e.get("sha256"), e.get("lizenz"))
           for e in quellen.get("pakete", []) if isinstance(e, dict)}
    erwartet = {(p.name, p.version, p.build, p.sha256, p.lizenz) for p in pakete}
    if ist != erwartet or len(quellen.get("pakete", [])) != len(pakete):
        raise NachweisFehler(_REGEL, f"{QUELLEN}: Pakete ≠ Lock")
    namen = {p.name for p in pakete}
    for eintrag in quellen["pakete"]:
        feedstock = eintrag.get("rezept", {}).get("feedstock", "")
        if not feedstock.startswith("https://github.com/conda-forge/"):
            raise NachweisFehler(_REGEL, f"{eintrag['name']}: Rezept (Feedstock) fehlt")
        up = eintrag.get("upstream", {})
        if not (up.get("dev") or up.get("home")) or not up.get("url"):
            raise NachweisFehler(_REGEL, f"{eintrag['name']}: Upstream bzw. Quell-URL fehlt")
        _pruefe_lizenzbezug(eintrag, namen)
    return {"pakete": len(pakete), "lockSha256": soll, "ergebnis": "bestanden"}


# --- SBOM ----------------------------------------------------------------------------------------

def _lizenzangabe(lizenz: str) -> dict[str, Any]:
    """Eine einzelne SPDX-ID als `license.id` (daraus leitet die Nachbearbeitung die
    Verbreitungslizenz ab), ein Ausdruck mit Operatoren als `expression`."""
    if re.fullmatch(r"[A-Za-z0-9.+-]+", lizenz):
        return {"license": {"id": lizenz}}
    return {"expression": lizenz}


def komponenten(lock_text: str, quellen: dict[str, Any] | None = None
                ) -> list[dict[str, Any]]:
    """CycloneDX-Komponenten (1.6) der Umgebung: Lizenz aus dem Lock (erklärt), SHA-256 des
    Pakets, Verteilung (Paket-URL) und Kennzeichnung `scanview:umgebung`, `scanview:lock-sha256`
    — daran bindet die Lizenzprüfung ihre Ausnahmen „nur externer Prozess“."""
    soll = lock_sha256(lock_text)
    out: list[dict[str, Any]] = []
    pakete = lies_lock(lock_text)
    bezug = {e["name"]: e for e in (quellen or {}).get("pakete", []) if isinstance(e, dict)}
    for p in pakete:
        e = bezug.get(p.name, {})
        up, rezept = e.get("upstream", {}), e.get("rezept", {})
        refs: list[dict[str, Any]] = [{"type": "distribution", "url": p.url}]
        if up.get("url"):
            refs.append({"type": "source-distribution", "url": up["url"],
                         "comment": "Upstream-Quelle laut conda-forge-Rezept"})
        if rezept.get("feedstock"):
            refs.append({"type": "vcs", "url": rezept["feedstock"],
                         "comment": f"conda-forge-Rezept, Commit {rezept.get('commit', '')}"})
        komponente: dict[str, Any] = {
            "type": "application" if p.name in PROGRAMME else "library",
            "bom-ref": f"{REF_PRAEFIX}{p.purl}",
            "name": p.name, "version": p.version, "purl": p.purl,
            "hashes": [{"alg": "SHA-256", "content": p.sha256}],
            "licenses": [_lizenzangabe(p.lizenz)],
            "externalReferences": refs,
            "properties": [{"name": "scanview:umgebung", "value": UMGEBUNG},
                           {"name": "scanview:conda-build", "value": p.build},
                           {"name": "scanview:lock-sha256", "value": soll}],
        }
        hersteller = up.get("dev") or up.get("home")
        if hersteller:
            komponente["manufacturer"] = {"url": [hersteller]}
        out.append(komponente)
    return out


def sha512_belege(lock_text: str, laden: Callable[[str], bytes]
                  ) -> dict[str, tuple[str, str]]:
    """Je Paket die Datei laden, SHA-256 gegen das Lock prüfen (Abweichung = Fehler) und SHA-512
    darüber bilden: bom-ref → (URL, SHA-512). Netz nur über `laden`."""
    belege: dict[str, tuple[str, str]] = {}
    for p in lies_lock(lock_text):
        daten = laden(p.url)
        if hashlib.sha256(daten).hexdigest() != p.sha256:
            raise NachweisFehler("sbom-hash", f"{p.url}: SHA-256 weicht von pixi.lock ab")
        belege[f"{REF_PRAEFIX}{p.purl}"] = (p.url, hashlib.sha512(daten).hexdigest())
    return belege


def abhaengigkeiten(lock_text: str) -> list[dict[str, Any]]:
    """CycloneDX-`dependencies` der Umgebung aus den `depends`-Angaben des Locks. Eine Abhängigkeit,
    die nicht im Lock steht, ist ein Fehler (der Solver hätte sie gelöst)."""
    pakete = lies_lock(lock_text)
    refs = {p.name: f"{REF_PRAEFIX}{p.purl}" for p in pakete}
    out = []
    for p in pakete:
        fehlt = [n for n in p.haengt_ab if n not in refs]
        if fehlt:
            raise _fehler(f"{p.name}: Abhängigkeit {', '.join(fehlt)} nicht im Lock")
        out.append({"ref": refs[p.name], "dependsOn": sorted(refs[n] for n in p.haengt_ab)})
    return out
