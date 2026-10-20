import { describe, expect, it } from 'vitest';
import { ABSTAND_MS, NACHHOLEN_MS, VORRANG_HALTEN_MS, WARTEN_MAX, ansageFaellig, ansageMelden, ansageSchritt, formatiereMeter, kurzText, leereSchlange, liveText, startZustand, vorschauNoetig } from '../src/ansage';
import type { AnsageSchlange, AnsageZustand } from '../src/ansage';

const text = (m: number): string => `ungefähr ${formatiereMeter(m)}`;

function lauf(n: number, f: (i: number) => { treffer: boolean; wertM: number | null }, z0: AnsageZustand = startZustand): string[] {
  let z = z0;
  const out: string[] = [];
  for (let i = 0; i < n; i++) {
    const r = ansageSchritt(z, { t: i * 16.7, ...f(i), text });
    z = r.zustand;
    if (r.ansage) out.push(r.ansage);
  }
  return out;
}

describe('Wert-Ansage', () => {
  it('60 Bilder mit schwankendem Wert erzeugen höchstens eine Wert-Ansage', () => {
    const out = lauf(60, (i) => ({ treffer: true, wertM: 4.32 + (i % 2 ? 0.004 : -0.004) }));
    expect(out.filter((a) => a.startsWith('ungefähr')).length).toBeLessThanOrEqual(1);
    expect(out.filter((a) => a === 'Treffer').length).toBe(1);
  });

  it('ruhiger Wert wird nach der Ruhezeit genau einmal angesagt', () => {
    const out = lauf(90, () => ({ treffer: true, wertM: 4.32 }));
    expect(out).toEqual(['Treffer', 'ungefähr 4,32 Meter']);
  });

  it('Wechsel Treffer → kein Treffer genau eine Ansage', () => {
    const out = lauf(60, (i) => ({ treffer: i < 30, wertM: i < 30 ? 2 : null }));
    expect(out.filter((a) => a === 'kein Treffer').length).toBe(1);
  });

  it('Wert, der sich laufend um mehr als 1 cm ändert, wird nicht angesagt', () => {
    const out = lauf(60, (i) => ({ treffer: true, wertM: 3 + i * 0.02 }));
    expect(out.filter((a) => a.startsWith('ungefähr'))).toEqual([]);
  });

  it('neuer ruhiger Wert nach einer Änderung wird erneut angesagt', () => {
    const out = lauf(180, (i) => ({ treffer: true, wertM: i < 60 ? 2 : 3.5 }));
    expect(out.filter((a) => a.startsWith('ungefähr'))).toEqual(['ungefähr 2,00 Meter', 'ungefähr 3,50 Meter']);
  });

  it('kein Treffer ohne Wert bleibt still, solange der Zustand gleich ist', () => {
    const out = lauf(60, () => ({ treffer: false, wertM: null }));
    expect(out).toEqual(['kein Treffer']);
  });
});

describe('Formatierung', () => {
  it('formatiereMeter de-DE', () => {
    expect(formatiereMeter(4.321)).toBe('4,32 Meter');
    expect(formatiereMeter(0.43)).toBe('43 Zentimeter');
    expect(formatiereMeter(0.999)).toBe('1,00 Meter');
  });
  it('liveText und kurzText', () => {
    expect(liveText({ wertM: 4.32, art: 'ziel' })).toBe('ungefähr 4,32 Meter bis Zielpunkt');
    expect(liveText({ wertM: 4.32, art: 'ziel', klasse: 'Wandfläche' })).toBe('ungefähr 4,32 Meter bis Zielpunkt, Wandfläche');
    expect(liveText({ wertM: 0.43, art: 'hoehe' })).toBe('ungefähr 43 Zentimeter Höhenunterschied');
    expect(kurzText(4.321)).toBe('≈ 4,32 m');
  });
});

describe('Vorschau-Takt', () => {
  it('ohne Vorschau immer, bei gleicher Kamera erst nach 30 Bildern, sonst höchstens jedes dritte Bild', () => {
    expect(vorschauNoetig({ hat: false, poseGleich: true, bilderSeitPick: 0 })).toBe(true);
    expect(vorschauNoetig({ hat: true, poseGleich: true, bilderSeitPick: 29 })).toBe(false);
    expect(vorschauNoetig({ hat: true, poseGleich: true, bilderSeitPick: 30 })).toBe(true);
    expect(vorschauNoetig({ hat: true, poseGleich: false, bilderSeitPick: 1 })).toBe(false);
    expect(vorschauNoetig({ hat: true, poseGleich: false, bilderSeitPick: 2 })).toBe(false);
    expect(vorschauNoetig({ hat: true, poseGleich: false, bilderSeitPick: 3 })).toBe(true);
    expect(vorschauNoetig({ hat: true, poseGleich: true, bilderSeitPick: 0, erzwingen: true })).toBe(true);
  });
});

