// CPU-Strahltest gegen geladene Punktgeometrie, ohne Renderer-/LOD-Zustand.
import { Box3, Matrix4, Vector3 } from 'three';
import type { BufferGeometry, Object3D, Points, Ray } from 'three';

/** Auch der einmalige Rasterbau gibt innerhalb großer Knoten den Frame frei. */
const PORTION = 2048;
const istPunkt = (o: Object3D): o is Points => (o as Points).isPoints === true;
type Position = ReturnType<BufferGeometry['getAttribute']>;
type Zelle = { x: number; y: number; z: number; n: number };
interface Raster {
  position: Position;
  version: number;
  ursprung: Vector3;
  schritt: number;
  anzahl: number[];
  zellen: Map<number | string, Zelle>;
}
const rasterCache = new WeakMap<BufferGeometry, Raster>();
const version = (p: Position): number => 'version' in p ? p.version : p.data.version;
function zellgroesse(box: Box3, n: number): number {
  const s = box.getSize(new Vector3());
  const flaeche = 2 * (s.x * s.y + s.x * s.z + s.y * s.z);
  return flaeche > 0 ? Math.max(1e-6, 2 * Math.sqrt(flaeche / n)) : 0.04;
}
const schluessel = (r: Raster, x: number, y: number, z: number): number | string =>
  Number.isSafeInteger(r.anzahl[0]! * r.anzahl[1]! * r.anzahl[2]!)
    ? x + r.anzahl[0]! * (y + r.anzahl[1]! * z) : `${x},${y},${z}`;

function* rasterBauen(g: BufferGeometry, position: Position): Generator<void, Raster> {
  const p = new Vector3(), box = new Box3(), stand = version(position);
  // Nicht geometry.boundingBox wiederverwenden: needsUpdate kann Positionen
  // außerhalb des bisherigen Quaders verschieben, ohne diesen zu aktualisieren.
  for (let i = 0; i < position.count; i++) {
    box.expandByPoint(p.fromBufferAttribute(position, i));
    if ((i + 1) % PORTION === 0) yield;
  }
  const s = box.getSize(new Vector3());
  // Oberflächenmodell: mittlerer Abstand ≈ sqrt(Hüllquader-Oberfläche / N).
  // Zwei Abstände pro Zelle bündeln Zufallslücken, statt jeden Punkt als
  // isolierte Scheibe zu behandeln. Bei planaren Knoten ist die Fläche 2*A
  // (beide Seiten der Hülle); nur bei Linien/Einzelpunkten: 4 cm Ersatzgröße.
  // Keine Garantie für dünne Hindernisse, echte Löcher oder ungleichmäßige Dichte:
  // unterhalb der Zellgröße werden sie vergröbert; fehlende Knoten fehlen weiter.
  const schritt = zellgroesse(box, position.count);
  const ursprung = box.min.clone().addScalar(-schritt / 2);
  const anzahl = [s.x, s.y, s.z].map((v) => Math.floor(v / schritt + 0.5) + 1);
  const r: Raster = { position, version: stand, ursprung, schritt, anzahl, zellen: new Map() };
  for (let i = 0; i < position.count; i++) {
    p.fromBufferAttribute(position, i);
    const key = schluessel(r, Math.floor((p.x - ursprung.x) / schritt), Math.floor((p.y - ursprung.y) / schritt), Math.floor((p.z - ursprung.z) / schritt));
    const z = r.zellen.get(key);
    if (z) { z.x += p.x; z.y += p.y; z.z += p.z; z.n++; }
    else r.zellen.set(key, { x: p.x, y: p.y, z: p.z, n: 1 });
    if ((i + 1) % PORTION === 0) yield;
  }
  // Erst vollständig gebaute Raster veröffentlichen (Abbruch bleibt unschädlich).
  if (g.getAttribute('position') !== position || version(position) !== stand) return yield* rasterBauen(g, g.getAttribute('position'));
  rasterCache.set(g, r);
  return r;
}

