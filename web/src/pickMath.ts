// Reine Picking-Logik der Streckenmessung („Picking liefert Kandidaten, danach
// Nachladen der feinsten Stufe in der Umgebung und Wahl des nächstgelegenen Originalpunkts“).
// Ohne three.js/Giro3D, damit Vitest sie ohne Bundler laden kann (constraints.md); die Anbindung an
// Giro3D steht in measure.ts.
//
// Ablauf (siehe measure.ts):
//   1. Grobe Stufe: Giro3Ds Pick im weiten Kreis (16 px) liefert die sichtbaren Punkte der
//      Umgebung. Nach Tiefe entlang des Klickstrahls in Flächen getrennt (Lücken > surfaceGapM);
//      die vorderste Fläche der Umgebung legt fest, *ab wo* gesucht wird, der vorderste Kandidat im
//      Cursor-Kreis (4 px, pixelOffset), *bis wohin*.
//   2. Feinste Stufe (findSurfaceAlongRay): den Strahl in diesem Fenster von vorn abschnittsweise
//      absuchen. Je Abschnitt alle COPC-Knoten jeder Tiefe lesen, deren Volumen ihn schneiden
//      (collectNodes — COPC verteilt die Originalpunkte ohne Dopplung über alle Stufen, erst die
//      Vereinigung ergibt die volle Auflösung), Punkte im Kegel um den Strahl sammeln
//      (pointsNearRay), an der ersten abgeschlossenen Fläche mit ≥ minClusterSize Punkten anhalten
//      (Streupunkt davor = Rauschen) und dort den strahlnächsten Originalpunkt nehmen.
//   3. Ersatz bei Zeitbudget/Fehler: coarseFallback — nie ein Punkt, der weiter als der
//      Cursor-Radius neben dem Klick liegt.
export type Vec3 = readonly [number, number, number];

/** Klickstrahl; ``dir`` normiert. */
export interface Ray {
  origin: Vec3;
  dir: Vec3;
}

/** Achsenparallele Box (Weltkoordinaten). */
export interface Box {
  min: Vec3;
  max: Vec3;
}

export interface SurfaceOptions {
  /** Mindest-Tiefenlücke zwischen zwei Flächen [m]. Standard 0,05. */
  gapAbsM?: number;
  /** Tiefenlücke relativ zur Tiefe. Standard 0,02 (2 %). */
  gapRel?: number;
  /**
   * Streupunkt-Regel: eine Fläche mit weniger Punkten gilt als Rauschen, **sofern dahinter noch
   * eine Fläche mit genügend Punkten liegt**. Gibt es keine solche, gewinnt die vorderste Fläche
   * (keine Messung ohne Punkt — aber auch kein Verwerfen des einzigen Treffers).
   * Standard 1 (= nichts verwerfen): so arbeitet die grobe Stufe, deren Darstellung aus der Nähe
   * lückig ist — dort kann ein echter Wandtreffer allein im Pickradius stehen. Die feine Stufe nutzt
   * 2: in voller Auflösung hat eine echte Fläche im Suchzylinder mehrere Punkte.
   */
  minClusterSize?: number;
}

const GAP_ABS_M = 0.05;
const GAP_REL = 0.02;

export function depthAlong(ray: Ray, p: Vec3): number {
  const { origin: o, dir: d } = ray;
  return (p[0] - o[0]) * d[0] + (p[1] - o[1]) * d[1] + (p[2] - o[2]) * d[2];
}

/** Senkrechter Abstand des Punkts zur Strahlgeraden [m]. */
export function rayDistance(ray: Ray, p: Vec3): number {
  const { origin: o, dir: d } = ray;
  const vx = p[0] - o[0];
  const vy = p[1] - o[1];
  const vz = p[2] - o[2];
  const t = vx * d[0] + vy * d[1] + vz * d[2];
  return Math.sqrt(Math.max(0, vx * vx + vy * vy + vz * vz - t * t));
}

