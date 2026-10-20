"""Git-Objekte mit Hashbindung: jeder gelesene Commit, Baum und Blob wird gegen seine Objekt-ID
nachgerechnet (`<typ> <länge>\\0<inhalt>` mit dem Hashverfahren des Repos). git selbst prüft beim
Lesen nicht, ob der Inhalt eines losen oder gepackten Objekts zu seiner ID passt — wer den
Objektspeicher beschreiben kann, könnte sonst unter einer bekannten, signierten ID einen anderen
Inhalt unterschieben.

Gelesen wird über einen dauerhaften `git cat-file --batch` (fester Aufruf `werkzeuge.git_befehl`:
ohne Ersatzobjekte, ohne commit-graph) mit der gefilterten Umgebung der Kette (ohne grafts).
Fremde Objektspeicher (`objects/info/alternates`) werden abgelehnt: dort läge Inhalt außerhalb
des geprüften Repos."""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Iterable
from pathlib import Path
from types import TracebackType

from .fehler import NachweisFehler
from .werkzeuge import gefilterte_umgebung, git_befehl

_VERFAHREN = {"sha1": 20, "sha256": 32}


def _git(repo: Path, regel: str, *args: str) -> str:
    try:
        r = subprocess.run(git_befehl(*args, repo=repo), capture_output=True, text=True,
                           check=False, stdin=subprocess.DEVNULL, env=gefilterte_umgebung())
    except OSError as e:
        raise NachweisFehler(regel, f"git nicht ausführbar: {e.strerror}") from None
    if r.returncode != 0:
        raise NachweisFehler(regel, f"git {args[0]} in {repo} endete mit Exit {r.returncode}: "
                                    f"{r.stderr.strip()[-500:]}")
    return r.stdout.strip()


def pruefe_keine_alternates(repo: Path, regel: str) -> None:
    """Abbruch, wenn das Repo fremde Objektspeicher einbindet (`objects/info/alternates`)."""
    gemeinsam = Path(_git(repo, regel, "rev-parse", "--path-format=absolute",
                          "--git-common-dir"))
    for name in ("alternates", "http-alternates"):
        if (gemeinsam / "objects" / "info" / name).exists():
            raise NachweisFehler(regel, f"{repo}: fremde Objektspeicher (objects/info/{name}) — "
                                        "Inhalte wären nicht an dieses Repo gebunden")


