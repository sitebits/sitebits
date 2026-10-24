# Controller-Steuerung

Der Viewer lässt sich mit einem Gamepad bedienen: in der Übersicht (Orbit), im Begehen, im Panorama und in Dialogen.
Es gilt ein Schema für alle Ansichten, nach der Konvention von Konsolen- und PC-Spielen. Tastatur und Maus bleiben
vollständig. Jede Controller-Funktion gibt es auch per Taste oder Maus.

Unterstützt sind Controller, die der Browser mit dem **Standard-Mapping** der Gamepad-API meldet (`mapping ===
"standard"`). Andere Controller werden einmal gemeldet („Controller ohne Standardbelegung wird nicht unterstützt“) und
dann übergangen.

## Belegung

Symbole: PlayStation / Xbox. Bei anderen Controllern stehen neutrale Namen (Knopf unten/rechts/links/oben, L-/R-Schulter …).

| Knopf | Übersicht (Orbit) | Begehen | Panorama | Menü, Dialog |
|---|---|---|---|---|
| Linker Stick | verschieben | gehen | – | Auswahl bewegen |
| Rechter Stick | um den Drehpunkt drehen | umschauen | umsehen | – |
| L2 / LT · R2 / RT (analog) | heraus- · hineinzoomen | abwärts · aufwärts | heraus- · hineinzoomen | – |
| L1 / LB · R1 / RB | Werkzeug zurück · weiter | ebenso | ebenso | – |
| ✕ / A | Punkt in Bildmitte setzen (mit Werkzeug) | Punkt setzen (mit Werkzeug) | Punkt setzen (mit Werkzeug) | auswählen |
| ○ / B | zurück: Zeichnung abbrechen | zurück: Zeichnung abbrechen, sonst Begehen beenden | Panorama verlassen | schließen |
| □ / X | Fang ein/aus | Zielen (fein, mit Fang) | Fang ein/aus | – |
| △ / Y | Begehen starten | Befund setzen | Befund setzen (mit Werkzeug) | – |
| Steuerkreuz ← → | Standpunkt zurück · weiter | ebenso | ebenso | Auswahl bewegen, Wert ändern |
| Steuerkreuz ↑ ↓ | – | Tempo schneller · langsamer | – | Auswahl bewegen |
| L3 / LS (drücken) | schneller verschieben (halten) | sprinten, bis der Stick ruht | – | – |
| R3 / RS (drücken) | alles zeigen | Blick waagerecht | Blick waagerecht | – |
| Options / Menü | Menü | Menü | Menü | schließen |
| Create / Ansicht, Touchpad-Klick | Belegung ein/aus | ebenso | ebenso | ebenso |

- **Rückfrage am Fadenkreuz** („Welche Fläche?“, „trotzdem setzen?“): ✕/A bestätigt, Steuerkreuz ↑/↓ wechselt, ○/B bricht ab.
- **○/B** folgt der Reihenfolge von Esc: zuerst eine offene Belegungsübersicht, dann ein Ziehen, dann eine Zeichnung.
  Erst danach beendet ○/B das Begehen oder verlässt das Panorama.
- **Ansichtswechsel:** Ein Knopf, der die Ansicht gewechselt hat, wirkt beim Loslassen nicht noch einmal. Beispiel:
  △ startet das Begehen, und sein Loslassen setzt dort keinen Befund.
- **Menü** (Options/Menü): Begehen starten oder beenden, Panorama verlassen, Alles zeigen, Karte, Ebenen, Messliste,
  Bedienung, Einstellungen. Das sind nur Funktionen, die es auch per Taste gibt. Das Menü ist ein modaler Dialog,
  das Begehen hält solange an.
- **Dialoge** (Menü, Bedienung, Einstellungen): Linker Stick und Steuerkreuz ↑/↓ bewegen den Fokus, gehalten mit
  Wiederholung. ←/→ ändern Auswahllisten und Zahlenfelder, ✕/A drückt Knöpfe und Kästchen, ○/B schließt.
- **Befund-Formular:** Solange es offen ist, ruht der Controller, denn es braucht die Tastatur.
- **Empfängeransicht:** nur ansehen (drehen, zoomen, Standpunkt, ausrichten), nichts setzen.
- **Umbelegen:** Jede Aktion lässt sich im Einstellungsdialog auf einen anderen Knopf legen (0–17) oder abschalten.
  Der Touchpad-Klick (17) zeigt die Belegung, solange keine andere Aktion auf ihm liegt. Umbelegungen aus Versionen
  vor dieser Steuerung (nur Begehen, andere Bedeutungen) werden verworfen.

