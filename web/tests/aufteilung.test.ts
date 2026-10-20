import { describe, expect, it } from 'vitest';
import {
  ANTEIL,
  anteilAusZeiger,
  doppeltipp,
  grenzen,
  klemme,
  ladeAnzeige,
  MIN_PX,
  schritt,
  speichereAnzeige,
  TRENNER_PX,
  umschalten,
  vorgabeAnzeige,
} from '../src/aufteilung';

describe('aufteilung: Grenzen', () => {
  it('Anteile aus der Tabelle, solange beide Bereiche ihre Mindestgröße haben', () => {
    expect(grenzen('hoch', 800)).toEqual({ min: ANTEIL.hoch.min, max: ANTEIL.hoch.max });
    expect(grenzen('quer', 900)).toEqual({ min: ANTEIL.quer.min, max: ANTEIL.quer.max });
  });
  it('hochkant nie über 60 % (die Karte schiebt die Ansicht, überdeckt sie nicht)', () => {
    expect(ANTEIL.hoch.max).toBeLessThanOrEqual(0.6);
  });
  it('kleine Fläche: Mindestpixel für Befund und 3D-Ansicht gehen vor', () => {
    const g = grenzen('hoch', 400);
    expect(g.min * 400).toBeGreaterThanOrEqual(MIN_PX - 1e-9);
    expect((1 - g.max) * 400 - TRENNER_PX).toBeGreaterThanOrEqual(MIN_PX - 1e-9);
  });
  it('zu klein für beide Mindestgrößen: ein fester Wert statt min > max', () => {
    const g = grenzen('hoch', 200);
    expect(g.min).toBe(g.max);
    expect(g.min).toBeGreaterThan(0);
    expect(g.min).toBeLessThan(1);
  });
  it('klemme hält den Anteil in den Grenzen', () => {
    const g = { min: 0.25, max: 0.6 };
    expect(klemme(0.1, g)).toBe(0.25);
    expect(klemme(0.9, g)).toBe(0.6);
    expect(klemme(0.4, g)).toBe(0.4);
    expect(klemme(Number.NaN, g)).toBe(0.25);
  });
});

describe('aufteilung: Tastatur', () => {
  const g = { min: 0.25, max: 0.6 };
  it('hochkant: Pfeil hoch vergrößert den Befundbereich, Pfeil runter verkleinert ihn', () => {
    expect(schritt('hoch', 'ArrowUp', 0.4, g)).toBeCloseTo(0.45);
    expect(schritt('hoch', 'ArrowDown', 0.4, g)).toBeCloseTo(0.35);
    expect(schritt('hoch', 'ArrowLeft', 0.4, g)).toBeNull();
  });
  it('quer: Pfeil links vergrößert den Befundbereich (er liegt rechts), Pfeil rechts verkleinert ihn', () => {
    expect(schritt('quer', 'ArrowLeft', 0.4, g)).toBeCloseTo(0.45);
    expect(schritt('quer', 'ArrowRight', 0.4, g)).toBeCloseTo(0.35);
    expect(schritt('quer', 'ArrowUp', 0.4, g)).toBeNull();
  });
  it('Pos1 und Ende auf die Grenzen, Schritte über die Grenze bleiben an ihr', () => {
    expect(schritt('hoch', 'Home', 0.4, g)).toBe(0.25);
    expect(schritt('quer', 'End', 0.4, g)).toBe(0.6);
    expect(schritt('hoch', 'ArrowUp', 0.58, g)).toBe(0.6);
    expect(schritt('hoch', 'a', 0.4, g)).toBeNull();
  });
});

describe('aufteilung: Umschalten und Doppeltipp', () => {
  const g = { min: 0.25, max: 0.6 };
  it('Standard ↔ 3D groß (kleinster Befundbereich)', () => {
    expect(umschalten(0.45, g, 0.45)).toBe(0.25);
    expect(umschalten(0.25, g, 0.45)).toBe(0.45);
    // Von einer gezogenen Stellung aus erst auf 3D groß.
    expect(umschalten(0.5, g, 0.45)).toBe(0.25);
    // Liegt die Vorgabe selbst auf der Grenze (kleine Fläche), bleibt es bei ihr.
    expect(umschalten(0.3, { min: 0.3, max: 0.3 }, 0.45)).toBe(0.3);
  });
  it('Doppeltipp: zwei kurze Tipps dicht beieinander, nicht zu langsam, nicht weit auseinander', () => {
    const a = { t: 1000, x: 100, y: 100 };
    expect(doppeltipp(null, a)).toBe(false);
    expect(doppeltipp(a, { t: 1250, x: 105, y: 98 })).toBe(true);
    expect(doppeltipp(a, { t: 1500, x: 100, y: 100 })).toBe(false);
    expect(doppeltipp(a, { t: 1200, x: 160, y: 100 })).toBe(false);
  });
  it('Zeigerstelle → Anteil: die Mitte des Trenners folgt dem Finger', () => {
    // Hochkant: 800 px Fläche ab y = 0; der Trenner mittig bei y = 500 → Befund 800 − 500 − 12 = 288 px.
    expect(anteilAusZeiger('hoch', 500, { anfang: 0, laenge: 800 })).toBeCloseTo((800 - 500 - TRENNER_PX / 2) / 800);
    // Mit Liste oben (Fläche beginnt bei 100).
    expect(anteilAusZeiger('hoch', 500, { anfang: 100, laenge: 700 })).toBeCloseTo((800 - 500 - TRENNER_PX / 2) / 700);
    expect(anteilAusZeiger('quer', 600, { anfang: 0, laenge: 900 })).toBeCloseTo((900 - 600 - TRENNER_PX / 2) / 900);
  });
});

describe('aufteilung: Speichern', () => {
  it('Vorgabe, Rundweg, Fremdes verworfen', () => {
    const v = vorgabeAnzeige();
    expect(v).toEqual({ schema: 1, hoch: ANTEIL.hoch.vorgabe, quer: ANTEIL.quer.vorgabe, messwerte: true });
    expect(ladeAnzeige(null)).toEqual(v);
    expect(ladeAnzeige('kaputt')).toEqual(v);
    expect(ladeAnzeige(JSON.stringify({ schema: 2, hoch: 0.3 }))).toEqual(v);
    const e = { ...v, hoch: 0.3, quer: 0.5, messwerte: false };
    expect(ladeAnzeige(speichereAnzeige(e))).toEqual(e);
  });
  it('klemmt auf die Anteilsgrenzen, verwirft falsche Typen je Feld', () => {
    const e = ladeAnzeige(JSON.stringify({ schema: 1, hoch: 0.99, quer: 'breit', messwerte: 'nein' }));
    expect(e.hoch).toBe(ANTEIL.hoch.max);
    expect(e.quer).toBe(ANTEIL.quer.vorgabe);
    expect(e.messwerte).toBe(true);
    expect(ladeAnzeige(JSON.stringify({ schema: 1, hoch: -1 })).hoch).toBe(ANTEIL.hoch.min);
  });
});