/** Tiefenlücke, ab der zwei Punkte zu verschiedenen Flächen gehören: max(5 cm, 2 % der Tiefe). */
export function surfaceGapM(depth: number, opts: SurfaceOptions = {}): number {
  return Math.max(opts.gapAbsM ?? GAP_ABS_M, (opts.gapRel ?? GAP_REL) * depth);
}

/**
 * Alle Flächen entlang des Strahls, nach Tiefe sortiert (jede Fläche ebenfalls nach Tiefe). Punkte
 * mit Tiefe ≤ 0 (hinter der Kamera) zählen nicht. Getrennt wird an Lücken zwischen
 * **aufeinanderfolgenden** Tiefen > surfaceGapM(vordere Tiefe) — eine schräge Fläche mit vielen
 * kleinen Schritten bleibt so eine Fläche, auch wenn ihre Gesamtspanne die Lücke übersteigt.
 */
export function surfaceClusters<T>(items: readonly T[], pos: (t: T) => Vec3, ray: Ray, opts: SurfaceOptions = {}): T[][] {
  const withDepth = items
    .map((item) => ({ item, depth: depthAlong(ray, pos(item)) }))
    .filter((e) => e.depth > 0)
    .sort((a, b) => a.depth - b.depth);
  if (withDepth.length === 0) return [];

  const clusters: T[][] = [];
  let current: T[] = [withDepth[0]!.item];
  for (let i = 1; i < withDepth.length; i++) {
    const prev = withDepth[i - 1]!.depth;
    const e = withDepth[i]!;
    if (e.depth - prev > surfaceGapM(prev, opts)) {
      clusters.push(current);
      current = [];
    }
    current.push(e.item);
  }
  clusters.push(current);
  return clusters;
}

/**
 * Elemente der vordersten Fläche (nach Tiefe sortiert); Streupunkt-Regel siehe
 * SurfaceOptions.minClusterSize.
 */
export function frontCluster<T>(items: readonly T[], pos: (t: T) => Vec3, ray: Ray, opts: SurfaceOptions = {}): T[] {
  const clusters = surfaceClusters(items, pos, ray, opts);
  if (clusters.length === 0) return [];
  const minSize = opts.minClusterSize ?? 1;
  return clusters.find((c) => c.length >= minSize) ?? clusters[0]!;
}

function nearestToRay<T>(items: readonly T[], pos: (t: T) => Vec3, ray: Ray): T | null {
  let best: T | null = null;
  let bestDist = Infinity;
  for (const item of items) {
    const d = rayDistance(ray, pos(item));
    if (d < bestDist) {
      bestDist = d;
      best = item;
    }
  }
  return best;
}

/** Vorderste Fläche, darin der Punkt mit kleinstem Strahlabstand; leere Liste → null. */
export function chooseFrontSurface<T>(items: readonly T[], pos: (t: T) => Vec3, ray: Ray, opts: SurfaceOptions = {}): T | null {
  return nearestToRay(frontCluster(items, pos, ray, opts), pos, ray);
}

/**
 * Grober Ersatztreffer (Zeitbudget/Fehler der feinsten Stufe): der strahlnächste Punkt der
 * vordersten Fläche der weiten Umgebung — aber nur, wenn er selbst im Cursor-Kreis liegt
 * (pixelOffset ≤ radiusPx). Sonst läge er bis zu 16 px daneben, womöglich auf einem anderen Objekt
 * (Türrahmen neben einer Öffnung); dann gilt die vorderste Fläche des Cursor-Kreises ``near``.
 * Nichts unter dem Cursor → null.
 */
export function coarseFallback<T>(
  wide: readonly T[],
  near: readonly T[],
  pos: (t: T) => Vec3,
  ray: Ray,
  k: number,
  radiusPx: number,
): T | null {
  if (near.length === 0) return null;
  const w = chooseFrontSurface(wide, pos, ray);
  if (w !== null && pixelOffset(ray, pos(w), k) <= radiusPx) return w;
  return chooseFrontSurface(near, pos, ray);
}

