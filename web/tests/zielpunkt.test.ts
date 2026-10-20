// Ablauf „Zielpunkt setzen“ mit nachgebildeter Strahlsuche: Sichtbarkeitsregel, Rückfrage und zweite
// Suche im gewählten Tiefenfenster.
import { describe, expect, it } from 'vitest';
import { suchfenster } from '../src/pickMath';
import type { Bewertung } from '../src/pickMath';
import type { PickOptions, RayPickResult, Vorschau } from '../src/rayPick';
import { keinPunktGrund, keinPunktText, rueckfrageTexte, zielpunktSetzen } from '../src/zielpunkt';
import type { ZielQuelle } from '../src/zielpunkt';

type Frage = Extract<Bewertung, { art: 'rueckfrage' }>;

const wand = Array.from({ length: 40 }, (_, i) => 5 + i * 0.001);
const hinten = Array.from({ length: 20 }, (_, i) => 8 + i * 0.001);

const vorschau = (mitte: number[], fenster: number[]): Vorschau => ({
  punktWelt: mitte.length ? [Math.min(...mitte), 0, 0] : null,
  tiefeM: mitte.length ? Math.min(...mitte) : null,
  mitteTiefen: mitte,
  fensterTiefen: fenster,
  ms: 1,
});
const treffer = (tiefe: number, level: 'fine' | 'coarse' = 'fine'): RayPickResult => ({
  point: [tiefe, 0, 0],
  info: { level, ms: 1, wideCandidates: 10, nearCandidates: 3, tiefeM: tiefe },
});
const nichts: RayPickResult = { point: null, info: { level: 'none', ms: 1, wideCandidates: 0, nearCandidates: 0, reason: 'empty' } };

/** Feste Vorschau; Picks der Reihe nach aus `antworten`, Aufrufe werden mitgeschrieben. */
function quelle(v: Vorschau, antworten: RayPickResult[]): ZielQuelle & { picks: (PickOptions | undefined)[]; vorschauen: number } {
  const q = {
    picks: [] as (PickOptions | undefined)[],
    vorschauen: 0,
    vorschauAtScreen: (): Vorschau => {
      q.vorschauen++;
      return v;
    },
    pickAtScreen: (_x: number, _y: number, opts?: PickOptions): Promise<RayPickResult> => {
      q.picks.push(opts);
      const r = antworten.shift();
      if (!r) throw new Error('mehr Picks als erwartet');
      return Promise.resolve(r);
    },
  };
  return q;
}

const nieFragen = (): Promise<'vorn' | 'hinten' | null> => {
  throw new Error('Rückfrage nicht erwartet');
};

