import { describe, expect, it } from 'vitest';
import { controllerTyp, kanten, lies, PAD_VORGABE, stickSchritt, TOTZONE, totzone } from '../src/gamepad';
import type { PadAktion, PadRoh } from '../src/gamepad';

const pad = (axes: number[] = [0, 0, 0, 0], gedrueckt: Record<number, number> = {}): PadRoh => ({
  id: 'Test Pad (STANDARD GAMEPAD)',
  axes,
  buttons: Array.from({ length: 17 }, (_, i) => ({ pressed: gedrueckt[i] === 1, value: gedrueckt[i] ?? 0 })),
});
const O = { invertY: false };

describe('Totzone', () => {
  it('ist 0,15', () => expect(TOTZONE).toBe(0.15));
  it('Werte innerhalb der Totzone ergeben 0', () => {
    expect(totzone(0.1, 0)).toEqual([0, 0]);
    expect(totzone(0, -0.14)).toEqual([0, 0]);
  });
  it('voller Ausschlag bleibt 1', () => {
    expect(totzone(1, 0)).toEqual([1, 0]);
    expect(totzone(0, -1)[1]).toBeCloseTo(-1, 10);
  });
  it('diagonal unter 0,15 Betrag ergibt 0', () => {
    expect(totzone(0.1, 0.1)).toEqual([0, 0]);
  });
  it('skaliert radial neu auf 0…1', () => {
    const [x, y] = totzone(0.575, 0);
    expect(x).toBeCloseTo(0.5, 10);
    expect(y).toBe(0);
  });
  it('klemmt Beträge über 1 und ignoriert nicht endliche Werte', () => {
    expect(Math.hypot(...totzone(1, 1))).toBeCloseTo(1, 10);
    expect(totzone(NaN, 0.5)).toEqual([0, 0]);
    expect(totzone(Infinity, 0)).toEqual([0, 0]);
  });
});

describe('lies', () => {
  it('LY −1 ergibt vor 1', () => {
    expect(lies(pad([0, -1, 0, 0]), PAD_VORGABE, O).vor).toBeCloseTo(1, 10);
  });
  it('LX rechts ergibt seit 1', () => {
    expect(lies(pad([1, 0, 0, 0]), PAD_VORGABE, O).seit).toBeCloseTo(1, 10);
  });
  it('rechter Stick nach rechts dreht nach rechts (dreh negativ), nach oben schaut nach oben', () => {
    const l = lies(pad([0, 0, 1, 0]), PAD_VORGABE, O);
    expect(l.dreh).toBeCloseTo(-1, 10);
    expect(lies(pad([0, 0, 0, -1]), PAD_VORGABE, O).neig).toBeCloseTo(1, 10);
  });
  it('invertY kehrt das Neigen um', () => {
    expect(lies(pad([0, 0, 0, -1]), PAD_VORGABE, { invertY: true }).neig).toBeCloseTo(-1, 10);
  });
  it('Stick in der Totzone bewegt nichts', () => {
    const l = lies(pad([0.1, -0.1, 0.1, 0.1]), PAD_VORGABE, O);
    expect([l.vor, l.seit, l.dreh, l.neig]).toEqual([0, 0, 0, 0]);
  });
  it('RT mit value 0,8 zählt als setzen gedrückt, 0,4 nicht', () => {
    expect(lies(pad([0, 0, 0, 0], { 7: 0.8 }), PAD_VORGABE, O).gedrueckt.has('setzen')).toBe(true);
    expect(lies(pad([0, 0, 0, 0], { 7: 0.4 }), PAD_VORGABE, O).gedrueckt.has('setzen')).toBe(false);
  });
  it('folgt einer geänderten Belegung', () => {
    const b: Record<PadAktion, number> = { ...PAD_VORGABE, 'werkzeug-naechstes': 2 };
    const l = lies(pad([0, 0, 0, 0], { 2: 1 }), b, O);
    expect(l.gedrueckt.has('werkzeug-naechstes')).toBe(true);
    expect(lies(pad([0, 0, 0, 0], { 5: 1 }), b, O).gedrueckt.has('werkzeug-naechstes')).toBe(false);
  });
  it('nicht belegte Aktionen (−1) werden nie ausgelöst', () => {
    const b = { ...PAD_VORGABE, zurueck: -1 };
    const g = lies(pad(), b, O).gedrueckt;
    expect(g.has('zurueck')).toBe(false);
  });
  it('zu kurze Achsen- und Knopflisten liefern Ruhe statt Fehler', () => {
    const l = lies({ id: 'x', axes: [], buttons: [] }, PAD_VORGABE, O);
    expect([l.vor, l.seit, l.dreh, l.neig]).toEqual([0, 0, 0, 0]);
    expect(l.gedrueckt.size).toBe(0);
  });
});

describe('Vorgabe', () => {
  it('entspricht dem Standard-Mapping, X bleibt frei', () => {
    expect(PAD_VORGABE).toEqual({
      zielen: 6, setzen: 7, 'werkzeug-voriges': 4, 'werkzeug-naechstes': 5, befund: 3,
      'standpunkt-voriger': 14, 'standpunkt-naechster': 15, hoch: 12, runter: 13, bestaetigen: 0, zurueck: 1, karte: 9,
    });
    expect(Object.values(PAD_VORGABE)).not.toContain(2);
  });
});

describe('kanten', () => {
  const s = (...a: PadAktion[]): ReadonlySet<PadAktion> => new Set(a);
  it('liefert Drücken und Loslassen genau einmal', () => {
    expect(kanten(s(), s('setzen'))).toEqual({ runter: ['setzen'], hoch: [] });
    expect(kanten(s('setzen'), s('setzen'))).toEqual({ runter: [], hoch: [] });
    expect(kanten(s('setzen'), s())).toEqual({ runter: [], hoch: ['setzen'] });
  });
});

describe('controllerTyp', () => {
  it('erkennt Xbox, PlayStation und andere', () => {
    expect(controllerTyp('Xbox Wireless Controller (STANDARD GAMEPAD Vendor: 045e Product: 0b13)')).toBe('xbox');
    expect(controllerTyp('DualSense Wireless Controller')).toBe('playstation');
    expect(controllerTyp('Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)')).toBe('playstation');
    expect(controllerTyp('Test Pad (STANDARD GAMEPAD)')).toBe('generisch');
    expect(controllerTyp('')).toBe('generisch');
  });
});

describe('stickSchritt', () => {
  it('löst einmal aus und erst nach Rückkehr unter 0,3 wieder', () => {
    let r = stickSchritt(0.6, true);
    expect(r).toEqual({ schritt: 1, bereit: false });
    r = stickSchritt(0.9, r.bereit);
    expect(r).toEqual({ schritt: 0, bereit: false });
    r = stickSchritt(0.4, r.bereit); // zwischen 0,3 und 0,5: noch nicht scharf
    expect(r).toEqual({ schritt: 0, bereit: false });
    r = stickSchritt(0.2, r.bereit);
    expect(r).toEqual({ schritt: 0, bereit: true });
    expect(stickSchritt(-0.7, r.bereit)).toEqual({ schritt: -1, bereit: false });
  });
  it('unter 0,5 löst nichts aus', () => {
    expect(stickSchritt(0.49, true)).toEqual({ schritt: 0, bereit: true });
  });
});
