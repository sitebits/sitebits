# Barrierefreiheit — Betreiber-Doku

Diese Seite richtet sich an die Stelle, die eine scanview-Instanz betreibt, und beschreibt, was
scanview für die Barrierefreiheit liefert, was die Stelle selbst verantwortet und wie die Erklärung
zur Barrierefreiheit eingerichtet wird. Sie ersetzt nicht die Erklärung selbst — die liegt als Datei
in der Instanz-Konfiguration (siehe „Einrichten“ unten).

## 1. Wer was liefert

**Pflichtig ist die öffentliche Stelle, die scanview betreibt.** Sie veröffentlicht die Erklärung zur
Barrierefreiheit, beruft sich gegebenenfalls auf eine unverhältnismäßige Belastung und beantwortet
Rückmeldungen. scanview kann diese Pflicht nicht übernehmen, weil nur die Stelle weiß, wie sie die
Instanz einsetzt und welche Ausnahmen sie vor sich selbst und ihrer Aufsicht vertreten kann.

**scanview liefert:**

- eine **Vorlage** der Erklärung nach der Mustererklärung (Durchführungsbeschluss (EU) 2018/1523) mit
  den produktseitigen Angaben vorausgefüllt: `server/src/scanview_server/barrierefreiheit_vorlage.toml`
  im Repo, auf dem Host unter `/opt/scanview/server/src/scanview_server/barrierefreiheit_vorlage.toml`.
- die **Prüfnachweise** (axe-Läufe, manuelle Prüfliste, Prüfprotokoll) als Grundlage für das Feld
  „Methode der Bewertung“ der Erklärung.
- eine **Portal-Seite „Barrierefreiheit“**, deren Inhalt die Stelle je Instanz über eine eigene Datei
  festlegt (kein Einstellmenü) — mit Vorgabetext aus der Vorlage, solange die Stelle sie nicht ersetzt.
- einen **konfigurierbaren Feedback-Weg** je Instanz (Abschnitt 5).

**Pflichtinhalte einer Erklärung** (Mustererklärung, Stand Bundesfachstelle Barrierefreiheit): Stand
der Vereinbarkeit (vollständig/teilweise/nicht vereinbar), nicht barrierefreie Inhalte mit Gründen,
verfügbare Alternativen, Datum der Erstellung/Aktualisierung und Methode der Bewertung, ein
Feedback-Mechanismus mit Kontakt, sowie ein Hinweis auf das Durchsetzungsverfahren (Schlichtungs- bzw.
Ombudsstelle nach Bundes- oder Landesrecht). Aktualisierungsrhythmus und genaue Antwortfristen richten
sich nach dem jeweiligen Landesrecht — scanview gibt hier keinen Wert vor. Die Stelle nennt sie in
den vorhandenen Freitextfeldern der Vorlage: die Antwortfrist unter `[durchsetzung]` im Feld `text`,
den Aktualisierungsrhythmus im Feld `methode_text`. Ein eigenes Feld dafür gibt es nicht; unbekannte
Schlüssel lässt der Dienst beim Start nicht zu.

## 2. Einrichten

Zwei Einstellungen der Instanz:

| Einstellung | Wert | Zweck |
|---|---|---|
| `SCANVIEW_BARRIEREFREIHEIT_KONTAKT` | `mailto:<adresse>` (ohne `?`, `&`, `%`) oder `https://…` (Formular der Stelle) | Ziel des Links „Barriere melden“ |
| `SCANVIEW_BARRIEREFREIHEIT_ERKLAERUNG` | Pfad zur ausgefüllten Erklärung, z. B. `/etc/scanview/barrierefreiheit.toml` | Inhalt der Seite „Barrierefreiheit“ |

