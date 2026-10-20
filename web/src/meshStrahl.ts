// Schneller Strahltest gegen Meshes, Ersatz für `raycaster.intersectObjects(meshes, false)`.
// three prüft jedes Dreieck über getVertexPosition/Vector3 (Vollstufe des Pilotpakets, 2,2 Mio. Dreiecke:
// ~1 s je Strahl, spürbar als Haken bei jedem Dreh-/Zoomstart, 15.10.). Hier: Dreiecke in Blöcken
// mit Hüllquader je Block (einmal je Geometrie gerechnet), Schnitt direkt auf den Zahlenfeldern mit
// derselben Rechenfolge wie Ray.intersectTriangle — Treffer, Abstand und Reihenfolge sind bitgleich
// mit three (Vergleichstest). Morph-, Skin- und Instanz-Meshes laufen weiter über three.
import { BackSide, FrontSide, Matrix4, Mesh, Ray, Sphere, Vector3 } from 'three';
import type { BufferAttribute, BufferGeometry, InterleavedBufferAttribute, Intersection, Material, Object3D, Raycaster } from 'three';

/** Dreiecke je Block. */
const BLOCK = 256;

interface Lage { feld: ArrayLike<number>; schritt: number; versatz: number }
interface Bloecke { version: number; indexVersion: number; quader: Float32Array; pos: Lage }
const cache = new WeakMap<BufferGeometry, Bloecke>();

/** Positionen als Zahlenfeld; normalisierte Attribute werden einmal entpackt (getX rechnet sie um). */
function lage(attr: BufferAttribute | InterleavedBufferAttribute): Lage {
  if (attr.normalized) {
    const f = new Float64Array(attr.count * 3); // getX liefert den entpackten Wert in Float64
    for (let i = 0; i < attr.count; i++) { f[i*3] = attr.getX(i); f[i*3+1] = attr.getY(i); f[i*3+2] = attr.getZ(i); }
    return { feld: f, schritt: 3, versatz: 0 };
  }
  if ('isInterleavedBufferAttribute' in attr && attr.isInterleavedBufferAttribute) {
    return { feld: attr.data.array as ArrayLike<number>, schritt: attr.data.stride, versatz: attr.offset };
  }
  return { feld: (attr as BufferAttribute).array as ArrayLike<number>, schritt: attr.itemSize, versatz: 0 };
}

const indexFeld = (g: BufferGeometry): ArrayLike<number> | null => g.index ? g.index.array as ArrayLike<number> : null;

function bloecke(g: BufferGeometry): Bloecke {
  const attr = g.attributes.position!;
  const version = 'version' in attr ? attr.version : attr.data.version;
  const indexVersion = g.index?.version ?? -1;
  const alt = cache.get(g);
  if (alt && alt.version === version && alt.indexVersion === indexVersion) return alt;
  const pos = lage(attr); const idx = indexFeld(g);
  const n = idx ? idx.length : attr.count; const dreiecke = Math.floor(n / 3);
  const anzahl = Math.ceil(dreiecke / BLOCK);
  const quader = new Float32Array(anzahl * 6);
  const { feld, schritt, versatz } = pos;
  let ax = Infinity, ay = Infinity, az = Infinity, bx = -Infinity, by = -Infinity, bz = -Infinity;
  for (let b = 0; b < anzahl; b++) {
    let x0 = Infinity, y0 = Infinity, z0 = Infinity, x1 = -Infinity, y1 = -Infinity, z1 = -Infinity;
    const ende = Math.min(n, (b + 1) * BLOCK * 3);
    for (let j = b * BLOCK * 3; j < ende; j++) {
      const v = (idx ? idx[j]! : j) * schritt + versatz;
      const x = feld[v]!, y = feld[v+1]!, z = feld[v+2]!;
      if (x < x0) x0 = x; if (x > x1) x1 = x;
      if (y < y0) y0 = y; if (y > y1) y1 = y;
      if (z < z0) z0 = z; if (z > z1) z1 = z;
    }
    quader.set([x0, y0, z0, x1, y1, z1], b * 6);
    if (x0 < ax) ax = x0; if (y0 < ay) ay = y0; if (z0 < az) az = z0;
    if (x1 > bx) bx = x1; if (y1 > by) by = y1; if (z1 > bz) bz = z1;
  }
  // Quader großzügig aufweiten: Der Blocktest ist nur Vorfilter und darf keinen Treffer verlieren,
  // den die Dreiecksrechnung (andere Rundung) noch findet.
  const rand = 1e-6 * Math.max(bx - ax, by - ay, bz - az, 1e-3);
  for (let i = 0; i < quader.length; i += 6) {
    for (let k = 0; k < 3; k++) { quader[i+k] = Math.fround(quader[i+k]! - rand); quader[i+3+k] = Math.fround(quader[i+3+k]! + rand); }
  }
  const neu = { version, indexVersion, quader, pos };
  cache.set(g, neu);
  return neu;
}