/**
 * Fehlerschranke des groben Ersatzes: er liegt höchstens ``radiusPx`` Pixel neben dem Klickstrahl,
 * das sind in seiner Tiefe radiusPx · k · Tiefe Meter. Im Panorama gilt jeder grobe
 * Ersatz nur, solange diese Schranke ≤ ``maxM`` bleibt — aus 1,5 m Orbit-Abstand sind das
 * ≈ 6 mm, aus 10 m Stationsabstand ≈ 40 mm.
 */
export function coarseWithinBound(ray: Ray, p: Vec3, k: number, radiusPx: number, maxM: number): boolean {
  return radiusPx * k * depthAlong(ray, p) <= maxM;
}

/** Warum die feinste Stufe keinen Punkt geliefert hat (PickInfo.reason in rayPick.ts). */
export type FineFailure = 'timeout' | 'error' | 'empty';

/**
 * Grober Ersatz unter der Fehlerschranke: ohne ``maxM`` (Orbit) unverändert; mit
 * ``maxM`` (Panorama) wird ein Ersatz über der Schranke verworfen — unabhängig davon, warum die
 * feinste Stufe versagt hat (``why``: Zeitbudget, Fehler, keine Originalpunkte am Strahl).
 */
export function boundedCoarse(
  coarse: Vec3 | null,
  why: FineFailure,
  ray: Ray,
  k: number,
  radiusPx: number,
  maxM?: number,
): { point: Vec3 | null; rejected: FineFailure | null } {
  if (!coarse || maxM === undefined || coarseWithinBound(ray, coarse, k, radiusPx, maxM)) return { point: coarse, rejected: null };
  return { point: null, rejected: why };
}

/**
 * Strahlabstand in Bildschirmpixeln; ``k`` = Pixelkante je Meter Tiefe (pixelFootprintM(1, …)).
 * Ersetzt Giro3Ds `coord` der Pick-Ergebnisse, das nicht zum gelesenen Pixel passt (measure.ts).
 */
export function pixelOffset(ray: Ray, p: Vec3, k: number): number {
  return rayDistance(ray, p) / (depthAlong(ray, p) * k);
}

/** Kantenlänge eines Bildschirmpixels [m] in der Tiefe ``depth`` (Perspektivkamera). */
export function pixelFootprintM(depth: number, vFovDeg: number, heightPx: number): number {
  return (2 * depth * Math.tan((vFovDeg * Math.PI) / 360)) / heightPx;
}

/**
 * Achsenparallele Box um das Strahlstück [depth − frontM, depth + backM], seitlich um radiusM
 * verdickt — der Suchbereich für die feinste Stufe.
 */
export function refineBox(ray: Ray, depth: number, frontM: number, backM: number, radiusM: number): Box {
  const { origin: o, dir: d } = ray;
  const at = (t: number): Vec3 => [o[0] + d[0] * t, o[1] + d[1] * t, o[2] + d[2] * t];
  const a = at(depth - frontM);
  const b = at(depth + backM);
  const min: Vec3 = [Math.min(a[0], b[0]) - radiusM, Math.min(a[1], b[1]) - radiusM, Math.min(a[2], b[2]) - radiusM];
  const max: Vec3 = [Math.max(a[0], b[0]) + radiusM, Math.max(a[1], b[1]) + radiusM, Math.max(a[2], b[2]) + radiusM];
  return { min, max };
}

/**
 * Tiefe, in der der Strahl die Box verlässt (Slab-Verfahren); verfehlt er sie oder liegt sie ganz
 * hinter dem Ursprung → 0.
 */
export function rayBoxExit(ray: Ray, box: Box): number {
  let tMin = -Infinity;
  let tMax = Infinity;
  for (let i = 0; i < 3; i++) {
    const o = ray.origin[i]!;
    const d = ray.dir[i]!;
    const lo = box.min[i]!;
    const hi = box.max[i]!;
    if (Math.abs(d) < 1e-12) {
      if (o < lo || o > hi) return 0;
      continue;
    }
    const t1 = (lo - o) / d;
    const t2 = (hi - o) / d;
    tMin = Math.max(tMin, Math.min(t1, t2));
    tMax = Math.min(tMax, Math.max(t1, t2));
  }
  return tMax >= tMin && tMax > 0 ? tMax : 0;
}