Vorgehen: die Vorlage nach `/etc/scanview/barrierefreiheit.toml` kopieren, Eigentümer/Rechte
`root:root 0644` setzen (nicht unter `/home` ablegen — der Dienst läuft mit `ProtectHome=yes` und kann
dort nichts lesen), jeden `@…@`-Platzhalter durch die eigenen Angaben ersetzen, dann beide
Einstellungen setzen (beim Betrieb als Systemdienst in `/etc/scanview/scanview-server.env`, der
`EnvironmentFile` der Dienst-Unit) und den Dienst **neu starten**. Ein Neuladen per Signal (`systemctl reload`)
genügt nicht — Umgebungsvariablen eines laufenden Prozesses ändern sich nur bei einem echten Neustart.

- **Ungültige Werte** (z. B. ein noch nicht ersetzter Platzhalter, eine `KONTAKT`-Adresse mit `?`/`&`/
  `%`, oder eine `ERKLAERUNG`-Datei mit weniger als einem oder mehr als 50 Einträgen unter
  `[[nicht_barrierefrei]]`): Der Dienst **startet nicht** und nennt in der Fehlermeldung, welche Datei
  und welches Feld betroffen ist.
- **Ohne gesetzte Werte** startet der Dienst trotzdem: Die Seite „Barrierefreiheit“ zeigt einen
  Hinweis für die Stelle statt eines toten Links, und im Journal steht eine `WARNING`-Zeile
  (`journalctl -u scanview-server | grep BARRIEREFREIHEIT`).

## 3. Die 3D-Ansicht in der Erklärung

Die 3D-Ansicht (Punktwolke, Panorama) ist der Teil, bei dem die Erklärung am meisten Sorgfalt
braucht. Die Vorlage schlägt eine **zweistufige Begründung** vor, warum die freie räumliche Erkundung
für Menschen, die nicht sehen, nicht barrierefrei ist:

1. **Gleichwertige Alternative für die Ergebnisse:** Befunde, Messwerte, Notizen, Koordinaten und
   Standpunkte stehen vollständig als Text zur Verfügung (Befundliste, Messliste, Inspektor, Exporte,
   API/MCP). Die WCAG-Ausnahme „sensorische Erfahrung“ passt hier **nicht** — die Punktwolke vermittelt
   Informationen und erfüllt eine Funktion, beides schließt diese Ausnahme aus.
2. **Unverhältnismäßige Belastung** für den verbleibenden Teil, die freie nichtvisuelle Erkundung
   selbst (z. B. Klangdarstellung oder Punkt-für-Punkt-Ansage der Punktwolke). **Diese Abwägung muss
   die Stelle selbst treffen und in ihre Erklärung übernehmen** (Art. 5 der Richtlinie (EU)
   2016/2102) — scanview kann nur beschreiben, was technisch möglich wäre und warum es heute nicht
   angeboten wird, nicht aber beurteilen, was für die konkrete Stelle verhältnismäßig ist.

Die Ausnahme für Kulturerbe (Art. 1 Abs. 4 Buchst. f der Richtlinie) ist in der Vorlage **bewusst
nicht vorausgefüllt** — ob sie für Museums- oder Denkmalscans einschlägig ist, ist eine Rechtsfrage,
die die Stelle selbst prüfen lassen muss, scanview trifft hier keine Vorentscheidung.

Alles um die 3D-Ansicht herum (Portal, Panels, Listen, Formulare, Exporte) wird **nicht** als Ausnahme
geführt — dieser Teil muss vereinbar sein. Für das Portal prüft die axe-Prüfung (Abschnitt 6)
automatisch erkennbare Verstöße. Die Panels und Listen der 3D-Ansicht sind nicht Teil dieses
Portal-Laufs; die Tests des Viewers prüfen sie mit axe gegen die WCAG-2.1-Tags in ausgewählten
Zuständen (Werkzeugansicht, Dialog „Bedienung“, Einstellungen, Begehen, offener Inspektor, Teilen,
Entwurfsangebot, Empfängeransicht am Desktop und mobil).

### 3D-Ansicht: Tastatur und Einstellungen

