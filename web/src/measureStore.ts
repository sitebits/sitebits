// Messspeicher (inzwischen mit mehreren Messarten): Messungen als Eckpunktlisten in
// Weltkoordinaten (Float64), Werte daraus abgeleitet — ohne Giro3D/three, in Vitest getestet.
// Jede Messung ist unveränderlich; jede Änderung ersetzt sie durch eine neu ausgewertete (evaluate), so
// laufen Werte und Eckpunkte nie auseinander. Rückgängig = Stapel früherer Listen (Referenzen, keine
// Kopien). Ungültige Änderungen (Selbstschnitt, zu wenige Eckpunkte) werfen MeasureError und ändern
// nichts. Strecken behalten a/b/distanceM (E2E-Haken, JSON-Download, `m=` im Deep-Link).
import { measurementNotes, parsePointSource } from './pointSources';
import type { PointSource, SourceLayer } from './pointSources';
import type { Project, Vec3 } from './manifest';
import { MIN_EDGE_M, polygonArea } from './areaMath';
import type { AreaFailure } from './areaMath';

export type MeasureKind = 'distance' | 'polyline' | 'area' | 'height';

import { exportGueltig } from './vorschlagApi';
import type { VorschlagAnzeige } from './vorschlagApi';
interface Base {
  vorschlag?: VorschlagAnzeige;
  id: string;
  points: readonly Vec3[];
  pointSources?: readonly (PointSource | null)[];
}
export interface DistanceMeasurement extends Base {
  kind: 'distance';
  a: Vec3;
  b: Vec3;
  distanceM: number;
}
export interface PolylineMeasurement extends Base {
  kind: 'polyline';
  segmentsM: number[];
  lengthM: number;
}
export interface AreaMeasurement extends Base {
  kind: 'area';
  /** Fläche der Projektion auf die bestangepasste Ebene [m²]. */
  areaM2: number;
  /** Umfang aus den echten 3D-Kanten [m]. */
  perimeterM: number;
  /** Größter Abstand eines Eckpunkts von der Ebene [m]. */
  planarityM: number;
  /** planarityM > PLANARITY_WARN_M. */
  nonPlanar: boolean;
  /** Schwerpunkt der Eckpunkte (Beschriftung). */
  centroid: Vec3;
}
export interface HeightMeasurement extends Base {
  kind: 'height';
  /** Zweiter minus erster Punkt entlang der Z-Achse des Pakets [m], vorzeichenbehaftet. */
  dzM: number;
  horizontalM: number;
  slantM: number;
}
export type Measurement = DistanceMeasurement | PolylineMeasurement | AreaMeasurement | HeightMeasurement;

export type MeasureFailure = AreaFailure | 'points';
export class MeasureError extends Error {
  readonly reason: MeasureFailure;
  constructor(message: string, reason: MeasureFailure) {
    super(message);
    this.reason = reason;
  }
}

export const MEASURE_LABEL: Record<MeasureKind, string> = { distance: 'Strecke', polyline: 'Polylinie', area: 'Fläche', height: 'Höhe' };
/** Erlaubte Eckpunktzahl je Art [min, max]. */
export const POINT_RANGE: Record<MeasureKind, readonly [number, number]> = {
  distance: [2, 2],
  polyline: [2, Infinity],
  area: [3, Infinity],
  height: [2, 2],
};
export const UNDO_LIMIT = 100;

const AREA_MSG: Record<AreaFailure, string> = {
  'too-few': 'Fläche braucht mindestens 3 Eckpunkte',
  duplicate: 'Zwei aufeinanderfolgende Eckpunkte fallen zusammen',
  degenerate: 'Eckpunkte liegen auf einer Linie – keine Fläche',
  'self-intersecting': 'Fläche schneidet sich selbst',
};

export const distance = (a: Vec3, b: Vec3): number => Math.hypot(b[0] - a[0], b[1] - a[1], b[2] - a[2]);

/** Werte aus den Eckpunkten; wirft MeasureError, wenn die Geometrie für die Art ungültig ist. */
function evaluateGeometry(id: string, kind: MeasureKind, points: readonly Vec3[]): Measurement {
  const [lo, hi] = POINT_RANGE[kind];
  if (points.length < lo || points.length > hi)
    throw new MeasureError(`${MEASURE_LABEL[kind]} braucht ${lo === hi ? lo : `mindestens ${lo}`} Eckpunkte`, 'points');
  const pts = points.map((p): Vec3 => [p[0], p[1], p[2]]);
  switch (kind) {
    case 'distance':
      return { id, kind, points: pts, a: pts[0]!, b: pts[1]!, distanceM: distance(pts[0]!, pts[1]!) };
    case 'polyline': {
      const segmentsM = pts.slice(1).map((p, i) => distance(pts[i]!, p));
      // Null-Teilstrecke (z. B. zweimal derselbe Punkt) wie bei der Fläche als doppelten Eckpunkt ablehnen.
      if (segmentsM.some((d) => d < MIN_EDGE_M)) throw new MeasureError(AREA_MSG.duplicate, 'duplicate');
      return { id, kind, points: pts, segmentsM, lengthM: segmentsM.reduce((s, x) => s + x, 0) };
    }
    case 'height': {
      const a = pts[0]!;
      const b = pts[1]!;
      return { id, kind, points: pts, dzM: b[2] - a[2], horizontalM: Math.hypot(b[0] - a[0], b[1] - a[1]), slantM: distance(a, b) };
    }
    case 'area': {
      const r = polygonArea(pts);
      if (!r.ok) throw new MeasureError(AREA_MSG[r.reason], r.reason);
      return {
        id,
        kind,
        points: pts,
        areaM2: r.areaM2,
        perimeterM: r.perimeterM,
        planarityM: r.plane.maxDeviationM,
        nonPlanar: r.nonPlanar,
        centroid: [...r.plane.centroid],
      };
    }
  }
}

