import { describe, expect, it } from 'vitest';
import { verdeckt } from '../src/messBeschriftung';

const r = (left: number, top: number, w: number, h: number) => ({ left, top, right: left + w, bottom: top + h });

describe('messBeschriftung: verdeckt', () => {
  const flaeche = r(0, 0, 400, 300);
  const leiste = r(250, 8, 140, 44);
  const zurueck = r(8, 8, 100, 32);
  it('frei in der Fläche: sichtbar', () => {
    expect(verdeckt(r(150, 150, 80, 16), flaeche, [leiste, zurueck])).toBe(false);
  });
  it('unter Werkzeugleiste oder „← Projekte“, auch nur teilweise: verdeckt', () => {
    expect(verdeckt(r(260, 20, 80, 16), flaeche, [leiste, zurueck])).toBe(true);
    expect(verdeckt(r(100, 30, 80, 16), flaeche, [leiste, zurueck])).toBe(true);
  });
  it('läuft über den Rand der Ansicht hinaus (angeschnitten): verdeckt', () => {
    expect(verdeckt(r(-20, 150, 80, 16), flaeche, [])).toBe(true);
    expect(verdeckt(r(350, 150, 80, 16), flaeche, [])).toBe(true);
    expect(verdeckt(r(150, 292, 80, 16), flaeche, [])).toBe(true);
  });
  it('bündig am Rand oder an einem Bedienelement: noch sichtbar (1 px Spiel)', () => {
    expect(verdeckt(r(0, 150, 80, 16), flaeche, [])).toBe(false);
    expect(verdeckt(r(150, 52, 80, 16), flaeche, [leiste])).toBe(false);
    expect(verdeckt(r(170, 52.5, 80, 16), r(0, 0, 400, 300), [r(250, 8, 140, 45)])).toBe(false);
  });
  it('leere Hindernisse (ausgeblendet, Breite 0) zählen nicht', () => {
    expect(verdeckt(r(150, 150, 80, 16), flaeche, [r(150, 150, 0, 0)])).toBe(false);
  });
});
