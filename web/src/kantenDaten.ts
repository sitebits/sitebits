import { kantenKern } from './kantenKern';
import type { Teil } from './kantenKern';
export interface KantenTeil { positionen: Float64Array; indizes: Uint32Array | null; matrix: number[] | null }
export interface KantenAnfrage { teile: KantenTeil[]; geschweisst: boolean }
/** Derselbe Kern für Worker und synchronen Rückfall; kompakte Paare über die Thread-Grenze. */
export function berechneKanten(o: KantenAnfrage): Uint32Array[] {
  return kantenKern(o.teile.map((t) => ({
    pos: { count: t.positionen.length / 3, getX: (i) => t.positionen[i * 3]!,
      getY: (i) => t.positionen[i * 3 + 1]!, getZ: (i) => t.positionen[i * 3 + 2]! },
    index: t.indizes ? { count: t.indizes.length, getX: (i) => t.indizes![i]! } : null,
    matrix: t.matrix,
  })), o.geschweisst).map((liste) => {
    const paare = new Uint32Array(liste.length * 2);
    liste.forEach((e, i) => { paare[i * 2] = e.a; paare[i * 2 + 1] = e.b; });
    return paare;
  });
}
export function kopiereKantenTeile(teile: readonly Teil[]): KantenTeil[] {
  return teile.map((t) => {
    // Getter erhalten auch normalisierte/interleaved Attribute und Float64-Positionen exakt.
    const positionen = new Float64Array(t.pos.count * 3);
    for (let i = 0; i < t.pos.count; i++) {
      positionen[i * 3] = t.pos.getX(i); positionen[i * 3 + 1] = t.pos.getY(i); positionen[i * 3 + 2] = t.pos.getZ(i);
    }
    const indizes = t.index ? new Uint32Array(t.index.count) : null;
    if (indizes) for (let i = 0; i < indizes.length; i++) indizes[i] = t.index!.getX(i);
    return { positionen, indizes, matrix: t.matrix ? Array.from(t.matrix) : null };
  });
}
