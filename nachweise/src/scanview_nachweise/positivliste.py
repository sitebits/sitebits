"""Positivliste des öffentlichen Exports (`veroeffentlichung/positivliste.txt`).

Dieselbe Auswahl gilt für den öffentlichen Export (`export.kandidaten`) und für die
ausgelieferten Release-Artefakte (`artefakte.baue_artefakte`): was ausgeliefert wird, muss auch
öffentlich sein dürfen. Muster wie bei `.gitignore`: das letzte passende entscheidet,
`!muster` schließt aus. Nur Standardbibliothek."""

from __future__ import annotations

import re

from .fehler import NachweisFehler

POSITIVLISTE = "veroeffentlichung/positivliste.txt"


def lies_positivliste(text: str) -> list[str]:
    """Muster je Zeile; `#` leitet einen Kommentar ein, Leerzeilen zählen nicht."""
    muster: list[str] = []
    for zeile in text.splitlines():
        zeile = zeile.split("#", 1)[0].strip()
        if zeile:
            muster.append(zeile)
    return muster


def glob_regex(muster: str) -> re.Pattern[str]:
    """Glob → Regex, verankert an der Repo-Wurzel, immer auf den ganzen Pfad:
    `*` = beliebig viele Zeichen außer `/`, `?` = ein Zeichen außer `/`, `**` als ganzer
    Pfadteil = beliebig viele Ordner (auch keiner), ein abschließendes `/` = alles darunter.
    Zeichenklassen (`[…]`), führendes `/` und `***` sind nicht erlaubt (lieber abbrechen als
    etwas anderes erfassen als gemeint)."""
    regel = "export-positivliste"
    if not muster or muster.startswith("/"):
        raise NachweisFehler(regel, f"Muster „{muster}“: leer oder mit führendem /")
    if "[" in muster or "]" in muster or "***" in muster:
        raise NachweisFehler(regel, f"Muster „{muster}“: Zeichenklassen und *** werden nicht "
                                    "unterstützt")
    if muster.endswith("/"):
        muster += "**"
    teile = muster.split("/")
    regex = ""
    for i, teil in enumerate(teile):
        letzter = i == len(teile) - 1
        if teil == "**":
            regex += ".*" if letzter else "(?:[^/]+/)*"
            continue
        if "**" in teil:
            raise NachweisFehler(regel, f"Muster „{muster}“: ** nur als ganzer Pfadteil")
        for zeichen in teil:
            if zeichen == "*":
                regex += "[^/]*"
            elif zeichen == "?":
                regex += "[^/]"
            else:
                regex += re.escape(zeichen)
        if not letzter:
            regex += "/"
    return re.compile(regex)


class Positivliste:
    """Geladene Muster; `nimmt_auf(pfad)` = das letzte passende Muster schließt ein."""

    def __init__(self, muster: list[str]) -> None:
        self.regeln: list[tuple[bool, re.Pattern[str]]] = []
        for m in muster:
            negiert = m.startswith("!")
            self.regeln.append((not negiert, glob_regex(m[1:] if negiert else m)))

    def nimmt_auf(self, pfad: str) -> bool:
        aufnehmen = False
        for einschliessen, regex in self.regeln:
            if regex.fullmatch(pfad):
                aufnehmen = einschliessen
        return aufnehmen