Die 3D-Ansicht lässt sich ohne Maus bedienen, soweit die Bedienung auf Tastatur oder Gamepad beruht:

- **Fokus:** Die Leinwand ist per Tab erreichbar und mit Umschalt+Tab wieder verlassbar; Fokus allein
  startet nichts. Esc führt aus jedem Zustand heraus (Dialog, Menü, Begehen).
- **Kürzel:** Alle Einzeltasten-Kürzel sind umbelegbar und gesammelt abschaltbar (Einstellungsdialog;
  Taste `?` oder der Knopf „Bedienung“ öffnet den Dialog „Bedienung“: oben Maus und Finger je
  Ansicht, die aktive zuerst, darunter die Tasten mit der aktuellen Belegung). Sie wirken nicht in Textfeldern und
  Dialogen. Belegung und Bedien-Einstellungen gelten je Browser und werden dort lokal gespeichert.
- **Begehen:** Bewegen, Drehen und Neigen per Tastatur oder Gamepad, wahlweise Drehen in festen
  Schritten, einstellbares Sichtfeld und Tempo; Bewegungsreduktion des Systems wird beachtet. Das
  Mausrad ändert das Tempo, auch nachdem Esc nur die Zeigersperre gelöst hat; das neue Tempo wird
  angesagt.
- **Orbit mit der Maus:** Links ziehen dreht um den Drehpunkt, rechts oder Umschalt+links
  verschiebt, das Mausrad zoomt zum Drehpunkt hin, ohne ihn zu verschieben. Die frühere Belegung
  (links verschiebt, rechts dreht) ist im Einstellungsdialog wählbar. Ein Doppelklick auf einen Punkt setzt den Drehpunkt;
  ein Marker zeigt ihn beim Setzen und beim Drehen und blendet nach kurzer Ruhe aus, bei
  Bewegungsreduktion ohne Übergang. Das Setzen wird angesagt („Drehpunkt gesetzt“). Der Marker
  selbst ist für Hilfstechnik verborgen; er ergänzt die Ansage nur sichtbar.
- **Fadenkreuz:** Der Messwert wird als Text angezeigt und nach kurzer Ruhe angesagt (Live-Region);
  Setzen, Abbrechen und Rückfragen sind per Tastatur möglich. Im Panorama gibt es das Fadenkreuz
  ebenfalls, dort ohne laufenden Messwert.
- **Befund setzen und teilen:** `F` (Gamepad: Y) setzt im Begehen und am Fadenkreuz im Panorama
  einen Befund am Zielpunkt, sonst der Knopf „Markieren“. Das Formular öffnet sich rechts als
  Inspektor; Esc schließt es und gibt den Fokus zurück. `t` oder der Knopf „Teilen“ erzeugt einen
  Befundlink zum ausgewählten Befund. Ein angefangener Befund bleibt nach einem Verbindungsabbruch
  als lokaler Entwurf erhalten und wird beim nächsten Öffnen angeboten.
- **Empfängeransicht und Befundliste:** Wer einen Befundlink öffnet, kommt in eine geführte Ansicht
  mit Befundkarte und Befundliste (Titel, Kategorie, Standpunkt, Koordinate, Datum, Urheber, Notiz
  als Text).
  Drehen und Zoomen gehen dort auch über Knöpfe statt über Ziehen; Sprunglinks führen zur
  Befundliste, zur 3D-Ansicht und zu den Werkzeugen. Die Befundliste ist die Textalternative zur
  räumlichen Ansicht; die Empfängeransicht ist auch auf schmalen Bildschirmen bedienbar. Mobil
  trennt bei ausgeklappter Befundkarte ein verschiebbarer Trenner die 3D-Ansicht von der Karte
  (hochkant liegt die Karte unten, quer rechts daneben): ziehen mit Maus oder Finger, Doppeltipp
  wechselt zwischen Standard und großer 3D-Ansicht; mit Fokus verschieben ihn die Pfeiltasten,
  Pos1 und Ende setzen ihn an die Grenzen, Eingabe wechselt wie der Doppeltipp, Esc klappt die
  Karte ein. Messbeschriftungen sind dort kleiner und mit „Messwerte zeigen“ abschaltbar.
