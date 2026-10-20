# Gaussian-Sichtschicht

Die Ebene „Sichtschicht“ zeigt Gaussian Splats für den ersten Eindruck. Ihr Schalter im
Ebenen-Panel blendet sie ohne Neuladen ein und aus. „Einrahmen“ richtet die Kamera darauf aus.
Fotos und Panoramen bleiben eigene Ebenen.

Messen und Fangen arbeiten ausschließlich auf Punktwolke oder Mesh, auch wenn die Sichtschicht
sichtbar bleibt. Ohne messbare Ebene entsteht kein Messpunkt. Die Rahmenprüfung belegt die
Deckung mit der Referenzwolke, keine absolute Vermessungsgenauigkeit der Gaussians.

Ein Paket kann nur Mesh, nur Splats oder beide enthalten: Ursprung und Startansicht kommen
vom Paketrahmen, die Ausdehnung aus den räumlichen Ebenen. RAD lädt eine grobe Ansicht und anschließend Detailseiten nach. Spark begrenzt die Darstellung
auf 500.000 Gaussians und den Pager auf 1.048.576 Plätze; auf Touch-/≤4-GB-Geräten gelten
100.000 und 131.072 Plätze. Das sind konfigurierte Budgets, keine gemessenen GPU-Bytes.
Die Ladeanzeige bleibt bis zum ersten gezeichneten Bild mit einer gefüllten Seite.
SPZ/PLY werden vollständig geladen: dafür bleiben eine Million Gaussians und 256/64 MiB
Datei-/Dekodierbudget. SH bis Grad 3 bleibt bei allen Formaten erhalten.

## Additiver Schema-2-Vertrag

`initialView.layers` ist optional: eindeutige IDs vorhandener, unterstützter Startebenen.
Fehlt es, gilt die bisherige Voreinstellung. Der Hashparameter `ly` hat Vorrang, auch ein leeres
`ly=` (alle aus). Schema 1 und bestehende Schema-2-Pakete benötigen keine Migration auf Platte.
Ungültige Referenzen werden abgewiesen. Kamera: `target` im Weltrahmen, positive endliche `distance`. Optional `position` im Weltrahmen, endlich und verschieden vom
Ziel; der Viewer verwendet sie auch beim Zurücksetzen.
Ohne deklarierte Startansicht rahmt der Viewer die sichtbaren räumlichen Ebenen.

Die Sichtschicht trägt `id`, `kind: "splats"`, `title`, `format: "rad"` (große Ebene), `"spz"` (klein) oder `"ply"` (Notweg), `url`, positive ganze
`splatCount`/`sizeBytes`, endliche geordnete `boundsWorld`, ganzzahligen `shDegree` von 0 bis 3 und einen positiven
`frameCheck` (`ok: true`, `samples ≥ 1000`, endliche `0 ≤ p50M ≤ p95M`). Höchstens eine pro Paket.
SPZ/PLY: optional `chunkCount: 1`. RAD: positive ganze `chunkCount` (Anzahl RADC-Dateien,
Hauptdatei nicht mitgezählt), `lodMethod: "bhatt-lod"`; `sizeBytes` summiert Hauptdatei und
Chunks. Alle RADC-Dateien liegen neben der Hauptdatei. `splatCount` zählt ausschließlich
Blätter, der RAD-Header zusätzlich LoD-Eltern. `buildLod` nennt Tag, Commit, Binary-SHA256,
Lizenz und Quelle; derselbe Herkunftsblock steht im Konvertierungsbericht.
Mittelpunkte liegen relativ zu `frame.originWorld`, die Weltgrenzen bleiben Float64.
Der Import ergänzt `sourceOptions`, `alignment`, `source`, `verify` und einen Konvertierungsbericht.
`sourceOptions.shPolicy: "rotate-and-preserve"` dokumentiert die gedrehten SH-Koeffizienten.
SPZ speichert RUB-Achsen; der Viewer dreht sie zurück in den Paketrahmen. Intern werden
Float32-Positionen (Spark ExtSplats) benutzt, keine Float16-Mittelpunkte.
SPZ v3 quantisiert Positionen mit 2^-12 m pro Achse; die dekodierte Rückprüfung erlaubt
√3 × 2^-13 m plus 10 nm Rechentoleranz (rund 0,2115 mm). PLY behält die 1-mm-Grenze. RAD entsteht aus transformiertem Float32-PLY in Paketachsen.
Der Import liest jede Seite und vergleicht sämtliche Blätter (`child_count == 0`) als
quantisierte Positionsmultimenge mit der transformierten Quelle. Doppelte/fehlende Blätter
werden erkannt. Die Toleranz ergibt sich je Achse aus einer halben Float32-ULP am größten
lokalen Betrag, als euklidische Norm plus 10 nm; Formel und gemessener Fehler stehen im Bericht.
Deckungsprüfung erfolgt vor dem Encoder und erneut auf den dekodierten Blättern.
LoD-Eltern sind erfundene Sichtstufen und fließen weder in Rahmenprüfung noch Messung ein.

Server-Range/ETag und Berechtigungen bleiben im bestehenden Dateipfad. `paket_lesen` zeigt
Format, Chunkanzahl, LoD-Methode, Anzahl, Bounds, SH-Grad, Kamera-Ausrichtung und frameCheck; die Mess-APIs akzeptieren Splats nicht.
Die vorhandene Viewer-CSP erlaubt Spark-WASM und Blob-Worker bereits. Kein CDN-Laden erforderlich.