function intersects(a: Box, b: Box): boolean {
  for (let i = 0; i < 3; i++) {
    if (a.max[i]! < b.min[i]! || a.min[i]! > b.max[i]!) return false;
  }
  return true;
}

/**
 * Alle Octree-Knoten, deren Volumen die Box schneidet — Vorfahren **und** Nachfahren, denn COPC
 * legt jeden Originalpunkt genau einmal in irgendeiner Stufe ab. Teilbäume außerhalb der Box werden
 * nicht betreten.
 */
export function collectNodes<N>(
  root: N,
  box: Box,
  volume: (n: N) => Box,
  children: (n: N) => readonly (N | undefined)[] | undefined,
): N[] {
  const out: N[] = [];
  const stack: N[] = [root];
  while (stack.length > 0) {
    const n = stack.pop()!;
    if (!intersects(volume(n), box)) continue;
    out.push(n);
    for (const c of children(n) ?? []) if (c !== undefined) stack.push(c);
  }
  return out;
}

/** Punktpuffer eines Knotens: lokale Koordinaten (xyz hintereinander) relativ zu ``origin``. */
export interface PointChunk {
  origin: Vec3;
  positions: ArrayLike<number>;
  /** PointSourceId je Punkt (Scanindex + 1) — nur dekodiert, wenn der Scanfilter aktiv ist. */
  ids?: ArrayLike<number>;
}

/**
 * Weltpunkte (Float64: origin + lokal) im Zylinder um den Strahl (Radius ``radiusM``) und im
 * Tiefenfenster [minDepth, maxDepth); ``accept`` z. B. für die Schnittbox. Mit ``sourceId``
 * (Scanfilter im Panorama) zählen nur Punkte dieser PointSourceId — ein Knoten ohne
 * dekodierte IDs trägt dann nichts bei (nie ein ungeprüfter, womöglich fremder Punkt).
 */
export function pointsNearRay(
  chunks: readonly PointChunk[],
  ray: Ray,
  radiusM: number,
  minDepth: number,
  maxDepth: number,
  accept?: (p: Vec3) => boolean,
  sourceId?: number,
): Vec3[] {
  const { origin: o, dir: d } = ray;
  const r2 = radiusM * radiusM;
  const out: Vec3[] = [];
  for (const { origin, positions, ids } of chunks) {
    if (sourceId !== undefined && !ids) continue;
    for (let i = 0; i + 2 < positions.length; i += 3) {
      if (sourceId !== undefined && (ids === undefined || ids[i / 3] !== sourceId)) continue;
      const x = origin[0] + positions[i]!;
      const y = origin[1] + positions[i + 1]!;
      const z = origin[2] + positions[i + 2]!;
      const vx = x - o[0];
      const vy = y - o[1];
      const vz = z - o[2];
      const t = vx * d[0] + vy * d[1] + vz * d[2];
      // Halboffen [min, max): aneinandergrenzende Suchabschnitte zählen keinen Punkt doppelt.
      if (t < minDepth || t >= maxDepth) continue;
      if (vx * vx + vy * vy + vz * vz - t * t > r2) continue;
      const p: Vec3 = [x, y, z];
      if (accept && !accept(p)) continue;
      out.push(p);
    }
  }
  return out;
}

export interface AlongRayOptions extends SurfaceOptions {
  /** Abschnittslänge ab Tiefe d [m]. Standard 4 × surfaceGapM(d). */
  segmentM?: (depth: number) => number;
  /** Vor jedem Abschnitt gefragt (Zeitbudget); true → abbrechen. */
  shouldStop?: () => boolean;
}

export interface AlongRayResult {
  point: Vec3 | null;
  /** Gelesene Abschnitte. */
  segments: number;
  /** Punkte im Suchzylinder insgesamt. */
  points: number;
  /** Abbruch durch shouldStop (Zeitbudget) — dann point = null. */
  stopped: boolean;
}

