"""MCP-Server `scanview` (stdio): Werkzeuge über die öffentliche API v1.

Start über die Konsole `scanview-mcp`. Umgebung: `SCANVIEW_URL` (https://… oder
http://localhost…) und `SCANVIEW_TOKEN` (`sv_…`, persönlicher API-Zugang aus dem Portal). Fehlt
eins oder ist es unbrauchbar → Exit 2 mit einer Meldung, die nur den Variablennamen nennt.
Datensparsam ab Werk (`datenschutz.py`); `SCANVIEW_MCP_PERSONENDATEN=voll` liefert
Originaldaten und warnt beim Start auf stderr.

Hinweis mcp 2.x: `FastMCP` heißt dort `MCPServer` (`mcp.server.mcpserver`), gleiche Dekoratoren.
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Mapping
from typing import Annotated, Any, NoReturn
from urllib.parse import urlsplit

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from . import datenschutz, werkzeuge
from .api import Client

LOKAL = {"localhost", "127.0.0.1", "::1"}

HINWEISE = (
    "Werkzeuge für die scanview-Plattform (Punktwolken-Projekte eines Vermessungsbüros). "
    "Jedes Projekt hat eine projekt_id (klein, z. B. 'musterhalle-nord-v2'); zuerst "
    "projekte_auflisten aufrufen. Koordinaten sind Weltkoordinaten des Pakets in Metern "
    "[x, y, z] — so, wie sie pois_lesen und messungen_lesen liefern. Punktwolken selbst sind "
    "nicht zugänglich; im Raum messen kann nur der Viewer. Änderungen sind sofort für alle "
    "Projektbeteiligten sichtbar und bleiben im Verlauf dauerhaft nachvollziehbar."
)

HINWEIS_DATENSPARSAM = (
    " Datensparsamer Modus: Namen anderer Konten und Token-Namen erscheinen als Pseudonym "
    "(Konto-…, Token-…; innerhalb einer Sitzung stabil), Freitexte (Notizen, Beschreibungen) als "
    f"„{datenschutz.AUSGEBLENDET}“. Diesen Platzhalter nie als Inhalt zurückschreiben — ein "
    "Feld, das unverändert bleiben soll, einfach weglassen."
)

_SICHTBAR = (
    " Die Änderung ist sofort für alle Projektbeteiligten sichtbar und bleibt im Verlauf "
    "(Protokoll) dauerhaft nachvollziehbar, auch wenn sie später geändert wird. Braucht einen "
    "Schreib-Token und "
)
SICHTBAR = _SICHTBAR + "die Rolle Bearbeiter."
# Nur POIs öffnen sich für die Rolle Rückmelder; Messungen und Beschreibung nicht.
SICHTBAR_POI = _SICHTBAR + (
    "die Rolle Bearbeiter; Rückmelder legen nur Einträge der Kategorie „Rückmeldung“ an und "
    "ändern nur eigene."
)

SICHTBAR_FADEN = _SICHTBAR + "die Rolle Rückmelder oder Bearbeiter."
# G7 (R-0.8-23/24): über einen Token „für einen KI-Agenten“ wird Geschriebenes zum Vorschlag.
VORSCHLAG = (" Mit einem Token „für einen KI-Agenten“ entsteht ein Vorschlag, den ein Mensch "
             "annimmt oder verwirft (pruefstand 'vorschlag').")
VORSCHLAG_FADEN = (" Mit einem Token „für einen KI-Agenten“ ändert sich der Stand nicht: es "
                   "entsteht ein Eintrag art 'vorschlag' (HTTP 202), den ein Mensch annimmt oder "
                   "verwirft; version zählt dann nicht.")
VORSCHLAG_LOESCHEN = (" Mit einem Token „für einen KI-Agenten“ wird ein gültiger Eintrag nur zum "
                      "Löschen vorgeschlagen (HTTP 202, Antwort {'loeschenVorgeschlagen': id, "
                      "'eintrag': …}); ein Mensch entscheidet. Eigene offene Vorschläge löscht "
                      "der Agent direkt.")

ProjektId = Annotated[str, Field(
    description="projekt_id aus projekte_auflisten (Kleinbuchstaben, Ziffern, '-').",
    pattern=r"^[a-z0-9][a-z0-9-]{0,63}$",
)]
EintragId = Annotated[str, Field(
    description="ID aus Buchstaben, Ziffern, '_' und '-', höchstens 64 Zeichen.",
    pattern=r"^[A-Za-z0-9_-]{1,64}$",
)]
Punkt = Annotated[list[float], Field(
    description="[x, y, z] in Metern, Weltkoordinaten des Pakets (wie in pois_lesen).",
    min_length=3, max_length=3,
)]
Version = Annotated[int, Field(
    ge=1, description="Feld 'version' des Eintrags aus dem letzten Lesen (optimistische Sperre).",
)]


def _objekt(ergebnis: Any) -> dict[str, Any]:
    """Werkzeugergebnis als JSON-Objekt; eine Liste wird eingepackt, alles andere ist ein Fehler
    mit verständlichem Text (statt einer TypeError-Meldung aus dict())."""
    if isinstance(ergebnis, dict):
        return ergebnis
    if isinstance(ergebnis, list):
        return {"eintraege": ergebnis}
    raise ToolError("Unerwartete Antwort vom Server (kein JSON-Objekt).")


def baue_server(client: Client) -> MCPServer:
    hinweise = HINWEISE if client.datenschutz.voll else HINWEISE + HINWEIS_DATENSPARSAM
    mcp = MCPServer("scanview", instructions=hinweise)
    c = client

    @mcp.tool(description=(
        "Listet alle Projekte, auf die der Token-Inhaber ein Recht hat: {'projekte': [Karte]}. "
        "Karte: id, titel, beschreibung, punkte, standpunkte, ausdehnung ([minX,minY,minZ,"
        "maxX,maxY,maxZ] in Metern, Weltkoordinaten), crs, scandatum, rolle "
        "(betrachter|rueckmelder|bearbeiter|projektleitung|admin), version."
    ))
    def projekte_auflisten() -> dict[str, Any]:
        return _objekt(werkzeuge.projekte_auflisten(c))

    @mcp.tool(description=(
        "Liest ein Projekt (Karte wie bei projekte_auflisten, inkl. eigener rolle, "
        "Metadaten-version, einstellungen und kategorien [{name, aktiv}] — aktive Kategorien "
        "sind die Vorschläge für poi_anlegen; andere Kategorien bleiben erlaubt). "
        "genauigkeit [{ebene, titel, art, zeilen}]: Genauigkeitsnachweis je Punktwolke/Mesh; "
        "nur „geprüft an … Kontrollpunkten/-strecken“ ist eine unabhängige Prüfung, "
        "„nicht unabhängig geprüft“ und „gesetzt, nicht geprüft“ sind keine. Messungen nennen "
        "ihre Ebenen in pointSources[].layerId."
    ))
    def projekt_lesen(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.projekt_lesen(c, projekt_id))

    @mcp.tool(description=(
        "Liest die Beteiligten eines Projekts: {'beteiligte': [{nutzerId, nutzer, rolle "
        "(betrachter|rueckmelder|bearbeiter|projektleitung), gewerk: {id, name, kurz} oder "
        "null}]}. Keine E-Mail-Adressen. Im datensparsamen Modus sind nutzer und nutzerId "
        "Pseudonyme. Verwalten (einladen, Rollen) geht nur im Browser."
    ))
    def beteiligte_lesen(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.beteiligte_lesen(c, projekt_id))

    @mcp.tool(description=(
        "Setzt die Projektbeschreibung (Freitext, höchstens 2000 Zeichen, Zeilenumbrüche "
        "erlaubt; leer = Beschreibung entfernen). Liest vorher selbst die aktuelle version."
        + SICHTBAR
    ))
    def beschreibung_setzen(
        projekt_id: ProjektId,
        beschreibung: Annotated[str, Field(max_length=2000, description="Neuer Text.")],
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.beschreibung_setzen(c, projekt_id, beschreibung))

    @mcp.tool(description=(
        "Liest alle POIs (Merkpunkte) eines Projekts: {'pois': [{id, version, daten: {title, "
        "category, note, position: [x, y, z] in Metern (Weltkoordinaten), station}, "
        "erstelltVonName, geaendertVonName, geaendertAm, herkunft (mensch|ki), pruefstand "
        "(gueltig|vorschlag), loeschenVorgeschlagen, …}]}. Offene KI-Vorschläge erscheinen mit "
        "pruefstand 'vorschlag' (fremde nur ab Rolle Bearbeiter); gültig ist nur 'gueltig'. Im "
        "datensparsamen Modus sind note und Namen ausgeblendet bzw. pseudonymisiert."
    ))
    def pois_lesen(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.pois_lesen(c, projekt_id))

    @mcp.tool(description=(
        "Legt einen POI (Merkpunkt) an einer bekannten Position an. position = [x, y, z] in "
        "Metern, Weltkoordinaten des Pakets (dasselbe System wie pois_lesen/messungen_lesen, "
        "z. B. aus einem vorhandenen POI oder einer Messung abgeleitet). titel Pflicht (bis "
        "200 Zeichen), kategorie bis 60, notiz bis 2000. poi_id optional — ohne vergibt der "
        "Server eine ('P-…'); ist sie schon vergeben, kommt ein Konflikt mit dem vorhandenen "
        "Eintrag. Höchstens 5000 POIs je Projekt (Rückmelder: 100 eigene)." + SICHTBAR_POI
        + VORSCHLAG
    ))
    def poi_anlegen(
        projekt_id: ProjektId,
        titel: Annotated[str, Field(min_length=1, max_length=200)],
        position: Punkt,
        kategorie: Annotated[str | None, Field(max_length=60)] = None,
        notiz: Annotated[str | None, Field(max_length=2000)] = None,
        standpunkt: Annotated[str | None, Field(
            pattern=r"^[A-Za-z0-9_-]{1,32}$",
            description="Optional: ID des Scan-Standpunkts, von dem aus der POI gesetzt wurde.",
        )] = None,
        poi_id: Annotated[EintragId | None, Field(description="Optional, sonst vom Server.")]
        = None,
        position_source: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.poi_anlegen(c, projekt_id, titel, position, kategorie, notiz,
                                             standpunkt, poi_id, position_source))

    @mcp.tool(description=(
        "Ändert einen vorhandenen POI: nur die angegebenen Felder werden ersetzt, der Rest "
        "bleibt. version = Feld 'version' des POI aus pois_lesen; hat jemand anderes "
        "inzwischen geändert, kommt ein Konflikt — dann neu lesen und erneut versuchen. "
        "position = [x, y, z] in Metern, Weltkoordinaten des Pakets. notiz/kategorie = '' "
        "leert das Feld, standpunkt_entfernen = true entfernt den Standpunkt." + SICHTBAR_POI
        + " Mit einem Token „für einen KI-Agenten“ nur eigene offene Vorschläge (sonst 403);"
        " eine Änderung an Gültigem als Kommentar im Befund-Faden vorschlagen."
    ))
    def poi_aendern(
        projekt_id: ProjektId,
        poi_id: EintragId,
        version: Version,
        titel: Annotated[str | None, Field(min_length=1, max_length=200)] = None,
        kategorie: Annotated[str | None, Field(max_length=60)] = None,
        notiz: Annotated[str | None, Field(max_length=2000)] = None,
        position: Annotated[Punkt | None, Field(description="[x, y, z] in Metern.")] = None,
        standpunkt: Annotated[str | None, Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")] = None,
        standpunkt_entfernen: Annotated[bool, Field(
            description="true = Standpunkt-Zuordnung des POI entfernen (nicht mit standpunkt).",
        )] = False,
        position_source: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.poi_aendern(c, projekt_id, poi_id, version, titel, kategorie,
                                             notiz, position, standpunkt,
                                             standpunkt_entfernen, position_source))

    @mcp.tool(description=(
        "Liest alle Messungen eines Projekts: {'messungen': [{id, version, daten: {kind "
        "(distance|polyline|area|height), points: [[x, y, z], …] in Metern (Weltkoordinaten), "
        "areaM2?, planarityM?}, herkunft, pruefstand, loeschenVorgeschlagen, …}]}. Offene "
        "KI-Vorschläge mit pruefstand 'vorschlag' (fremde nur ab Rolle Bearbeiter)."
    ))
    def messungen_lesen(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.messungen_lesen(c, projekt_id))

    @mcp.tool(description=(
        "Legt eine Messung aus bekannten Eckpunkten an (kein Messen in der Punktwolke). "
        "punkte = Liste von [x, y, z] in Metern, Weltkoordinaten des Pakets. art: 'distance' "
        "(genau 2 Punkte, Strecke), 'height' (genau 2 Punkte, Höhenunterschied), 'polyline' "
        "(2 bis 1000 Punkte), 'area' (3 bis 1000 Punkte, Fläche). Flächeninhalt und Ebenheit "
        "berechnet der Viewer beim Anzeigen aus den Eckpunkten; im Export stehen sie bei so "
        "angelegten Flächen nicht. messung_id optional — ohne vergibt der Server eine ('M-…')."
        + SICHTBAR + VORSCHLAG
    ))
    def messung_anlegen(
        projekt_id: ProjektId,
        art: werkzeuge.Messart,
        punkte: Annotated[list[Punkt], Field(min_length=2, max_length=1000)],
        messung_id: Annotated[EintragId | None, Field(description="Optional, sonst vom Server.")]
        = None,
        point_sources: list[dict[str, str] | None] | None = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.messung_anlegen(
            c, projekt_id, art, punkte, messung_id, point_sources))

    @mcp.tool(description=(
        "Liest den Bearbeitungsverlauf eines Projekts, neueste zuerst: {'ereignisse': [{id, "
        "zeit (Unix-Sekunden), nutzer, kanal (web|api), art (z. B. poi.angelegt, "
        "meta.geaendert), objekt, vorher, nachher, token}], 'weiter'}. Im datensparsamen Modus "
        "sind nutzer/token Pseudonyme und Freitexte ausgeblendet. Für ältere Einträge "
        "vor = 'weiter' der vorigen Seite. Nur für Bearbeiter; per Token nur Bearbeitungen."
    ))
    def protokoll_lesen(
        projekt_id: ProjektId,
        vor: Annotated[int | None, Field(ge=1, description="Ereignis-ID ('weiter').")] = None,
        limit: Annotated[int | None, Field(ge=1, le=200, description="Einträge je Seite.")]
        = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.protokoll_lesen(c, projekt_id, vor, limit))

    @mcp.tool(description=(
        "Exportiert Titel, Beschreibung, alle POIs und Messungen eines Projekts als JSON "
        "(Koordinaten in Metern, Weltkoordinaten). Nur für Bearbeiter — Betrachter bekommen "
        "'Keine Berechtigung'."
    ))
    def export(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.export(c, projekt_id))

    @mcp.tool(description=(
        "Liest den Befund-Faden eines POI (nur in Projekten mit eingeschalteten Befund-Fäden, "
        "sonst 404): {'stand': {status (offen|in_arbeit|erledigt|abgeschlossen|abgelehnt), "
        "zustaendig: {gewerk_id, nutzer_id} oder null, frist (JJJJ-MM-TT), version}, "
        "'eintraege': [{schluessel, art (kommentar|link|status|zustaendig|frist|markierung|"
        "vorschlag), text, wert, intern, erstellt_von, erstellt_am, kanal, zurueckgezogen_am, "
        "herkunft (mensch|ki), bezug, offen (nur bei vorschlag)}], 'rechte': "
        "{kommentieren, status: [erlaubte Zielstatus], zustaendig, frist, intern}}. Interne "
        "Einträge nur für die Bauherrenseite. Im datensparsamen Modus sind Kommentar- und "
        "Linktexte ausgeblendet und Konten Pseudonyme; Art, Status, Frist und Zeiten bleiben."
    ))
    def faden_lesen(projekt_id: ProjektId, poi_id: EintragId) -> dict[str, Any]:
        return _objekt(werkzeuge.faden_lesen(c, projekt_id, poi_id))

    @mcp.tool(description=(
        "Schreibt einen Kommentar in den Befund-Faden eines POI (bis 4000 Zeichen, eine Zeile). "
        "intern = true nur für die Bauherrenseite (sonst 403): dann sehen ihn Firmen und "
        "Betrachter nicht." + SICHTBAR_FADEN
    ))
    def kommentar_anlegen(
        projekt_id: ProjektId,
        poi_id: EintragId,
        text: Annotated[str, Field(min_length=1, max_length=4000)],
        intern: Annotated[bool, Field(description="Nur Bauherrenseite.")] = False,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.kommentar_anlegen(c, projekt_id, poi_id, text, intern))

    @mcp.tool(description=(
        "Setzt den Status eines Befunds. version = stand.version aus faden_lesen; hat jemand "
        "inzwischen geändert, kommt ein Konflikt — dann neu lesen. Erlaubte Ziele stehen in "
        "rechte.status von faden_lesen. 'abgelehnt' und Wiederöffnen ('offen' aus erledigt, "
        "abgeschlossen oder abgelehnt) brauchen eine begruendung; abschließen, ablehnen und "
        "wieder öffnen darf nur die Bauherrenseite, Rückmelder nur in_arbeit/erledigt, wenn sie "
        "zuständig sind." + SICHTBAR_FADEN + VORSCHLAG_FADEN
    ))
    def status_setzen(
        projekt_id: ProjektId,
        poi_id: EintragId,
        status: werkzeuge.Status,
        version: Version,
        begruendung: Annotated[str | None, Field(max_length=4000)] = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.status_setzen(c, projekt_id, poi_id, status, version,
                                               begruendung))

    _verwaltung(mcp, c)
    _auswertungen(mcp, c)
    _importe(mcp, c)
    return mcp


def _verwaltung(mcp: MCPServer, c: Client) -> None:
    """Löschen, Link, Zuständigkeit, Frist, Gewerke: Verwaltung wie im Portal, für Agenten."""

    @mcp.tool(description=(
        "Löscht einen POI (weich: das Bearbeitungsprotokoll hält Inhalt und Löschung fest). "
        "version = Feld 'version' aus pois_lesen; bei Konflikt neu lesen. Rechte wie in der "
        "Oberfläche. Antwort: {'geloescht': poi_id}." + VORSCHLAG_LOESCHEN
    ))
    def poi_loeschen(projekt_id: ProjektId, poi_id: EintragId, version: Version) -> dict[str, Any]:
        return _objekt(werkzeuge.poi_loeschen(c, projekt_id, poi_id, version))

    @mcp.tool(description=(
        "Löscht eine Messung (weich, im Protokoll nachvollziehbar). version aus messungen_lesen; "
        "bei Konflikt neu lesen. Antwort: {'geloescht': messung_id}." + VORSCHLAG_LOESCHEN
    ))
    def messung_loeschen(projekt_id: ProjektId, messung_id: EintragId,
                         version: Version) -> dict[str, Any]:
        return _objekt(werkzeuge.messung_loeschen(c, projekt_id, messung_id, version))

    @mcp.tool(description=(
        "Hängt einen Link an den Befund-Faden eines POI: url nur https:// oder ein Befundlink "
        "dieser Instanz (/ansicht?p=…#…), titel kurz. intern = true nur für die Bauherrenseite."
        + SICHTBAR_FADEN
    ))
    def link_anlegen(
        projekt_id: ProjektId,
        poi_id: EintragId,
        url: Annotated[str, Field(min_length=1, max_length=2000)],
        titel: Annotated[str, Field(min_length=1, max_length=200)],
        intern: Annotated[bool, Field(description="Nur Bauherrenseite.")] = False,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.link_anlegen(c, projekt_id, poi_id, url, titel, intern))

    @mcp.tool(description=(
        "Setzt die Zuständigkeit eines Befunds: gewerk_id aus gewerke_lesen, optional nutzer_id "
        "einer Person dieses Gewerks (aus beteiligte_lesen); gewerk_id = null hebt sie auf. "
        "version = stand.version aus faden_lesen; erlaubt laut rechte.zustaendig." + SICHTBAR_FADEN
        + VORSCHLAG_FADEN
    ))
    def zustaendig_setzen(
        projekt_id: ProjektId,
        poi_id: EintragId,
        version: Version,
        gewerk_id: Annotated[int | None, Field(ge=1)],
        nutzer_id: Annotated[int | None, Field(ge=1)] = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.zustaendig_setzen(c, projekt_id, poi_id, version, gewerk_id,
                                                   nutzer_id))

    @mcp.tool(description=(
        "Setzt die Frist eines Befunds als Datum JJJJ-MM-TT; null hebt sie auf. version = "
        "stand.version aus faden_lesen; erlaubt laut rechte.frist." + SICHTBAR_FADEN
        + VORSCHLAG_FADEN
    ))
    def frist_setzen(
        projekt_id: ProjektId,
        poi_id: EintragId,
        version: Version,
        frist: Annotated[str | None, Field(pattern=r"^\d{4}-\d{2}-\d{2}$")],
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.frist_setzen(c, projekt_id, poi_id, version, frist))

    @mcp.tool(description=(
        "Liest die Gewerke des Projekts: {'gewerke': [{id, name, kurz, bauherrenseite}]} — "
        "für zustaendig_setzen."
    ))
    def gewerke_lesen(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.gewerke_lesen(c, projekt_id))

    @mcp.tool(description=(
        "Liest den Paketinhalt: {revision, einheit (z. B. '[m, ETRS89/UTM33, NHN]'), "
        "hoehensystem, undulation {atOriginM, maxDeviationM, grid} (ellipsoidische Höhe = NHN + "
        "atOriginM, nur gültig bei maxDeviationM ≤ 0.01), ebenen [{id, art, titel, pointCount, "
        "boundsWorld, attributes, classes {ASPRS-Klasse: Punktzahl}, count}], genauigkeit}. "
        "Ohne Punktdaten und Bilder; die Fotos-Ebene fehlt, solange die Rolle die Fotos nicht "
        "sehen darf. Messungen verweisen mit pointSources[].layerId auf ebenen[].id."
    ))
    def paket_lesen(projekt_id: ProjektId) -> dict[str, Any]:
        return _objekt(werkzeuge.paket_lesen(c, projekt_id))


AuswertungId = Annotated[str, Field(
    description="id einer Auswertung aus auswertung_status oder auswertung_starten.",
    pattern=r"^[0-9a-f]{16}$",
)]


def _auswertungen(mcp: MCPServer, c: Client) -> None:
    """Auswertungsjobs: starten, Stand abfragen, Ergebnis lesen."""

    @mcp.tool(description=(
        "Startet eine Auswertung des aktiven Pakets auf dem Server (isolierter Job, rechnet nur, "
        "ändert nichts am Projekt). Verfügbar: typ 'auswerten_pruefen' (Lieferung gegen einen "
        "Anforderungskatalog prüfen) mit optionen {'profil': 'bestandserfassung'}. Andere Typen "
        "(Sonne, Import) lehnt der Server noch als nicht verfügbar ab. Braucht einen "
        "Schreib-Token und die Rolle Projektleitung; höchstens 3 offene Aufträge je Projekt. "
        "Antwort: Auswertung mit id, status (wartend), warteplatz und worker (aktiv|still|aus — "
        "bei 'still' oder 'aus' rechnet gerade niemand). Den Bericht für Bearbeiter freigeben "
        "kann nur ein Mensch."
    ))
    def auswertung_starten(
        projekt_id: ProjektId,
        typ: Annotated[str, Field(description="Auswertungstyp, z. B. 'auswerten_pruefen'.",
                                  pattern=r"^[a-z_]{1,40}$")],
        optionen: Annotated[dict[str, str] | None, Field(
            description="Feste Optionen, z. B. {'profil': 'bestandserfassung'}.")] = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.auswertung_starten(c, projekt_id, typ, optionen))

    @mcp.tool(description=(
        "Stand der Auswertungen eines Projekts: {'auswertungen': [{id, typ, optionen, status "
        "(wartend|laufend|fertig|fehlgeschlagen|abgebrochen), warteplatz, urteil, zaehlung, "
        "fehler, freigegeben, erstelltAm, beendetAm}], 'worker'}. Mit auswertung_id nur diese "
        "eine. Projektleitung sieht alle, Bearbeiter nur freigegebene."
    ))
    def auswertung_status(
        projekt_id: ProjektId, auswertung_id: AuswertungId | None = None,
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.auswertung_status(c, projekt_id, auswertung_id))

    @mcp.tool(description=(
        "Liest das Ergebnis einer fertigen Auswertung: {katalog {titel, sha256}, gesamt {urteil, "
        "zaehlung}, zeilen [{id, anforderung, pruefart, parameter, urteil (erfüllt|nicht "
        "erfüllt|nicht prüfbar), grund, belege}]}. Keine Rohdaten, keine Messwerttabellen; den "
        "vollständigen Bericht (HTML) lädt ein Mensch im Portal. Ergebnis dem Menschen "
        "vorlegen, nicht selbst als abgenommen werten."
    ))
    def auswertung_lesen(projekt_id: ProjektId, auswertung_id: AuswertungId) -> dict[str, Any]:
        return _objekt(werkzeuge.auswertung_lesen(c, projekt_id, auswertung_id))


def _importe(mcp: MCPServer, c: Client) -> None:
    """Neue Projekte aus fertig hochgeladenen Dateien (nur Admin)."""

    @mcp.tool(description=(
        "Startet ein neues Projekt aus einem fertig hochgeladenen Upload (Admin der Instanz, "
        "Schreib-Token; Import muss auf der Instanz eingeschaltet sein). Hochladen selbst geht "
        "nicht über dieses Werkzeug — große Dateien lädt ein Mensch im Portal (Seite Uploads) "
        "oder ein Skript über die API hoch; von dort kommt die upload_id. auftragstyp "
        "'neu_mesh' (eine GLB- oder PLY-Datei ohne Textur) oder 'neu_splats' (eine PLY-, SPZ- "
        "oder SPLAT-Datei, höchstens 1 Mio. Gaussians). optionen: projekt (Pflicht, neue ID aus "
        "Kleinbuchstaben, Ziffern, Bindestrich), titel, crs (leer = lokal, Modellmaß; sonst "
        "'EPSG:<Nummer>'), quellHoehe/zielHoehe (dhhn2016, ellipsoidal-etrs89, dhhn92; nur mit "
        "crs). Antwort: Import mit id, status (wartend) und worker. Ohne Koordinatensystem sind "
        "Lage und Maßstab nicht belegt — das dem Menschen sagen."
    ))
    def import_starten(
        upload_id: Annotated[str, Field(description="id eines abgeschlossenen Uploads.",
                                        pattern=r"^[0-9a-f]{32}$")],
        auftragstyp: Annotated[str, Field(description="'neu_mesh' oder 'neu_splats'.",
                                          pattern=r"^neu_(mesh|splats)$")],
        optionen: Annotated[dict[str, str], Field(
            description="{'projekt': 'neue-id', 'titel': '…', 'crs': 'EPSG:25833'}.")],
    ) -> dict[str, Any]:
        return _objekt(werkzeuge.import_starten(c, upload_id, auftragstyp, optionen))

    @mcp.tool(description=(
        "Stand der Importe (neue Projekte): {'importe': [{id, typ, projekt, titel, status "
        "(wartend|laufend|fertig|fehlgeschlagen|abgebrochen), stufe (konvertiert|prueft|"
        "freischalten), warteplatz, fehler, beendetAm}], 'worker'}. Mit projekt_id nur die "
        "Importe für dieses Projekt. Nach 'fertig' ist das Projekt in projekte_auflisten."
    ))
    def import_status(projekt_id: ProjektId | None = None) -> dict[str, Any]:
        return _objekt(werkzeuge.import_status(c, projekt_id))


def _abbruch(meldung: str) -> NoReturn:
    print(f"scanview-mcp: {meldung}", file=sys.stderr)
    raise SystemExit(2)


def umgebung_lesen(env: Mapping[str, str]) -> tuple[str, str]:
    """Prüft SCANVIEW_URL/SCANVIEW_TOKEN; Meldungen nennen nie einen Wert."""
    url = env.get("SCANVIEW_URL", "").strip().rstrip("/")
    token = env.get("SCANVIEW_TOKEN", "").strip()
    if not url:
        _abbruch("SCANVIEW_URL fehlt (Adresse der Plattform, z. B. https://scanview.example.de).")
    teile = urlsplit(url)
    erlaubt = (teile.scheme == "https" and bool(teile.hostname)) or (
        teile.scheme == "http" and teile.hostname in LOKAL
    )
    if not erlaubt:
        _abbruch("SCANVIEW_URL muss mit https:// beginnen (http:// nur für localhost).")
    if not token:
        _abbruch("SCANVIEW_TOKEN fehlt (persönlicher API-Zugang aus dem Portal, beginnt mit sv_).")
    if not token.startswith("sv_") or len(token) < 4 or not token.isascii() or not all(
        ch.isalnum() or ch in "_-" for ch in token
    ):
        _abbruch("SCANVIEW_TOKEN hat nicht das Format eines scanview-Tokens (sv_…).")
    return url, token


def main() -> None:
    url, token = umgebung_lesen(os.environ)
    schutz = datenschutz.aus_umgebung(os.environ)
    # httpx meldet jede Anfrage auf INFO (Methode + URL, nie Header) — auf stderr nur Rauschen.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    baue_server(Client(url, token, datenschutz=schutz)).run()


if __name__ == "__main__":
    main()
