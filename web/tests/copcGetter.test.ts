import { describe, expect, it } from 'vitest';
import { createGate, createPrefetchingGetter, parseEvlrInfo } from '../src/copcGetter';

const SIZE = 4000;
const EVLR = 3000;

/** Künstliche LAS-1.4-Datei: Byte i hat den Wert i % 251, Header mit evlrOffset/evlrCount. */
function makeFile(evlrOffset = EVLR, evlrCount = 1, minor = 4): Uint8Array {
  const f = new Uint8Array(SIZE).map((_, i) => i % 251);
  f.set([0x4c, 0x41, 0x53, 0x46], 0); // 'LASF'
  f[24] = 1;
  f[25] = minor;
  const dv = new DataView(f.buffer);
  dv.setBigUint64(235, BigInt(evlrOffset), true);
  dv.setUint32(243, evlrCount, true);
  return f;
}

function fakeFetch(file: Uint8Array, opts: { failTail?: boolean; truncateTail?: boolean } = {}) {
  const calls: [number, number][] = [];
  const fetchRange = async (begin: number, end: number): Promise<Uint8Array> => {
    calls.push([begin, end]);
    const isTail = end === file.length && begin > 0;
    if (isTail && opts.failTail) throw new Error('Netzfehler');
    if (isTail && opts.truncateTail) return file.slice(begin, end - 1);
    return file.slice(begin, Math.min(end, file.length));
  };
  return { calls, fetchRange };
}

describe('parseEvlrInfo', () => {
  it('liest evlrOffset und evlrCount aus LAS 1.4', () => {
    expect(parseEvlrInfo(makeFile(1234, 2))).toEqual({ evlrOffset: 1234, evlrCount: 2 });
  });
  it('null bei LAS < 1.4, fremder Signatur oder zu kurzem Puffer', () => {
    expect(parseEvlrInfo(makeFile(EVLR, 1, 2))).toBeNull();
    const bad = makeFile();
    bad[0] = 0;
    expect(parseEvlrInfo(bad)).toBeNull();
    expect(parseEvlrInfo(makeFile().slice(0, 200))).toBeNull();
  });
});

describe('createPrefetchingGetter', () => {
  it('lädt den EVLR-Bereich in einer Anfrage und liefert korrekte Bytes (end exklusiv)', async () => {
    const file = makeFile();
    const { calls, fetchRange } = fakeFetch(file);
    const get = createPrefetchingGetter(fetchRange, SIZE);
    await get(0, 1000); // Header-Block wie copc.js (0…65535)
    const parts = await Promise.all([get(EVLR, EVLR + 60), get(3500, 3600), get(3900, 4000), get(3060, 3061)]);
    expect(parts.map((p) => [...p])).toEqual([
      [...file.slice(EVLR, EVLR + 60)],
      [...file.slice(3500, 3600)],
      [...file.slice(3900, 4000)],
      [file[3060]],
    ]);
    expect(calls).toEqual([
      [0, 1000],
      [EVLR, SIZE],
    ]);
  });

  it('Punktdaten vor evlrOffset gehen direkt ans Netz', async () => {
    const { calls, fetchRange } = fakeFetch(makeFile());
    const get = createPrefetchingGetter(fetchRange, SIZE);
    await get(0, 1000);
    await get(1000, 2000);
    await get(2990, 3010); // reicht über evlrOffset hinaus → nicht vollständig im Vorab-Bereich
    expect(calls).toEqual([
      [0, 1000],
      [1000, 2000],
      [2990, 3010],
    ]);
  });

  it('liefert Kopien: Ablösen eines Ergebnisses beschädigt den Zwischenspeicher nicht', async () => {
    const file = makeFile();
    const { calls, fetchRange } = fakeFetch(file);
    const get = createPrefetchingGetter(fetchRange, SIZE);
    await get(0, 1000);
    const a = await get(3100, 3200);
    structuredClone(a.buffer, { transfer: [a.buffer] }); // wie postMessage an den Worker
    expect([...(await get(3100, 3200))]).toEqual([...file.slice(3100, 3200)]);
    expect(calls).toHaveLength(2); // weiterhin aus dem Speicher, kein Rückfall auf Einzelanfragen
  });

  it('fällt bei Fehler oder falscher Länge des Vorabladens auf Einzelanfragen zurück', async () => {
    for (const opt of [{ failTail: true }, { truncateTail: true }]) {
      const file = makeFile();
      const { calls, fetchRange } = fakeFetch(file, opt);
      const get = createPrefetchingGetter(fetchRange, SIZE);
      await get(0, 1000);
      expect([...(await get(3100, 3200))]).toEqual([...file.slice(3100, 3200)]);
      expect([...(await get(3300, 3400))]).toEqual([...file.slice(3300, 3400)]);
      expect(calls).toEqual([
        [0, 1000],
        [EVLR, SIZE],
        [3100, 3200],
        [3300, 3400],
      ]);
    }
  });

  it('kein Vorabladen ohne EVLRs, ohne Header-Zugriff oder über der Größengrenze', async () => {
    const cases: { file: Uint8Array; max?: number; header: boolean }[] = [
      { file: makeFile(EVLR, 0), header: true },
      { file: makeFile(), header: false },
      { file: makeFile(), max: 500, header: true },
    ];
    for (const c of cases) {
      const { calls, fetchRange } = fakeFetch(c.file);
      const get = createPrefetchingGetter(fetchRange, SIZE, c.max);
      if (c.header) await get(0, 1000);
      await get(3100, 3200);
      expect(calls.at(-1)).toEqual([3100, 3200]);
      expect(calls.some(([b, e]) => b === EVLR && e === SIZE)).toBe(false);
    }
  });
});

describe('createGate (Punktwolken-Nachladen während des Panorama-Sprungs anhalten)', () => {
  const flush = () => new Promise((r) => setTimeout(r, 0));

  it('offen: wait() hält nicht auf', async () => {
    const g = createGate();
    let done = false;
    void g.wait().then(() => (done = true));
    await flush();
    expect(done).toBe(true);
    expect(g.closed).toBe(false);
  });

  it('geschlossen: wait() wartet bis open(), dann laufen alle Wartenden weiter', async () => {
    const g = createGate();
    g.close();
    const order: string[] = [];
    void g.wait().then(() => order.push('a'));
    void g.wait().then(() => order.push('b'));
    await flush();
    expect(order).toEqual([]);
    g.open();
    await flush();
    expect(order).toEqual(['a', 'b']);
    expect(g.closed).toBe(false);
  });

  it('close()/open() sind idempotent; ein erneutes close() hält neue Anfragen wieder an', async () => {
    const g = createGate();
    g.close();
    g.close();
    let n = 0;
    void g.wait().then(() => n++);
    g.open();
    g.open();
    await flush();
    expect(n).toBe(1);
    g.close();
    void g.wait().then(() => n++);
    await flush();
    expect(n).toBe(1);
    g.open();
    await flush();
    expect(n).toBe(2);
  });

  it('als Vorschalter vor fetchRange: geschlossen geht keine Anfrage ans Netz', async () => {
    const file = makeFile();
    const { calls, fetchRange } = fakeFetch(file);
    const g = createGate();
    const gated = async (b: number, e: number) => {
      await g.wait();
      return fetchRange(b, e);
    };
    g.close();
    const p = gated(100, 200);
    await flush();
    expect(calls).toEqual([]);
    g.open();
    expect(Array.from(await p)).toEqual(Array.from(file.slice(100, 200)));
    expect(calls).toEqual([[100, 200]]);
  });
});