/**
 * Feinste Stufe: den Strahl von vorn nach hinten in Abschnitten absuchen und an der ersten echten
 * Fläche anhalten. ``load(a, b)`` liefert die Originalpunkte im Suchzylinder mit Tiefe in [a, b).
 * Angehalten wird, sobald die vorderste Fläche mit ≥ minClusterSize Punkten **abgeschlossen** ist
 * (dahinter liegt schon eine weitere Fläche oder der Abschnitt reicht eine Flächenlücke über sie
 * hinaus) — so wird eine Fläche über einer Abschnittsgrenze vollständig gelesen, ein Streupunkt
 * davor übersprungen, und ein echtes Loch führt bis zum Hintergrund. Ist das Fenster erschöpft,
 * gilt chooseFrontSurface (Einzelpunkt bleibt Treffer, nichts → null).
 */
export async function findSurfaceAlongRay(
  ray: Ray,
  minDepth: number,
  maxDepth: number,
  load: (a: number, b: number) => Promise<Vec3[]>,
  opts: AlongRayOptions = {},
): Promise<AlongRayResult> {
  const segmentM = opts.segmentM ?? ((d: number): number => 4 * surfaceGapM(d, opts));
  const minSize = opts.minClusterSize ?? 1;
  const pts: Vec3[] = [];
  const id = (p: Vec3): Vec3 => p;
  let segments = 0;
  let a = minDepth;
  while (a < maxDepth) {
    if (opts.shouldStop?.()) return { point: null, segments, points: pts.length, stopped: true };
    const b = Math.min(maxDepth, a + Math.max(segmentM(a), 1e-3));
    pts.push(...(await load(a, b)));
    segments++;
    const clusters = surfaceClusters(pts, id, ray, opts);
    const idx = clusters.findIndex((c) => c.length >= minSize);
    if (idx >= 0) {
      const c = clusters[idx]!;
      const last = depthAlong(ray, c[c.length - 1]!);
      if (idx < clusters.length - 1 || last + surfaceGapM(last, opts) < b) {
        return { point: nearestToRay(c, id, ray), segments, points: pts.length, stopped: false };
      }
    }
    a = b;
  }
  return { point: chooseFrontSurface(pts, id, ray, opts), segments, points: pts.length, stopped: false };
}

/** Tiefenfenster der feinen Suche brauchbar: endlich, min ≥ 0, min < max. */
export function tiefenFensterGueltig(f: { min: number; max: number }): boolean {
  return Number.isFinite(f.min) && Number.isFinite(f.max) && f.min >= 0 && f.min < f.max;
}

/**
 * Tiefen der „Mitte“ einer Vorschau: Treffer bis ``mittePx`` Pixelabstand vom Zielpunkt. Liegt keiner so
 * nah — große Punkte decken das Mittelpixel, ihr projizierter Mittelpunkt liegt aber weiter weg —, gilt
 * die Gruppe mit dem kleinsten Pixelabstand (bis 1 px darüber). Sonst hieße eine Wand dicht vor der
 * Kamera „kein Treffer“.
 */
export function mitteTiefen(eintraege: readonly { tiefe: number; px: number }[], mittePx: number): number[] {
  const nah = eintraege.filter((e) => e.px <= mittePx);
  if (nah.length > 0 || eintraege.length === 0) return nah.map((e) => e.tiefe);
  const min = Math.min(...eintraege.map((e) => e.px));
  return eintraege.filter((e) => e.px <= min + 1).map((e) => e.tiefe);
}

/** Mindestabstand [px], ab dem ein Treffer einer tieferen Fläche nicht mehr als Durchscheinen gilt. */
export const DURCHSCHEINEN_MIN_PX = 3;
/** Eine tiefere Fläche mit weniger Treffern im Fenster gilt als Streupunkt (wie in der feinen Suche). */
export const SCHICHT_MIN_TREFFER = 3;