## Gefühl

| Größe | Wert |
|---|---|
| Totzone der Sticks | radial 0,15, danach neu skaliert. Ab 0,95 Ausschlag voll |
| Totzone der Auslöser | 0,08 |
| Ansprechkurve | `f(m) = 0,2·m + 0,8·m²` auf den Betrag. Bei halbem Ausschlag ergibt das 0,3, bei vollem 1 |
| Begehen, Umschauen | 2,4 rad/s waagerecht, 1,56 rad/s senkrecht |
| Beschleunigung beim Halten | ab 90 % Ausschlag und 0,2 s Haltezeit innerhalb von 0,35 s auf das 1,75-Fache, nur waagerecht |
| Nachlauf | Zeitkonstante 0,05 s für An- und Abbremsen, als exaktes Integral (unabhängig von der Bildrate) |
| Zielen (□/X im Begehen) | Drehrate × 0,4, dazu Fang beim Setzen |
| Begehen, Gehen | Tempo aus den Einstellungen (Vorgabe 2 m/s), Steuerkreuz ↑/↓ je × 1,2 wie das Mausrad. Sprinten × 4 |
| Übersicht, Drehen | 2,0 rad/s um den Drehpunkt, 1,3 rad/s senkrecht |
| Übersicht, Verschieben | 0,9 × Abstand zum Drehpunkt je Sekunde, so passt es zu jedem Maßstab. L3 gehalten × 3 |
| Zoom (Übersicht, Panorama) | exponentiell, Faktor e^1,6 je Sekunde bei vollem Auslöser |
| Panorama, Umsehen | 120 °/s waagerecht, 78 °/s senkrecht, mal Sichtfeld/80°: wer hineinzoomt, dreht langsamer |

**Maßstab:** Übersicht und Panorama passen sich von selbst an: Verschieben und Zoom rechnen mit dem Abstand zum
Drehpunkt, das Umsehen mit dem Sichtfeld. Das Gehen im Begehen bleibt bewusst beim eingestellten Tempo in m/s.
Tastatur und Controller teilen sich dieses Tempo, und ein Gehtempo, das sich je nach Modell von selbst ändert, wäre
für die Tastatur eine Verschlechterung. Wer ein großes Gelände begeht, stellt es mit Steuerkreuz ↑/↓ (oder dem
Mausrad) ein oder sprintet mit L3.

Einstellungen (Dialog „Tastatur, Controller und Bewegung“, gespeichert im Browser):

- **Controller-Empfindlichkeit** 0,25–3 (Vorgabe 1): Faktor auf alle Dreh- und Umsehraten.
- **Blick nach oben/unten umkehren** gilt gemeinsam für Maus und rechten Stick.
- **Controller vibriert beim Setzen und Bestätigen**: kurz (40 ms, `dual-rumble`), nur wenn der Browser
  `vibrationActuator` anbietet. Sonst entfällt die Vibration ohne Meldung.
- **Drehen in Schritten** und **Bewegung reduzieren** gelten auch für den Controller. Unter reduzierter Bewegung gibt
  es keinen Nachlauf und keine Beschleunigung. Bei Drehen in Schritten dreht der Stick je Ausschlag einen Schritt.

## Sichtbarkeit

- **Beim Verbinden** steht in der Meldungszeile für einige Sekunden „Controller verbunden (PlayStation)“ mit den
  wichtigsten Knöpfen. Derselbe Satz wird angesagt.
- **Kontextleiste** unten mittig, wie in Spielen: nur was jetzt wirkt, höchstens fünf Einträge, z. B.
  „✕ Punkt setzen · △ Befund · □ Zielen · ○ Abbrechen · Create Belegung“. Sie erscheint, solange der Controller
  zuletzt benutzt wurde. Ein Tastendruck oder Mausklick blendet sie aus, und die Tastenhinweise kehren zurück.
- **Belegungsübersicht** (Create/Ansicht oder Touchpad-Klick): die vollständige Tabelle der aktuellen Ansicht, dazu
  Typ und Kennung des Controllers (`gamepad.id`). Sie ist nicht modal, das Begehen läuft weiter.
- **Fadenkreuz in der Übersicht:** Ist ein Werkzeug gewählt und wird der Controller benutzt, zeigt ein Fadenkreuz die
  Bildmitte, an der ✕/A setzt.