/** Trifft der Strahl (t ≥ 0) den Quader ab Feldstelle i? Achsen mit Richtung 0: inv = ±Infinity. */
function quaderTreffer(q: Float32Array, i: number, ox: number, oy: number, oz: number, ix: number, iy: number, iz: number): boolean {
  let tmin = 0, tmax = Infinity, t1: number, t2: number;
  if (Number.isFinite(ix)) {
    t1 = (q[i]! - ox) * ix; t2 = (q[i+3]! - ox) * ix;
    if (t1 > t2) { const t = t1; t1 = t2; t2 = t; }
    if (t1 > tmin) tmin = t1; if (t2 < tmax) tmax = t2;
  } else if (ox < q[i]! || ox > q[i+3]!) return false;
  if (Number.isFinite(iy)) {
    t1 = (q[i+1]! - oy) * iy; t2 = (q[i+4]! - oy) * iy;
    if (t1 > t2) { const t = t1; t1 = t2; t2 = t; }
    if (t1 > tmin) tmin = t1; if (t2 < tmax) tmax = t2;
  } else if (oy < q[i+1]! || oy > q[i+4]!) return false;
  if (Number.isFinite(iz)) {
    t1 = (q[i+2]! - oz) * iz; t2 = (q[i+5]! - oz) * iz;
    if (t1 > t2) { const t = t1; t1 = t2; t2 = t; }
    if (t1 > tmin) tmin = t1; if (t2 < tmax) tmax = t2;
  } else if (oz < q[i+2]! || oz > q[i+5]!) return false;
  return tmin <= tmax;
}

const _kugel = new Sphere(); const _ray = new Ray(); const _inv = new Matrix4(); const _kugelTreffer = new Vector3();
const _punkt = new Vector3();