describe('zielpunktSetzen', () => {
  it('eindeutige Wand: gesetzt mit dem Feintreffer', async () => {
    const q = quelle(vorschau([5, 5.02], wand), [treffer(5.01)]);
    const r = await zielpunktSetzen({ quelle: q, x: 10, y: 20, opts: { snapEdges: true }, sichtbarkeitPruefen: true, frage: nieFragen });
    expect(r).toMatchObject({ art: 'gesetzt', punkt: [5.01, 0, 0], level: 'fine' });
    expect(q.picks).toEqual([{ snapEdges: true }]);
    expect(q.vorschauen).toBe(1);
  });

  it('verdeckt: kein Treffer, keine Rückfrage', async () => {
    let gefragt = 0;
    const q = quelle(vorschau([5], wand), [treffer(9)]);
    const r = await zielpunktSetzen({
      quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true,
      frage: () => {
        gefragt++;
        return Promise.resolve('hinten');
      },
    });
    expect(r.art).toBe('kein-treffer');
    expect(gefragt).toBe(0);
  });

  it('kein Punkt aus der feinen Suche: kein Treffer', async () => {
    const q = quelle(vorschau([5], wand), [nichts]);
    const r = await zielpunktSetzen({ quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true, frage: nieFragen });
    expect(r.art).toBe('kein-treffer');
  });

  it('Rückfrage, Antwort = getroffene Schicht: gesetzt mit dem ersten Punkt, ohne zweite Suche', async () => {
    const q = quelle(vorschau([8, 8.01], [...wand, ...hinten]), [treffer(8.005)]);
    let frage: Frage | null = null;
    const r = await zielpunktSetzen({
      quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true,
      frage: (b) => {
        frage = b;
        return Promise.resolve('hinten');
      },
    });
    expect(frage).toMatchObject({ trefferSchicht: 'hinten' });
    expect(r).toMatchObject({ art: 'gesetzt', punkt: [8.005, 0, 0] });
    expect(q.picks).toHaveLength(1);
  });

  it('Rückfrage, Antwort = andere Schicht: zweite Suche im Tiefenfenster dieser Schicht', async () => {
    const q = quelle(vorschau([8, 8.01], [...wand, ...hinten]), [treffer(8.005), treffer(5.02)]);
    let frage: Frage | null = null;
    const r = await zielpunktSetzen({
      quelle: q, x: 0, y: 0, opts: { snapEdges: false }, sichtbarkeitPruefen: true,
      frage: (b) => {
        frage = b;
        return Promise.resolve('vorn');
      },
    });
    expect(q.picks).toHaveLength(2);
    expect(q.picks[1]!.tiefenFenster).toEqual(suchfenster(frage!, 'vorn'));
    expect(q.picks[1]!.snapEdges).toBe(false);
    expect(r).toMatchObject({ art: 'gesetzt', punkt: [5.02, 0, 0] });
  });

  it('andere Schicht ohne Punkt im Fenster: kein Treffer', async () => {
    const q = quelle(vorschau([8, 8.01], [...wand, ...hinten]), [treffer(8.005), nichts]);
    const r = await zielpunktSetzen({ quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true, frage: () => Promise.resolve('vorn') });
    expect(r.art).toBe('kein-treffer');
  });

  it('Rückfrage abgebrochen: nichts gesetzt', async () => {
    const q = quelle(vorschau([8], [...wand, ...hinten]), [treffer(8.005)]);
    const r = await zielpunktSetzen({ quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true, frage: () => Promise.resolve(null) });
    expect(r.art).toBe('abgebrochen');
    expect(q.picks).toHaveLength(1);
  });

  it('grobe Stufe: Rückfrage vor dem Setzen', async () => {
    const q = quelle(vorschau([5], wand), [treffer(5, 'coarse')]);
    let gefragt = 0;
    const r = await zielpunktSetzen({
      quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true,
      frage: (b) => {
        gefragt++;
        return Promise.resolve(b.trefferSchicht);
      },
    });
    expect(gefragt).toBe(1);
    expect(r).toMatchObject({ art: 'gesetzt', level: 'coarse' });
  });

  it('ohne Sichtbarkeitsprüfung: keine Bewertung, keine Vorschau', async () => {
    // Die Vorschau würde „verdeckt“ ergeben — sie wird gar nicht gelesen.
    const q = quelle(vorschau([5], wand), [treffer(9)]);
    const r = await zielpunktSetzen({ quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: false, frage: nieFragen });
    expect(r).toMatchObject({ art: 'gesetzt', punkt: [9, 0, 0] });
    expect(q.vorschauen).toBe(0);
  });

  it('ohne Sichtbarkeitsprüfung und ohne Punkt: kein Treffer', async () => {
    const q = quelle(vorschau([], []), [nichts]);
    const r = await zielpunktSetzen({ quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: false, frage: nieFragen });
    expect(r.art).toBe('kein-treffer');
  });

  it('die Vorschau wird vor der feinen Suche gelesen (dieselbe Kamera)', async () => {
    const reihenfolge: string[] = [];
    const q: ZielQuelle = {
      vorschauAtScreen: () => {
        reihenfolge.push('vorschau');
        return vorschau([5], wand);
      },
      pickAtScreen: () => {
        reihenfolge.push('pick');
        return Promise.resolve(treffer(5));
      },
    };
    await zielpunktSetzen({ quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true, frage: nieFragen });
    expect(reihenfolge).toEqual(['vorschau', 'pick']);
  });

  it('Ansicht hat sich seit der Eingabe bewegt: abgebrochen, keine zweite Suche', async () => {
    const q = quelle(vorschau([8, 8.01], [...wand, ...hinten]), [treffer(8.005), treffer(5.02)]);
    const r = await zielpunktSetzen({
      quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true,
      frage: () => Promise.resolve('vorn'),
      ansichtUnveraendert: () => false,
    });
    expect(r).toMatchObject({ art: 'abgebrochen', grund: 'ansicht' });
    expect(q.picks).toHaveLength(1);
  });

  it('Ansicht unverändert: zweite Suche wie gewohnt', async () => {
    const q = quelle(vorschau([8, 8.01], [...wand, ...hinten]), [treffer(8.005), treffer(5.02)]);
    const r = await zielpunktSetzen({
      quelle: q, x: 0, y: 0, opts: {}, sichtbarkeitPruefen: true,
      frage: () => Promise.resolve('vorn'),
      ansichtUnveraendert: () => true,
    });
    expect(r).toMatchObject({ art: 'gesetzt', punkt: [5.02, 0, 0] });
  });
});

