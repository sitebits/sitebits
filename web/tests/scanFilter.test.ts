// Im Panorama nur Punkte des eigenen Scans picken — reine Logik.
import { describe, expect, it } from 'vitest';
import { afterEach, vi } from 'vitest';
import { classifyHit, fineWindow, planCoarse, scanFilterFor, scanFilterHint, validPointSourceId, verdictsWithin } from '../src/scanFilter';
import type { HitVerdict } from '../src/scanFilter';
import type { Ray, Vec3 } from '../src/pickMath';

describe('validPointSourceId', () => {
  it.each([1, 3, 65535])('%s gilt', (x) => expect(validPointSourceId(x)).toBe(true));
  it.each([0, -1, 2.5, 65536, NaN, '3', null, undefined])('%s gilt als unbekannt', (x) =>
    expect(validPointSourceId(x)).toBe(false),
  );
});

describe('scanFilterFor (Aktivierung)', () => {
  it('Orbit (keine Station) → kein Filter, kein Vermerk', () => {
    expect(scanFilterFor(true, null)).toBeNull();
  });
  it('Flag gesetzt und Station mit ID → an', () => {
    expect(scanFilterFor(true, { pointSourceId: 3 })).toEqual({ mode: 'on', pointSourceId: 3 });
  });
  it.each([undefined, false])('Flag %s (Altpaket) → off-legacy, auch mit Stations-ID', (flag) => {
    expect(scanFilterFor(flag, { pointSourceId: 3 })).toEqual({ mode: 'off-legacy' });
  });
  it('Flag gesetzt, Station ohne ID (Carry-over) → off-unknown', () => {
    expect(scanFilterFor(true, { pointSourceId: null })).toEqual({ mode: 'off-unknown' });
  });
});

describe('scanFilterHint (Dauerhinweis je Modus)', () => {
  it('Altpaket → fester Hinweistext', () => {
    expect(scanFilterHint('off-legacy')).toBe('Paket ohne Scan-Zuordnung — Messpunkte können aus anderen Scans stammen');
  });
  it('Station ohne pointSourceId → eigener Text über den Standpunkt, nicht über das Paket', () => {
    expect(scanFilterHint('off-unknown')).toBe('Standpunkt ohne Scan-Zuordnung — Messpunkte können aus anderen Scans stammen');
  });
  it('Filter an → kein Hinweis', () => {
    expect(scanFilterHint('on')).toBeNull();
  });
});

describe('classifyHit (grobe Kandidaten per Dekodierung prüfen)', () => {
  // Knoten mit Ursprung (100,0,0): Punkt 0 = (101,0,0) Scan 2, Punkt 1 = (102,0,0) Scan 3.
  const chunk = { origin: [100, 0, 0] as Vec3, positions: new Float32Array([1, 0, 0, 2, 0, 0]), ids: new Uint16Array([2, 3]) };
  it('eigener Scan und Lage passt → own', () => {
    expect(classifyHit(chunk, 0, [101, 0, 0.0004], 2)).toBe('own');
  });
  it('fremder Scan → foreign', () => {
    expect(classifyHit(chunk, 1, [102, 0, 0], 2)).toBe('foreign');
  });
  it('Lage passt nicht zum Index (Anzeige ≠ Dekodierung) → unverified, nie own', () => {
    expect(classifyHit(chunk, 0, [101.01, 0, 0], 2)).toBe('unverified');
  });
  it('Index außerhalb, kein Knoten, keine IDs → unverified', () => {
    expect(classifyHit(chunk, 5, [101, 0, 0], 2)).toBe('unverified');
    expect(classifyHit(null, 0, [101, 0, 0], 2)).toBe('unverified');
    expect(classifyHit({ origin: chunk.origin, positions: chunk.positions }, 0, [101, 0, 0], 2)).toBe('unverified');
  });
});

describe('fineWindow (Tiefenfenster der feinsten Stufe)', () => {
  it('ohne Scanfilter bzw. mit eigenem Cursor-Kandidaten: vorderste Fläche − Lücke … Cursor-Tiefe + 3 Lücken', () => {
    const w = fineWindow(4, 5);
    expect(w.minDepth).toBeCloseTo(4 - 0.08);
    expect(w.maxDepth).toBeCloseTo(5 + 3 * 0.1);
  });
  it('Cursor-Kandidat vor der vordersten Fläche: Obergrenze ab der vordersten Fläche', () => {
    expect(fineWindow(4, 3).maxDepth).toBeCloseTo(4 + 3 * 0.06);
  });
  it('kein eigener Kandidat unter dem Cursor: bis zur Fernschranke (fremder Verdecker legt das Fenster nicht fest)', () => {
    const w = fineWindow(1.33, undefined, 40);
    expect(w.minDepth).toBeCloseTo(1.28);
    expect(w.maxDepth).toBe(40);
  });
  it('Fernschranke vor der vordersten Fläche → wenigstens die übliche Obergrenze', () => {
    expect(fineWindow(4, undefined, 2).maxDepth).toBeCloseTo(4 + 3 * 0.08);
  });
});

