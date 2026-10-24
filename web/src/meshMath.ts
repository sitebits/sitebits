// Reiner Rechenkern: glTF Y-oben relativ zum Paketursprung → Z-oben in Weltkoordinaten.
import type { Vec3 } from './manifest';
export function meshRootTransform(originWorld: Vec3): { position: Vec3; rotationX: number } {
  return { position: [...originWorld], rotationX: Math.PI / 2 };
}
export function gltfToWorld(o: Vec3, [x, y, z]: Vec3): Vec3 {
  return [o[0] + x, o[1] - z, o[2] + y];
}
/** Ab diesem Anteil an der Bildhöhe lädt die Vollstufe (Detailstufe nach Bildgröße, wie bei 3D Tiles). */
export const MESH_VOLL_BILDANTEIL = 0.25;
export interface MeshSicht { fovGrad: number; sparsam: boolean }
/**
 * Nähe lädt immer (Kamera näher als die Mesh-Diagonale). Mit Sicht zusätzlich, sobald die Hüllkugel
 * mindestens MESH_VOLL_BILDANTEIL der Bildhöhe einnimmt — außer der Browser bittet um Datensparen.
 */
export function shouldLoadFull(cameraWorld: Vec3, [lo, hi]: [Vec3, Vec3], sicht?: MeshSicht): boolean {
  const d = Math.hypot(...lo.map((x, i) => hi[i]! - x));
  const abstand = Math.hypot(...cameraWorld.map((x, i) => x - (lo[i]! + hi[i]!) / 2));
  if (abstand < d) return true;
  if (!sicht || sicht.sparsam || !(sicht.fovGrad > 0)) return false;
  const winkel = 2 * Math.asin(Math.min(1, d / 2 / abstand));
  return winkel / (sicht.fovGrad * Math.PI / 180) >= MESH_VOLL_BILDANTEIL;
}
/** Datensparen laut Browser (Network Information API, nicht überall vorhanden). */
export function datenSparen(nav: { connection?: { saveData?: boolean; effectiveType?: string } }): boolean {
  const c = nav.connection;
  return !!c && (c.saveData === true || /^(slow-2g|2g|3g)$/.test(c.effectiveType ?? ''));
}
export function stageBytes(s: {
  attributeBytes: number;
  indexBytes: number;
  textures: { width: number; height: number; compressed: boolean; mipmaps: boolean }[];
}): number {
  return s.attributeBytes + s.indexBytes + s.textures.reduce((n, t) =>
    n + Math.round(t.width * t.height * (t.compressed ? 1 : 4) * (t.mipmaps ? 4 / 3 : 1)), 0);
}

/** Die `n` schärfsten Mipmap-Stufen einer komprimierten Textur verwerfen (mindestens eine bleibt).
 *  Halbe Kantenlänge je Stufe, also ein Viertel des Speichers; `image` folgt der neuen obersten Stufe. */
export function mipsVerwerfen(t: { mipmaps: { width: number; height: number }[]; image: { width: number; height: number };
  needsUpdate: boolean }, n: number): void {
  const weg = Math.min(n, t.mipmaps.length - 1);
  if (weg <= 0) return;
  t.mipmaps = t.mipmaps.slice(weg);
  t.image = { ...t.image, width: t.mipmaps[0]!.width, height: t.mipmaps[0]!.height };
  t.needsUpdate = true;
}

/** Datei größer als erlaubt; `bytes` = gemeldete oder bis zum Abbruch gelesene Größe. */
export class MeshDateiZuGross extends Error {
  constructor(readonly bytes: number) { super(`Mesh-Datei ${Math.round(bytes / 2 ** 20)} MB über der Gerätegrenze`); }
}
/**
 * Körper einer Antwort höchstens bis `max` Bytes lesen. Meldet der Server die Länge, wird ohne Lesen
 * abgebrochen; sonst beim Überschreiten. `abbrechen` beendet die Übertragung (Body/Fetch).
 */
export async function leseBegrenzt(body: ReadableStream<Uint8Array> | null, laenge: number | null, max: number,
  abbrechen: () => void): Promise<ArrayBuffer> {
  if (laenge !== null && laenge > max) { abbrechen(); throw new MeshDateiZuGross(laenge); }
  if (!body) return new ArrayBuffer(0);
  const leser = body.getReader(); const teile: Uint8Array[] = []; let n = 0;
  for (;;) {
    const { done, value } = await leser.read();
    if (done) break;
    n += value.byteLength;
    if (n > max) { void leser.cancel().catch(() => undefined); abbrechen(); throw new MeshDateiZuGross(n); }
    teile.push(value);
  }
  const aus = new Uint8Array(n); let o = 0;
  for (const t of teile) { aus.set(t, o); o += t.byteLength; }
  return aus.buffer;
}

/** Drahtgitter als Linienindex: je Dreieck drei Kanten (6 Indizes) direkt als Uint32Array. three.js'
 *  `wireframe: true` baut denselben Index über ein JS-Zahlenarray (≈ 48 Byte je Dreieck vorübergehend) —
 *  bei 4 Mio. Dreiecken ein Ruck von ~200 MB. Gemeinsame Kanten bleiben doppelt; Entdoppeln kostete eine
 *  Hashtabelle derselben Größe. Ohne Index: Ecken der Reihe nach (nicht indizierte Dreiecksliste). */
export function drahtIndex(index: ArrayLike<number> | null, ecken: number): Uint32Array {
  const n = index ? Math.floor(index.length / 3) : Math.floor(ecken / 3);
  const aus = new Uint32Array(n * 6);
  for (let i = 0; i < n; i++) {
    const a = index ? index[3 * i]! : 3 * i, b = index ? index[3 * i + 1]! : 3 * i + 1, c = index ? index[3 * i + 2]! : 3 * i + 2;
    const o = 6 * i;
    aus[o] = a; aus[o + 1] = b; aus[o + 2] = b; aus[o + 3] = c; aus[o + 4] = c; aus[o + 5] = a;
  }
  return aus;
}
/** Grafikspeicher des Drahtgitters (Uint32-Linienindex, Positionen werden geteilt). */
export const drahtBytes = (dreiecke: number): number => dreiecke * 6 * 4;

/** Darstellung der Mesh-Fläche (meshLayer.ts); hier, damit das Ebenen-Panel ohne three.js auskommt. */
export type MeshFlaeche = 'textur' | 'grau' | 'normalen' | 'aus';
export const MESH_FLAECHEN: readonly MeshFlaeche[] = ['textur', 'grau', 'normalen', 'aus'];