- **Tastenhinweise:** Eine Leiste nennt die Tasten zum aktuellen Zustand mit der aktuellen
  Belegung und verschwindet für eine Aktion, nachdem sie dreimal ausgeführt wurde. Im Orbit nennt
  eine Zeile davor die Bedienung mit der Maus bzw. auf reinen Touch-Geräten mit dem Finger. Im
  Einstellungsdialog lassen sich die Hinweise immer zeigen oder zurücksetzen.
- **Bewegungsreduktion:** Ist sie eingeschaltet (System oder Einstellung), ist auch der Wechsel
  zwischen Panoramen ein Schnitt ohne Fahrt.
- **Ansichtswechsel und Anzeige:** Der Wechsel zwischen Punktwolke und Panorama wird mit einem
  kurzen Satz angesagt. Die Leistungsanzeige (Bildrate, Punkte) ist ein Schalter im
  Einstellungsdialog. Wo der Browser es erlaubt, schaltet der Knopf „Vollbild“ (mit
  `aria-pressed`) den ganzen Bildschirm ein; auf dem iPhone startet scanview ohne Adresszeile als
  Web-App vom Home-Bildschirm, der Dialog „Bedienung“ nennt den Weg.
- **Farbe:** Die Höhenfarben folgen einer Farbskala, die auch bei Farbsehschwäche unterscheidbar
  bleibt, mit Legende in Metern; Befund und Rückmeldung unterscheiden sich am Marker auch durch
  die Form, nicht nur durch die Farbe.

### 3D-Ansicht: bewusste Festlegungen

Einige Punkte der Bedienung sind bewusst so entschieden; sie sind kein Fehler und sollen in einer
Prüfung nicht als solcher gemeldet werden:

- **Empfängeransicht nur über einen Befundlink.** Sie öffnet sich für jede Rolle, auch für
  Betrachter, nur mit einem Befundlink; ohne Link öffnet sich die Werkzeugansicht, in der
  Betrachter wie bisher lokal messen können. „Im Werkzeug öffnen“ steht allen offen. Neuladen in
  der Empfängeransicht bleibt dort, Neuladen in der Werkzeugansicht öffnet keine
  Empfängeransicht.
- **Befundlink ohne Geheimnis.** Er enthält Ansicht, Befund und Datensatzstand, aber keinen
  Zugangsschlüssel: Öffnen verlangt Anmeldung und Projektrecht. Einen Link, der ohne Konto
  Inhalte zeigt, gibt es nicht. Ist die Ansicht im Link beschädigt, öffnet er trotzdem den Befund
  und springt zu ihm. Weicht der Datensatzstand ab, sagt ein Hinweis, ob der
  Befund auf einem älteren oder neueren Stand gesetzt wurde.
- **Taste `t` für „Befundlink teilen“.** Sie ist umbelegbar, mit den übrigen Einzeltasten
  abschaltbar und wirkt auch im Begehen; dort gibt sie zuerst die Zeigersperre frei und setzt den
  Fokus auf das Link-Feld.
- **`F` am Fadenkreuz im Panorama** setzt einen Befund statt „Alles zeigen“; der Dialog
  „Bedienung“ nennt das.
- **Was je Konto und was je Browser gilt.** Der Zähler der Tastenhinweise und der lokale
  Befundentwurf gelten je Konto (der Entwurf je Konto und Projekt); Tastenbelegung,
  Bedien-Einstellungen, die Aufteilung der mobilen Empfängeransicht und der Schalter „Messwerte
  zeigen“ gelten für den ganzen Browser.
- **Lage der Hinweisleiste.** Sie steht unten links und weicht dem fokussierten Element und
  geöffneten Panels aus; liegt rechts unten kein Panel, steht sie dort. Auf reinen Touch-Geräten erscheint
  sie erst nach dem ersten Tastendruck, in der mobilen Empfängeransicht oben rechts.
