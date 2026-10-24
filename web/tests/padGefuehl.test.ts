// Eingabeschicht Stufe 2: Drehrate mit Beschleunigung beim Halten, Nachlauf, Bildzeit-Unabhängigkeit, Zoom.
import { describe, expect, it } from 'vitest';
import {
  BESCHLEUNIGUNG_DAUER_S, BESCHLEUNIGUNG_FAKTOR, BESCHLEUNIGUNG_VERZUG_S, beschleunigung, drehRuhe, drehSchritt, zoomFaktor,
} from '../src/padGefuehl';
import type { DrehZustand } from '../src/padGefuehl';

const raten = { waagerecht: 2, senkrecht: 1 };
const lauf = (x: number, y: number, dt: number, dauer: number, reduziert = false): { winkelX: number; winkelY: number; z: DrehZustand } => {
  let z = drehRuhe();
  let winkelX = 0;
  let winkelY = 0;
  const n = Math.round(dauer / dt);
  for (let i = 0; i < n; i++) {
    const r = drehSchritt(z, x, y, dt, { raten, faktor: 1, reduziert });
    z = r.z;
    winkelX += r.dx;
    winkelY += r.dy;
  }
  return { winkelX, winkelY, z };
};

describe('Beschleunigung', () => {
  it('erst nach dem Verzug, dann linear bis zum Faktor', () => {
    expect(beschleunigung(0)).toBe(1);
    expect(beschleunigung(BESCHLEUNIGUNG_VERZUG_S)).toBe(1);
    expect(beschleunigung(BESCHLEUNIGUNG_VERZUG_S + BESCHLEUNIGUNG_DAUER_S / 2)).toBeCloseTo(1 + (BESCHLEUNIGUNG_FAKTOR - 1) / 2, 12);
    expect(beschleunigung(10)).toBe(BESCHLEUNIGUNG_FAKTOR);
  });
  it('voller Ausschlag beschleunigt die waagerechte Drehung, halber nicht', () => {
    const voll = lauf(1, 0, 1 / 60, 1.5).z.rateX;
    expect(voll).toBeCloseTo(raten.waagerecht * BESCHLEUNIGUNG_FAKTOR, 3);
    const halb = lauf(0.5, 0, 1 / 60, 1.5).z.rateX;
    expect(halb).toBeCloseTo(raten.waagerecht * 0.5, 3);
  });
  it('senkrecht wird nicht beschleunigt', () => {
    expect(lauf(0, 1, 1 / 60, 1.5).z.rateY).toBeCloseTo(raten.senkrecht, 3);
  });
  it('Loslassen setzt die Haltezeit zurück', () => {
    let z = lauf(1, 0, 1 / 60, 1).z;
    expect(z.gehalten).toBeGreaterThan(0.9);
    z = drehSchritt(z, 0.5, 0, 1 / 60, { raten, faktor: 1, reduziert: false }).z;
    expect(z.gehalten).toBe(0);
  });
});

describe('Nachlauf', () => {
  it('sanftes Anlaufen: die erste Bildrate liegt unter dem Ziel', () => {
    const r = drehSchritt(drehRuhe(), 0.5, 0, 1 / 60, { raten, faktor: 1, reduziert: false });
    expect(r.z.rateX).toBeGreaterThan(0);
    expect(r.z.rateX).toBeLessThan(1);
  });
  it('sanftes Abbremsen und danach Ruhe (exakt 0, keine Bilder mehr nötig)', () => {
    let z = lauf(0.5, 0, 1 / 60, 1).z;
    const r1 = drehSchritt(z, 0, 0, 1 / 60, { raten, faktor: 1, reduziert: false });
    expect(r1.dx).toBeGreaterThan(0);
    z = r1.z;
    for (let i = 0; i < 120; i++) z = drehSchritt(z, 0, 0, 1 / 60, { raten, faktor: 1, reduziert: false }).z;
    expect(z.rateX).toBe(0);
  });
  it('reduzierte Bewegung: kein Nachlauf, keine Beschleunigung', () => {
    const r = drehSchritt(drehRuhe(), 1, 0, 1 / 60, { raten, faktor: 1, reduziert: true });
    expect(r.z.rateX).toBe(raten.waagerecht);
    expect(lauf(1, 0, 1 / 60, 2, true).z.rateX).toBe(raten.waagerecht);
    const stopp = drehSchritt(r.z, 0, 0, 1 / 60, { raten, faktor: 1, reduziert: true });
    expect(stopp.dx).toBe(0);
  });
  it('Faktor (Empfindlichkeit, Zielen) skaliert die Rate', () => {
    const r = drehSchritt(drehRuhe(), 1, 0, 1 / 60, { raten, faktor: 0.5, reduziert: true });
    expect(r.z.rateX).toBe(raten.waagerecht * 0.5);
  });
});

describe('Bildzeit-Unabhängigkeit', () => {
  it('zwei Schritte à 8 ms ≈ ein Schritt à 16 ms (Rate und Winkel)', () => {
    let a = drehRuhe();
    let wa = 0;
    for (let i = 0; i < 2; i++) {
      const r = drehSchritt(a, 1, 0.3, 0.008, { raten, faktor: 1, reduziert: false });
      a = r.z;
      wa += r.dx;
    }
    const b = drehSchritt(drehRuhe(), 1, 0.3, 0.016, { raten, faktor: 1, reduziert: false });
    expect(a.rateX).toBeCloseTo(b.z.rateX, 10);
    expect(wa).toBeCloseTo(b.dx, 10);
  });
  it('gleicher Gesamtwinkel bei 30, 60 und 144 Bildern je Sekunde (±2 %)', () => {
    const w60 = lauf(1, 0, 1 / 60, 2).winkelX;
    for (const fps of [30, 144]) expect(Math.abs(lauf(1, 0, 1 / fps, 2).winkelX - w60) / w60).toBeLessThan(0.02);
  });
  it('Bildzeit wird geklemmt (Tab im Hintergrund), Unsinn zählt als 0', () => {
    const r = drehSchritt(drehRuhe(), 1, 0, 5, { raten, faktor: 1, reduziert: true });
    expect(r.dx).toBeCloseTo(raten.waagerecht * 0.1, 12);
    expect(drehSchritt(drehRuhe(), 1, 0, NaN, { raten, faktor: 1, reduziert: true }).dx).toBe(0);
  });
});

describe('Zoom', () => {
  it('exponentiell: ohne Auslöser 1, hinein < 1, heraus > 1, zwei Halbschritte = ein Schritt', () => {
    expect(zoomFaktor(0, 1 / 60)).toBe(1);
    expect(zoomFaktor(1, 1 / 60)).toBeLessThan(1);
    expect(zoomFaktor(-1, 1 / 60)).toBeGreaterThan(1);
    expect(zoomFaktor(1, 0.008) ** 2).toBeCloseTo(zoomFaktor(1, 0.016), 12);
  });
});