describe('Reihenfolge der Ansagen', () => {
  /** Meldungen und Takte in Zeitfolge abspielen; liefert, was wann gezeigt wird. */
  const spiele = (ereignisse: ({ t: number; text: string; vorrang?: boolean } | { t: number; takt: true })[]): [number, string][] => {
    let s: AnsageSchlange = leereSchlange;
    const out: [number, string][] = [];
    for (const e of ereignisse) {
      const r = 'takt' in e ? ansageFaellig(s, e.t) : ansageMelden(s, e);
      s = r.schlange;
      if (r.jetzt !== null) out.push([e.t, r.jetzt]);
    }
    return out;
  };

  it('ohne Vorrang: jede Meldung sofort, nichts wartet', () => {
    expect(spiele([{ t: 0, text: 'a' }, { t: 10, text: 'b' }, { t: 20, text: 'a' }, { t: 5000, takt: true }])).toEqual([[0, 'a'], [10, 'b'], [20, 'a']]);
  });

  it('Vorrang hält; was währenddessen kommt, folgt danach im Abstand und in Reihenfolge', () => {
    expect(spiele([
      { t: 0, text: 'Befund 1 von 3: Riss', vorrang: true },
      { t: 100, text: 'Angebot' },
      { t: 200, text: 'Hinweis' },
      { t: VORRANG_HALTEN_MS - 1, takt: true },
      { t: VORRANG_HALTEN_MS, takt: true },
      { t: VORRANG_HALTEN_MS + 10, text: 'später' },
      { t: VORRANG_HALTEN_MS + ABSTAND_MS, takt: true },
      { t: VORRANG_HALTEN_MS + 2 * ABSTAND_MS, takt: true },
    ])).toEqual([
      [0, 'Befund 1 von 3: Riss'],
      [VORRANG_HALTEN_MS, 'Angebot'],
      [VORRANG_HALTEN_MS + ABSTAND_MS, 'Hinweis'],
      [VORRANG_HALTEN_MS + 2 * ABSTAND_MS, 'später'],
    ]);
  });

  it('eine eben gezeigte Meldung, die der Vorrang verdrängt, wird danach wiederholt; eine ältere nicht', () => {
    expect(spiele([
      { t: 0, text: 'älterer Stand' },
      { t: 50, text: 'Befund 2 von 3: Riss', vorrang: true },
      { t: 50 + VORRANG_HALTEN_MS, takt: true },
    ])).toEqual([[0, 'älterer Stand'], [50, 'Befund 2 von 3: Riss'], [50 + VORRANG_HALTEN_MS, 'älterer Stand']]);
    expect(spiele([
      { t: 0, text: 'alt' },
      { t: NACHHOLEN_MS, text: 'Befund', vorrang: true },
      { t: NACHHOLEN_MS + VORRANG_HALTEN_MS, takt: true },
    ])).toEqual([[0, 'alt'], [NACHHOLEN_MS, 'Befund']]);
  });

  it('Nachholen bei voller Schlange verdrängt die älteste wartende Meldung, nicht die neueste', () => {
    const s = { bis: 0, zuletzt: { text: 'x', t: 1000, vorrang: false }, warten: ['a', 'b', 'c'] };
    const r = ansageMelden(s, { t: 1000 + NACHHOLEN_MS - 1, text: 'Befund', vorrang: true });
    expect(r.schlange.warten).toEqual(['x', 'b', 'c']);
    expect(r.schlange.warten).toHaveLength(WARTEN_MAX);
  });
  it('Vorrang während des Haltens ersetzt sofort; gleiche wartende Texte nur einmal, höchstens drei', () => {
    let s: AnsageSchlange = leereSchlange;
    s = ansageMelden(s, { t: 0, text: 'B1', vorrang: true }).schlange;
    for (const text of ['x', 'y', 'x', 'z', 'w']) s = ansageMelden(s, { t: 10, text }).schlange;
    expect(s.warten).toEqual(['x', 'z', 'w'].slice(-WARTEN_MAX));
    const r = ansageMelden(s, { t: 20, text: 'B2', vorrang: true });
    expect(r.jetzt).toBe('B2');
    expect(r.schlange.bis).toBe(20 + VORRANG_HALTEN_MS);
    expect(r.schlange.warten).toEqual(s.warten);
  });
});
