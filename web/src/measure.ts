import { sourceFromPick, measurementNotes, sourceReusable } from './pointSources';
import type { PointSource, SourceLayer } from './pointSources';
// Messwerkzeug (Strecke, Polylinie, Fläche, Höhe/Lot, Eckpunkte ziehen/
// einfügen/löschen, Rückgängig). Jeder Eckpunkt kommt aus der Strahlsuche hinter der Naht PickSource
// (rayPick.ts). Importiert Giro3D/three, deshalb ohne Vitest-Test — die reinen Teile liegen in
// measureStore.ts, areaMath.ts, handleMath.ts, keymap.ts (Vitest); das Übrige sichern typecheck/build,
// die E2E-Specs (measure-tools, reference, pano-reference) und die Sichtprüfung (screenshot-measure.mjs).
//
// API-Fakten (geprüft in node_modules/@giro3d/giro3d 2.0.4, kein "exports"-Feld → tiefe Pfade):
//   - PointCloud.pick() (core/picking/PickPointsAt.ts) liefert PointsPickResult mit `point`
//     bereits als Welt-Vector3 (positions.applyMatrix4(objectMatrixWorld)) und `distance` = Abstand
//     Kamera→Punkt (nicht Abstand zum Klickstrahl). Das Pick-Rendering läuft mit Tiefentest, jedes
//     Pixel zeigt also den vordersten *angezeigten* Punkt — wo die grob dargestellte Fläche aus der
//     Nähe Lücken hat, sieht der Pickradius durch sie hindurch auf Hintergrundpunkte
//     (Ausreißer bis +29,7 m). COPC-Knoten bestehen aus Originalpunkten (keine synthetischen
//     LOD-Punkte), jeder Treffer ist also ein echter gemessener Punkt.
//   - Picking tiefenbewusst, zweistufig — Logik in pickMath.ts (Vitest), Umsetzung in
//     rayPick.ts:
//     (1) grobe Stufe: ein Pick mit PICK_RADIUS_WIDE (16 px). Daraus
//         - „unter dem Cursor“ = Kandidaten mit ≤ PICK_RADIUS (4 px) Strahlabstand (pixelOffset),
//         - die vorderste Fläche der Umgebung (Tiefenlücke > max(5 cm, 2 % Tiefe) trennt Vorder-
//           von Hintergrund). Aus der Nähe ist die angezeigte Wand bei 1 Mio. Punktbudget nur ein
//           Punkteraster (≈ 10 px Abstand, per Playwright-Ausschnitt belegt). Diagnose der drei
//           Ausreißer eines Zwischenstands (Referenzstrecken 4B/8B/9A, Kamera 1,5 m): im 4-px-Kreis
//           nur Hintergrundpunkte (3,5–19,6 m), im 15-px-Kreis je 16–22 Wandpunkte; im Endlauf lag
//           an 7 von 20 Endpunkten der vorderste 4-px-Kandidat im Hintergrund. Deshalb bestimmt die Umgebung, *ab wo*
//           gesucht wird, und der Cursor-Kreis, *bis wohin* (ein echtes Loch führt zum Hintergrund).
//     (2) feinste Stufe: source.getHierarchy()/getNodeData() (öffentliche PointCloudSource-API, kein
//         direkter copc-Import). Der Strahl wird von der vordersten Fläche an abschnittsweise
//         abgesucht (findSurfaceAlongRay): je Abschnitt alle Knoten *jeder* Tiefe lesen, deren
//         Volumen die Abschnitts-Box schneidet (COPC legt jeden Originalpunkt genau einmal in
//         irgendeine Stufe; erst die Vereinigung ist die volle Auflösung), Punkte im 4-px-Kegel um
//         den Strahl sammeln, an der ersten echten Fläche (Streupunkt-Regel minClusterSize 2)
//         anhalten und dort den strahlnächsten Originalpunkt nehmen. Bereits angezeigte Knoten
//         kommen aus Giro3Ds GlobalCache (kein HTTP, nur Dekodieren im Worker); je Klick wird jeder
//         Knoten höchstens einmal dekodiert.
//         Zeitbudget FINE_BUDGET_MS ab Klick; sonst grober Treffer mit Hinweis „grobe Stufe“ — im
//         Panorama PANO_FINE_BUDGET_MS (1500 ms) und jeder grobe Ersatz nur bis PANO_COARSE_MAX_M
//         (10 mm), sonst kein Messwert; gilt für jeden Eckpunkt, auch beim Ziehen und
//         Einfügen.
//   - Instance.pickObjectsAt (core/Instance.js): mit sortByDistance wird je Quelle *ohne* Limit
//     gepickt, erst danach nach Kameraabstand sortiert und auf `limit` gekürzt — das alte `limit: 8`
//     schnitt also nicht vor der Sortierung ab, hielt aber nur die 8 kameranächsten Kandidaten.
//     Jetzt ohne Sortierung (wir sortieren selbst nach Tiefe) und mit PICK_LIMIT = Pixelzahl der
//     Pickzone (2r+1)² = 1089, d. h. ohne Kürzung: mehr verschiedene Punkte als gelesene Pixel
//     kann der Pick nicht liefern (gemessen an 40 Referenz-Klicks: 38–368 Kandidaten im 16-px-Kreis).
//   - Giro3D-Eigenheit (core/picking/PickingCircle.js): der Pufferindex wird als
//     realY·(2r)+realX statt realY·(2r+1)+realX berechnet — die gelesenen Pixel sind eine
//     gescherte Auswahl aus der (2r+1)²-Zone, `coord` des Ergebnisses stimmt nicht mit dem
//     gelesenen Pixel überein. Deshalb nutzen wir nur `point` (Tiefe/Strahlabstand selbst
//     berechnet), nie `coord`.
//   - ConstantSizeSphere (renderer/ConstantSizeSphere.js) ist Giro3Ds eigene Lösung für "Kugel mit
//     konstanter Bildschirmgröße" (Radius in Pixeln, Skalierung läuft automatisch über
//     Mesh.onBeforeRender) — deshalb kein eigener Kamera-Änderungs-Listener nötig. Die Marker sind
//     inzwischen KugelMarker (markerKugel.ts, erbt davon): Radius in Weltmetern zwischen Mindest- und
//     Höchstgröße am Bildschirm, schattiert per Matcap; der Fangradius eines Griffs wächst mit der
//     sichtbaren Kugel (Handle.r, handleMath.ts).
//   - CSS2DObject (three/examples/jsm/renderers/CSS2DRenderer.js) wird von Giro3D bei jedem Frame
//     automatisch mitgerendert (renderer/c3DEngine.ts: render() ruft immer
//     labelRenderer.render(scene, camera) auf, unabhängig davon ob CSS2DObjects existieren) — das
//     Distanz-Label braucht keine eigene Renderschleife, nur instance.notifyChange() nach Änderung.
//   - Marker- und Label-Position laufen über object.position (Welt, Float64 in JS-Zahlen); nur die
//     Linien-/Flächen-Geometrie bekommt einen lokalen Vertexpuffer relativ zum ersten Eckpunkt, damit
//     ein Float32Array bei UTM-Koordinaten (mehrere 100.000 m) nicht schon im Dezimeterbereich
//     rauscht (vgl. viewer.ts-Kommentar zu Knotenpuffern).
//   - WICHTIG, per Sichtprüfung gefunden (zwei getrennte Ursachen, beide nur mit EDL sichtbar):
//     (1) Frisch per group.add() eingefügte Objekte waren im allerersten Redraw unsichtbar (Marker
//     blieben auf ConstantSizeSphere-Skalierung [1,1,1] hängen, das CSS2D-Label bekam
//     display:none) — ihr matrixWorld war noch Identität (Übersetzung [0,0,0] statt der
//     Weltposition aus object.position), per Playwright-Debug verifiziert. drei.js schreibt
//     matrixWorld normalerweise beim nächsten Szenendurchlauf fort; Giro3Ds Custom-Pipeline
//     (RenderPipeline.ts, aktiv sobald EDL an ist) traversiert die Szene für ihr Eimer-System aber
//     off-screen und offenbar bevor dieser Durchlauf für gerade erst hinzugefügte Objekte
//     stattgefunden hat. Fix: group.updateMatrixWorld(true) direkt vor jedem
//     instance.notifyChange(group). (2) Mit korrektem matrixWorld, aber gewöhnlichem opaken
//     Material, blieben Marker/Linie sichtbar-aber-blass: der EDL-Pass (RenderPipeline.ts,
//     BUCKETS.POINT_CLOUD) rendert nach dem OPAQUE-Eimer in dasselbe Render-Target und schwächt an
//     jedem Pixel, an dem zugleich ein echter Punkt liegt (Marker sitzen exakt auf Picking-
//     Ergebnissen, also fast immer der Fall), das dort schon Gezeichnete sichtbar ab. Der
//     TRANSPARENT-Eimer rendert dagegen nach dem Punktwolken-Pass — deshalb `transparent: true` auf
//     Marker- und Linienmaterial, unabhängig von der tatsächlichen Deckkraft (Opacity bleibt 1).
//     Beides zusammen (per Screenshot-Vergleich mit/ohne verifiziert) ergibt die volle Deckkraft.
//   - Ist eine Schnittbox aktiv, werden Pick-Kandidaten (grob wie fein) außerhalb
//     davon vor der Auswahl verworfen (pointAllowed aus clipBoxMath.ts). Weitgehend Verteidigung in der
//     Tiefe — Giro3Ds Clipping-Planes laufen im selben Shader wie das Picking, ausgeblendete Punkte
//     tauchen also GPU-seitig meist schon gar nicht in den Pick-Ergebnissen auf (siehe clipBox.ts).
import { Group, Line, Mesh, Vector3 } from 'three';
import type { MeshMatcapMaterial, Object3D } from 'three';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import ConstantSizeSphere from '@giro3d/giro3d/renderer/ConstantSizeSphere.js';
import { nearestHandle, schliesstFlaeche } from './handleMath';
import { istTouch } from './geraet';
import type { Handle } from './handleMath';
import { formatM, num } from './measureCsv';
import { KUGEL_ECKPUNKT, KUGEL_MITTELPUNKT, MAT, areaFill, localLine, makeLabel, makeMarker, mid, vec } from './measureDraw';
import { vorschlagText } from './vorschlagApi';
import { kugelMaterial, kugelKamera } from './markerKugel';
import { kugelRadius } from './markerMath';
import type { KugelGroesse } from './markerMath';
import { MeasureError } from './measureStore';
import type { Measurement, MeasureKind, MeasureStore } from './measureStore';
import { panoPickOptions } from './rayPick';
import type { PickInfo, PickSource, RayPickResult } from './rayPick';
import type { Vec3 } from './manifest';
import type { Viewer } from './viewer';
import { keinPunktGrund, keinPunktText, zielpunktSetzen } from './zielpunkt';
import type { Rueckfrage, Wahl } from './zielpunkt';

