import { describe, expect, it } from 'vitest';
import {
  AUSTRITT_ABSTAND_M, Haltezustand, SetzZaehler, PITCH_MAX, blick, drehSchritt, drehe, mausBlick, neigSchritt, schritt,
  tempoAusRad, tempoText, vignette, yawPitch, zielBeimAustritt,
} from '../src/walkControls';
import type { WalkZustand } from '../src/walkControls';

const P = { tempo: 2, schnellFaktor: 4, zeitkonstante: 0.15, hoeheHalten: false, reduziert: false };
const z0 = { pos: [0, 0, 0] as const, yaw: 0, pitch: 0, vel: [0, 0, 0] as const };
const n = (e: Partial<{ vor: number; seit: number; hoch: number; schnell: boolean }>) => ({ vor: 0, seit: 0, hoch: 0, schnell: false, ...e });

describe('walkControls', () => {
  it('blick/yawPitch Rundreise, Z oben', () => {
    const d = blick(0.7, 0.3);
    const r = yawPitch(d);
    expect(r.yaw).toBeCloseTo(0.7, 9);
    expect(r.pitch).toBeCloseTo(0.3, 9);
    expect(blick(0, Math.PI / 2 * 0.999)[2]).toBeGreaterThan(0.99);
  });
  it('yawPitch des Nullvektors: Blick geradeaus', () => {
    expect(yawPitch([0, 0, 0])).toEqual({ yaw: 0, pitch: 0 });
  });
  it('beschleunigt sanft und erreicht das Tempo', () => {
    let z = schritt(z0, n({ vor: 1 }), P, 1 / 60);
    expect(z.vel[0]).toBeGreaterThan(0);
    expect(z.vel[0]).toBeLessThan(2);
    for (let i = 0; i < 120; i++) z = schritt(z, n({ vor: 1 }), P, 1 / 60);
    expect(z.vel[0]).toBeCloseTo(2, 2);
    expect(z.pos[0]).toBeGreaterThan(2);
  });
  it('reduzierte Bewegung: kein Nachgleiten', () => {
    const z = schritt({ ...z0, vel: [2, 0, 0] }, n({}), { ...P, reduziert: true }, 1 / 60);
    expect(z.vel).toEqual([0, 0, 0]);
  });
  it('ohne Eingabe bremst die Bewegung sanft auf null', () => {
    let z = schritt({ ...z0, vel: [2, 0, 0] }, n({}), P, 1 / 60);
    expect(z.vel[0]).toBeGreaterThan(0);
    expect(z.vel[0]).toBeLessThan(2);
    for (let i = 0; i < 300; i++) z = schritt(z, n({}), P, 1 / 60);
    expect(z.vel[0]).toBeCloseTo(0, 3);
  });
  it('Höhe halten: W bleibt waagerecht, Q/E gesperrt', () => {
    const z = schritt({ ...z0, pitch: 0.8 }, n({ vor: 1, hoch: 1 }), { ...P, hoeheHalten: true, reduziert: true }, 0.1);
    expect(z.pos[2]).toBe(0);
    expect(z.pos[0]).toBeGreaterThan(0);
  });
  it('frei: W folgt der Neigung, hoch geht entlang Z, seitwärts waagerecht', () => {
    const R = { ...P, reduziert: true };
    const hoch = schritt({ ...z0, pitch: Math.PI / 4 }, n({ vor: 1 }), R, 0.1);
    expect(hoch.vel[2]).toBeGreaterThan(1);
    expect(hoch.vel[0]).toBeCloseTo(hoch.vel[2], 9);
    const q = schritt(z0, n({ hoch: 1 }), R, 0.1);
    expect(q.vel).toEqual([0, 0, 2]);
    const rechts = schritt(z0, n({ seit: 1 }), R, 0.1);
    expect(rechts.vel[1]).toBeCloseTo(-2, 9);
    expect(rechts.vel[2]).toBe(0);
  });
  it('Schrägfahrt wird normiert: nicht schneller als geradeaus', () => {
    const z = schritt(z0, n({ vor: 1, seit: 1 }), { ...P, reduziert: true }, 0.1);
    expect(Math.hypot(...z.vel)).toBeCloseTo(2, 9);
  });
  it('schnell vervierfacht, dt wird geklemmt', () => {
    const z = schritt(z0, n({ vor: 1, schnell: true }), { ...P, reduziert: true }, 5);
    expect(z.vel[0]).toBeCloseTo(8, 9);
    expect(z.pos[0]).toBeCloseTo(0.8, 9);
  });
  it('Neigung geklemmt, Invert', () => {
    expect(mausBlick(0, 0, 0, -1e6, 1, false).pitch).toBeCloseTo(PITCH_MAX, 9);
    expect(Math.sign(mausBlick(0, 0, 0, 10, 1, true).pitch)).toBe(-Math.sign(mausBlick(0, 0, 0, 10, 1, false).pitch));
  });
  it('drehe: positive Drehung geht nach links, Neigung nach oben, geklemmt und normiert', () => {
    const r = drehe(0, 0, 1, 1, 1, 0.05);
    expect(r.yaw).toBeCloseTo(0.05, 12);
    expect(r.pitch).toBeCloseTo(0.05, 12);
    expect(drehe(0, 0, 0, 1, 100, 0.1).pitch).toBeCloseTo(PITCH_MAX, 12);
    expect(drehe(Math.PI - 0.01, 0, 1, 0, 1, 0.1).yaw).toBeLessThan(0);
  });
  it('Drehen in Schritten: 15° je Druck', () => {
    expect(drehSchritt(0, 1, 15)).toBeCloseTo((15 * Math.PI) / 180, 12);
    expect(drehSchritt(Math.PI, 1, 90)).toBeCloseTo(-Math.PI / 2, 9);
  });
  it('Neigen in Schritten: geklemmt auf ±89°', () => {
    expect(neigSchritt(0, 1, 15)).toBeCloseTo((15 * Math.PI) / 180, 12);
    expect(neigSchritt(PITCH_MAX - 0.01, 1, 15)).toBeCloseTo(PITCH_MAX, 12);
    expect(neigSchritt(-PITCH_MAX + 0.01, -1, 15)).toBeCloseTo(-PITCH_MAX, 12);
  });
  it('Austritt: Treffer oder 10 m in Blickrichtung', () => {
    expect(zielBeimAustritt([1, 2, 3], [1, 0, 0], [5, 5, 5])).toEqual([5, 5, 5]);
    expect(zielBeimAustritt([1, 2, 3], [1, 0, 0], null)).toEqual([1 + AUSTRITT_ABSTAND_M, 2, 3]);
  });
  it('Mausrad-Tempo begrenzt, Vignette abschaltbar', () => {
    expect(tempoAusRad(50, -100)).toBe(50);
    expect(tempoAusRad(0.2, 100)).toBe(0.2);
    expect(vignette(8, 2, false)).toBe(0);
    expect(vignette(8, 2, true)).toBeGreaterThan(0);
    expect(vignette(1, 2, true)).toBe(0);
  });
  it('Mausrad-Tempo: Rad hoch schneller, ×1,2 je Raste unabhängig vom Betrag', () => {
    expect(tempoAusRad(2, -1)).toBeCloseTo(2.4, 12);
    expect(tempoAusRad(2, -120)).toBeCloseTo(2.4, 12);
    expect(tempoAusRad(2, 120)).toBeCloseTo(2 / 1.2, 12);
    expect(tempoAusRad(2, 0)).toBe(2);
  });
  it('Tempo-Ansage: eine Nachkommastelle mit Komma, ganze Werte ohne', () => {
    expect(tempoText(2.4)).toBe('Tempo 2,4 m/s');
    expect(tempoText(2)).toBe('Tempo 2 m/s');
    expect(tempoText(2 / 1.2)).toBe('Tempo 1,7 m/s');
    expect(tempoText(50)).toBe('Tempo 50 m/s');
  });
  it('Vignette: linear von 1,5× bis 4× Tempo, gedeckelt bei 0,6', () => {
    expect(vignette(3, 2, true)).toBe(0);
    expect(vignette(8, 2, true)).toBeCloseTo(0.6, 12);
    expect(vignette(100, 2, true)).toBeCloseTo(0.6, 12);
    expect(vignette(5.5, 2, true)).toBeCloseTo(0.3, 12);
  });
  it('Haltezustand: leere() stoppt alles (Fokusverlust)', () => {
    const h = new Haltezustand();
    h.druecke('begehen.vor');
    h.druecke('begehen.links');
    expect(h.eingabe(false)).toMatchObject({ vor: 1, seit: -1 });
    h.leere();
    expect(h.eingabe(false)).toEqual({ vor: 0, seit: 0, hoch: 0, schnell: false });
  });
  it('Haltezustand: Gegenrichtungen heben sich auf; Schnell umschalten', () => {
    const h = new Haltezustand();
    h.druecke('begehen.vor'); h.druecke('begehen.zurueck');
    expect(h.eingabe(false).vor).toBe(0);
    h.druecke('begehen.schnell'); h.lasse('begehen.schnell');
    expect(h.eingabe(true).schnell).toBe(true);
    expect(h.eingabe(false).schnell).toBe(false);
  });
  it('Haltezustand: andere Aktionen werden ignoriert, Tastenwiederholung schaltet nicht zurück', () => {
    const h = new Haltezustand();
    h.druecke('begehen.setzen');
    expect(h.gehalten('begehen.setzen')).toBe(false);
    h.druecke('begehen.schnell'); h.druecke('begehen.schnell');
    expect(h.eingabe(true).schnell).toBe(true);
    h.lasse('begehen.schnell'); h.druecke('begehen.schnell');
    expect(h.eingabe(true).schnell).toBe(false);
    h.druecke('begehen.hoch'); h.druecke('begehen.rechts');
    expect(h.eingabe(false)).toMatchObject({ hoch: 1, seit: 1 });
  });
  it('nicht endliches dt bewegt und dreht nicht', () => {
    const lauf = { ...z0, vel: [1, 0, 0] as const };
    for (const dt of [Number.NaN, Number.POSITIVE_INFINITY, Number.NEGATIVE_INFINITY]) {
      const z = schritt(lauf, n({ vor: 1 }), P, dt);
      expect(z.pos).toEqual([0, 0, 0]);
      expect(z.vel.every(Number.isFinite)).toBe(true);
      const d = drehe(0.5, 0.1, 1, 1, 1.5, dt);
      expect(d.yaw).toBeCloseTo(0.5, 12);
      expect(d.pitch).toBeCloseTo(0.1, 12);
    }
  });
  it('Mausrad-Tempo: nicht endliches deltaY lässt das Tempo unverändert', () => {
    expect(tempoAusRad(3, Number.NaN)).toBe(3);
    expect(tempoAusRad(3, Number.POSITIVE_INFINITY)).toBe(3);
    expect(tempoAusRad(3, Number.NEGATIVE_INFINITY)).toBe(3);
  });
  it('Höhe halten bei steilster Neigung: volles Tempo waagerecht, Höhe bleibt', () => {
    let z: WalkZustand = { ...z0, pitch: PITCH_MAX };
    const p = { ...P, hoeheHalten: true, reduziert: true };
    for (let i = 0; i < 10; i++) z = schritt(z, n({ vor: 1 }), p, 1 / 60);
    expect(Math.hypot(z.vel[0], z.vel[1], z.vel[2])).toBeCloseTo(P.tempo, 9);
    expect(z.pos[2]).toBe(0);
  });
  it('analoger Teilausschlag wird nicht hochnormiert: halber Stick = halbes Tempo', () => {
    const z = schritt(z0, n({ vor: 0.5 }), { ...P, reduziert: true }, 1 / 60);
    expect(Math.hypot(z.vel[0], z.vel[1], z.vel[2])).toBeCloseTo(0.5 * P.tempo, 9);
  });
});

describe('SetzZaehler', () => {
  it('läuft, solange ein Vorgang offen ist', () => {
    const z = new SetzZaehler();
    const a = z.start();
    const b = z.start();
    expect(z.laeuft).toBe(true);
    a();
    expect(z.laeuft).toBe(true);
    b();
    expect(z.laeuft).toBe(false);
  });
  it('doppeltes Fertig zählt einmal', () => {
    const z = new SetzZaehler();
    const a = z.start();
    const b = z.start();
    a();
    a();
    expect(z.laeuft).toBe(true);
    b();
    expect(z.laeuft).toBe(false);
  });
  it('nach dem Zurücksetzen (Ende des Begehens) ändert ein Fertig aus der alten Generation nichts', () => {
    const z = new SetzZaehler();
    const alt = z.start();
    z.zuruecksetzen();
    expect(z.laeuft).toBe(false);
    const neu = z.start();
    alt();
    expect(z.laeuft).toBe(true);
    neu();
    expect(z.laeuft).toBe(false);
  });
});