- **Drehen am Fadenkreuz im Panorama** geschieht immer in Schritten (Schrittweite aus dem
  Einstellungsdialog), auch wenn „Drehen in Schritten“ sonst ausgeschaltet ist. Ein Gamepad
  steuert das Panorama-Fadenkreuz nicht.
- **Befund im Panorama bricht ab, wenn sich die Ansicht ändert.** Dreht die Ansicht oder wechselt
  der Standpunkt, während der Zielpunkt gesucht wird, öffnet sich kein Formular; es kommt eine
  Meldung.
- **Offenes Formular im Begehen.** Solange es offen ist, steht die Kamera, und die Knöpfe des
  Gamepads ruhen. Am Panorama-Fadenkreuz drehen die Pfeiltasten nicht, solange es offen ist; mit
  der Maus lässt sich das Panorama weiter ziehen. Im Orbit bleibt die Ansicht bedienbar. Der Punkt
  ist in allen Fällen beim Öffnen fest erfasst.
- **Trenner nur mobil und bei ausgeklappter Karte.** Eingeklappt bleibt die Befundkarte eine
  Titelleiste unten; am Desktop haben Liste, Ansicht und Karte feste Spalten. Beide Bereiche
  behalten eine Mindestgröße; hochkant nimmt die Karte höchstens 60 % der Höhe ein.
- **Messbeschriftungen weichen in der Empfängeransicht ganz.** Läge eine Beschriftung unter der
  Werkzeugleiste oder „← Projekte“ oder liefe sie über den Rand der 3D-Ansicht, wird sie
  ausgeblendet statt angeschnitten oder verschoben; die Bedienelemente haben Vorrang. In der
  Werkzeugansicht bleiben die Beschriftungen wie bisher.
- **Befundliste nur in der Empfängeransicht.** Die Werkzeugansicht behält die POI-Liste; dort
  heißen die Einträge weiter „POI“.
- **Keine Klassenlegende.** Es gibt keinen Farbmodus nach Klassen, darum keine Legende dafür;
  Messlinien tragen ihren Wert als Beschriftung.
- **Rückmeldungen** tragen die Kategorie „Rückmeldung“; der Bezug zum Befund steht als erste
  Notizzeile „Zu Befund: …“. Eine Frage an den Empfänger steht in der Notiz ab einer Zeile, die mit
  „Frage:“ beginnt; Befundkarte und Befundliste zeigen sie als „Frage an Sie“.

Das ist eine Beschreibung der vorhandenen Bedienung, keine Aussage zur Konformität: Die freie
räumliche Erkundung bleibt für Menschen, die nicht sehen, wie in diesem Abschnitt beschrieben nicht
barrierefrei, und Prüfungen mit Screenreadern und Betroffenen stehen für die 3D-Ansicht noch aus.

## 4. KI-Assistent und MCP als zusätzlicher Zugang

Wer einen Screenreader nutzt, kann über einen KI-Assistenten mit MCP-Anbindung Fragen in Sprache oder
Text stellen (z. B. „Welche Befunde gibt es in Projekt X?“) und eine Textantwort bekommen — für
Punktwolken ein ungewöhnlich direkter Zugang, den die Erklärung als zusätzliche Alternative nennen
kann.

- **Kein Ersatz.** Das Portal und die Befundliste müssen ohne KI-Assistent zugänglich sein. Nicht
  jede Stelle erlaubt KI-Werkzeuge, und die Antwort eines Modells ist keine gesicherte Auskunft.
- **Nur wo freigegeben:** Der lokale MCP-Zugang ist nur aktiv, wenn die Stelle eine lokale
  Modell-URL konfiguriert — ohne diese Einstellung ist er aus. Der zugängliche Zugang über KI steht
  also nur dort zur Verfügung, wo die Stelle das ausdrücklich einrichtet.
