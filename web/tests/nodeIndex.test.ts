// Gemerkte Knotenkarte der Strahlsuche (rayPick.ts): die Prüfung der groben Kandidaten ruft
// nodeById für alle Kandidaten gleichzeitig auf — Hierarchie holen und Karte bauen darf nur einmal passieren.
import { describe, expect, it, vi } from 'vitest';
import { memoNodeIndex } from '../src/nodeIndex';

type N = { id: string; children?: (N | undefined)[] };

const tree = (): N => ({
  id: '0-0-0-0',
  children: [{ id: '1-0-0-0', children: [{ id: '2-0-0-0' }, undefined] }, { id: '1-1-0-0' }],
});

describe('memoNodeIndex', () => {
  it('N gleichzeitige Aufrufe → Hierarchie genau einmal geholt, alle Ergebnisse richtig', async () => {
    const getRoot = vi.fn(() => Promise.resolve(tree()));
    const nodeById = memoNodeIndex(getRoot).get;
    const ids = ['2-0-0-0', '1-1-0-0', '0-0-0-0', 'fehlt', ...Array.from({ length: 50 }, () => '1-0-0-0')];
    const out = await Promise.all(ids.map((id) => nodeById(id)));
    expect(getRoot).toHaveBeenCalledTimes(1);
    expect(out.map((n) => n?.id)).toEqual(ids.map((id) => (id === 'fehlt' ? undefined : id)));
  });
  it('spätere Aufrufe nutzen dieselbe Karte', async () => {
    const getRoot = vi.fn(() => Promise.resolve(tree()));
    const nodeById = memoNodeIndex(getRoot).get;
    await nodeById('0-0-0-0');
    expect((await nodeById('2-0-0-0'))?.id).toBe('2-0-0-0');
    expect(getRoot).toHaveBeenCalledTimes(1);
  });
  it('warm baut die Karte vorab; ein Fehler dabei wird beim nächsten Aufruf neu versucht', async () => {
    const getRoot = vi.fn<() => Promise<N>>().mockRejectedValueOnce(new Error('x')).mockResolvedValue(tree());
    const index = memoNodeIndex(getRoot);
    index.warm();
    await vi.waitFor(() => expect(getRoot).toHaveBeenCalledTimes(1));
    await new Promise((r) => setTimeout(r, 0));
    expect((await index.get('1-1-0-0'))?.id).toBe('1-1-0-0');
    expect(getRoot).toHaveBeenCalledTimes(2);
  });
});
