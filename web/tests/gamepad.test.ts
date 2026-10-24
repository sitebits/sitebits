// Eingabeschicht Stufe 1: Totzone, Ansprechkurve, Auslöser, Aktionen und Flanken, Controllertyp.
import { describe, expect, it } from 'vitest';
import {
  ausloeser, AUSSEN, controllerTyp, kanten, KNOPF_TOUCHPAD, kurve, lies, PAD_AKTIONEN, PAD_KNOPF_MAX, PAD_VORGABE, stick, stickSchritt,
  TOTZONE, totzone, TRIGGER_TOTZONE,
} from '../src/gamepad';
import type { PadAktion, PadRoh } from '../src/gamepad';

const pad = (axes: number[] = [0, 0, 0, 0], gedrueckt: Record<number, number> = {}, n = 18): PadRoh => ({
  id: 'Test Pad (STANDARD GAMEPAD)',
  axes,
  buttons: Array.from({ length: n }, (_, i) => ({ pressed: (gedrueckt[i] ?? 0) >= 0.5, value: gedrueckt[i] ?? 0 })),
});
const O = { invertY: false };

describe('Totzone', () => {
  it('ist radial 0,15 mit Sättigung ab 0,95', () => {
    expect(TOTZONE).toBe(0.15);
    expect(AUSSEN).toBe(0.95);
  });
  it('Werte innerhalb der Totzone ergeben 0, auch diagonal', () => {
    expect(totzone(0.1, 0)).toEqual([0, 0]);
    expect(totzone(0.1, 0.1)).toEqual([0, 0]); // Betrag 0,141
  });
  it('radial statt je Achse: diagonal knapp außerhalb wirkt in beide Richtungen', () => {
    const [x, y] = totzone(0.12, 0.12); // Betrag 0,17
    expect(x).toBeGreaterThan(0);
    expect(y).toBeCloseTo(x, 12);
  });
  it('skaliert neu: Rand der Totzone 0, ab 0,95 voll', () => {
    expect(totzone(0.15 + 1e-9, 0)[0]).toBeCloseTo(0, 6);
    expect(totzone(0.55, 0)[0]).toBeCloseTo(0.5, 12);
    expect(totzone(0.95, 0)[0]).toBeCloseTo(1, 12);
    expect(totzone(1, 0)[0]).toBeCloseTo(1, 12);
  });
  it('klemmt Beträge über 1 und ignoriert nicht endliche Werte', () => {
    const [x, y] = totzone(1, 1);
    expect(Math.hypot(x, y)).toBeCloseTo(1, 12);
    expect(totzone(NaN, 0.5)).toEqual([0, 0]);
    expect(totzone(Infinity, 0)).toEqual([0, 0]);
  });
});

describe('Ansprechkurve', () => {
  it('0 → 0, 1 → 1, monoton, unter der Diagonale (fein in der Mitte)', () => {
    expect(kurve(0)).toBe(0);
    expect(kurve(1)).toBe(1);
    let vorher = -1;
    for (let m = 0; m <= 1.0001; m += 0.05) {
      const f = kurve(m);
      expect(f).toBeGreaterThan(vorher);
      expect(f).toBeLessThanOrEqual(m + 1e-12);
      vorher = f;
    }
    expect(kurve(0.5)).toBeCloseTo(0.2 * 0.5 + 0.8 * 0.25, 12); // 0,3
  });
  it('kleiner linearer Anteil: knapp über der Totzone reagiert der Stick schon', () => {
    expect(kurve(0.05)).toBeGreaterThan(0.01);
  });
  it('klemmt und verträgt Unsinn', () => {
    expect(kurve(2)).toBe(1);
    expect(kurve(-1)).toBe(0);
    expect(kurve(NaN)).toBe(0);
  });
  it('stick(): Richtung bleibt, Betrag = Kurve des Betrags nach der Totzone', () => {
    const s = stick(0.55 / Math.SQRT2, 0.55 / Math.SQRT2);
    expect(Math.hypot(s.x, s.y)).toBeCloseTo(kurve(0.5), 12);
    expect(s.x).toBeCloseTo(s.y, 12);
    expect(stick(0.1, 0)).toEqual({ x: 0, y: 0 });
  });
});

