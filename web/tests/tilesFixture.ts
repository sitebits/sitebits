// Synthetisches Tileset für die Kachel-Tests: bildet die Form der Kachler-Ausgabe nach (Wurzel ohne transform,
// Kinder mit absoluter Verschiebung, Enkel relativ zum Elternrahmen, Wurzel-GLB mit Ursprung als Knoten-Translation,
// glTF Y oben). Flächen sind waagerechte Rechtecke; gröbere Kacheln liegen absichtlich 5 cm über den Blättern,
// damit ein Wert aus einer gröberen Stufe sofort auffällt.
export const MITTE: [number, number, number] = [365000, 5806000, 70];
export const GROB_VERSATZ_M = 0.05;
export const EXTRAS = { coordinateReferenceSystem: 'EPSG:25833', heightSystem: 'DHHN2016', axis: 'Z', unit: 'm' };

/** GLB mit einem Mesh; Positionen in glTF-Y-oben, Indizes uint16, optional Knoten-Translation und Textur-Verweis. */
export function glb(o: { pos: number[]; idx: number[]; knoten?: [number, number, number]; zweiseitig?: boolean; mitTextur?: boolean }): ArrayBuffer {
  const posBytes = new Float32Array(o.pos).buffer; const idxBytes = new Uint16Array(o.idx);
  const idxPad = Math.ceil(idxBytes.byteLength / 4) * 4;
  const bin = new Uint8Array(posBytes.byteLength + idxPad);
  bin.set(new Uint8Array(posBytes), 0); bin.set(new Uint8Array(idxBytes.buffer), posBytes.byteLength);
  const json = {
    asset: { version: '2.0' }, scene: 0, scenes: [{ nodes: [0] }],
    nodes: [{ mesh: 0, ...(o.knoten ? { translation: o.knoten } : {}) }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0 }, indices: 1, mode: 4, material: 0 }] }],
    materials: [{ doubleSided: o.zweiseitig ?? true, pbrMetallicRoughness: o.mitTextur ? { baseColorTexture: { index: 0 } } : { baseColorFactor: [1, 1, 1, 1] } }],
    ...(o.mitTextur ? { images: [{ uri: 'texturen/1.jpg' }], textures: [{ source: 0 }] } : {}),
    buffers: [{ byteLength: bin.byteLength }],
    bufferViews: [{ buffer: 0, byteOffset: 0, byteLength: posBytes.byteLength }, { buffer: 0, byteOffset: posBytes.byteLength, byteLength: idxBytes.byteLength }],
    accessors: [
      { bufferView: 0, componentType: 5126, count: o.pos.length / 3, type: 'VEC3' },
      { bufferView: 1, componentType: 5123, count: o.idx.length, type: 'SCALAR' },
    ],
  };
  const jb = new TextEncoder().encode(JSON.stringify(json)); const jp = Math.ceil(jb.length / 4) * 4;
  const out = new ArrayBuffer(12 + 8 + jp + 8 + bin.length); const v = new DataView(out);
  v.setUint32(0, 0x46546c67, true); v.setUint32(4, 2, true); v.setUint32(8, out.byteLength, true);
  v.setUint32(12, jp, true); v.setUint32(16, 0x4e4f534a, true);
  new Uint8Array(out, 20, jp).fill(32); new Uint8Array(out, 20, jb.length).set(jb);
  v.setUint32(20 + jp, bin.length, true); v.setUint32(24 + jp, 0x004e4942, true); new Uint8Array(out, 28 + jp).set(bin);
  return out;
}

/** Rechteck x∈[x0,x1], y∈[y0,y1] bei Höhe z, in Kachel-Z-oben-Koordinaten → glTF-Y-oben (x, z, −y). */
export function flaeche(x0: number, x1: number, y0: number, y1: number, z: number): { pos: number[]; idx: number[] } {
  const p = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]];
  return { pos: p.flatMap(([x, y]) => [x!, z, -y!]), idx: [0, 1, 2, 0, 2, 3] };
}

export type Verschiebung = [number, number, number];
const transform = (t: Verschiebung): number[] => [1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, t[0], t[1], t[2], 1];
const box = (c: Verschiebung, h: Verschiebung): number[] => [...c, h[0], 0, 0, 0, h[1], 0, 0, 0, h[2]];

/**
 * Wurzel (grob, 5 cm zu hoch) mit zwei Kindern: K1 (y ∈ [−1,0], grob) mit den Blättern G1 (x ∈ [−1,0]) und G2 (x ∈ [0,1]),
 * und dem Blatt K2 (y ∈ [0,1]). Welt: Mitte MITTE, Fläche der Blätter bei z = MITTE[2].
 */
export function synthetischesTileset(extras: Record<string, unknown> | undefined = EXTRAS): { json: Record<string, unknown>; dateien: Map<string, ArrayBuffer> } {
  const [cx, cy, cz] = MITTE;
  const dateien = new Map<string, ArrayBuffer>();
  const gross = (u: string, f: { pos: number[]; idx: number[] }, knoten?: Verschiebung): void => { dateien.set(u, glb({ ...f, ...(knoten ? { knoten } : {}) })); };
  // Wurzel: Ursprung als Knoten-Translation (glTF Y oben): (cx, cz, −cy); Fläche relativ dazu bei z = +GROB_VERSATZ_M.
  gross('wurzel.glb', flaeche(-1, 1, -1, 1, GROB_VERSATZ_M), [cx, cz, -cy]);
  gross('k1.glb', flaeche(-1, 1, -0.5, 0.5, GROB_VERSATZ_M));
  gross('k2.glb', flaeche(-1, 1, -0.5, 0.5, 0));
  gross('g1.glb', flaeche(-0.5, 0.5, -0.5, 0.5, 0));
  gross('g2.glb', flaeche(-0.5, 0.5, -0.5, 0.5, 0));
  const json = {
    asset: { version: '1.1' }, geometricError: 0.4, ...(extras ? { extras } : {}),
    root: {
      boundingVolume: { box: box([cx, cy, cz], [1, 1, 0.2]) }, geometricError: 0.4, refine: 'REPLACE', content: { uri: 'wurzel.glb' },
      children: [
        { boundingVolume: { box: box([0, 0, 0], [1, 0.5, 0.2]) }, geometricError: 0.2, refine: 'REPLACE', content: { uri: 'k1.glb' },
          transform: transform([cx, cy - 0.5, cz]),
          children: [
            { boundingVolume: { box: box([0, 0, 0], [0.5, 0.5, 0.2]) }, geometricError: 0, refine: 'REPLACE', content: { uri: 'g1.glb' }, transform: transform([-0.5, 0, 0]) },
            { boundingVolume: { box: box([0, 0, 0], [0.5, 0.5, 0.2]) }, geometricError: 0, refine: 'REPLACE', content: { uri: 'g2.glb' }, transform: transform([0.5, 0, 0]) },
          ] },
        { boundingVolume: { box: box([0, 0, 0], [1, 0.5, 0.2]) }, geometricError: 0, refine: 'REPLACE', content: { uri: 'k2.glb' },
          transform: transform([cx, cy + 0.5, cz]) },
      ],
    },
  };
  return { json, dateien };
}