export function evaluate(id: string, kind: MeasureKind, points: readonly Vec3[], pointSources?: readonly (PointSource|null)[]): Measurement {
  const m = evaluateGeometry(id,kind,points);
  if (pointSources !== undefined) {
    if (!Array.isArray(pointSources) || pointSources.length !== points.length) throw new MeasureError('Eine Quelle je Eckpunkt erforderlich', 'points');
    try { m.pointSources = pointSources.map(parsePointSource); }
    catch { throw new MeasureError('Ungültige Punktquelle', 'points'); }
  }
  return m;
}

export class MeasureStore {
  private items: Measurement[] = [];
  private history: Measurement[][] = [];
  private n = 0;

  /** Strecke aus zwei Punkten (Schnittstelle für E2E, Deep-Link `m=`). */
  add(a: Vec3, b: Vec3): DistanceMeasurement {
    return this.addGeometry('distance', [a, b]) as DistanceMeasurement;
  }

  addGeometry(kind: MeasureKind, points: readonly Vec3[], sources?: readonly (PointSource|null)[]): Measurement {
    const m = evaluate(`M${this.n + 1}`, kind, points, sources);
    this.n++;
    this.commit([...this.items, m]);
    return m;
  }

  get(id: string): Measurement | undefined {
    return this.items.find((m) => m.id === id);
  }

  moveVertex(id: string, index: number, p: Vec3, source?: PointSource|null): Measurement {
    return this.replace(id, (pts) => pts.map((q, i) => (i === index ? p : q)),
      (sources) => sources.map((q,i) => i === index ? source ?? null : q), source !== undefined);
  }

  /** Neuer Eckpunkt vor ``index`` (index = Anzahl → anhängen). */
  insertVertex(id: string, index: number, p: Vec3, source?: PointSource|null): Measurement {
    return this.replace(id, (pts) => [...pts.slice(0, index), p, ...pts.slice(index)],
      (sources) => [...sources.slice(0,index),source ?? null,...sources.slice(index)], source !== undefined);
  }

  removeVertex(id: string, index: number): Measurement {
    return this.replace(id, (pts) => pts.filter((_, i) => i !== index), (sources) => sources.filter((_,i) => i !== index));
  }

  remove(id: string): void {
    if (this.items.some((m) => m.id === id)) this.commit(this.items.filter((m) => m.id !== id));
  }

  /** Alle löschen — rückgängig machbar wie jede andere Änderung. */
  clear(vorschlaegeBehalten = false): void {
    const next = vorschlaegeBehalten ? this.items.filter(m => m.vorschlag) : [];
    if (next.length !== this.items.length) this.commit(next);
  }

  list(): Measurement[] {
    return [...this.items];
  }

  canUndo(): boolean {
    return this.history.length > 0;
  }

  undo(): boolean {
    const prev = this.history.pop();
    if (!prev) return false;
    this.items = prev;
    return true;
  }

  /** Nach dem Laden aus dem Deep-Link: das Laden selbst ist kein rückgängig zu machender Schritt. */
  resetHistory(): void {
    this.history = [];
  }

  /** Download: Projekt-Revision + alle Messungen in Weltkoordinaten (Strecken weiter mit a/b/distanceM). */
  toJSON(m: Pick<Project, 'projectId' | 'revision'>, layers: readonly SourceLayer[] = []): object {
    const measurements = this.list().filter(exportGueltig).map(({vorschlag: _vorschlag, ...m}) => {
      void _vorschlag;
      const hinweise = measurementNotes(m,layers);
      return {...m, ...(hinweise.length ? {hinweise} : {})};
    });
    return {schemaVersion: 2, projectId: m.projectId, revision: m.revision, unit: 'm', frame: 'world', measurements};
  }

  private replace(id: string, change: (pts: readonly Vec3[]) => Vec3[],
    sourceChange?: (sources: readonly (PointSource|null)[]) => (PointSource|null)[], withSource = false): Measurement {
    const i = this.items.findIndex((m) => m.id === id);
    if (i < 0) throw new MeasureError(`Messung ${id} gibt es nicht`, 'points');
    const old = this.items[i]!;
    const sources = old.pointSources ?? (withSource ? old.points.map(() => null) : undefined);
    const m = evaluate(old.id, old.kind, change(old.points), sources && (sourceChange ? sourceChange(sources) : sources));
    m.vorschlag = old.vorschlag;
    this.commit(this.items.map((x, j) => (j === i ? m : x)));
    return m;
  }

  setGeometry(id: string, points: readonly Vec3[], sources?: readonly (PointSource|null)[]): Measurement {
    const old = this.get(id); if (!old) throw new MeasureError(`Messung ${id} gibt es nicht`, 'points');
    const m = evaluate(id,old.kind,points,sources);
    m.vorschlag = old.vorschlag;
    this.commit(this.items.map((x) => x.id === id ? m : x)); return m;
  }

  private commit(next: Measurement[]): void {
    this.history.push(this.items);
    if (this.history.length > UNDO_LIMIT) this.history.shift();
    this.items = next;
  }
}