describe('Auslöser', () => {
  it('analog mit eigener Totzone 0,08 und Kurve', () => {
    expect(TRIGGER_TOTZONE).toBe(0.08);
    expect(ausloeser(0.05)).toBe(0);
    expect(ausloeser(1)).toBe(1);
    expect(ausloeser(0.54)).toBeCloseTo(kurve(0.5), 12);
    expect(ausloeser(NaN)).toBe(0);
  });
});

describe('lies', () => {
  it('linker Stick oben = y +1, rechts = x +1 (Achse oben ist in der API negativ)', () => {
    const l = lies(pad([1, -1, 0, 0]), PAD_VORGABE, O);
    expect(l.links.x).toBeGreaterThan(0.69);
    expect(l.links.y).toBeGreaterThan(0.69);
    expect(lies(pad([0, -1, 0, 0]), PAD_VORGABE, O).links.y).toBe(1);
  });
  it('rechter Stick: oben schauen = y positiv, invertY kehrt nur den rechten Stick um', () => {
    expect(lies(pad([0, 0, 0, -1]), PAD_VORGABE, O).rechts.y).toBe(1);
    expect(lies(pad([0, 0, 0, -1]), PAD_VORGABE, { invertY: true }).rechts.y).toBe(-1);
    expect(lies(pad([0, -1, 0, 0]), PAD_VORGABE, { invertY: true }).links.y).toBe(1);
  });
  it('Auslöser sind die Höhen-/Zoomachse: R2 hoch, L2 runter, analog', () => {
    expect(lies(pad([0, 0, 0, 0], { 7: 1 }), PAD_VORGABE, O).hochAchse).toBe(1);
    expect(lies(pad([0, 0, 0, 0], { 6: 1 }), PAD_VORGABE, O).hochAchse).toBe(-1);
    const halb = lies(pad([0, 0, 0, 0], { 7: 0.54 }), PAD_VORGABE, O).hochAchse;
    expect(halb).toBeCloseTo(kurve(0.5), 12);
    expect(lies(pad([0, 0, 0, 0], { 6: 1, 7: 1 }), PAD_VORGABE, O).hochAchse).toBe(0);
  });
  it('Knöpfe als Aktionen: Vorgabe nach Spielkonvention', () => {
    const g = (i: number): ReadonlySet<PadAktion> => lies(pad([0, 0, 0, 0], { [i]: 1 }), PAD_VORGABE, O).gedrueckt;
    expect([...g(0)]).toEqual(['bestaetigen']);
    expect([...g(1)]).toEqual(['zurueck']);
    expect([...g(2)]).toEqual(['zielen']);
    expect([...g(3)]).toEqual(['kontext']);
    expect([...g(9)]).toEqual(['menue']);
    expect([...g(8)]).toEqual(['belegung']);
    expect([...g(10)]).toEqual(['schnell']);
    expect([...g(11)]).toEqual(['ausrichten']);
  });
  it('Touchpad-Klick (17) zeigt die Belegung, solange er nicht umbelegt ist', () => {
    expect(KNOPF_TOUCHPAD).toBe(17);
    expect([...lies(pad([0, 0, 0, 0], { 17: 1 }), PAD_VORGABE, O).gedrueckt]).toEqual(['belegung']);
    const um = { ...PAD_VORGABE, menue: 17 };
    expect([...lies(pad([0, 0, 0, 0], { 17: 1 }), um, O).gedrueckt]).toEqual(['menue']);
  });
  it('folgt einer geänderten Belegung, nicht belegte Aktionen (−1) lösen nie aus', () => {
    const b = { ...PAD_VORGABE, kontext: 2, zielen: -1 };
    expect(lies(pad([0, 0, 0, 0], { 2: 1 }), b, O).gedrueckt.has('kontext')).toBe(true);
    expect(lies(pad([0, 0, 0, 0], { 2: 1 }), b, O).gedrueckt.has('zielen')).toBe(false);
  });
  it('zu kurze Achsen- und Knopflisten liefern Ruhe statt Fehler', () => {
    const l = lies({ id: 'x', axes: [], buttons: [] }, PAD_VORGABE, O);
    expect(l.links).toEqual({ x: 0, y: 0 });
    expect(l.rechts).toEqual({ x: 0, y: 0 });
    expect(l.hochAchse).toBe(0);
    expect(l.gedrueckt.size).toBe(0);
  });
});