/** Abstandsmaß des Durchscheinens: R = DURCHSCHEINEN_FAKTOR × mittlerer Punktabstand der näheren Fläche,
 *  begrenzt auf DURCHSCHEINEN_MIN_PX … DURCHSCHEINEN_MAX_PX. Gemessen am Prüfpaket (Sichtfeld 30° und 80°):
 *  Durchscheinen höchstens 2,7 px neben dem nächsten Wandpunkt bei 1,2–2,0 px Punktabstand der Wand; an
 *  einer echten Öffnungskante liegen die Punkte dahinter im Median 5–9 px von der Wand. 1,5 × Punktabstand
 *  ergibt dort 3 px — über dem Durchscheinen, deutlich unter dem Kantenabstand; 5 px als Deckel, damit
 *  eine dünn dargestellte Fläche eine schmale Öffnung nicht ganz zudeckt. */
export const DURCHSCHEINEN_FAKTOR = 1.5;
export const DURCHSCHEINEN_MAX_PX = 5;

/**
 * Treffer der Vorschau ohne das, was nur durch Lücken der Darstellung einer näheren Fläche durchscheint.
 * Die Vorschau liest die angezeigten Punkte; aus der Nähe ist eine Wand dort ein lückiges Raster, und
 * zwischen ihren Punkten tauchen Punkte einer Fläche dahinter auf — obwohl die Wand in voller Auflösung
 * geschlossen ist. Ein Treffer fällt weg, wenn ein Treffer einer näheren Fläche höchstens R Pixel daneben
 * liegt (R siehe DURCHSCHEINEN_FAKTOR). Unterdrücken darf nur eine Fläche mit mindestens
 * SCHICHT_MIN_TREFFER Treffern — ein paar Streupunkte davor verdecken nichts. Eine echte Öffnung verliert
 * so nur ihren Saum an der Kante. Bleiben von einer tieferen Fläche weniger als SCHICHT_MIN_TREFFER
 * Treffer, fällt sie ganz weg (Streupunkt). Es fallen nur tiefere Treffer weg: die Sichtbarkeitsregel
 * fragt dadurch seltener nach, ein Punkt hinter der Wand wird nie sichtbar gemacht.
 * Grenze: Hinter Maschendraht oder einem Gitter mit sehr feiner Masche kann die Fläche dahinter als
 * Durchscheinen gelten (dann gilt das Gitter als geschlossen); Annähern hilft, die Masche wird größer.
 */
export function ohneDurchscheinen<T extends { tiefe: number; x: number; y: number }>(eintraege: readonly T[]): T[] {
  const sortiert = eintraege.filter((e) => Number.isFinite(e.tiefe) && e.tiefe > 0).sort((a, b) => a.tiefe - b.tiefe);
  const flaechen: T[][] = [];
  for (const e of sortiert) {
    const letzte = flaechen[flaechen.length - 1];
    const tiefste = letzte?.[letzte.length - 1];
    if (letzte && tiefste && e.tiefe - tiefste.tiefe <= surfaceGapM(tiefste.tiefe)) letzte.push(e);
    else flaechen.push([e]);
  }
  if (flaechen.length < 2) return [...eintraege];
  const px = (a: T, b: T): number => Math.hypot(a.x - b.x, a.y - b.y);
  const raus = new Set<T>();
  // Nähere Flächen, die unterdrücken dürfen (mindestens SCHICHT_MIN_TREFFER Treffer), je mit ihrem R.
  const deckend: { treffer: T[]; r: number }[] = [];
  const nimmAuf = (treffer: T[]): void => {
    if (treffer.length < SCHICHT_MIN_TREFFER) return;
    const abstaende = treffer
      .map((a) => treffer.reduce((m, b) => (b === a ? m : Math.min(m, px(a, b))), Infinity))
      .sort((a, b) => a - b);
    const median = abstaende[Math.floor(abstaende.length / 2)]!;
    const r = Math.min(DURCHSCHEINEN_MAX_PX, Math.max(DURCHSCHEINEN_MIN_PX, DURCHSCHEINEN_FAKTOR * median));
    deckend.push({ treffer, r });
  };
  nimmAuf(flaechen[0]!);
  for (let i = 1; i < flaechen.length; i++) {
    const nah = flaechen[i]!.filter((e) => !deckend.some((d) => d.treffer.some((a) => px(a, e) <= d.r)));
    const bleiben = nah.length >= SCHICHT_MIN_TREFFER ? nah : [];
    for (const e of flaechen[i]!) if (!bleiben.includes(e)) raus.add(e);
    nimmAuf(bleiben);
  }
  return eintraege.filter((e) => !raus.has(e));
}

