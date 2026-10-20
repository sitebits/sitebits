export const FEATURE_ANGLE_DEG = 30;
export interface FeatureEdge { a: number; b: number }

// Kern der Merkmalskanten (Kantenfang beim Messen) über Zahlenfelder und offene Hash-Tabellen statt
// Text-Schlüsseln und Einzelobjekten. Verhalten unverändert: Positionen
// werden exakt (bitgleich) verschweißt, eine Kante ist Merkmal, sobald eine weitere Fläche um mehr als
// FEATURE_ANGLE_DEG gegen die ERSTE Fläche dieser Kante geneigt ist; entartete Flächen zählen nicht.
export interface Teil { pos: { count: number; getX(i: number): number; getY(i: number): number; getZ(i: number): number };
  index: { count: number; getX(i: number): number } | null; matrix: ArrayLike<number> | null }
const COS_MERKMAL = Math.cos(FEATURE_ANGLE_DEG * Math.PI / 180) + 1e-12;
const zweierpotenz = (n: number): number => 2 ** Math.ceil(Math.log2(Math.max(16, n * 2)));
/** Je Teil die Merkmalskanten; `geschweisst` = Ausgabe als verschweißte Vertex-IDs (erster Index der Position,
 *  nur sinnvoll bei einem Teil), sonst die Indizes des Teils. */
export function kantenKern(teile: readonly Teil[], geschweisst: boolean): FeatureEdge[][] {
  let n = 0; for (const t of teile) n += t.pos.count;
  const xyz = new Float64Array(n * 3); const start: number[] = [];
  let o = 0;
  for (const t of teile) {
    start.push(o);
    const e = t.matrix;
    for (let i = 0; i < t.pos.count; i++, o++) {
      let x = t.pos.getX(i), y = t.pos.getY(i), z = t.pos.getZ(i);
      if (e) { // wie Vector3.applyMatrix4 (gleiche Rechenreihenfolge → bitgleiche Ergebnisse)
        const w = 1 / (e[3]! * x + e[7]! * y + e[11]! * z + e[15]!);
        const nx = (e[0]! * x + e[4]! * y + e[8]! * z + e[12]!) * w;
        const ny = (e[1]! * x + e[5]! * y + e[9]! * z + e[13]!) * w;
        z = (e[2]! * x + e[6]! * y + e[10]! * z + e[14]!) * w; x = nx; y = ny;
      }
      xyz[o * 3] = x; xyz[o * 3 + 1] = y; xyz[o * 3 + 2] = z;
    }
  }
  // Verschweißen: offene Adressierung über die Bitmuster der drei Koordinaten.
  const bits = new Uint32Array(xyz.buffer);
  const vMaske = zweierpotenz(n) - 1; const vTab = new Int32Array(vMaske + 1).fill(-1);
  const welded = new Int32Array(n);
  for (let i = 0; i < n; i++) {
    let h = 0x811c9dc5;
    for (let k = 0; k < 6; k++) h = Math.imul(h ^ bits[i * 6 + k]!, 0x01000193);
    let s = (h >>> 0) & vMaske;
    for (;;) {
      const j = vTab[s]!;
      if (j < 0) { vTab[s] = i; welded[i] = i; break; }
      if (xyz[j * 3] === xyz[i * 3] && xyz[j * 3 + 1] === xyz[i * 3 + 1] && xyz[j * 3 + 2] === xyz[i * 3 + 2]) { welded[i] = j; break; }
      s = (s + 1) & vMaske;
    }
  }
  // Kanten: Schlüssel (min, max) der verschweißten IDs; erste Normale, Merkmalsflag, Verweisliste je Kante.
  let dreiecke = 0; for (const t of teile) dreiecke += (t.index?.count ?? t.pos.count) / 3;
  const maxKanten = Math.max(1, Math.floor(dreiecke) * 3);
  const eMaske = zweierpotenz(maxKanten) - 1; const eTab = new Int32Array(eMaske + 1).fill(-1);
  const kA = new Int32Array(maxKanten), kB = new Int32Array(maxKanten);
  const normale = new Float64Array(maxKanten * 3); const merkmal = new Uint8Array(maxKanten);
  const erster = new Int32Array(maxKanten).fill(-1);
  const refTeil = new Int32Array(maxKanten), refA = new Int32Array(maxKanten), refB = new Int32Array(maxKanten), refNext = new Int32Array(maxKanten);
  let kanten = 0, refs = 0;
  const ids = [0, 0, 0];
  teile.forEach((t, ti) => {
    const s0 = start[ti]!; const count = t.index?.count ?? t.pos.count;
    for (let f = 0; f + 2 < count; f += 3) {
      for (let j = 0; j < 3; j++) ids[j] = t.index ? t.index.getX(f + j) : f + j;
      const p0 = (s0 + ids[0]!) * 3, p1 = (s0 + ids[1]!) * 3, p2 = (s0 + ids[2]!) * 3;
      const ux = xyz[p1]! - xyz[p0]!, uy = xyz[p1 + 1]! - xyz[p0 + 1]!, uz = xyz[p1 + 2]! - xyz[p0 + 2]!;
      const vx = xyz[p2]! - xyz[p0]!, vy = xyz[p2 + 1]! - xyz[p0 + 1]!, vz = xyz[p2 + 2]! - xyz[p0 + 2]!;
      let nx = uy * vz - uz * vy, ny = uz * vx - ux * vz, nz = ux * vy - uy * vx;
      const len = Math.sqrt(nx * nx + ny * ny + nz * nz);
      if (!(len > 0)) continue;
      nx /= len; ny /= len; nz /= len;
      for (let j = 0; j < 3; j++) {
        const a = ids[j]!, b = ids[(j + 1) % 3]!;
        const wa = welded[s0 + a]!, wb = welded[s0 + b]!; const lo = Math.min(wa, wb), hi = Math.max(wa, wb);
        let s = (Math.imul(lo, 0x9e3779b1) ^ Math.imul(hi, 0x85ebca77)) >>> 0 & eMaske; let k = -1;
        for (;;) {
          const c = eTab[s]!;
          if (c < 0) { k = kanten++; eTab[s] = k; kA[k] = lo; kB[k] = hi; normale[k * 3] = nx; normale[k * 3 + 1] = ny; normale[k * 3 + 2] = nz; break; }
          if (kA[c] === lo && kB[c] === hi) {
            k = c;
            if (!merkmal[k] && normale[k * 3]! * nx + normale[k * 3 + 1]! * ny + normale[k * 3 + 2]! * nz <= COS_MERKMAL) merkmal[k] = 1;
            break;
          }
          s = (s + 1) & eMaske;
        }
        if (!geschweisst) { refTeil[refs] = ti; refA[refs] = Math.min(a, b); refB[refs] = Math.max(a, b); refNext[refs] = erster[k]!; erster[k] = refs++; }
      }
    }
  });
  const aus: FeatureEdge[][] = teile.map(() => []);
  for (let k = 0; k < kanten; k++) {
    if (!merkmal[k]) continue;
    if (geschweisst) { aus[0]!.push({ a: kA[k]! - start[0]!, b: kB[k]! - start[0]! }); continue; }
    for (let r = erster[k]!; r >= 0; r = refNext[r]!) aus[refTeil[r]!]!.push({ a: refA[r]!, b: refB[r]! });
  }
  return aus.map((liste) => {
    liste.sort((x, y) => x.a - y.a || x.b - y.b);
    return liste.filter((e, i) => i === 0 || e.a !== liste[i - 1]!.a || e.b !== liste[i - 1]!.b);
  });
}