export type { PickInfo } from './rayPick';

const MSG_MS = 2000;
const CLICK_MOVE_TOLERANCE_PX = 4;
/** Zweiter Druck auf denselben Griff innerhalb dieser Zeit = zweiter Klick eines Doppelklicks, zählt nicht
 *  (Zeitstempel statt ev.detail: pointerdown trägt detail in Chromium nicht verlässlich). */
const DOUBLE_CLICK_MS = 500;
/** Handle-ID der offenen Zeichnung beim Eckpunkt-Fang (Messungs-IDs beginnen mit „M“). */
const OPEN_ID = '';

/** Hinweis ohne Punkt: derselbe Satz wie beim Fadenkreuz und bei POIs (zielpunkt.ts), hier mit der Folge
 *  für Messpunkte bzw. den gezogenen Eckpunkt. */
const noPointMessage = (info: PickInfo, hiddenCloudMessage: string, folge: string, verdeckt?: boolean): string =>
  keinPunktText(keinPunktGrund(info, verdeckt ? 'verdeckt' : undefined), { folge, ausgeblendet: hiddenCloudMessage, feinGrund: info.fineReason, mesh:info.source === 'mesh' });

function say(text: string): void {
  const el = document.getElementById('msg');
  if (!el) return;
  el.textContent = text;
  // Danach die Grundmeldung (z. B. den CRS-Hinweis aus main.ts, data-base) wiederherstellen.
  window.setTimeout(() => {
    if (el.textContent === text) el.textContent = el.dataset.base ?? '';
  }, MSG_MS);
}