/** 3D-DDA in lokalen Zellen; t bleibt Weltmeter, auch bei nichtuniformer Skalierung. */
function* rasterStrahl(r: Raster, o: Points, ray: Ray, far: number): Generator<void, Vector3 | null> {
  if (o.matrixWorld.determinant() === 0) return null;
  const invers = new Matrix4().copy(o.matrixWorld).invert();
  const start = ray.origin.clone().applyMatrix4(invers);
  const richtung = ray.direction.clone().add(ray.origin).applyMatrix4(invers).sub(start);
  const a = start.toArray(), d = richtung.toArray(), min = r.ursprung.toArray();
  let ein = 0, aus = far;
  for (let k = 0; k < 3; k++) {
    const oben = min[k]! + r.anzahl[k]! * r.schritt;
    if (d[k] === 0) { if (a[k]! < min[k]! || a[k]! > oben) return null; }
    else {
      const t1 = (min[k]! - a[k]!) / d[k]!, t2 = (oben - a[k]!) / d[k]!;
      ein = Math.max(ein, Math.min(t1, t2)); aus = Math.min(aus, Math.max(t1, t2));
    }
  }
  if (ein > aus || aus <= 0 || richtung.lengthSq() === 0) return null;
  const zelle = a.map((v, k) => Math.max(0, Math.min(r.anzahl[k]! - 1, Math.floor((v + ein * d[k]! - min[k]!) / r.schritt))));
  const schritt = d.map(Math.sign);
  const delta = d.map((v) => v === 0 ? Infinity : r.schritt / Math.abs(v));
  const naechste = d.map((v, k) => v === 0 ? Infinity : (min[k]! + (zelle[k]! + (v > 0 ? 1 : 0)) * r.schritt - a[k]!) / v);
  let zaehler = 0;
  while (ein <= aus) {
    const ende = Math.min(...naechste, aus);
    const belegt = r.zellen.get(schluessel(r, zelle[0]!, zelle[1]!, zelle[2]!));
    if (belegt) {
      // Der Zellschwerpunkt liefert die Tiefe auf dem Strahl, nicht den
      // vorgezogenen Zellrand: vermeidet eine halbe Zelle Bias an der 0,3-m-Grenze.
      const p = new Vector3(belegt.x / belegt.n, belegt.y / belegt.n, belegt.z / belegt.n).applyMatrix4(o.matrixWorld);
      const t = Math.max(ein, Math.min(ende, p.sub(ray.origin).dot(ray.direction)));
      if (ende > 0) return ray.at(t > 0 ? t : Math.min(ende, 1e-6), new Vector3());
    }
    if (ende >= aus) break;
    // Bei Kanten-/Ecktreffern alle gebundenen Achsen gleichzeitig weiterschalten.
    for (let k = 0; k < 3; k++) if (naechste[k]! <= ende) { zelle[k]! += schritt[k]!; naechste[k]! += delta[k]!; }
    if (zelle.some((v, k) => v < 0 || v >= r.anzahl[k]!)) break;
    ein = ende;
    if (++zaehler % PORTION === 0) yield;
  }
  return null;
}

/** null = keine Geometrie geladen; point:null = geladen, kein belegtes Voxel.
 * Winkel bleibt als Aufrufparameter kompatibel; das Raster hängt nur vom Knoten
 * ab, weder von Fotopixeln noch vom Ansichts-/LOD-Zustand.
 */
export function* punktStrahl(root: Object3D, ray: Ray, _winkel: number, far = 250, stichprobe?: number): Generator<void, { point: Vector3 | null } | null> {
  const knoten: Points[] = [];
  root.traverse((o) => {
    if (istPunkt(o) && o.geometry.getAttribute('position')?.count > 0) knoten.push(o);
  });
  if (!knoten.length) return null;
  const geladen: { o: Points; raster: Raster }[] = [];
  for (const o of knoten) {
    o.updateWorldMatrix(true, false);
    const g = o.geometry, position = g.getAttribute('position');
    let raster = rasterCache.get(g);
    const aktuell = raster?.position === position && raster.version === version(position);
    // Ein gelieferter Quader darf nur vor dem ersten Bau zum Verwerfen dienen.
    // Nach Positionsänderung kann er veraltet sein und wird nicht mehr vertraut.
    if (!raster && g.boundingBox) {
      const rand = zellgroesse(g.boundingBox, position.count) * Math.sqrt(3) / 2 * o.matrixWorld.getMaxScaleOnAxis();
      if (!ray.intersectsBox(g.boundingBox.clone().applyMatrix4(o.matrixWorld).expandByScalar(rand))) { yield; continue; }
    }
    if (!aktuell) raster = yield* rasterBauen(g, position);
    geladen.push({ o, raster: raster! });
    yield;
  }
  // Zuerst ausschließlich bis Stichprobe minus unveränderter Verdeckungstoleranz.
  // Nur falls dort kein Hindernis liegt, bis far nach Zielbelegung suchen:
  // Ein leerer Strahl darf weiterhin keine Sichtbarkeit bestätigen.
  const grenze = stichprobe === undefined ? far : Math.max(0, stichprobe - Math.max(0.3, 0.02 * stichprobe));
  for (const reichweite of grenze < far ? [grenze, far] : [far]) {
    let point: Vector3 | null = null, abstand = reichweite;
    for (const { o, raster } of geladen) {
      const treffer = yield* rasterStrahl(raster, o, ray, abstand);
      if (treffer) { point = treffer; abstand = ray.origin.distanceTo(treffer); }
      yield;
    }
    if (point && (reichweite === far || abstand < grenze)) return { point };
  }
  return { point: null };
}
