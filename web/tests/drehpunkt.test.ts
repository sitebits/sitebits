import { MOUSE, TOUCH } from 'three';
import { describe, expect, it } from 'vitest';
import { DREHPUNKT_RUHE_MS, drehpunktSichtbar, fingerGesten, istDrehung, mausTasten } from '../src/drehpunkt';

type V3 = [number, number, number];

describe('drehpunkt', () => {
  it('Maustasten im Orbit: links dreht (Vorgabe), rechts verschiebt; alte Belegung tauscht beide, Mitte zoomt immer', () => {
    expect(mausTasten('drehen')).toEqual({ LEFT: MOUSE.ROTATE, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.PAN });
    expect(mausTasten('verschieben')).toEqual({ LEFT: MOUSE.PAN, MIDDLE: MOUSE.DOLLY, RIGHT: MOUSE.ROTATE });
  });

  it('Finger folgen der Maus: ein Finger wie links ziehen, zwei Finger zoomen und nehmen die andere Geste', () => {
    // „drehen“ wie Sketchfab: ein Finger dreht um den Drehpunkt, zwei Finger zoomen und verschieben.
    expect(fingerGesten('drehen')).toEqual({ ONE: TOUCH.ROTATE, TWO: TOUCH.DOLLY_PAN });
    expect(fingerGesten('verschieben')).toEqual({ ONE: TOUCH.PAN, TWO: TOUCH.DOLLY_ROTATE });
  });

  const ziel: V3 = [10, 20, 5];
  it('Drehung: Drehpunkt und Abstand bleiben, die Kamera wandert auf der Kugel', () => {
    expect(istDrehung({ kamera: [10, 10, 5], ziel }, { kamera: [20, 20, 5], ziel })).toBe(true);
  });
  it('keine Drehung: Stillstand, Verschieben (Drehpunkt wandert) und Zoomen (Abstand ändert sich)', () => {
    const a = { kamera: [10, 10, 5] as V3, ziel };
    expect(istDrehung(a, a)).toBe(false);
    expect(istDrehung(a, { kamera: [11, 10, 5], ziel: [11, 20, 5] })).toBe(false);
    expect(istDrehung(a, { kamera: [10, 15, 5], ziel })).toBe(false);
  });
  it('Drehung mit Rundungsrauschen in Weltkoordinaten zählt', () => {
    const z: V3 = [500010, 5600000, 300];
    const a = { kamera: [500000, 5600000, 300] as V3, ziel: z };
    const b = { kamera: [500010 - 10 * Math.cos(0.1), 5600000 - 10 * Math.sin(0.1), 300] as V3, ziel: [z[0], z[1], z[2] + 1e-7] as V3 };
    expect(istDrehung(a, b)).toBe(true);
  });

  it('Marker sichtbar ab dem Anlass, aus nach der Ruhezeit; ohne Anlass nie', () => {
    expect(drehpunktSichtbar(null, 0)).toBe(false);
    expect(drehpunktSichtbar(1000, 1000)).toBe(true);
    expect(drehpunktSichtbar(1000, 1000 + DREHPUNKT_RUHE_MS - 1)).toBe(true);
    expect(drehpunktSichtbar(1000, 1000 + DREHPUNKT_RUHE_MS)).toBe(false);
  });
});