/**
 * Tiefenfenster der zweiten Suche nach der Rückfrage: die gewählte Schicht ± surfaceGapM (eine Schicht aus
 * einer einzigen Probe hätte sonst min = max), nie unter 0, und nicht über die Mitte zwischen den beiden
 * Schichten hinaus — so greift das Fenster der vorderen Fläche nie auf die hintere über und umgekehrt.
 * Bei der groben Stufe (eine Fläche, vorn = hinten) entfällt die Mitte.
 */
export function suchfenster(b: Extract<Bewertung, { art: 'rueckfrage' }>, w: 'vorn' | 'hinten'): { min: number; max: number } {
  const s = b[w];
  let min = Math.max(0, s.min - surfaceGapM(s.min));
  let max = s.max + surfaceGapM(s.max);
  if (b.vorn.max < b.hinten.min) {
    const mitte = (b.vorn.max + b.hinten.min) / 2;
    if (w === 'vorn') max = Math.min(max, mitte);
    else min = Math.max(min, mitte);
  }
  return { min, max };
}

/** Tiefenbereich einer Fläche entlang des Strahls [m]. */
export interface Schicht {
  min: number;
  max: number;
}

export type Bewertung =
  | { art: 'kein-treffer' }
  | { art: 'verdeckt' }
  | { art: 'eindeutig' }
  | { art: 'rueckfrage'; grund: 'zwei-schichten' | 'grobe-stufe'; vorn: Schicht; hinten: Schicht; trefferSchicht: 'vorn' | 'hinten' };

/** Tiefen zu Flächen gruppiert (Lücke > surfaceGapM), sortiert von vorn nach hinten. */
export function schichten(tiefen: readonly number[]): Schicht[] {
  const sortiert = tiefen.filter((t) => Number.isFinite(t) && t > 0).sort((a, b) => a - b);
  const result: Schicht[] = [];
  for (const t of sortiert) {
    const letzte = result[result.length - 1];
    if (letzte && t - letzte.max <= surfaceGapM(letzte.max)) letzte.max = t;
    else result.push({ min: t, max: t });
  }
  return result;
}

function enthaelt(s: Schicht, t: number): boolean {
  return t >= s.min - surfaceGapM(s.min) && t <= s.max + surfaceGapM(s.max);
}

/**
 * Darf ein feiner Treffer ohne Rückfrage gesetzt werden? Vergleicht ihn mit der sichtbaren Tiefe
 * in der Bildmitte (verdeckt → nie) und mit den Flächen im Fenster um die Mitte (mehrere Flächen
 * oder grobe Stufe → Rückfrage).
 */
export function bewerteTreffer(o: {
  trefferTiefe: number | null;
  level: 'fine' | 'coarse' | 'none';
  mitteTiefen: readonly number[];
  fensterTiefen: readonly number[];
}): Bewertung {
  const t = o.trefferTiefe;
  if (t === null || !Number.isFinite(t) || t <= 0 || o.level === 'none' || o.mitteTiefen.length === 0) return { art: 'kein-treffer' };
  const c = Math.min(...o.mitteTiefen);
  if (t > c + surfaceGapM(c)) return { art: 'verdeckt' };

  const s = schichten(o.fensterTiefen);
  if (s.length >= 2) {
    const vorn = s[0]!;
    const inVorn = enthaelt(vorn, t);
    const hinten = inVorn ? s[1]! : (s.find((x) => enthaelt(x, t)) ?? s[1]!);
    return { art: 'rueckfrage', grund: 'zwei-schichten', vorn, hinten, trefferSchicht: inVorn ? 'vorn' : 'hinten' };
  }
  if (o.level === 'coarse') {
    const g = surfaceGapM(t);
    const flaeche = { min: t - g, max: t + g };
    return { art: 'rueckfrage', grund: 'grobe-stufe', vorn: flaeche, hinten: flaeche, trefferSchicht: 'vorn' };
  }
  return { art: 'eindeutig' };
}