describe('verdictsWithin (Prüfphase unter der Frist ab Klick, Review 12c)', () => {
  afterEach(() => vi.useRealTimers());
  /** Wie ein getNodeData mit Scan-IDs, das erst nach ``ms`` antwortet (gedrosselter Fetch). */
  const delayed = (v: HitVerdict, ms: number) => (): Promise<HitVerdict> => new Promise((r) => setTimeout(() => r(v), ms));

  it('alle rechtzeitig → alle Urteile', async () => {
    vi.useFakeTimers();
    const p = verdictsWithin([delayed('own', 10), delayed('foreign', 20)], (f) => f(), 100);
    await vi.advanceTimersByTimeAsync(20);
    expect(await p).toEqual(['own', 'foreign']);
  });
  it('Frist überschritten → offene Urteile „unverified“, spätere Antworten ändern nichts', async () => {
    vi.useFakeTimers();
    const p = verdictsWithin([delayed('own', 10), delayed('own', 5000)], (f) => f(), 1500);
    await vi.advanceTimersByTimeAsync(1500);
    const out = await p;
    expect(out).toEqual(['own', 'unverified']);
    await vi.advanceTimersByTimeAsync(5000);
    expect(out).toEqual(['own', 'unverified']);
  });
  it('Frist schon verbraucht → sofort, alles offen ungeprüft', async () => {
    vi.useFakeTimers();
    const p = verdictsWithin([delayed('own', 1)], (f) => f(), -20);
    await vi.advanceTimersByTimeAsync(0);
    expect(await p).toEqual(['unverified']);
  });
  it('Fehler beim Dekodieren → unverified', async () => {
    const out = await verdictsWithin([0], () => Promise.reject(new Error('x')), 100);
    expect(out).toEqual(['unverified']);
  });
});

describe('planCoarse (grobe Stufe mit/ohne Scanfilter)', () => {
  const RAY: Ray = { origin: [0, 0, 0], dir: [1, 0, 0] };
  const K = 0.001; // 1 mm je m Tiefe und Pixel
  // Fremder Zaun in 1,33 m direkt unter dem Cursor, eigene Wand in 6 m am Rand des 16-px-Kreises (10 px daneben).
  const fence: Vec3[] = [[1.33, 0, 0], [1.331, 0.001, 0]];
  const wall: Vec3[] = [[6, 0.06, 0]];
  const pts = [...fence, ...wall];

  it('fremde Vorderfläche ohne eigenen Kandidaten unter dem Cursor → kein grober Ersatz, erweitertes Fenster', () => {
    const plan = planCoarse(pts, ['foreign', 'foreign', 'own'], RAY, K, 4, 40);
    expect(plan.wide).toEqual(wall);
    expect(plan.near).toEqual([]);
    expect(plan.foreign).toBe(2);
    expect(plan.front!.coarse).toBeNull();
    expect(plan.front!.nearDepth).toBeUndefined();
    expect(plan.front!.frontDepth).toBeCloseTo(1.33); // Untergrenze aus allen Kandidaten
    expect(plan.front!.minDepth).toBeCloseTo(1.28);
    expect(plan.front!.maxDepth).toBe(40);
  });
  it('ohne Filter (Orbit/Altpaket) wie bisher: Zaun ist Cursor-Kandidat und grober Ersatz, Fenster am Zaun', () => {
    const plan = planCoarse(pts, null, RAY, K, 4, 40);
    expect(plan.wide).toHaveLength(3);
    expect(plan.foreign).toBe(0);
    expect(plan.front!.coarse).toEqual(fence[0]);
    expect(plan.front!.nearDepth).toBeCloseTo(1.33);
    expect(plan.front!.maxDepth).toBeCloseTo(1.331 + 3 * 0.05, 2);
  });
  it('eigener Cursor-Kandidat → übliche Obergrenze, grober Ersatz nur eigen', () => {
    const own: Vec3 = [5, 0.001, 0];
    const plan = planCoarse([...fence, own], ['foreign', 'foreign', 'own'], RAY, K, 4, 40);
    expect(plan.front!.coarse).toEqual(own);
    expect(plan.front!.nearDepth).toBeCloseTo(5);
    expect(plan.front!.maxDepth).toBeCloseTo(5 + 3 * 0.1);
  });
  it('alles ungeprüft (Frist) → kein eigener Kandidat, kein Ersatz; nichts unter dem Klick → keine Fläche', () => {
    const plan = planCoarse(fence, ['unverified', 'unverified'], RAY, K, 4, 40);
    expect(plan.unverified).toBe(2);
    expect(plan.front!.coarse).toBeNull();
    expect(planCoarse([], [], RAY, K, 4, 40).front).toBeNull();
  });
});