/** Wie Mesh.raycast, Dreiecksschnitt über die Blöcke. */
function meshSchnitte(mesh: Mesh, raycaster: Raycaster, out: Intersection[]): void {
  const g = mesh.geometry; const material = mesh.material as Material | Material[] | undefined;
  if (material === undefined) return;
  if (g.boundingSphere === null) g.computeBoundingSphere();
  _kugel.copy(g.boundingSphere!).applyMatrix4(mesh.matrixWorld);
  _ray.copy(raycaster.ray).recast(raycaster.near);
  if (!_kugel.containsPoint(_ray.origin)) {
    if (_ray.intersectSphere(_kugel, _kugelTreffer) === null) return;
    if (_ray.origin.distanceToSquared(_kugelTreffer) > (raycaster.far - raycaster.near) ** 2) return;
  }
  _inv.copy(mesh.matrixWorld).invert();
  _ray.copy(raycaster.ray).applyMatrix4(_inv);
  if (g.boundingBox !== null && !_ray.intersectsBox(g.boundingBox)) return;

  const { quader, pos: { feld, schritt, versatz } } = bloecke(g);
  const idx = indexFeld(g);
  const n = idx ? g.index!.count : g.attributes.position!.count;
  const ox = _ray.origin.x, oy = _ray.origin.y, oz = _ray.origin.z;
  const dx = _ray.direction.x, dy = _ray.direction.y, dz = _ray.direction.z;
  const ix = 1 / dx, iy = 1 / dy, iz = 1 / dz;
  const mw = mesh.matrixWorld;

  const bereich = (start: number, end: number, mat: Material | undefined, materialIndex: number): void => {
    if (mat === undefined) return;
    const rueck = mat.side === BackSide; const cull = rueck || mat.side === FrontSide;
    for (let b = Math.floor(start / (BLOCK * 3)); b * BLOCK * 3 < end; b++) {
      // Beginnt ein Bereich nicht auf einer Dreiecksgrenze, liegen seine Dreiecke quer zu den Blöcken:
      // dann ohne Vorfilter (kommt bei glTF nicht vor).
      if (start % 3 === 0 && !quaderTreffer(quader, b * 6, ox, oy, oz, ix, iy, iz)) continue;
      const j0 = Math.max(start, b * BLOCK * 3); const j1 = Math.min(end, (b + 1) * BLOCK * 3);
      // Gleiche Schrittweite wie three (j += 3 ab start), auch wenn start nicht auf einem Dreieck liegt.
      const erstes = j0 === start ? start : start + Math.ceil((j0 - start) / 3) * 3;
      for (let j = erstes; j < j1; j += 3) {
        const ia = idx ? idx[j]! : j, ib = idx ? idx[j+1]! : j + 1, ic = idx ? idx[j+2]! : j + 2;
        // BackSide: three tauscht a und c und schneidet mit Rückseitenausschluss.
        const va = (rueck ? ic : ia) * schritt + versatz, vb = ib * schritt + versatz, vc = (rueck ? ia : ic) * schritt + versatz;
        const axv = feld[va]!, ayv = feld[va+1]!, azv = feld[va+2]!;
        // Ray.intersectTriangle, Rechenfolge wie dort.
        const e1x = feld[vb]! - axv, e1y = feld[vb+1]! - ayv, e1z = feld[vb+2]! - azv;
        const e2x = feld[vc]! - axv, e2y = feld[vc+1]! - ayv, e2z = feld[vc+2]! - azv;
        const nx = e1y * e2z - e1z * e2y, ny = e1z * e2x - e1x * e2z, nz = e1x * e2y - e1y * e2x;
        let DdN = dx * nx + dy * ny + dz * nz; let sign: number;
        if (DdN > 0) { if (cull) continue; sign = 1; }
        else if (DdN < 0) { sign = -1; DdN = -DdN; }
        else continue;
        const qx = ox - axv, qy = oy - ayv, qz = oz - azv;
        const cx = qy * e2z - qz * e2y, cy = qz * e2x - qx * e2z, cz = qx * e2y - qy * e2x;
        const DdQxE2 = sign * (dx * cx + dy * cy + dz * cz);
        if (DdQxE2 < 0) continue;
        const kx = e1y * qz - e1z * qy, ky = e1z * qx - e1x * qz, kz = e1x * qy - e1y * qx;
        const DdE1xQ = sign * (dx * kx + dy * ky + dz * kz);
        if (DdE1xQ < 0) continue;
        if (DdQxE2 + DdE1xQ > DdN) continue;
        const QdN = -sign * (qx * nx + qy * ny + qz * nz);
        if (QdN < 0) continue;
        const t = QdN / DdN;
        _punkt.set(dx * t + ox, dy * t + oy, dz * t + oz).applyMatrix4(mw);
        const distance = raycaster.ray.origin.distanceTo(_punkt);
        if (distance < raycaster.near || distance > raycaster.far) continue;
        out.push({ distance, point: _punkt.clone(), object: mesh, faceIndex: Math.floor(j / 3),
          face: { a: ia, b: ib, c: ic, normal: new Vector3(), materialIndex } });
      }
    }
  };

  const dr = g.drawRange;
  if (Array.isArray(material)) {
    for (const grp of g.groups) {
      const start = Math.max(grp.start, dr.start);
      const end = Math.min(n, Math.min(grp.start + grp.count, dr.start + dr.count));
      bereich(start, end, material[grp.materialIndex!], grp.materialIndex ?? 0);
    }
  } else {
    bereich(Math.max(0, dr.start), Math.min(n, dr.start + dr.count), material, 0);
  }
}

const eigenerWeg = (o: Object3D): o is Mesh => {
  const m = o as Mesh & { isSkinnedMesh?: boolean; isInstancedMesh?: boolean };
  return m.isMesh === true && !m.isSkinnedMesh && !m.isInstancedMesh && !m.geometry.morphAttributes.position
    && m.geometry.attributes.position !== undefined && m.raycast === Mesh.prototype.raycast;
};

/** Wie `raycaster.intersectObjects(objekte, false)`: nach Abstand sortiert, gleiche Treffer wie three
 *  (ohne uv/normal/barycoord; `face.normal` bleibt leer). */
export function strahlSchnitte(raycaster: Raycaster, objekte: readonly Object3D[]): Intersection[] {
  const out: Intersection[] = [];
  for (const o of objekte) {
    if (!o.layers.test(raycaster.layers)) continue;
    if (eigenerWeg(o)) meshSchnitte(o, raycaster, out);
    else o.raycast(raycaster, out);
  }
  return out.sort((a, b) => a.distance - b.distance);
}