describe('Vorgabe', () => {
  it('jede Aktion auf einem eigenen Knopf 0–15', () => {
    const k = PAD_AKTIONEN.map((a) => PAD_VORGABE[a]);
    expect(new Set(k).size).toBe(k.length);
    for (const n of k) expect(n >= 0 && n <= 15).toBe(true);
    expect(PAD_KNOPF_MAX).toBe(17);
  });
});

describe('kanten', () => {
  it('liefert Drücken und Loslassen genau einmal', () => {
    const a = new Set<PadAktion>();
    const b = new Set<PadAktion>(['bestaetigen']);
    expect(kanten(a, b)).toEqual({ runter: ['bestaetigen'], hoch: [] });
    expect(kanten(b, b)).toEqual({ runter: [], hoch: [] });
    expect(kanten(b, a)).toEqual({ runter: [], hoch: ['bestaetigen'] });
  });
});

describe('controllerTyp', () => {
  it('DualSense in Chrome und Firefox, Edge, DualShock 4', () => {
    expect(controllerTyp('DualSense Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)')).toBe('playstation');
    expect(controllerTyp('Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 0ce6)')).toBe('playstation');
    expect(controllerTyp('054c-0ce6-Sony Interactive Entertainment DualSense Wireless Controller')).toBe('playstation');
    expect(controllerTyp('054c-0df2-DualSense Edge Wireless Controller')).toBe('playstation');
    expect(controllerTyp('Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 09cc)')).toBe('playstation');
  });
  it('Xbox in Chrome und Firefox', () => {
    expect(controllerTyp('Xbox Wireless Controller (STANDARD GAMEPAD Vendor: 045e Product: 0b13)')).toBe('xbox');
    expect(controllerTyp('045e-02ea-Microsoft X-Box One S pad')).toBe('xbox');
    expect(controllerTyp('xinput')).toBe('xbox');
  });
  it('andere (auch Nintendo mit vertauschtem A/B) neutral', () => {
    expect(controllerTyp('Pro Controller (STANDARD GAMEPAD Vendor: 057e Product: 2009)')).toBe('neutral');
    expect(controllerTyp('Test Pad (STANDARD GAMEPAD)')).toBe('neutral');
    expect(controllerTyp('')).toBe('neutral');
  });
});

describe('stickSchritt', () => {
  it('löst einmal aus und erst nach Rückkehr unter 0,3 wieder', () => {
    let r = stickSchritt(0.8, true);
    expect(r).toEqual({ schritt: 1, bereit: false });
    r = stickSchritt(0.9, r.bereit);
    expect(r.schritt).toBe(0);
    r = stickSchritt(0.4, r.bereit);
    expect(r).toEqual({ schritt: 0, bereit: false });
    r = stickSchritt(0.2, r.bereit);
    expect(r.bereit).toBe(true);
    expect(stickSchritt(-0.6, true).schritt).toBe(-1);
  });
  it('unter 0,5 löst nichts aus', () => {
    expect(stickSchritt(0.45, true)).toEqual({ schritt: 0, bereit: true });
  });
});