class Objektleser:
    """Liest Objekte eines Repos und prüft jede ID gegen den Inhalt. Als Kontextmanager
    benutzen (hält einen `git cat-file --batch`-Prozess)."""

    def __init__(self, repo: Path, regel: str) -> None:
        self.repo = repo
        self.regel = regel
        pruefe_keine_alternates(repo, regel)
        self.verfahren = _git(repo, regel, "rev-parse", "--show-object-format")
        if self.verfahren not in _VERFAHREN:
            raise NachweisFehler(regel, f"unbekanntes Hashverfahren {self.verfahren}")
        self.hexlaenge = 2 * _VERFAHREN[self.verfahren]
        self._cache: dict[str, tuple[str, bytes]] = {}
        try:
            self._proz = subprocess.Popen(git_befehl("cat-file", "--batch", repo=repo),
                                          stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.DEVNULL, env=gefilterte_umgebung())
        except OSError as e:
            raise NachweisFehler(regel, f"git nicht ausführbar: {e.strerror}") from None

    def __enter__(self) -> Objektleser:
        return self

    def __exit__(self, typ: type[BaseException] | None, wert: BaseException | None,
                 spur: TracebackType | None) -> None:
        self.schliessen()

    def schliessen(self) -> None:
        if self._proz.stdin:
            self._proz.stdin.close()
        self._proz.wait()

    def _id(self, oid: str) -> str:
        if len(oid) != self.hexlaenge or any(c not in "0123456789abcdef" for c in oid):
            raise NachweisFehler(self.regel, f"„{oid[:80]}“ ist keine volle Objekt-ID")
        return oid

    def lies(self, oid: str, typ: str | None = None) -> bytes:
        """Inhalt von `oid` (optional mit erwartetem Typ); Hash über `<typ> <länge>\\0<inhalt>`
        muss `oid` ergeben."""
        oid = self._id(oid)
        if oid not in self._cache:
            assert self._proz.stdin is not None and self._proz.stdout is not None
            try:
                self._proz.stdin.write(oid.encode() + b"\n")
                self._proz.stdin.flush()
                kopf = self._proz.stdout.readline().decode("ascii", "replace").split()
            except OSError as e:
                raise NachweisFehler(self.regel, f"git cat-file nicht lesbar: {e}") from None
            if len(kopf) != 3 or kopf[0] != oid or not kopf[2].isdigit():
                raise NachweisFehler(self.regel, f"Objekt {oid[:12]} fehlt in {self.repo}")
            art, laenge = kopf[1], int(kopf[2])
            inhalt = self._proz.stdout.read(laenge)
            self._proz.stdout.read(1)
            h = hashlib.new(self.verfahren)
            h.update(f"{art} {len(inhalt)}\0".encode() + inhalt)
            if len(inhalt) != laenge or h.hexdigest() != oid:
                raise NachweisFehler(self.regel, f"Objekt {oid[:12]} in {self.repo}: Inhalt passt "
                                                 "nicht zur Objekt-ID (manipulierter "
                                                 "Objektspeicher?)")
            self._cache[oid] = (art, inhalt)
        art, inhalt = self._cache[oid]
        if typ is not None and art != typ:
            raise NachweisFehler(self.regel, f"Objekt {oid[:12]} ist {art}, erwartet {typ}")
        return inhalt

    def art(self, oid: str) -> str:
        """Objektart (commit, tree, blob, tag) nach Hashprüfung."""
        self.lies(oid)
        return self._cache[oid][0]

    # --- Commits ---------------------------------------------------------------------------

    def commit(self, oid: str) -> tuple[str, tuple[str, ...]]:
        """(Baum, Eltern) aus dem geprüften Commit-Objekt. Der Kopf muss git-konform beginnen:
        erste Zeile `tree`, direkt danach die `parent`-Zeilen, später keine `tree`/`parent` mehr
        (git selbst liest Eltern nur an dieser Stelle)."""
        zeilen = self.lies(oid, "commit").split(b"\n\n", 1)[0].decode("utf-8", "replace")
        kopf = zeilen.split("\n")
        if not kopf or not kopf[0].startswith("tree "):
            raise NachweisFehler(self.regel, f"Commit {oid[:12]}: Kopf nicht git-konform "
                                             "(tree nicht zuerst)")
        baum = self._id(kopf[0][5:])
        eltern: list[str] = []
        i = 1
        while i < len(kopf) and kopf[i].startswith("parent "):
            eltern.append(self._id(kopf[i][7:]))
            i += 1
        if any(z.startswith(("tree ", "parent ")) for z in kopf[i:]):
            raise NachweisFehler(self.regel, f"Commit {oid[:12]}: Kopf nicht git-konform "
                                             "(tree/parent an falscher Stelle)")
        return baum, tuple(eltern)

    def erreichbar(self, starts: Iterable[str]) -> dict[str, tuple[str, ...]]:
        """Alle von `starts` erreichbaren Commits → Eltern, jedes Objekt hashgeprüft."""
        ergebnis: dict[str, tuple[str, ...]] = {}
        offen = list(starts)
        while offen:
            c = offen.pop()
            if c in ergebnis:
                continue
            ergebnis[c] = self.commit(c)[1]
            offen.extend(p for p in ergebnis[c] if p not in ergebnis)
        return ergebnis

    # --- Bäume -------------------------------------------------------------------------------

    def baum(self, oid: str) -> list[tuple[str, str, str]]:
        """Einträge (Modus, Name, ID) des geprüften Baums."""
        roh = self.lies(oid, "tree")
        laenge = self.hexlaenge // 2
        eintraege: list[tuple[str, str, str]] = []
        i = 0
        while i < len(roh):
            leer = roh.index(b" ", i)
            null = roh.index(b"\0", leer)
            modus = roh[i:leer].decode("ascii")
            name = roh[leer + 1:null].decode("utf-8", "surrogateescape")
            eintraege.append((modus, name, roh[null + 1:null + 1 + laenge].hex()))
            i = null + 1 + laenge
        return eintraege

    def dateien(self, commit: str) -> dict[str, tuple[str, str]]:
        """Alle Einträge des Commit-Baums außer Verzeichnissen: Pfad → (Modus, ID)."""
        ergebnis: dict[str, tuple[str, str]] = {}
        offen = [("", self.commit(commit)[0])]
        while offen:
            praefix, baum = offen.pop()
            for modus, name, oid in self.baum(baum):
                pfad = f"{praefix}{name}"
                if modus == "40000":
                    offen.append((pfad + "/", oid))
                else:
                    ergebnis[pfad] = (modus, oid)
        return ergebnis

    def datei(self, commit: str, pfad: str) -> tuple[str, bytes] | None:
        """(Modus, Inhalt) von `pfad` im Commit oder None; alle Objekte auf dem Weg geprüft."""
        teile = pfad.split("/")
        baum = self.commit(commit)[0]
        for nr, teil in enumerate(teile):
            treffer = [(m, o) for m, n, o in self.baum(baum) if n == teil]
            if not treffer:
                return None
            modus, oid = treffer[0]
            if modus == "160000":
                raise NachweisFehler(self.regel, f"{pfad} in {commit[:12]}: Submodul-Verweis "
                                                 "statt Datei")
            if nr < len(teile) - 1:
                if modus != "40000":
                    return None
                baum = oid
            else:
                if modus == "40000":
                    raise NachweisFehler(self.regel, f"{pfad} in {commit[:12]} ist ein "
                                                     "Verzeichnis")
                return modus, self.lies(oid, "blob")
        return None


def blob_id(inhalt: bytes, verfahren: str) -> str:
    h = hashlib.new(verfahren)
    h.update(f"blob {len(inhalt)}\0".encode() + inhalt)
    return h.hexdigest()
