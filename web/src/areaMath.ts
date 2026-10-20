// Fläche eines 3D-Polygons aus Originalpunkten (die PCA wird auch vom Kantenfang wiederverwendet).
// Reine Logik ohne three.js (Vitest).
//   - Ebene: bestangepasst per PCA (Hauptachsen der Kovarianz, Jacobi-Verfahren für die symmetrische
//     3×3-Matrix); Normale = Achse zum kleinsten Eigenwert.
//   - Fläche = Fläche der Projektion auf diese Ebene (Gaußsche Trapezformel in Ebenenkoordinaten u, v);
//     Umfang = Summe der echten 3D-Kantenlängen zwischen den Eckpunkten.
//   - Ebenheit: größter Abstand eines Eckpunkts von der Ebene; über PLANARITY_WARN_M wird gewarnt,
//     nicht abgelehnt (eine gewölbte Fläche hat eine projizierte Fläche, nur ist sie dann nicht „die“ Fläche).
//   - Abgelehnt: < 3 Eckpunkte, zusammenfallende Nachbarn (< MIN_EDGE_M), alle auf einer Linie,
//     Selbstschnitt oder Berührung nicht benachbarter Kanten (in der Projektion geprüft).
// Genauigkeit: vor allem anderen auf den ersten Eckpunkt verschoben — bei UTM-Werten (5,7·10⁶ m)
// verlöre die Kovarianz sonst Stellen; Float64 durchgehend.
export type Vec3 = readonly [number, number, number];
export type Vec2 = readonly [number, number];

/** Ab dieser Abweichung eines Eckpunkts von der Ebene [m] wird „nicht eben“ angezeigt. */
export const PLANARITY_WARN_M = 0.02;
/** Kürzere Kanten gelten als doppelter Eckpunkt [m]. */
export const MIN_EDGE_M = 1e-4;
/** Kleinere Flächen gelten als entartet [m²]. */
export const MIN_AREA_M2 = 1e-6;

export interface PlaneFit {
  centroid: Vec3;
  /** Einheitsnormale (Vorzeichen beliebig). */
  normal: Vec3;
  /** Orthonormale Achsen in der Ebene. */
  u: Vec3;
  v: Vec3;
  maxDeviationM: number;
}
export type AreaFailure = 'too-few' | 'duplicate' | 'degenerate' | 'self-intersecting';
export type AreaResult =
  | { ok: true; areaM2: number; perimeterM: number; plane: PlaneFit; nonPlanar: boolean }
  | { ok: false; reason: AreaFailure };

const sub = (a: Vec3, b: Vec3): Vec3 => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
const dot = (a: Vec3, b: Vec3): number => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
const len = (a: Vec3): number => Math.hypot(a[0], a[1], a[2]);

/** Jacobi-Verfahren für eine symmetrische 3×3-Matrix; Spalte i von ``vectors`` gehört zu values[i]. */
function symEigen3(a: number[][]): { values: number[]; vectors: number[][] } {
  const m = a.map((r) => [...r]);
  const v = [
    [1, 0, 0],
    [0, 1, 0],
    [0, 0, 1],
  ];
  const pairs: [number, number][] = [
    [0, 1],
    [0, 2],
    [1, 2],
  ];
  for (let sweep = 0; sweep < 64; sweep++) {
    const off = m[0]![1]! ** 2 + m[0]![2]! ** 2 + m[1]![2]! ** 2;
    const diag = m[0]![0]! ** 2 + m[1]![1]! ** 2 + m[2]![2]! ** 2;
    if (off === 0 || off <= 1e-30 * diag) break;
    for (const [p, q] of pairs) {
      const apq = m[p]![q]!;
      if (apq === 0) continue;
      const theta = (m[q]![q]! - m[p]![p]!) / (2 * apq);
      const t = (theta >= 0 ? 1 : -1) / (Math.abs(theta) + Math.sqrt(theta * theta + 1));
      const c = 1 / Math.sqrt(t * t + 1);
      const s = t * c;
      for (let k = 0; k < 3; k++) {
        const kp = m[k]![p]!;
        const kq = m[k]![q]!;
        m[k]![p] = c * kp - s * kq;
        m[k]![q] = s * kp + c * kq;
      }
      for (let k = 0; k < 3; k++) {
        const pk = m[p]![k]!;
        const qk = m[q]![k]!;
        m[p]![k] = c * pk - s * qk;
        m[q]![k] = s * pk + c * qk;
      }
      for (let k = 0; k < 3; k++) {
        const kp = v[k]![p]!;
        const kq = v[k]![q]!;
        v[k]![p] = c * kp - s * kq;
        v[k]![q] = s * kp + c * kq;
      }
    }
  }
  return { values: [m[0]![0]!, m[1]![1]!, m[2]![2]!], vectors: v };
}

export function centroidOf(points: readonly Vec3[]): Vec3 {
  let x = 0;
  let y = 0;
  let z = 0;
  for (const p of points) {
    x += p[0];
    y += p[1];
    z += p[2];
  }
  return [x / points.length, y / points.length, z / points.length];
}