export interface MeasureToolOptions {
  sourceLayers?: readonly SourceLayer[];
  vorschlaegeSichtbar?: () => boolean;
  v: Viewer;
  store: MeasureStore;
  /** Nach jeder Änderung am Speicher (Hash, Liste). */
  onChange: () => void;
  source: PickSource;
  inPanorama: () => boolean;
  /** Panorama-Blick sperren, solange ein Eckpunkt gezogen wird (Orbit: controls.enabled). */
  setLookLocked: (on: boolean) => void;
  /** Zeichenzustand an die Leiste melden („Fertig“/„Abbrechen“ zeigen). */
  onDrawingChange: (drawing: boolean) => void;
  /** POI-Anlegemodus aktiv → Griffe nicht greifbar (der Klick gehört pois.ts). */
  poiMode: () => boolean;
  /** Kantenfang an (Leiste/Taste E) — gilt für Zeichnen, Ziehen und Einfügen gleich. */
  snapEdges?: () => boolean;
  /** Meldung, wenn die Punktwolke ausgeblendet ist (Ebenen-Register). */
  hiddenCloudMessage: string;
  /** Pointer-Lock aktiv (Begehen): der Klick trägt eine eingefrorene Mausposition und setzt nichts. */
  unterPointerLock?: () => boolean;
  /** Rückfrage „vorn/hinten“ beim Klick (Sichtbarkeitsregel außerhalb des Panoramas); ohne sie gilt der
   *  Treffer der Strahlsuche wie bisher. */
  frage?: (b: Rueckfrage, ort: { x: number; y: number }) => Promise<Wahl | null>;
  /** Eine Rückfrage ist offen: weitere Klicks setzen nichts. */
  rueckfrageOffen?: () => boolean;
  /** Kamerapose zum Vergleich: bewegt sie sich zwischen Klick und zweiter Suche, wird abgebrochen. */
  pose?: () => readonly number[];
}

/** Ergebnis eines Setzens an Bildschirmkoordinaten (Fadenkreuz), nach dem Anwenden. */
export type SetzStatus = 'gesetzt' | 'geschlossen' | 'kein-treffer' | 'abgebrochen' | 'ansicht' | 'verworfen';

export interface MeasureSelection {
  id: string;
  /** Eckpunkt-Index; fehlt → ganze Messung (Auswahl über die Messliste). */
  index?: number;
}

export interface MeasureTool {
  /** null = kein Messwerkzeug (Navigieren, POI). Wechsel verwirft eine offene Zeichnung mit Hinweis. */
  setKind(kind: MeasureKind | null): void;
  kind(): MeasureKind | null;
  /** Ein Messwerkzeug ist aktiv (Standpunkt-Klick hält sich raus). */
  isEnabled(): boolean;
  isDrawing(): boolean;
  isDragging(): boolean;
  hasSelection(): boolean;
  selection(): MeasureSelection | null;
  /** Offene Polylinie/Fläche abschließen — in Klickreihenfolge nach noch laufenden Picks; ungültig →
   *  Hinweis, Zeichnung bleibt offen. */
  finish(): void;
  cancelDrawing(): boolean;
  cancelDrag(): boolean;
  /** Erst den letzten offenen Eckpunkt, sonst store.undo() — in Klickreihenfolge nach laufenden Picks. */
  undo(): void;
  deleteSelection(): void;
  select(sel: MeasureSelection | null): void;
  /** Liegt ein Griff unter (x, y)? Der Standpunkt-Klick in main.ts wertet dann nicht aus. */
  hitsHandle(x: number, y: number): boolean;
  /** Overlays neu aus store.list() zeichnen (nach Änderungen von außen: Deep-Link, Messliste). */
  sync(): void;
  /** Offene Zeichnung (E2E-Lesezugriff). */
  pendingPoints(): Vec3[];
  /** Die laufende Zeichnung mit ihrer Messart (Weltkoordinaten); ohne Messwerkzeug oder Punkte: leer. */
  offeneZeichnung(): { kind: MeasureKind | null; punkte: readonly Vec3[] };
  /** Punkt an Fensterkoordinaten setzen wie ein Klick dort (Fadenkreuz im Begehen): Eckpunkt-Fang,
   *  „erster Eckpunkt schließt die Fläche“, Sichtbarkeitsregel und Rückfrage, Klickreihenfolge. Startet
   *  sofort; das Versprechen löst nach dem Anwenden auf. `vorher`: die Rückfrage erst danach. */
  setzeAmBildschirm(x: number, y: number, o?: { snapEdges?: boolean; vorher?: Promise<void>; keinPunkt?: (text: string) => void }): Promise<SetzStatus>;
  /** Anzahl abgeschlossener Pick-Auswertungen und Diagnose der letzten (E2E). */
  readonly picks: { count: number; last: PickInfo | null };
  /** E2E-Haken: erzwingt ein Panorama-Zeitbudget (z. B. 1 ms → 'imprecise' auslösbar); null = Standard. */
  readonly debug: { panoBudgetMs: number | null };
}