- Dieselben Tabellen (PlayStation und Xbox nebeneinander) stehen im Dialog „Bedienung“ (`?`).

## Erkennung und Plattformen

Der Typ kommt aus `gamepad.id`. Die Kennungen unten sind die erwarteten Formen der Browser. Am echten Gerät sind sie
noch nicht abgelesen; die Erkennung stützt sich deshalb auf die Vendor-Kennung, nicht auf den genauen Text.

| Controller | Chrome/Chromium (Linux, Windows) | Firefox | Symbole |
|---|---|---|---|
| DualSense (PS5) | `DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)` | `054c-0ce6-…DualSense Wireless Controller` | PlayStation |
| DualSense Edge | `… Vendor: 054c Product: 0df2` | `054c-0df2-…` | PlayStation |
| DualShock 4 | `Wireless Controller (… Vendor: 054c Product: 05c4/09cc)` | `054c-05c4-…` | PlayStation |
| Xbox | `Xbox … (… Vendor: 045e …)` | `045e-…` | Xbox |
| alle anderen (auch Nintendo) | | | neutral |

Erkannt werden die Vendor-Kennung 054c bzw. 045e in beiden Schreibweisen und die Namen DualSense, DualShock und Xbox.
Bei Nintendo-Controllern liegen A und B vertauscht. Sie bekommen deshalb neutrale Namen nach Lage („Unten“, „Rechts“).

Unterschiede zwischen Browsern und Linux. Belegt sind sie aus dem Quelltext der Browser (Stand Oktober 2026),
an einem echten DualSense geprüft sind sie noch nicht:

- **Chrome/Chromium unter Linux:** `device/gamepad/gamepad_standard_mappings_linux.cc` bildet 054c:0ce6 und 054c:0df2
  mit `MapperDualSense` auf das Standard-Mapping ab, den Touchpad-Klick als zusätzlichen Knopf direkt nach den 17
  Standardknöpfen, also Knopf 17. Meldet der Kernel-Treiber `hid-playstation` die bereits umgestellte Belegung (hohes
  Bit in bcdHID), nimmt Chrome stattdessen `MapperPs4Ps5`, und der hat **keinen Touchpad-Knopf**. Unter aktuellen
  Kerneln ist deshalb zu erwarten, dass nur Create (Knopf 8) die Belegung zeigt, nicht das Touchpad. Create ist
  darum die Vorgabe, der Touchpad-Klick nur eine Zugabe.
- **Firefox:** `dom/gamepad/GamepadRemapping.cpp` hat einen `DualSenseRemapper` für 0ce6 und 0df2 (Touchpad ebenfalls
  als Knopf 17) und einen `Dualshock4Remapper`. Das Standard-Mapping ist damit auch in Firefox zu erwarten.
  Unbestätigt ist, ob die Knopfreihenfolge unter `hid-playstation` stimmt. Weicht sie ab, zeigt die
  Belegungsübersicht falsche Wirkungen. Dann hilft Chrome oder die Umbelegung.
- **Vibration:** `GamepadHapticActuator.playEffect` gibt es in Chrome seit Version 68. Firefox unterstützt es nicht
  (MDN browser-compat-data). In Firefox entfällt die Vibration also.
- **Die Bewegung auf dem Touchpad** hat im Standard-Mapping keine Achse und wird nicht genutzt.
- **Kennung ablesen:** Die Belegungsübersicht zeigt die vom Browser gemeldete Kennung an. Weicht sie ab und stimmen
  die Symbole deshalb nicht, genügt diese Zeile, um die Erkennung zu ergänzen.

## Aufbau (für Entwickler)

Die Eingabeschicht ist in reine, getestete Stufen getrennt:

1. `web/src/gamepad.ts`: Rohwerte → radiale Totzone → Ansprechkurve → benannte Aktionen mit Flanken, analoge
   Auslöserachse, Controllertyp.
2. `web/src/padGefuehl.ts`: Drehrate mit Beschleunigung und Nachlauf (dt-basiert), exponentieller Zoom.
3. `web/src/padModus.ts`: Bedeutung je Ansicht, Symbole, Belegungstabelle und Kontextleiste.
4. `web/src/padSteuerung.ts` (DOM): eine Abfrage je Bild, Verteilung je Ansicht, Kamera in Übersicht und Panorama,
   Menü, Übersicht, Leiste, Meldung, Vibration. Im Begehen liest dessen Bildschleife über `quelle()`.
