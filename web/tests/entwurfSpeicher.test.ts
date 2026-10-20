// Browser-Speicher für lokale Befundentwürfe mit einer nachgebauten IndexedDB: Das Öffnen endet nie mit einer
// offenen, unbeaufsichtigten Verbindung (späte Erfolge werden sofort geschlossen, eine Blockade wartet auf den
// Erfolg oder die Frist), und ein ungültiger Datensatz ist von „kein Entwurf“ unterscheidbar.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { ENTWURF_STORE } from '../src/befundEntwurf';
import type { Entwurf } from '../src/befundEntwurf';
import { oeffneEntwurfSpeicher } from '../src/entwurfSpeicher';

type Handler = (() => void) | null;

/** Datenbank mit einer Ablage (Map); Anfragen und Transaktionen enden im nächsten Mikrotask. */
function falscheDb(daten = new Map<string, unknown>()) {
  const db = {
    objectStoreNames: { contains: (n: string) => n === ENTWURF_STORE },
    close: vi.fn(),
    onversionchange: null as Handler,
    onclose: null as Handler,
    transaction: () => {
      const tx = { oncomplete: null as Handler, onerror: null as Handler, onabort: null as Handler, objectStore: () => ablage };
      let offen = 0;
      const fertig = (): void => {
        if (--offen === 0) queueMicrotask(() => tx.oncomplete?.());
      };
      const anfrage = <T>(arbeit: () => T) => {
        offen++;
        const r = { result: undefined as T | undefined, onsuccess: null as Handler };
        queueMicrotask(() => {
          r.result = arbeit();
          r.onsuccess?.();
          fertig();
        });
        return r;
      };
      const ablage = {
        get: (k: string) => anfrage(() => daten.get(k)),
        put: (v: unknown, k: string) => anfrage(() => void daten.set(k, v)),
        delete: (k: string) => anfrage(() => void daten.delete(k)),
      };
      return tx;
    },
  };
  return { db, daten };
}

/** Öffnen-Anfrage, deren Ereignisse der Test auslöst. */
function fabrik(db: unknown) {
  const r = { result: db, transaction: { abort: vi.fn() }, onupgradeneeded: null as Handler, onsuccess: null as Handler, onerror: null as Handler, onblocked: null as Handler };
  const idb = { open: vi.fn(() => r) } as unknown as IDBFactory;
  return { idb, r };
}

const entwurf = (over: Partial<Entwurf> = {}): Entwurf => ({
  schema: 1, schluessel: '7|kirche', zeit: 1_759_300_000_000, punkt: [1, 2, 3], station: null,
  ansicht: 'cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000', poiId: 'P-1',
  formular: { title: 'Riss', category: 'Mangel', note: '' }, ...over,
});

afterEach(() => {
  vi.useRealTimers();
});

describe('oeffneEntwurfSpeicher: Öffnen', () => {
  it('blockiert, dann Erfolg: Speicher ist nutzbar (eine Blockade bricht nicht ab)', async () => {
    const { db } = falscheDb();
    const { idb, r } = fabrik(db);
    const p = oeffneEntwurfSpeicher(idb);
    r.onblocked?.();
    r.onsuccess?.();
    expect(await p).not.toBeNull();
    expect(db.close).not.toHaveBeenCalled();
  });
  it('dauerhaft blockiert: nach der Frist kein Speicher', async () => {
    vi.useFakeTimers();
    const { db } = falscheDb();
    const { idb, r } = fabrik(db);
    let ergebnis: unknown = 'offen';
    void oeffneEntwurfSpeicher(idb).then((s) => {
      ergebnis = s;
    });
    r.onblocked?.();
    await vi.advanceTimersByTimeAsync(2999);
    expect(ergebnis).toBe('offen');
    await vi.advanceTimersByTimeAsync(1);
    expect(ergebnis).toBeNull();
  });
  it('Erfolg erst nach der Frist: die späte Verbindung wird sofort wieder geschlossen', async () => {
    vi.useFakeTimers();
    const { db } = falscheDb();
    const { idb, r } = fabrik(db);
    const p = oeffneEntwurfSpeicher(idb);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await p).toBeNull();
    r.onsuccess?.();
    expect(db.close).toHaveBeenCalledTimes(1);
  });
  it('Anlegen erst nach der Frist (Datenbank inzwischen gelöscht): wird abgebrochen, nichts neu angelegt', async () => {
    vi.useFakeTimers();
    const { db } = falscheDb();
    const anlegen = vi.fn();
    const { idb, r } = fabrik({ ...db, objectStoreNames: { contains: () => false }, createObjectStore: anlegen });
    const p = oeffneEntwurfSpeicher(idb);
    await vi.advanceTimersByTimeAsync(3000);
    expect(await p).toBeNull();
    r.onupgradeneeded?.();
    expect(r.transaction.abort).toHaveBeenCalledTimes(1);
    expect(anlegen).not.toHaveBeenCalled();
  });
});

describe('oeffneEntwurfSpeicher: Lesen', () => {
  const offen = async (daten: Map<string, unknown>) => {
    const { db } = falscheDb(daten);
    const { idb, r } = fabrik(db);
    const p = oeffneEntwurfSpeicher(idb);
    r.onsuccess?.();
    const s = await p;
    if (!s) throw new Error('kein Speicher');
    return s;
  };
  it('kein Datensatz → null', async () => {
    expect(await (await offen(new Map())).lies('7|kirche')).toBeNull();
  });
  it('gültiger Datensatz → Entwurf (Bild nur als Blob)', async () => {
    const s = await offen(new Map([['7|kirche', { entwurf: entwurf(), bild: 'kein Blob' }]]));
    expect(await s.lies('7|kirche')).toEqual({ entwurf: entwurf(), bild: null });
  });
  it('ungültiger oder fremder Datensatz → „ungueltig“ (vorhanden, aber nicht anzubieten)', async () => {
    const s = await offen(new Map<string, unknown>([
      ['7|kirche', { entwurf: { ...entwurf(), punkt: [1, 2] }, bild: null }],
      ['8|kirche', { entwurf: entwurf(), bild: null }],
      ['9|kirche', 'Unsinn'],
    ]));
    expect(await s.lies('7|kirche')).toBe('ungueltig');
    expect(await s.lies('8|kirche')).toBe('ungueltig');
    expect(await s.lies('9|kirche')).toBe('ungueltig');
  });
});