export function attachMeasureTool(o: MeasureToolOptions): MeasureTool {
  const { v, store, onChange, source, inPanorama, setLookLocked, onDrawingChange, poiMode, snapEdges, hiddenCloudMessage } = o;
  const { instance } = v;
  const group = new Group();
  group.name = 'scanview-measurements';
  instance.threeObjects.add(group);
  // Offene Zeichnung: Marken direkt in group (die gelbe Vormarke zählt reference.spec als Kind von
  // 'scanview-measurements', Inventar 1). Geisterpunkt beim Ziehen in einer eigenen Gruppe.
  let previewObjs: Object3D[] = [];
  const ghost = new Group();
  ghost.name = 'scanview-measure-ghost';
  instance.threeObjects.add(ghost);

  let kind: MeasureKind | null = null;
  /** Offene Eckpunkte (Polylinie/Fläche bis finish(); Strecke/Höhe bis zum 2. Punkt). */
  let drawing: Vec3[] = [];
  let drawingSources: (PointSource|null)[] = [];
  /** Erhöht bei Werkzeugwechsel/Verwerfen: noch laufende Picks der alten Zeichnung verfallen. */
  let drawGen = 0;
  // ref = die Messung (unveränderlich, jede Änderung ersetzt sie) zum Zeitpunkt der Eckpunkt-Auswahl:
  // hat sie sich inzwischen geändert (Rückgängig, Löschen, Ziehen), zeigt der Index womöglich auf einen
  // anderen Eckpunkt → Auswahl verfällt statt den falschen zu löschen.
  let selection: (MeasureSelection & { ref?: Measurement }) | null = null;
  let down: { x: number; y: number } | null = null;
  let suppressNextClick = false;
  // ref wie bei selection: der Drop gilt nur, wenn die Messung seit dem Druck unverändert ist.
  // ignore = zweiter Druck eines Doppelklicks: sperrt Kamera/Blick wie ein Griff-Druck; ohne Ziehen bewirkt er
  // nichts (ein Klick zählt nicht), wird er gezogen, ist es ein gewöhnliches Ziehen (Auswählen, dann sofort ziehen).
  let grab: { handle: Handle; ref: Measurement; x0: number; y0: number; dragging: boolean; ignore: boolean } | null = null;
  let lastHandleDown: { t: number; x: number; y: number } | null = null;
  let orbitWasEnabled = true;
  const picks: { count: number; last: PickInfo | null } = { count: 0, last: null };
  const debug: { panoBudgetMs: number | null } = { panoBudgetMs: null };

  const redraw = (): void => {
    // Siehe Kommentarblock oben: frisch hinzugefügte Kinder brauchen ihre matrixWorld sofort,
    // bevor der nächste (evtl. EDL-)Redraw die Szene traversiert.
    group.updateMatrixWorld(true);
    ghost.updateMatrixWorld(true);
    instance.notifyChange(group);
  };

  const dispose = (g: Group, child: Object3D): void => {
    g.remove(child);
    if ((child instanceof Line || child instanceof Mesh) && !(child instanceof ConstantSizeSphere)) child.geometry.dispose();
    if (child instanceof CSS2DObject) child.element.remove();
  };
  const clear = (g: Group, keep: readonly Object3D[] = []): void => {
    for (const child of [...g.children]) if (!keep.includes(child)) dispose(g, child);
  };

  let vorschlagBeimZeichnen: Measurement['vorschlag'];
  const addLabel = (g: Group, text: string, at: Vec3, zusatz = ''): void => {
    // Handy: Zusätze nur an der ausgewählten Messung; sonst nur ein Zeichen, dass es welche gibt (volle Angaben in
    // der Messliste). Am Desktop immer als zweite Zeile.
    const voll = !istTouch() || zusatzVoll;
    const label = makeLabel(voll ? text : `${text}${zusatz ? (zusatz.includes('⚠') ? ' ⚠' : ' ⓘ') : ''}`, voll ? zusatz : '');
    if (vorschlagBeimZeichnen) { label.element.classList.add('sv-vorschlag'); if (vorschlagBeimZeichnen.loeschen) label.element.classList.add('sv-loeschvorschlag'); }
    label.position.set(at[0], at[1], at[2]);
    g.add(label);
  };

  const addMarker = (g: Group, at: Vec3, mat: MeshMatcapMaterial, groesse: KugelGroesse = KUGEL_ECKPUNKT): void => {
    const marker = makeMarker(mat, groesse);
    marker.position.set(at[0], at[1], at[2]);
    g.add(marker);
  };

  /** Mittelpunkt-Griffe: [Einfügeindex, Position]; Fläche einschließlich der schließenden Kante. */
  const midpoints = (m: Measurement): [number, Vec3][] => {
    if (m.kind !== 'polyline' && m.kind !== 'area') return [];
    const pts = m.points;
    const n = m.kind === 'area' ? pts.length : pts.length - 1;
    return Array.from({ length: n }, (_, i): [number, Vec3] => [i + 1, mid(pts[i]!, pts[(i + 1) % pts.length]!)]);
  };

  /** Eckpunkte, auf die ein gezogener Griff nicht einrasten darf: er selbst und seine Nachbarn. */
  const neighbourIndices = (m: Measurement, h: Handle): number[] => {
    const n = m.points.length;
    const closed = m.kind === 'area';
    const around = (i: number): number[] => [i - 1, i + 1].filter((j) => closed || (j >= 0 && j < n)).map((j) => (j + n) % n);
    return h.kind === 'vertex' ? [h.index, ...around(h.index)] : [h.index - 1, h.index % n];
  };

  const vorschlagMat = kugelMaterial(0x33d1ff,0.5);
  let zusatzVoll = false;
  const drawMeasurement = (m: Measurement): void => {
    if (m.vorschlag && o.vorschlaegeSichtbar?.() === false) return;
    vorschlagBeimZeichnen = m.vorschlag;
    const notes = measurementNotes(m,o.sourceLayers ?? []);
    const hint = [...(m.vorschlag ? [vorschlagText(m.vorschlag)] : []), ...notes].join(' · ');
    const sel = selection?.id === m.id ? selection : null;
    zusatzVoll = sel !== null;
    const wholeSel = sel !== null && sel.index === undefined;
    const lineMat = wholeSel ? MAT.lineSel : MAT.line;
    const pts = m.points;
    switch (m.kind) {
      case 'distance':
        group.add(localLine(pts, lineMat));
        addLabel(group, formatM(m.distanceM), mid(m.a, m.b), hint);
        break;
      case 'polyline':
        group.add(localLine(pts, lineMat));
        m.segmentsM.forEach((d, i) => addLabel(group, formatM(d), mid(pts[i]!, pts[i + 1]!)));
        addLabel(group, `Σ ${formatM(m.lengthM)}`, pts[pts.length - 1]!, hint);
        break;
      case 'area': {
        const fill = areaFill(pts, wholeSel ? MAT.fillSel : MAT.fill);
        if (fill) group.add(fill);
        group.add(localLine(pts, lineMat, true));
        const warn = m.nonPlanar ? `⚠ nicht eben (max ${(m.planarityM * 100).toFixed(1).replace('.', ',')} cm)` : '';
        addLabel(group, `${num(m.areaM2)} m² · U ${formatM(m.perimeterM)}`, m.centroid, [warn, hint].filter(Boolean).join(' · '));
        break;
      }
      case 'height': {
        // Rechtwinkliges Dreieck: Lot vom tieferen Punkt bis auf die Höhe des höheren, von dort waagerecht
        // zum höheren Punkt; die Schräge gestrichelt.
        const [a, b] = [pts[0]!, pts[1]!];
        const [lo, hi] = a[2] <= b[2] ? [a, b] : [b, a];
        const corner: Vec3 = [lo[0], lo[1], hi[2]];
        group.add(localLine([lo, corner, hi], lineMat));
        group.add(localLine([lo, hi], MAT.dashed));
        const sign = m.dzM > 0 ? '+' : '';
        addLabel(group, `Δz ${sign}${formatM(m.dzM)}`, mid(lo, hi), [`horiz. ${formatM(m.horizontalM)} · schräg ${formatM(m.slantM)}`, hint].filter(Boolean).join(' · '));
        break;
      }
    }
    pts.forEach((p, i) => addMarker(group, p, m.vorschlag ? vorschlagMat : sel && (wholeSel || sel.index === i) ? MAT.vertexSel : MAT.vertex));
    for (const [, p] of (m.vorschlag ? [] : midpoints(m))) addMarker(group, p, MAT.midpoint, KUGEL_MITTELPUNKT);
  };

  const drawPreview = (): void => {
    vorschlagBeimZeichnen = undefined;
    for (const obj of previewObjs) dispose(group, obj);
    const before = new Set(group.children);
    if (drawing.length > 1) group.add(localLine(drawing, MAT.preview));
    for (const p of drawing) addMarker(group, p, MAT.pending);
    previewObjs = group.children.filter((x) => !before.has(x));
  };

  const sync = (): void => {
    if (selection) {
      const m = store.get(selection.id);
      if (!m || (selection.index !== undefined && selection.ref !== m)) selection = null;
    }
    clear(group, previewObjs);
    for (const m of store.list()) drawMeasurement(m);
    drawPreview();
    redraw();
  };

  /** Jede Speicheränderung läuft hierüber: Overlays, gedrosselter Hash (m=/g=/o=), Messliste. */
  const changed = (): void => {
    sync();
    onChange();
  };

  const setDrawing = (pts: Vec3[]): void => {
    const was = drawing.length > 0;
    drawing = pts;
    drawingSources = drawingSources.slice(0,pts.length);
    if (was !== drawing.length > 0) onDrawingChange(drawing.length > 0);
  };

  // --- Punkte holen -----------------------------------------------------------------------------
  // Einziger Weg zu einem Eckpunkt (Zeichnen, Ziehen, Einfügen): dieselbe Strahlsuche,
  // im Panorama mit denselben Panorama-Optionen. Ergebnisse in Klickreihenfolge (Warteschlange); das
  // Pick-Rendering läuft trotzdem sofort beim Ereignis (Kamerastand).
  let queue: Promise<void> = Promise.resolve();
  const pickAt = (ev: MouseEvent): Promise<RayPickResult> =>
    source.pickAtEvent(ev, { ...(inPanorama() ? panoPickOptions(debug.panoBudgetMs ?? undefined) : {}), snapEdges: snapEdges?.() ?? false });
  /** Eckpunkt-Fang als Pick-Ergebnis: genau die Koordinaten des Eckpunkts, ohne Strahlsuche. */
  const vertexPick = (p: Vec3, origin?: PointSource|null): Promise<RayPickResult> =>
    Promise.resolve({
      point: p,
      info: { level: 'fine', ms: 0, wideCandidates: 0, nearCandidates: 0, ...(origin ?? {}), snap: { result: 'vertex', original: true } },
    });
  /** Klick (bzw. Fadenkreuz) → Punkt über zielpunktSetzen (Sichtbarkeitsregel, Rückfrage). `abgebrochen` =
   *  Rückfrage ohne Wahl geschlossen, `ansicht` = Kamera hat sich vor der zweiten Suche bewegt, `verdeckt` =
   *  der Treffer liegt hinter der sichtbaren Fläche. Mit Ereignis läuft die erste Suche über pickAtEvent;
   *  die Rückfrage erst nach den Punkten davor. Die Kamerapose wird hier, bei der Eingabe, gemerkt. */
  type ZielErgebnis = RayPickResult & { abgebrochen?: boolean; ansicht?: boolean; verdeckt?: boolean };
  const zielAt = (x: number, y: number, ev: MouseEvent | null, snap: boolean, vorher2?: Promise<void>): Promise<ZielErgebnis> => {
    const frage = o.frage;
    const panorama = inPanorama();
    const opts = { ...(panorama ? panoPickOptions(debug.panoBudgetMs ?? undefined) : {}), snapEdges: snap };
    if (!frage) return ev ? source.pickAtEvent(ev, opts) : source.pickAtScreen(x, y, opts);
    const vorher = vorher2 ? Promise.all([queue, vorher2]) : queue;
    const pose0 = o.pose?.();
    return zielpunktSetzen({
      quelle: {
        pickAtScreen: (px, py, op) => (op?.tiefenFenster || !ev ? source.pickAtScreen(px, py, op) : source.pickAtEvent(ev, op)),
        vorschauAtScreen: (px, py) => source.vorschauAtScreen(px, py),
      },
      x,
      y,
      opts,
      sichtbarkeitPruefen: !panorama,
      frage: (b) => vorher.then(() => frage(b, { x, y })),
      ansichtUnveraendert: () => {
        const jetzt = o.pose?.();
        return !pose0 || !jetzt || jetzt.every((w, i) => Math.abs(w - pose0[i]!) <= 1e-9);
      },
    }).then((e): ZielErgebnis => {
      if (e.art === 'gesetzt') return { point: e.punkt, info: e.info };
      if (e.art === 'abgebrochen') return { point: null, info: e.info, abgebrochen: true, ansicht: e.grund === 'ansicht' };
      return { point: null, info: e.info, verdeckt: e.grund === 'verdeckt' };
    });
  };
  const enqueue = (pending: Promise<ZielErgebnis>, apply: (p: Vec3 | null, info: PickInfo, e: ZielErgebnis) => void): void => {
    queue = queue
      .then(() => pending)
      .then((e) => {
        const { point, info } = e;
        picks.count++;
        picks.last = info;
        if (point && info.level === 'coarse') {
          say(
            info.reason === 'empty'
              ? 'Messpunkt aus grober Stufe (kein Originalpunkt direkt am Cursor)'
              : 'Messpunkt aus grober Stufe (feinste Stufe nicht rechtzeitig geladen)',
          );
        }
        apply(point ? [point[0], point[1], point[2]] : null, info, e);
      })
      .catch((e: unknown) => {
        console.error('Messpunkt-Auswertung fehlgeschlagen:', e);
        picks.count++;
        picks.last = null;
        say('Messpunkt konnte nicht ausgewertet werden');
      });
  };

  const addPoint = (p: Vec3, info: PickInfo): void => {
    if (!kind) return;
    const pts = [...drawing, p];
    const sources = [...drawingSources,sourceFromPick(info)];
    if ((kind === 'distance' || kind === 'height') && pts.length === 2) {
      setDrawing([]);
      try {
        store.addGeometry(kind,pts,sources);
      } catch (e) {
        say(e instanceof Error ? e.message : String(e));
      }
      changed();
      return;
    }
    setDrawing(pts);
    if (kind === 'area' && pts.length === 3) say('Schließen: auf den ersten Eckpunkt tippen oder ✓ (Enter)');
    drawingSources = sources;
    drawPreview();
    redraw();
  };

  // --- Klick = Punkt setzen -------------------------------------------------------------------
  // 'click' statt 'pointerup': feuert nur bei echtem Klick, aber auch nach einem Kamera-Drag
  // (MapControls) — deshalb zusätzlich die Bewegungsdistanz seit pointerdown prüfen. ev.detail > 1
  // filtert den zweiten Klick eines Doppelklicks heraus (Drehpunkt-Picking in viewer.ts).
  instance.domElement.addEventListener('pointerdown', (ev) => {
    down = { x: ev.clientX, y: ev.clientY };
  });
  instance.domElement.addEventListener('click', (ev) => {
    if (o.unterPointerLock?.()) {
      down = null;
      return;
    }
    if (suppressNextClick) {
      suppressNextClick = false;
      down = null;
      return;
    }
    if (!kind || !down) return;
    const moved = Math.hypot(ev.clientX - down.x, ev.clientY - down.y);
    down = null;
    if (moved > CLICK_MOVE_TOLERANCE_PX || ev.detail > 1) return;
    if (o.rueckfrageOffen?.()) return say('Erst die Rückfrage beantworten');
    void setzeAn(ev.clientX, ev.clientY, ev, snapEdges?.() ?? false);
  });

  /** Gemeinsamer Weg für Klick und Fadenkreuz: Eckpunkt-Fang, sonst Strahlsuche; Anwenden in Reihenfolge. */
  // `keinPunkt`: wohin der Hinweis ohne Punkt geht (Fadenkreuz: auch in die Ansage), sonst die Meldungszeile.
  const setzeAn = (x: number, y: number, ev: MouseEvent | null, snap: boolean, vorher?: Promise<void>, keinPunkt: (t: string) => void = say): Promise<SetzStatus> =>
    new Promise<SetzStatus>((fertig) => {
      const gen = drawGen;
      // Eckpunkt-Fang (immer an): ≤ HANDLE_HIT_PX (bzw. sichtbarer Kugelradius, falls größer) neben einem Eckpunkt einer Messung oder der
      // offenen Zeichnung → genau dessen Koordinaten. Nie auf den zuletzt gesetzten Punkt (Nullkante).
      // Fläche schließen: großzügiger Fang auf den ersten Eckpunkt (auf Touch-Geräten 36 px), vor dem Eckpunkt-Fang.
      const schliessen = kind === 'area' && drawing.length >= 3 &&
        schliesstFlaeche(toScreen(drawing[0]!, instance.domElement.getBoundingClientRect()), x, y, drawing.length, istTouch());
      const target = schliessen ? { p: drawing[0]!, openIndex: 0, source: drawingSources[0] ?? null }
        : drawing.length > 0 ? vertexAt(x, y, { open: drawing.length - 1 }) : null;
      let status: SetzStatus = 'verworfen';
      enqueue(target ? vertexPick(target.p,target.source) : zielAt(x, y, ev, snap, vorher), (p, info, e) => {
        if (gen !== drawGen || !kind) return;
        if (e.ansicht) {
          status = 'ansicht';
          return say('abgebrochen – Ansicht hat sich bewegt');
        }
        if (e.abgebrochen) {
          status = 'abgebrochen';
          return say('abgebrochen – kein Messpunkt gesetzt');
        }
        status = 'kein-treffer';
        if (e.verdeckt || !p) return keinPunkt(noPointMessage(info, hiddenCloudMessage, 'kein Messpunkt gesetzt', e.verdeckt));
        // Erster Eckpunkt einer offenen Fläche schließt sie — entschieden in Klickreihenfolge, nach den noch
        // laufenden Picks davor (dann zählt auch ein dritter Punkt, der erst jetzt angekommen ist).
        if (target?.openIndex === 0 && kind === 'area') {
          if (drawing.length >= 3) {
            status = 'geschlossen';
            return finishNow();
          }
          status = 'verworfen';
          return say('Zum Schließen mindestens drei Eckpunkte setzen');
        }
        status = 'gesetzt';
        addPoint(p,info);
      });
      // Nach dem Anwenden (die Warteschlange fängt Fehler selbst ab).
      void queue.then(() => fertig(status));
    });

  // --- Griffe -----------------------------------------------------------------------------------
  const camera = instance.view.camera;
  const toScreen = (p: Vec3, rect: DOMRect): { x: number; y: number } | null => {
    const w = vec(p);
    // Hinter der Kamera → kein Griff.
    if (w.clone().applyMatrix4(camera.matrixWorldInverse).z >= 0) return null;
    w.project(camera);
    return { x: rect.left + ((w.x + 1) / 2) * rect.width, y: rect.top + ((1 - w.y) / 2) * rect.height };
  };
  /** Sichtbarer Radius des Kugelmarkers an p [px] — derselbe Wert, den die Kugel beim Zeichnen bestimmt. */
  const markerPx = (p: Vec3, rect: DOMRect, groesse: KugelGroesse): number => kugelRadius(kugelKamera(camera, vec(p), rect.height), groesse).px;
  const screenHandles = (): Handle[] => {
    const rect = instance.domElement.getBoundingClientRect();
    const out: Handle[] = [];
    for (const m of store.list().filter(m => !m.vorschlag)) {
      m.points.forEach((p, index) => {
        const s = toScreen(p, rect);
        if (s) out.push({ id: m.id, index, kind: 'vertex', ...s, r: markerPx(p, rect, KUGEL_ECKPUNKT) });
      });
      for (const [index, p] of midpoints(m)) {
        const s = toScreen(p, rect);
        if (s) out.push({ id: m.id, index, kind: 'midpoint', ...s, r: markerPx(p, rect, KUGEL_MITTELPUNKT) });
      }
    }
    return out;
  };

  /**
   * Eckpunkt unter (x, y) für den Eckpunkt-Fang: Eckpunkte aller Messungen und der offenen Zeichnung.
   * ``open`` = Index der offenen Zeichnung, der nicht zählt (zuletzt gesetzter Punkt); ``skip`` = Eckpunkte
   * einer Messung, die nicht zählen (beim Ziehen: der gezogene Punkt und seine Nachbarn).
   */
  const vertexAt = (
    x: number,
    y: number,
    ex: { open?: number; skip?: { id: string; indices: number[] } } = {},
  ): { p: Vec3; openIndex?: number; source?: PointSource|null } | null => {
    const rect = instance.domElement.getBoundingClientRect();
    const hs = screenHandles().filter((h) => h.kind === 'vertex' && sourceReusable(store.get(h.id)?.pointSources?.[h.index],inPanorama()) && !(ex.skip && h.id === ex.skip.id && ex.skip.indices.includes(h.index)));
    drawing.forEach((p, index) => {
      if (index === ex.open || !sourceReusable(drawingSources[index],inPanorama())) return;
      const sc = toScreen(p, rect);
      if (sc) hs.push({ id: OPEN_ID, index, kind: 'vertex', ...sc, r: markerPx(p, rect, KUGEL_ECKPUNKT) });
    });
    const h = nearestHandle(hs, x, y);
    if (!h) return null;
    if (h.id === OPEN_ID) return { p: drawing[h.index]!, openIndex: h.index, source:drawingSources[h.index] ?? null };
    const m = store.get(h.id);
    return m ? { p: m.points[h.index]!, source:m.pointSources?.[h.index] ?? null } : null;
  };

  const lock = (on: boolean): void => {
    if (on) {
      orbitWasEnabled = v.controls.enabled;
      v.controls.enabled = false;
    } else v.controls.enabled = orbitWasEnabled;
    setLookLocked(on);
  };

  const clearGhost = (): void => {
    clear(ghost);
    redraw();
  };

  // Vorschau beim Ziehen: Geisterpunkt unter dem Zeiger in der Tiefe des alten Punkts, gestrichelte
  // Nachbarkanten, Label „…“ — ausdrücklich kein Messwert (keine Strahlsuche während der Bewegung).
  const drawGhost = (h: Handle, x: number, y: number): void => {
    const m = store.get(h.id);
    if (!m) return;
    const pts = m.points;
    const closed = m.kind === 'area';
    const n = pts.length;
    const anchor = h.kind === 'vertex' ? pts[h.index]! : mid(pts[h.index - 1]!, pts[h.index % n]!);
    const rect = instance.domElement.getBoundingClientRect();
    const ndc = vec(anchor).project(camera);
    const g = new Vector3(((x - rect.left) / rect.width) * 2 - 1, 1 - ((y - rect.top) / rect.height) * 2, ndc.z).unproject(camera);
    const gp: Vec3 = [g.x, g.y, g.z];
    const neighbours: Vec3[] = [];
    if (h.kind === 'vertex') {
      if (h.index > 0 || closed) neighbours.push(pts[(h.index - 1 + n) % n]!);
      if (h.index < n - 1 || closed) neighbours.push(pts[(h.index + 1) % n]!);
    } else neighbours.push(pts[h.index - 1]!, pts[h.index % n]!);
    clear(ghost);
    for (const q of neighbours) ghost.add(localLine([q, gp], MAT.preview));
    addMarker(ghost, gp, MAT.ghost);
    addLabel(ghost, '…', gp);
    redraw();
  };

  // Ziehen eines Griffs. Der Listener hängt in der Capture-Phase an window, damit er vor MapControls
  // (pointerdown am Canvas) und vor dem Panorama-Umsehen (pointerdown am Canvas) läuft: steht er auf einem
  // Griff, sperrt er beide, bevor sie das Ereignis sehen. Erst > CLICK_MOVE_TOLERANCE_PX Bewegung macht aus
  // dem Druck ein Ziehen; darunter ist es ein Klick auf den Griff (= Auswahl; Mittelpunkt: Einfügen am Klick).
  window.addEventListener(
    'pointerdown',
    (ev) => {
      // Ein neuer Druck beendet eine liegen gebliebene Klick-Sperre (pointerup außerhalb des Canvas).
      suppressNextClick = false;
      // instance.domElement ist in Giro3D 2.0.4 der Canvas des Renderers (core/Instance.js: get domElement →
      // renderer.domElement); CSS2D-Labels haben pointer-events:none, Ziel ist also der Canvas selbst.
      // Im POI-Modus sind Griffe nicht greifbar (sonst griffe derselbe Druck den Eckpunkt und legte einen POI an).
      if (ev.button !== 0 || ev.target !== instance.domElement || drawing.length > 0 || poiMode()) return;
      const handle = nearestHandle(screenHandles(), ev.clientX, ev.clientY);
      const ref = handle ? store.get(handle.id) : undefined;
      if (!handle || !ref) return;
      const t = performance.now();
      const prev = lastHandleDown;
      const ignore =
        prev !== null && t - prev.t < DOUBLE_CLICK_MS && Math.hypot(ev.clientX - prev.x, ev.clientY - prev.y) <= CLICK_MOVE_TOLERANCE_PX;
      lastHandleDown = ignore ? null : { t, x: ev.clientX, y: ev.clientY };
      grab = { handle, ref, x0: ev.clientX, y0: ev.clientY, dragging: false, ignore };
      lock(true);
    },
    true,
  );
  window.addEventListener('pointermove', (ev) => {
    if (!grab) return;
    if (!grab.dragging && Math.hypot(ev.clientX - grab.x0, ev.clientY - grab.y0) > CLICK_MOVE_TOLERANCE_PX) grab.dragging = true;
    if (grab.dragging) drawGhost(grab.handle, ev.clientX, ev.clientY);
  });
  window.addEventListener('pointerup', (ev) => {
    const g = grab;
    if (!g) return;
    grab = null;
    clearGhost();
    lock(false);
    // Nach dem Griff-Druck feuert der Browser noch 'click' — der darf keinen neuen Messpunkt setzen.
    suppressNextClick = true;
    if (g.ignore && !g.dragging) return;
    const stale = (): boolean => store.get(g.handle.id) !== g.ref;
    if (!g.dragging && g.handle.kind === 'vertex') {
      if (stale()) return say('Messung inzwischen geändert – keine Auswahl');
      return select({ id: g.handle.id, index: g.handle.index });
    }
    // Losgelassen (oder Klick auf einen Mittelpunkt-Griff): Punkt über dieselbe Strahlsuche wie ein Klick —
    // oder Eckpunkt-Fang, aber nie auf den gezogenen Punkt selbst (er rastete auf seine alte Lage ein) und
    // nie auf einen Nachbarn derselben Messung (doppelter Eckpunkt).
    const target = g.dragging || g.handle.kind === 'midpoint' ? vertexAt(ev.clientX, ev.clientY, { skip: { id: g.handle.id, indices: neighbourIndices(g.ref, g.handle) } }) : null;
    enqueue(target ? vertexPick(target.p,target.source) : pickAt(ev), (p, info) => {
      if (!p) return say(noPointMessage(info, hiddenCloudMessage, 'Eckpunkt bleibt'));
      // Rückgängig/Löschen während des Ziehens oder des laufenden Drop-Picks: Index wäre veraltet.
      if (stale()) return say('Messung inzwischen geändert – Eckpunkt bleibt');
      try {
        if (g.handle.kind === 'vertex') store.moveVertex(g.handle.id, g.handle.index, p, sourceFromPick(info));
        else store.insertVertex(g.handle.id, g.handle.index, p, sourceFromPick(info));
      } catch (e) {
        return say(`${e instanceof Error ? e.message : String(e)} – Eckpunkt bleibt`);
      }
      // Auswahl auf die neue (ersetzte) Messung nachführen — sonst verwirft sync() sie als veraltet (ref).
      if (g.handle.kind === 'midpoint') selection = { id: g.handle.id, index: g.handle.index, ref: store.get(g.handle.id) };
      else if (selection?.id === g.handle.id && selection.index === g.handle.index) selection = { ...selection, ref: store.get(g.handle.id) };
      changed();
    });
  });

  // --- Befehle ----------------------------------------------------------------------------------
  const select = (sel: MeasureSelection | null): void => {
    selection = sel ? { ...sel, ref: sel.index === undefined ? undefined : store.get(sel.id) } : null;
    sync();
  };

  /** Befehle, die die offene Zeichnung betreffen, laufen hinter den noch ausstehenden Picks (Klickreihenfolge):
   *  Enter direkt nach dem letzten Klick schließt mit diesem Punkt ab, Strg+Z nimmt ihn. gen = Stand beim
   *  Tastendruck — ist die Zeichnung inzwischen verworfen (Werkzeugwechsel, Esc), verfällt der Befehl. */
  const afterPicks = (run: () => void): void => {
    const gen = drawGen;
    queue = queue
      .then(() => {
        if (gen === drawGen) run();
      })
      .catch((e: unknown) => console.error('Messbefehl fehlgeschlagen:', e));
  };

  /** Verwirft die offene Zeichnung und — immer, auch ohne Punkte — alle noch laufenden Picks dafür. */
  const discardDrawing = (): boolean => {
    drawGen++;
    if (!drawing.length) return false;
    setDrawing([]);
    drawPreview();
    redraw();
    return true;
  };

  const finishNow = (): void => {
    if (kind !== 'polyline' && kind !== 'area') return;
    try {
      store.addGeometry(kind, drawing,drawingSources);
    } catch (e) {
      say(e instanceof MeasureError ? e.message : String(e));
      return;
    }
    setDrawing([]);
    changed();
  };

  const undoNow = (): void => {
    if (drawing.length) {
      setDrawing(drawing.slice(0, -1));
      drawPreview();
      redraw();
      return;
    }
    if (store.undo()) changed();
    else say('Nichts rückgängig zu machen');
  };

  const deleteSelection = (): void => {
    const sel = selection;
    if (!sel) return;
    if (sel.index !== undefined && store.get(sel.id) !== sel.ref) {
      select(null);
      say('Messung inzwischen geändert – Auswahl aufgehoben');
      return;
    }
    try {
      if (sel.index === undefined) store.remove(sel.id);
      else store.removeVertex(sel.id, sel.index);
    } catch (e) {
      say(`${e instanceof Error ? e.message : String(e)} – Eckpunkt bleibt`);
      return;
    }
    selection = null;
    changed();
  };

  const setKind = (k: MeasureKind | null): void => {
    if (k === kind) return;
    // discardDrawing erhöht drawGen immer: ein noch laufender Pick des alten Werkzeugs (z. B. der erste
    // Streckenklick) darf nicht erster Punkt des neuen werden.
    if (discardDrawing()) say('Offene Messung verworfen');
    kind = k;
    // Hierarchie vorwärmen, damit der erste Klick nicht den Baumaufbau ins Zeitbudget zahlt.
    if (k) source.warm();
  };

  sync();
  return {
    setKind,
    kind: () => kind,
    isEnabled: () => kind !== null,
    isDrawing: () => drawing.length > 0,
    isDragging: () => grab !== null,
    hasSelection: () => selection !== null,
    selection: () => (selection ? { id: selection.id, index: selection.index } : null),
    finish: () => afterPicks(finishNow),
    cancelDrawing: discardDrawing,
    cancelDrag: () => {
      if (!grab) return false;
      grab = null;
      clearGhost();
      lock(false);
      suppressNextClick = true;
      return true;
    },
    undo: () => afterPicks(undoNow),
    deleteSelection,
    select,
    hitsHandle: (x, y) => nearestHandle(screenHandles(), x, y) !== null,
    setzeAmBildschirm: (x, y, opt) => setzeAn(x, y, null, opt?.snapEdges ?? snapEdges?.() ?? false, opt?.vorher, opt?.keinPunkt),
    pendingPoints: () => [...drawing],
    offeneZeichnung: () => ({ kind, punkte: [...drawing] }),
    sync,
    picks,
    debug,
  };
}
