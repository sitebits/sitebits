// Float64-Rechenkern für Mesh-Treffer; GPU-Puffer bleiben unverändert im lokalen System.
import { Matrix4, Ray, Vector3 } from 'three';
import type { BufferGeometry, Camera, Mesh } from 'three';
export const MESH_SNAP_PX = 8;
export { FEATURE_ANGLE_DEG } from './kantenKern';
export type { FeatureEdge } from './kantenKern';
import type { FeatureEdge, Teil } from './kantenKern';
import { starteKanten } from './kantenAuftrag';
export const localRay = (ray: Ray, matrix: Matrix4): Ray => ray.clone().applyMatrix4(matrix.clone().invert());
export const worldPoint = (point: Vector3, matrix: Matrix4): Vector3 => point.clone().applyMatrix4(matrix);
const edgeCache = new WeakMap<BufferGeometry, FeatureEdge[]>();

// Eine Generation besitzt ihre Worker und Dispose-Listener. Auch verspätete Antworten
// dürfen nach dispose()/erneutem Vormerken keine alten Caches mehr befüllen.
interface Generation { starten(): void; verwerfen(): void }
const geometrieGeneration = new WeakMap<BufferGeometry, Generation>();
const meshGeneration = new WeakMap<Mesh, Generation>();
const meshEdges = new WeakMap<Mesh, FeatureEdge[]>();
const LEER: FeatureEdge[] = [];
function generation(geometrien: readonly BufferGeometry[], teile: () => Teil[], geschweisst: boolean,
  speichern: (listen: FeatureEdge[][]) => void, loeschen: () => void): Generation {
  let aktiv = true, gestartet = false;
  let abbrechen: (() => void) | undefined;
  const g: Generation = {
    starten() {
      if (!aktiv || gestartet) return;
      gestartet = true;
      abbrechen = starteKanten(teile(), geschweisst, (listen) => { if (aktiv) speichern(listen); });
    },
    verwerfen() {
      if (!aktiv) return;
      aktiv = false; abbrechen?.(); loeschen();
      for (const geo of geometrien) geo.removeEventListener('dispose', g.verwerfen);
    },
  };
  for (const geo of geometrien) geo.addEventListener('dispose', g.verwerfen);
  return g;
}
/** Startet einmal je Geometrie; bis zur Worker-Antwort bleibt nur der Vertex-Fang aktiv. */
export function featureEdges(g: BufferGeometry): FeatureEdge[] {
  if (!geometrieGeneration.has(g)) {
    const gen = generation([g], () => [{ pos: g.getAttribute('position'), index: g.index, matrix: null }], true,
      (listen) => edgeCache.set(g, listen[0]!), () => { edgeCache.delete(g); geometrieGeneration.delete(g); });
    geometrieGeneration.set(g, gen);
  }
  geometrieGeneration.get(g)!.starten();
  return edgeCache.get(g) ?? LEER;
}
export function meshFeatureEdges(mesh: Mesh): FeatureEdge[] {
  const gen = meshGeneration.get(mesh);
  if (!gen) return featureEdges(mesh.geometry);
  gen.starten();
  return meshEdges.get(mesh) ?? LEER;
}
/** Gruppe billig vormerken; Matrizen und Puffer erst beim ersten Bedarf übernehmen. */
export function vormerkenMeshFeatureEdges(meshes: readonly Mesh[], rootMatrix: Matrix4): void {
  for (const m of meshes) meshGeneration.get(m)?.verwerfen();
  const gruppe = [...meshes], matrix = rootMatrix.clone();
  const gen = generation([...new Set(gruppe.map((m) => m.geometry))], () => {
    const inverse = matrix.clone().invert();
    return gruppe.map((m) => ({ pos: m.geometry.getAttribute('position'), index: m.geometry.index,
      matrix: gruppe.length === 1 ? null : inverse.clone().multiply(m.matrixWorld).elements }));
  }, gruppe.length === 1, (listen) => gruppe.forEach((m, i) => meshEdges.set(m, listen[i]!)),
  () => { for (const m of gruppe) { meshEdges.delete(m); meshGeneration.delete(m); } });
  for (const m of gruppe) meshGeneration.set(m, gen);
}
export function prepareMeshFeatureEdges(meshes: readonly Mesh[], rootMatrix: Matrix4): void {
  vormerkenMeshFeatureEdges(meshes, rootMatrix);
  if (meshes.length) meshGeneration.get(meshes[0]!)!.starten();
}
export interface MeshSnap { point: Vector3; kind: 'none' | 'vertex' | 'edge'; index?: number }
export function snapMeshHit(o: {
  geometry: BufferGeometry; triangle: number; hitLocal: Vector3; matrixWorld: Matrix4;
  camera: Camera; width: number; height: number; x: number; y: number; edges: boolean;
  accept?: (point: Vector3) => boolean;
  featureEdges?: readonly FeatureEdge[];
}): MeshSnap {
  const pos = o.geometry.getAttribute('position'); const index = o.geometry.index;
  const project = (local: Vector3): { point: Vector3; x: number; y: number; depth: number } | null => {
    const point = worldPoint(local, o.matrixWorld); const cameraPoint = point.clone().applyMatrix4(o.camera.matrixWorldInverse);
    const ndc = point.clone().project(o.camera);
    if (cameraPoint.z >= 0 || ndc.z < -1 || ndc.z > 1 || (o.accept && !o.accept(point))) return null;
    return { point, x: (ndc.x + 1) * o.width / 2, y: (1 - ndc.y) * o.height / 2, depth: -cameraPoint.z };
  };
  const hitWorld = worldPoint(o.hitLocal, o.matrixWorld);
  let best: MeshSnap = { point: hitWorld, kind: 'none' }; let bestD = MESH_SNAP_PX ** 2;
  const ids = [0,1,2].map((j) => index ? index.getX(o.triangle * 3 + j) : o.triangle * 3 + j).sort((a,b) => a-b);
  for (const i of ids) {
    const p = project(new Vector3().fromBufferAttribute(pos, i)); if (!p) continue;
    const d = (p.x - o.x) ** 2 + (p.y - o.y) ** 2;
    if (d <= bestD && (best.kind !== 'vertex' || d < bestD)) { bestD = d; best = { point: p.point, kind: 'vertex', index: i }; }
  }
  if (best.kind === 'vertex' || !o.edges) return best;
  const hitDepth = -hitWorld.clone().applyMatrix4(o.camera.matrixWorldInverse).z;
  // Nur Kanten in der räumlichen Klickumgebung, keine verdeckte Rückseite hinter der Fläche.
  const projectionScale = Math.abs(o.camera.projectionMatrix.elements[5]!);
  const radiusM = 2 * hitDepth * MESH_SNAP_PX / (projectionScale * o.height);
  const elements = o.matrixWorld.elements;
  const minScale = Math.min(...[0,4,8].map((i) => Math.hypot(elements[i]!, elements[i+1]!, elements[i+2]!)));
  const localRadius2 = (radiusM * 1.5 / minScale) ** 2;
  for (const [i,e] of (o.featureEdges ?? featureEdges(o.geometry)).entries()) {
    // Billiger lokaler Abstandstest vor Kamera-Projektion (hunderttausende Kanten am Reiter).
    const ax = pos.getX(e.a), ay = pos.getY(e.a), az = pos.getZ(e.a);
    const dx0 = pos.getX(e.b)-ax, dy0 = pos.getY(e.b)-ay, dz0 = pos.getZ(e.b)-az;
    const hx = o.hitLocal.x-ax, hy = o.hitLocal.y-ay, hz = o.hitLocal.z-az;
    const length2 = dx0*dx0 + dy0*dy0 + dz0*dz0;
    const along = length2 === 0 ? 0 : Math.max(0, Math.min(1, (hx*dx0+hy*dy0+hz*dz0)/length2));
    if ((hx-along*dx0)**2 + (hy-along*dy0)**2 + (hz-along*dz0)**2 > localRadius2) continue;
    const a = project(new Vector3().fromBufferAttribute(pos, e.a)); const b = project(new Vector3().fromBufferAttribute(pos, e.b));
    if (!a || !b) continue;
    const dx = b.x - a.x; const dy = b.y - a.y; const len = dx*dx + dy*dy;
    const u = len === 0 ? 0 : Math.max(0, Math.min(1, ((o.x-a.x)*dx + (o.y-a.y)*dy) / len));
    const d = (a.x + u*dx-o.x)**2 + (a.y + u*dy-o.y)**2;
    if (d > bestD || (best.kind === 'edge' && d >= bestD)) continue;
    // Perspektivisch korrekter Parameter: Bildschirmmitte ist bei verschiedenen Tiefen nicht Weltmitte.
    const t = u * a.depth / ((1-u)*b.depth + u*a.depth);
    const point = a.point.clone().lerp(b.point, t);
    if (point.distanceTo(hitWorld) > radiusM * 1.5 || (o.accept && !o.accept(point))) continue;
    bestD = d; best = { point, kind: 'edge', index: i };
  }
  return best;
}