- **Notizen sind ab Werk ausgeblendet.** Für einen zugänglichen Zugang über Notizinhalte müsste die
  Stelle sie eigens freigeben.

## 5. Feedback-Weg

Der Link „Barriere melden“ steht in der Fußzeile jeder Portal-Seite, auch ohne Anmeldung — wer an der
Anmeldung selbst scheitert, muss trotzdem melden können. Er funktioniert unabhängig vom
Instanz-Zustand: Ist kein Kontakt hinterlegt, führt der Link zur Seite „Barrierefreiheit“ selbst
(Abschnitt „Barriere melden“ dort); ist ein Kontakt konfiguriert, öffnet er `mailto:` mit
vorausgefülltem Betreff und der aktuellen Seite, oder das Formular der Stelle.

**scanview baut kein eigenes Ticketsystem und speichert Rückmeldungen nicht selbst** — die Mail
enthält nur Betreff und Seite (ohne Parameter, keine personenbezogenen Daten aus der Sitzung); das
Formular wird ohne Zusatzangaben geöffnet. Damit entsteht keine neue Datenhaltung, für die scanview verantwortlich wäre. Die
Datenschutzhinweise der Stelle sollten diesen Weg trotzdem nennen, weil die Rückmeldung selbst
personenbezogen sein kann (Absenderadresse, Freitext).

## 6. Prüfnachweise und Methode

- **Automatische Prüfung (Skript, kein erzwungenes Tor):** axe-core prüft jede zu prüfende Portal-Ansicht in jedem anwendbaren Zustand (26 Kombinationen) gegen die
  WCAG-2.2-AA-Tags (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`, `wcag22aa`). Ausnahmen sind nur mit
  Begründung erlaubt und stehen in einer eigenen Ausnahmeliste, nicht im Test selbst versteckt.
- **Manuelle Prüfliste:** `docs/public/barrierefreiheit-pruefliste.md`, zehn Punkte auf Grundlage der
  BIK-BITV-Prüfschritte (interne Selbstbewertung).
- **Stand des letzten Laufs** (2026-09-29, Zusammenfassung ohne Befunddetails — die stehen im
  internen Prüfprotokoll, nicht hier): axe fand 0 Verstöße in allen 26 geprüften Ansicht/Zustand-
  Kombinationen, unter Chromium und unter Firefox; 8 der 10 Prüflisten-Punkte ganz oder
  stichprobenhaft automatisch bzw. per Quelltext geprüft (Punkte 1, 5, 6 und 8 nur für Teilmengen der
  Ansichten); die Screenreader-Prüfung (NVDA, VoiceOver, TalkBack) steht noch aus.
- **Ein grüner automatischer Lauf ist kein Konformitätsnachweis.** Automatische Prüfungen finden nur
  einen Teil der Barrieren; wie viel, hängt von der Studie ab, verlässliche eigene Zahlen dazu liegen
  nicht vor. Konformität kann nur eine vollständige manuelle Prüfung mit echten Hilfsmitteln und
  im besten Fall mit Betroffenen belegen.
- **Vor dem ersten Hochschul-Pilotbetrieb** ist ein externer BIK-BITV-Test vorgesehen — die interne
  Selbstbewertung ersetzt keine externe Prüfung.

## 7. Offen für die Stelle

- **Leichte Sprache und Gebärdensprache auf der Startseite** (§ 4 BITV 2.0): ob diese Pflicht auf das
  Portal einer einzelnen scanview-Instanz zutrifft, ist rechtlich nicht abschließend geklärt — die
  Stelle sollte das für ihren Fall prüfen (lassen).
- **Aktualisierungsrhythmus und genaue Antwortfristen** für die Erklärung und für Rückmeldungen:
  richten sich nach Landesrecht; die Stelle trägt sie in die Freitextfelder der Vorlage ein
  (`[durchsetzung] text` bzw. `methode_text`), scanview macht dazu keine Vorgabe.
