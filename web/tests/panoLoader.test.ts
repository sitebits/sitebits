import { describe, expect, it, vi } from 'vitest';
import { MAX_INFLIGHT, PanoLoader, levelKey } from '../src/panoLoader';
import type { LevelRequest, LoaderEvent } from '../src/panoLoader';
import { FACES } from '../src/stations';
import type { FaceName } from '../src/stations';

interface Bmp { url: string; closed: boolean }
interface Tex { img: Bmp; disposed: boolean }

interface Opts {
  /** makeTexture wirft für diese URL (Fehler im Erfolgszweig). */
  makeThrows?: (url: string) => boolean;
  /** Ereignis-Rückruf des Verbrauchers wirft. */
  eventThrows?: (e: LoaderEvent) => boolean;
}

function setup(limitBytes = 1e12, honourAbort = true, opts: Opts = {}) {
  const pending = new Map<string, { resolve: (b: Bmp) => void; reject: (e: Error) => void; signal: AbortSignal }>();
  const calls: string[] = [];
  const images: Bmp[] = [];
  const textures: Tex[] = [];
  const events: LoaderEvent[] = [];
  const loader = new PanoLoader<Bmp, Tex>(
    {
      fetchImage: (url, signal) =>
        new Promise<Bmp>((resolve, reject) => {
          calls.push(url);
          pending.set(url, { resolve, reject, signal });
          if (honourAbort) signal.addEventListener('abort', () => reject(new DOMException('abgebrochen', 'AbortError')));
        }),
      makeTexture: (img) => {
        if (opts.makeThrows?.(img.url)) throw new Error('Textur kaputt');
        const t = { img, disposed: false };
        textures.push(t);
        return t;
      },
      discardImage: (img) => {
        img.closed = true;
      },
      disposeTexture: (t) => {
        t.disposed = true;
      },
      bytesOf: (lv) => lv * lv * 4,
    },
    limitBytes,
    (e) => {
      events.push(e);
      if (opts.eventThrows?.(e)) throw new Error('Verbraucher kaputt');
    },
  );
  const finish = (url: string): void => {
    const b = { url, closed: false };
    images.push(b);
    pending.get(url)!.resolve(b);
    pending.delete(url);
  };
  const fail = (url: string, msg = 'HTTP 404'): void => {
    pending.get(url)!.reject(new Error(msg));
    pending.delete(url);
  };
  return { loader, pending, calls, images, textures, events, finish, fail };
}

const flush = (): Promise<void> => new Promise((r) => setTimeout(r, 0));
const req = (station: string, level: number, faces: readonly FaceName[] = FACES): LevelRequest[] =>
  faces.map((face) => ({ station, face, level, url: `${station}/${face}/${level}.jpg` }));
async function drain(t: ReturnType<typeof setup>): Promise<void> {
  while (t.pending.size) {
    for (const u of [...t.pending.keys()]) t.finish(u);
    await flush();
  }
}

