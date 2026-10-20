import { describe, expect, it } from 'vitest';
import { ENTWURF_MAX_ALTER_MS, entwurfAbgelaufen, entwurfSchluessel, entwurfSchluesselFuer, entwurfText, pruefeEntwurf, vorschauGroesse } from '../src/befundEntwurf';
import type { Entwurf } from '../src/befundEntwurf';

const gut = (over: Partial<Entwurf> = {}): Entwurf => ({
  schema: 1, schluessel: '7|kirche', zeit: 1_759_300_000_000, punkt: [10.5, -2, 3.25], station: 'S012',
  ansicht: 'cam=1.000,2.000,3.000&tgt=0.000,0.000,0.000', poiId: null,
  formular: { title: 'Riss Nordwand', category: 'Mangel', note: 'Frage: seit wann?' }, ...over,
});

describe('entwurfSchluessel', () => {
  it('je Konto und Projekt, Einzelmodus „lokal“', () => {
    expect(entwurfSchluessel(7, 'kirche')).toBe('7|kirche');
    expect(entwurfSchluessel(null, 'kirche')).toBe('lokal|kirche');
    expect(entwurfSchluessel(7, 'kirche')).not.toBe(entwurfSchluessel(8, 'kirche'));
  });
});

describe('entwurfSchluesselFuer', () => {
  it('Einzelmodus lokal, Plattform je Konto, Plattform ohne Konto-ID kein Entwurf', () => {
    expect(entwurfSchluesselFuer({ art: 'einzeln', sicher: true }, 'kirche')).toBe('lokal|kirche');
    expect(entwurfSchluesselFuer({ art: 'plattform', nutzerId: 7 }, 'kirche')).toBe('7|kirche');
    expect(entwurfSchluesselFuer({ art: 'plattform', nutzerId: null }, 'kirche')).toBeNull();
  });
  it('Einzelmodus nur als Rückfall (Server nicht erreichbar, unerwartete Antwort): kein Entwurf', () => {
    expect(entwurfSchluesselFuer({ art: 'einzeln', sicher: false }, 'kirche')).toBeNull();
  });
});

describe('pruefeEntwurf', () => {
  it('gültiger Entwurf bleibt gleich', () => {
    expect(pruefeEntwurf(JSON.parse(JSON.stringify(gut())), '7|kirche')).toEqual(gut());
    expect(pruefeEntwurf(gut({ poiId: 'P-abc-1', station: null }), '7|kirche')).toEqual(gut({ poiId: 'P-abc-1', station: null }));
  });
  it('fremder Schlüssel (anderes Konto) wird nie geliefert', () => {
    expect(pruefeEntwurf(gut(), '8|kirche')).toBeNull();
  });
  it('kaputte oder überlange Daten werden verworfen', () => {
    for (const roh of [null, 'x', { ...gut(), schema: 2 }, { ...gut(), punkt: [0, 0] }, { ...gut(), punkt: [0, Number.NaN, 0] },
      { ...gut(), station: '../x' }, { ...gut(), poiId: 'a b' }, { ...gut(), formular: { title: 'x'.repeat(201), category: '', note: '' } },
      { ...gut(), formular: { title: 1, category: '', note: '' } }, { ...gut(), ansicht: 'x'.repeat(20_001) }, { ...gut(), zeit: -1 },
      { ...gut(), formular: null }, { ...gut(), ansicht: 3 }]) {
      expect(pruefeEntwurf(roh, '7|kirche')).toBeNull();
    }
  });
  it('liefert eine Kopie ohne fremde Zusatzfelder', () => {
    const roh = { ...gut(), extra: '<script>', formular: { ...gut().formular, extra: 1 } };
    expect(pruefeEntwurf(roh, '7|kirche')).toEqual(gut());
  });
});

describe('vorschauGroesse', () => {
  it('verkleinert auf höchstens 320 px Breite, Seitenverhältnis bleibt, nie vergrößert', () => {
    expect(vorschauGroesse(1920, 1080)).toEqual({ breite: 320, hoehe: 180 });
    expect(vorschauGroesse(200, 100)).toEqual({ breite: 200, hoehe: 100 });
    expect(vorschauGroesse(0, 0)).toEqual({ breite: 0, hoehe: 0 });
  });
});

describe('entwurfText', () => {
  it('Titel, Standpunkt und Koordinate als Textalternative', () => {
    expect(entwurfText(gut(), { titelLeer: 'ohne Titel' })).toBe('Vorschau des Entwurfs „Riss Nordwand“, Standpunkt S012, Koordinate 10,500 / −2,000 / 3,250 m');
    expect(entwurfText(gut({ station: null, formular: { title: '', category: '', note: '' } }), { titelLeer: 'ohne Titel' }))
      .toBe('Vorschau des Entwurfs „ohne Titel“, Koordinate 10,500 / −2,000 / 3,250 m');
  });
});

describe('entwurfAbgelaufen (Höchstalter)', () => {
  const jetzt = 1_800_000_000_000;
  it('höchstens 30 Tage: genau 30 Tage alt gilt noch, eine Millisekunde mehr nicht', () => {
    expect(ENTWURF_MAX_ALTER_MS).toBe(30 * 24 * 60 * 60 * 1000);
    expect(entwurfAbgelaufen(gut({ zeit: jetzt }), jetzt)).toBe(false);
    expect(entwurfAbgelaufen(gut({ zeit: jetzt - ENTWURF_MAX_ALTER_MS }), jetzt)).toBe(false);
    expect(entwurfAbgelaufen(gut({ zeit: jetzt - ENTWURF_MAX_ALTER_MS - 1 }), jetzt)).toBe(true);
  });
  it('weit in der Zukunft (verstellte Uhr) gilt als abgelaufen, sonst bliebe er ewig', () => {
    expect(entwurfAbgelaufen(gut({ zeit: jetzt + 60_000 }), jetzt)).toBe(false);
    expect(entwurfAbgelaufen(gut({ zeit: jetzt + 2 * 24 * 60 * 60 * 1000 }), jetzt)).toBe(true);
  });
});
