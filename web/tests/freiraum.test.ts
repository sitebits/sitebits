import { describe, expect, it } from 'vitest';
import { freieSpanne } from '../src/freiraum';

describe('freieSpanne', () => {
  it('ohne Hindernis: ganze Breite abzüglich Rand', () => {
    expect(freieSpanne(1000, 0, 50, [])).toEqual({ left: 8, right: 992 });
  });
  it('Panel links und rechts: die Lücke dazwischen', () => {
    const links = { left: 8, top: 0, right: 300, bottom: 600 };
    const rechts = { left: 700, top: 0, right: 992, bottom: 600 };
    expect(freieSpanne(1000, 100, 150, [links, rechts])).toEqual({ left: 308, right: 692 });
  });
  it('Hindernisse außerhalb des Bands zählen nicht', () => {
    const tief = { left: 0, top: 500, right: 1000, bottom: 600 };
    expect(freieSpanne(1000, 0, 50, [tief])).toEqual({ left: 8, right: 992 });
  });
  it('überlappende Hindernisse: breiteste Lücke', () => {
    const a = { left: 100, top: 0, right: 400, bottom: 50 };
    const b = { left: 300, top: 0, right: 500, bottom: 50 };
    expect(freieSpanne(1000, 0, 50, [a, b])).toEqual({ left: 508, right: 992 });
  });
  it('leere Rechtecke (ausgeblendet) zählen nicht', () => {
    expect(freieSpanne(1000, 0, 50, [{ left: 100, top: 10, right: 100, bottom: 10 }])).toEqual({ left: 8, right: 992 });
  });
});