/** Hauptachsen: Eigenwerte aufsteigend (≥ 0), Achsen dazu. Auch vom Kantenfang genutzt. */
export function pca(points: readonly Vec3[], c: Vec3 = centroidOf(points)): { values: [number, number, number]; axes: [Vec3, Vec3, Vec3] } {
  const cov = [
    [0, 0, 0],
    [0, 0, 0],
    [0, 0, 0],
  ];
  for (const p of points) {
    const d = sub(p, c);
    for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) cov[i]![j]! += d[i]! * d[j]!;
  }
  const { values, vectors } = symEigen3(cov.map((r) => r.map((x) => x / points.length)));
  const order = [0, 1, 2].sort((i, j) => values[i]! - values[j]!);
  const axis = (i: number): Vec3 => [vectors[0]![i]!, vectors[1]![i]!, vectors[2]![i]!];
  return {
    values: order.map((i) => Math.max(0, values[i]!)) as [number, number, number],
    axes: order.map(axis) as [Vec3, Vec3, Vec3],
  };
}

export function fitPlane(points: readonly Vec3[]): PlaneFit {
  const centroid = centroidOf(points);
  const [normal, v, u] = pca(points, centroid).axes;
  let maxDeviationM = 0;
  for (const p of points) maxDeviationM = Math.max(maxDeviationM, Math.abs(dot(sub(p, centroid), normal)));
  return { centroid, normal, u, v, maxDeviationM };
}

export function projectToPlane(points: readonly Vec3[], f: PlaneFit): Vec2[] {
  return points.map((p): Vec2 => {
    const d = sub(p, f.centroid);
    return [dot(d, f.u), dot(d, f.v)];
  });
}

/** Vorzeichenbehaftete Fläche (Gaußsche Trapezformel). */
export function shoelace(pts: readonly Vec2[]): number {
  let s = 0;
  for (let i = 0; i < pts.length; i++) {
    const a = pts[i]!;
    const b = pts[(i + 1) % pts.length]!;
    s += a[0] * b[1] - b[0] * a[1];
  }
  return s / 2;
}

const orient = (a: Vec2, b: Vec2, c: Vec2): number => (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
const inBox = (a: Vec2, b: Vec2, p: Vec2, eps: number): boolean =>
  Math.min(a[0], b[0]) - eps <= p[0] &&
  p[0] <= Math.max(a[0], b[0]) + eps &&
  Math.min(a[1], b[1]) - eps <= p[1] &&
  p[1] <= Math.max(a[1], b[1]) + eps;

/** Schneiden oder berühren sich die Strecken p1p2 und p3p4 (Berührung zählt)? */
export function segmentsTouch(p1: Vec2, p2: Vec2, p3: Vec2, p4: Vec2, eps = 1e-9): boolean {
  const sign = (x: number): number => (x > eps ? 1 : x < -eps ? -1 : 0);
  const d1 = sign(orient(p3, p4, p1));
  const d2 = sign(orient(p3, p4, p2));
  const d3 = sign(orient(p1, p2, p3));
  const d4 = sign(orient(p1, p2, p4));
  if (d1 * d2 < 0 && d3 * d4 < 0) return true;
  return (
    (d1 === 0 && inBox(p3, p4, p1, eps)) ||
    (d2 === 0 && inBox(p3, p4, p2, eps)) ||
    (d3 === 0 && inBox(p1, p2, p3, eps)) ||
    (d4 === 0 && inBox(p1, p2, p4, eps))
  );
}

/** Geschlossener Ring: schneidet/berührt eine Kante eine nicht benachbarte? */
export function selfIntersects(pts: readonly Vec2[]): boolean {
  const n = pts.length;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue;
      if (segmentsTouch(pts[i]!, pts[(i + 1) % n]!, pts[j]!, pts[(j + 1) % n]!)) return true;
    }
  }
  return false;
}

export function polygonArea(input: readonly Vec3[]): AreaResult {
  const n = input.length;
  if (n < 3) return { ok: false, reason: 'too-few' };
  const o = input[0]!;
  const points = input.map((p) => sub(p, o));
  let perimeterM = 0;
  for (let i = 0; i < n; i++) {
    const e = len(sub(points[(i + 1) % n]!, points[i]!));
    if (e < MIN_EDGE_M) return { ok: false, reason: 'duplicate' };
    perimeterM += e;
  }
  const local = fitPlane(points);
  // Alle Punkte auf einer Geraden (mittlerer Eigenwert ≈ 0): keine Ebene bestimmbar.
  if (pca(points, local.centroid).values[1] < MIN_EDGE_M ** 2) return { ok: false, reason: 'degenerate' };
  const flat = projectToPlane(points, local);
  if (selfIntersects(flat)) return { ok: false, reason: 'self-intersecting' };
  const areaM2 = Math.abs(shoelace(flat));
  if (areaM2 < MIN_AREA_M2) return { ok: false, reason: 'degenerate' };
  const c = local.centroid;
  const plane: PlaneFit = { ...local, centroid: [c[0] + o[0], c[1] + o[1], c[2] + o[2]] };
  return { ok: true, areaM2, perimeterM, plane, nonPlanar: plane.maxDeviationM > PLANARITY_WARN_M };
}