describe('PanoLoader', () => {
  it('höchstens MAX_INFLIGHT gleichzeitig, in Aufruferreihenfolge (kleinste Stufe zuerst)', async () => {
    const t = setup();
    t.loader.setStation('S1');
    t.loader.want([...req('S1', 512), ...req('S1', 2048, ['px'])]);
    expect(t.loader.inflight).toBe(MAX_INFLIGHT);
    expect(t.calls).toEqual(['S1/px/512.jpg', 'S1/nx/512.jpg', 'S1/py/512.jpg', 'S1/ny/512.jpg']);
    await drain(t);
    expect(t.loader.best('S1', 'px', [512, 2048])?.level).toBe(2048);
    expect(t.loader.best('S1', 'nz', [512, 2048])?.level).toBe(512);
    expect(t.events.filter((e) => e.type === 'loaded')).toHaveLength(7);
  });

  it('Standpunktwechsel bricht laufende Anfragen ab und gibt alte Texturen frei', async () => {
    const t = setup();
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512, ['px']));
    await drain(t);
    t.loader.want(req('S1', 2048, ['px']));
    const sig = t.pending.get('S1/px/2048.jpg')!.signal;
    t.loader.setStation('S2');
    expect(sig.aborted).toBe(true);
    expect(t.textures[0]!.disposed).toBe(true);
    expect(t.loader.keys()).toEqual([]);
    await flush();
    expect(t.events.some((e) => e.type === 'failed')).toBe(false); // Abbruch ist kein Fehler
  });

  it('verspätetes Ergebnis eines alten Standpunkts wird verworfen, nie angezeigt', async () => {
    const t = setup(1e12, false);
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512, ['px']));
    t.loader.setStation('S2');
    t.finish('S1/px/512.jpg');
    await flush();
    expect(t.images[0]!.closed).toBe(true);
    expect(t.loader.best('S1', 'px', [512])).toBeNull();
    expect(t.events).toEqual([]);
  });

  it('404 → einmal "failed", kein erneuter Versuch im selben Standpunkt', async () => {
    const t = setup();
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512, ['px']));
    t.fail('S1/px/512.jpg');
    await flush();
    expect(t.events).toEqual([{ type: 'failed', station: 'S1', face: 'px', level: 512, reason: 'HTTP 404' }]);
    t.loader.want(req('S1', 512, ['px']));
    expect(t.calls.filter((u) => u === 'S1/px/512.jpg')).toHaveLength(1);
    expect(t.loader.failures()).toEqual([levelKey('S1', 'px', 512)]);
  });

  it('Budget: gewünschte Stufen werden nie verdrängt; was nicht passt, ist "failed" (kein Pingpong)', async () => {
    const MiB = 2 ** 20;
    const t = setup(6 * MiB + 2 * 16 * MiB); // sechs 512er + zwei 2048er
    t.loader.setStation('S1');
    t.loader.want([...req('S1', 512), ...req('S1', 2048, ['px', 'nx', 'py'])]);
    await drain(t);
    expect(t.loader.usedBytes).toBeLessThanOrEqual(6 * MiB + 2 * 16 * MiB);
    expect(t.events.filter((e) => e.type === 'failed').map((e) => e.face)).toEqual(['py']);
    for (const f of FACES) expect(t.loader.best('S1', f, [512])).not.toBeNull();
    // Blick gedreht: nur noch "ny" braucht 2048 → die nicht mehr gewünschten 2048er dürfen weichen.
    t.loader.want([...req('S1', 512), ...req('S1', 2048, ['ny'])]);
    await drain(t);
    expect(t.loader.best('S1', 'ny', [512, 2048])?.level).toBe(2048);
    expect(t.loader.usedBytes).toBeLessThanOrEqual(6 * MiB + 2 * 16 * MiB);
  });

  it('reset() nach Kontextverlust: alles frei, danach wird neu geladen', async () => {
    const t = setup();
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512, ['px']));
    await drain(t);
    t.loader.reset();
    expect(t.textures[0]!.disposed).toBe(true);
    t.loader.want(req('S1', 512, ['px']));
    expect(t.calls.filter((u) => u === 'S1/px/512.jpg')).toHaveLength(2);
  });

  it('Budget-Fehlschlag klebt nicht: gleiche Anfrage lädt nicht neu, frei gewordener Platz schon', async () => {
    const MiB = 2 ** 20;
    const t = setup(6 * MiB + 2 * 16 * MiB);
    t.loader.setStation('S1');
    const three = [...req('S1', 512), ...req('S1', 2048, ['px', 'nx', 'py'])];
    t.loader.want(three);
    await drain(t);
    expect(t.loader.failures()).toEqual([levelKey('S1', 'py', 2048)]);
    t.loader.want(three); // unverändert: passt weiterhin nicht → kein erneuter Download (kein Pingpong)
    expect(t.calls.filter((u) => u === 'S1/py/2048.jpg')).toHaveLength(1);
    t.loader.want([...req('S1', 512), ...req('S1', 2048, ['py'])]); // px/nx nicht mehr gewünscht → Platz
    expect(t.calls.filter((u) => u === 'S1/py/2048.jpg')).toHaveLength(2);
    await drain(t);
    expect(t.loader.best('S1', 'py', [512, 2048])?.level).toBe(2048);
    expect(t.loader.failures()).toEqual([]);
  });

  it('404 bleibt gemerkt, auch wenn want() sich ändert (nur Budget-Fehlschläge werden neu bewertet)', async () => {
    const t = setup();
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512, ['px']));
    t.fail('S1/px/512.jpg');
    await flush();
    t.loader.want(req('S1', 512, ['px', 'nx']));
    expect(t.calls.filter((u) => u === 'S1/px/512.jpg')).toHaveLength(1);
  });

  it('nicht mehr gewünschte Downloads werden abgebrochen, ohne als Fehler zu gelten', async () => {
    const t = setup();
    t.loader.setStation('S1');
    t.loader.want([...req('S1', 512, ['px']), ...req('S1', 2048, ['nx'])]);
    const sig = t.pending.get('S1/nx/2048.jpg')!.signal;
    t.loader.want(req('S1', 512, ['px']));
    expect(sig.aborted).toBe(true);
    expect(t.loader.inflight).toBe(1);
    await flush();
    expect(t.events).toEqual([]);
    expect(t.loader.failures()).toEqual([]);
    t.loader.want([...req('S1', 512, ['px']), ...req('S1', 2048, ['nx'])]); // wieder gewünscht → neu laden
    expect(t.calls.filter((u) => u === 'S1/nx/2048.jpg')).toHaveLength(2);
    await drain(t);
    expect(t.loader.best('S1', 'nx', [512, 2048])?.level).toBe(2048);
  });

  it('verspätete Antwort eines abgebrochenen Downloads stört den neuen für dieselbe Stufe nicht', async () => {
    const t = setup(1e12, false); // fetch ignoriert das Abbruchsignal (wie createImageBitmap)
    t.loader.setStation('S1');
    t.loader.want(req('S1', 2048, ['px']));
    const old = t.pending.get('S1/px/2048.jpg')!;
    t.loader.want([]);
    t.loader.want(req('S1', 2048, ['px']));
    expect(t.loader.inflight).toBe(1);
    const late = { url: 'S1/px/2048.jpg', closed: false };
    old.resolve(late);
    await flush();
    expect(late.closed).toBe(true);
    expect(t.loader.inflight).toBe(1); // der neue läuft weiter
    expect(t.loader.best('S1', 'px', [2048])).toBeNull();
    old.reject(new Error('zu spät')); // schon erledigt — darf nichts mehr bewirken
    await drain(t);
    expect(t.loader.best('S1', 'px', [2048])?.level).toBe(2048);
    expect(t.events).toEqual([{ type: 'loaded', station: 'S1', face: 'px', level: 2048 }]);
  });

  it('Ausnahme in makeTexture: Bild geschlossen, "failed", der nächste Download startet', async () => {
    const t = setup(1e12, true, { makeThrows: (u) => u === 'S1/px/512.jpg' });
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512)); // 6 Anfragen, 4 laufen
    expect(t.loader.inflight).toBe(MAX_INFLIGHT);
    t.finish('S1/px/512.jpg');
    await flush();
    expect(t.images[0]!.closed).toBe(true);
    expect(t.events).toEqual([{ type: 'failed', station: 'S1', face: 'px', level: 512, reason: 'Textur kaputt' }]);
    expect(t.loader.inflight).toBe(MAX_INFLIGHT); // Platz nicht verloren: S1/pz/512 läuft nach
    expect(t.calls).toContain('S1/pz/512.jpg');
    expect(t.loader.failures()).toEqual([levelKey('S1', 'px', 512)]);
  });

  it('Ausnahme im Ereignis-Rückruf: Warteschlange läuft weiter, Textur bleibt', async () => {
    const log = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const t = setup(1e12, true, { eventThrows: () => true });
    t.loader.setStation('S1');
    t.loader.want(req('S1', 512));
    t.finish('S1/px/512.jpg');
    await flush();
    expect(t.calls).toContain('S1/pz/512.jpg');
    expect(t.loader.best('S1', 'px', [512])?.level).toBe(512);
    await drain(t);
    expect(t.loader.keys()).toHaveLength(6);
    expect(log).toHaveBeenCalled(); // sichtbar in der Konsole, nicht verschluckt
    log.mockRestore();
  });
});