describe('Texte der Rückfrage je Grund', () => {
  it('zwei Flächen: vordere oder hintere Fläche, Ansage unverändert', () => {
    expect(rueckfrageTexte('zwei-schichten')).toEqual({
      titel: 'Welche Fläche?',
      ansage: 'Zwei Flächen. Vordere Fläche oder hintere Fläche. Pfeiltasten wählen, Enter bestätigt.',
      vorn: 'Vordere Fläche',
      hinten: 'Hintere Fläche',
      hintenBrichtAb: false,
    });
  });
  it('grobe Stufe: trotzdem setzen oder nicht setzen', () => {
    expect(rueckfrageTexte('grobe-stufe')).toEqual({
      titel: 'Punkt aus grober Stufe – trotzdem setzen?',
      ansage: 'Punkt aus grober Stufe – trotzdem setzen? Pfeiltasten wählen, Enter bestätigt.',
      vorn: 'Setzen',
      hinten: 'Nicht setzen',
      hintenBrichtAb: true,
    });
  });
});

describe('keinPunktText: ein Satz für Maus und Fadenkreuz', () => {
  const o = { folge: 'kein Befund gesetzt', ausgeblendet: 'Punktwolke ausgeblendet – zum Messen einblenden (Taste 1)' };
  it('ausgeblendete Punktwolke: nur der Hinweis zum Einblenden', () => {
    expect(keinPunktGrund({ reason: 'hidden' })).toBe('hidden');
    expect(keinPunktText('hidden', o)).toBe(o.ausgeblendet);
  });
  it('verdeckt: Ursache und Folge', () => {
    expect(keinPunktGrund({ reason: undefined }, 'verdeckt')).toBe('verdeckt');
    expect(keinPunktText('verdeckt', o)).toBe('Punkt verdeckt – kein Befund gesetzt');
  });
  it('nur grobe Stufe: je nach Ursache der feinen Suche', () => {
    expect(keinPunktGrund({ reason: 'imprecise' })).toBe('imprecise');
    expect(keinPunktText('imprecise', o)).toBe('kein Originalpunkt, grobe Stufe zu ungenau – kein Befund gesetzt');
    expect(keinPunktText('imprecise', { ...o, feinGrund: 'empty' })).toBe('kein Originalpunkt, grobe Stufe zu ungenau – kein Befund gesetzt');
    expect(keinPunktText('imprecise', { ...o, feinGrund: 'timeout' })).toBe('Feinsuche nicht rechtzeitig fertig – kein Befund gesetzt');
    expect(keinPunktText('imprecise', { ...o, feinGrund: 'error' })).toBe('Feinsuche fehlgeschlagen – kein Befund gesetzt');
  });
  it('leer (kein Punkt am Strahl, Zeitbudget, Fehler): kein Treffer', () => {
    for (const reason of [undefined, 'empty', 'timeout', 'error'] as const) expect(keinPunktGrund({ reason })).toBe('leer');
    expect(keinPunktText('leer', o)).toBe('kein Treffer – kein Befund gesetzt');
    expect(keinPunktText('leer', { ...o, folge: 'kein Messpunkt gesetzt' })).toBe('kein Treffer – kein Messpunkt gesetzt');
  });
  it('ausgeblendet geht vor verdeckt', () => {
    expect(keinPunktGrund({ reason: 'hidden' }, 'verdeckt')).toBe('hidden');
  });
});
