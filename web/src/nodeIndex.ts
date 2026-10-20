// Knoten nach ID für die Strahlsuche (rayPick.ts) — reine Logik ohne three.js/Giro3D (Vitest).

export interface IndexedNode<N> {
  id: string;
  children?: (N | undefined)[];
}

/**
 * Liefert ``nodeById`` über eine einmal gebaute Karte aller Knoten. Als *ein* Promise gemerkt: die Prüfung
 * der groben Kandidaten ruft nodeById für alle Kandidaten gleichzeitig auf — ein Merker auf die fertige Map
 * baute sie je Aufrufer neu (großes Referenzpaket Fassade: 129 819 Knoten × 295 Kandidaten ≈ 5,3 s blockierter Hauptfaden beim
 * ersten Klick, gemessen). ``getRoot`` wird genau einmal aufgerufen.
 */
export interface NodeIndex<N> {
  get: (id: string) => Promise<N | undefined>;
  /** Karte vorab bauen (nicht im ersten Klick); schlägt das fehl, versucht der nächste Aufruf es neu. */
  warm: () => void;
}

export function memoNodeIndex<N extends IndexedNode<N>>(getRoot: () => Promise<N>): NodeIndex<N> {
  let byId: Promise<Map<string, N>> | null = null;
  const getById = (): Promise<Map<string, N>> =>
    (byId ??= getRoot().then((root) => {
      const m = new Map<string, N>();
      const stack = [root];
      while (stack.length > 0) {
        const n = stack.pop()!;
        m.set(n.id, n);
        for (const c of n.children ?? []) if (c) stack.push(c);
      }
      return m;
    }));
  return {
    get: async (id) => (await getById()).get(id),
    warm: () => {
      getById().catch(() => {
        byId = null;
      });
    },
  };
}
