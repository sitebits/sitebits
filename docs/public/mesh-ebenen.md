# Mesh-Ebenen im Viewer

Pakete aus dem Mesh-Import Weg A können eine Punktwolke und eine oder mehrere
Mesh-Oberflächen im gleichen Koordinatenrahmen enthalten. Der Viewer zeigt zuerst
die Vorschau. Beim Heranzoomen lädt er einmalig die Vollstufe nach und ersetzt die
Vorschau, sobald sie bereit ist.

Im Ebenen-Register schalten Sie jede Ebene einzeln ein oder aus und stellen ihre
Deckkraft ein. Die angezeigten Zahlentasten schalten dieselben Ebenen. Bei Meshes
aktiviert „Schattierung“ eine beleuchtete Oberfläche; standardmäßig bleiben die
Farben ohne zusätzliche Beleuchtung erhalten. Die Ebenenwahl bleibt beim Nachladen
und beim Wechsel in ein Standpunkt-Panorama erhalten.

„Einrahmen“ richtet die Ansicht auf die gewählte Ebene aus. `F` rahmt alle sichtbaren
Geometrie-Ebenen gemeinsam. Ohne vorgegebene Startansicht beginnt der Viewer mit
diesem gemeinsamen Rahmen; eine Startansicht im Paket oder im Link hat Vorrang.
Die Schnittbox beschneidet Punktwolke und Meshes gemeinsam, auch nach dem Nachladen.

## Herkunft und Messen

Die Untertitel der Ebenen erläutern die Herkunft. „Modellmaß – Maßstab nicht belegt“
bedeutet, dass keine belastbare metrische Skalierung belegt ist. „Oberfläche teils
interpoliert“ weist auf ergänzte Oberflächenbereiche hin. Solche Hinweise sind bei
der Beurteilung von Abständen und Oberflächen zu berücksichtigen.

Messungen und POIs treffen sichtbare Mesh-Ebenen auf ihrer **Vollstufe**.
Ein aktives Messwerkzeug lädt diese sofort nach. Vor Ende des Ladens gibt es
keinen Ersatzwert aus der Vorschau: „Feinste Stufe nicht rechtzeitig — erneut
klicken“. Die Schnittbox gilt auch für Messpunkte.

Ohne Fang gilt der Schnittpunkt mit der Fläche unter dem Mauszeiger. Die Taste
`E` schaltet den Fang ein: Liegt ein Vertex des getroffenen Dreiecks höchstens
8 Pixel vom Klick entfernt, rastet der Punkt genau darauf ein, sonst auf
Merkmalskanten (Diederwinkel ab 30°); gewöhnliche Dreieckskanten bleiben
unberücksichtigt. Aus der Ferne sind 8 Pixel auf einem vereinfachten Modell
mehrere Zentimeter — der Fang ist deshalb nur auf Wunsch an. Gleichstände werden über den kleineren Vertex- bzw. Kantenindex
aufgelöst. Bei zwei Flächen lässt sich wie bisher die vordere oder hintere wählen.

In gemischten Szenen zählt die nächste Datenfläche entlang des Klickstrahls.
Liegen Punktwolke und Mesh höchstens 5 mm auseinander, entscheidet „Messen auf“
im Werkzeugbereich: **Automatisch (Punktwolke vor Mesh)**, **Punktwolke** oder
**Mesh**. Die Auswahl bleibt beim Neuladen erhalten. Im Panorama misst stets
nur die Punktwolke der Station; Meshes bleiben eine sichtbare Überlagerung.

Die Messliste zeigt zu jedem Eckpunkt seine Ebene. JSON und CSV (Spalte „Quelle“)
führen die Herkunft mit, ebenso Server, MCP und Git-Export. „unbekannt“ bezeichnet
alte Daten ohne Quellenangabe; ihre Herkunft wird nicht geraten. Die Koordinaten
im Deep-Link bleiben unverändert und enthalten keine Herkunft.

Messwerte aus Ebenen ohne belegten Maßstab tragen „Modellmaß — Maßstab nicht belegt“
mit der deklarierten Quelle. Messwerte aus interpolierten Meshes tragen zusätzlich
„Modelloberfläche (teils interpoliert)“. Beide Hinweise stehen an Messwerten,
in der Liste und im Export. Innere Stimmigkeit zwischen Mesh und Wolke belegt
keine absolute Messgenauigkeit. Pakete, die ausschließlich Meshes enthalten,
werden weiterhin noch nicht unterstützt.

## Grenzen und Ladefehler

Unterstützt werden glTF-Meshes aus Import Weg A mit Meshopt-Kompression und
KTX2-Texturen. Andere Mesh-Formate bleiben im Register als nicht unterstützt sichtbar;
die übrigen Ebenen können weiter genutzt werden. PNG-/JPEG-Texturen im Mesh werden
abgelehnt, die betroffene Ebene wird gesperrt.

Kann der Browser komprimierte Texturen nicht darstellen, bleibt die Vorschau sichtbar;
die Vollstufe wird nicht geladen. Der Viewer reserviert für alle Meshes zusammen
höchstens 512 MiB Grafikspeicher, zusätzlich zum getrennten Panorama-Budget.
Bei einem Ladefehler oder zu großer Vollstufe bleibt die Vorschau nutzbar. Der Viewer
zeigt einen Hinweis und versucht die Vollstufe in derselben Sitzung nicht erneut.
Nach Behebung der Ursache startet ein Neuladen der Seite eine neue Sitzung.
