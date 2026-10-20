// Setzen eines Zielpunkts (Fadenkreuz im Begehen und Mausklick im Orbit): feine Suche an den
// Bildschirmkoordinaten, danach die Sichtbarkeitsregel gegen die Vorschau derselben Stelle. Ein verdeckter
// Treffer wird nie gesetzt; liegen dort zwei Flächen (oder stammt der Treffer nur aus der groben Stufe),
// entscheidet eine Rückfrage (zweite Suche im Fenster der gewählten Fläche, pickMath.suchfenster). Gesetzt wird ausschließlich ein Punkt aus der Strahlsuche — die Vorschau
// zeigt nur an. Ohne DOM: die Rückfrage kommt als Rückruf herein.
import { bewerteTreffer, suchfenster } from './pickMath';
import type { Bewertung, FineFailure } from './pickMath';
import type { PickInfo, PickOptions, RayPickResult, Vorschau } from './rayPick';

type Vec3 = readonly [number, number, number];
export type Rueckfrage = Extract<Bewertung, { art: 'rueckfrage' }>;
export type Wahl = 'vorn' | 'hinten';

/** Beschriftung der Rückfrage. `hintenBrichtAb`: der zweite Knopf setzt nichts (Antwort null). */
export interface RueckfrageTexte {
  titel: string;
  ansage: string;
  vorn: string;
  hinten: string;
  hintenBrichtAb: boolean;
}

/** Text je Grund: zwei Flächen → welche; nur grobe Stufe (eine Fläche) → trotzdem setzen oder nicht. */
export function rueckfrageTexte(grund: Rueckfrage['grund']): RueckfrageTexte {
  const bedienung = 'Pfeiltasten wählen, Enter bestätigt.';
  if (grund === 'grobe-stufe') {
    const titel = 'Punkt aus grober Stufe – trotzdem setzen?';
    return { titel, ansage: `${titel} ${bedienung}`, vorn: 'Setzen', hinten: 'Nicht setzen', hintenBrichtAb: true };
  }
  return {
    titel: 'Welche Fläche?',
    ansage: `Zwei Flächen. Vordere Fläche oder hintere Fläche. ${bedienung}`,
    vorn: 'Vordere Fläche',
    hinten: 'Hintere Fläche',
    hintenBrichtAb: false,
  };
}

/** Warum kein Punkt gesetzt wurde: Punktwolke ausgeblendet, Treffer verdeckt, nur grobe Stufe (zu
 *  ungenau) oder gar kein Punkt am Strahl. */
export type KeinPunktGrund = 'hidden' | 'verdeckt' | 'imprecise' | 'leer';

/** Ein Befund-Formular ist offen: Setzen, Werkzeugwechsel und ein zweites Formular warten, bis es gespeichert
 *  oder verworfen ist. */
export const FORMULAR_OFFEN = 'Formular offen – erst speichern oder abbrechen';

export function keinPunktGrund(info: Pick<PickInfo, 'reason'>, grund?: 'verdeckt'): KeinPunktGrund {
  if (info.reason === 'hidden') return 'hidden';
  if (grund === 'verdeckt') return 'verdeckt';
  return info.reason === 'imprecise' ? 'imprecise' : 'leer';
}

/** Ein Satz für Maus und Fadenkreuz: „<Ursache> – <Folge>“, z. B. „kein Treffer – kein Befund gesetzt“.
 *  `ausgeblendet` ist der Hinweis zur ausgeblendeten Punktwolke (nennt die Taste zum Einblenden). */
export function keinPunktText(grund: KeinPunktGrund, o: { folge: string; ausgeblendet: string; feinGrund?: FineFailure; mesh?: boolean }): string {
  if (grund === 'hidden') return o.ausgeblendet;
  if (grund === 'imprecise' && o.mesh) return 'Feinste Stufe nicht rechtzeitig — erneut klicken';
  const ursache =
    grund === 'verdeckt'
      ? 'Punkt verdeckt'
      : grund === 'leer'
        ? 'kein Treffer'
        : o.feinGrund === 'timeout'
          ? 'Feinsuche nicht rechtzeitig fertig'
          : o.feinGrund === 'error'
            ? 'Feinsuche fehlgeschlagen'
            : 'kein Originalpunkt, grobe Stufe zu ungenau';
  return `${ursache} – ${o.folge}`;
}

/** `info` = Diagnose der maßgeblichen Suche (Hinweistexte, E2E); `grund` nennt den verdeckten Treffer. */
export type SetzErgebnis =
  | { art: 'gesetzt'; punkt: Vec3; level: 'fine' | 'coarse'; info: PickInfo }
  | { art: 'kein-treffer'; info: PickInfo; grund?: 'verdeckt' }
  | { art: 'abgebrochen'; info: PickInfo; grund?: 'ansicht' };

export interface ZielQuelle {
  pickAtScreen(x: number, y: number, opts?: PickOptions): Promise<RayPickResult>;
  vorschauAtScreen(x: number, y: number): Vorschau;
}

const gesetzt = (r: RayPickResult): SetzErgebnis =>
  r.point && r.info.level !== 'none'
    ? { art: 'gesetzt', punkt: [r.point[0], r.point[1], r.point[2]], level: r.info.level, info: r.info }
    : { art: 'kein-treffer', info: r.info };

/**
 * ``sichtbarkeitPruefen: false`` (Panorama): die Punktwolke ist dort nicht das sichtbare Bild, es gilt der
 * Treffer der feinen Suche wie beim Klick bisher. Die Vorschau wird dann gar nicht gelesen.
 */
export async function zielpunktSetzen(o: {
  quelle: ZielQuelle;
  x: number;
  y: number;
  opts: PickOptions;
  sichtbarkeitPruefen: boolean;
  frage: (b: Rueckfrage) => Promise<Wahl | null>;
  /** Steht die Kamera noch wie bei der Eingabe? Sonst liefe die zweite Suche auf einem anderen Strahl,
   *  dessen Sichtbarkeit nie geprüft wurde → abgebrochen. Ohne Angabe: ja. */
  ansichtUnveraendert?: () => boolean;
}): Promise<SetzErgebnis> {
  // Frisch und vor der feinen Suche: beide sehen dieselbe Kamera.
  const v = o.sichtbarkeitPruefen ? o.quelle.vorschauAtScreen(o.x, o.y) : null;
  const r = await o.quelle.pickAtScreen(o.x, o.y, o.opts);
  if (!v) return gesetzt(r);
  const b = bewerteTreffer({
    trefferTiefe: r.info.tiefeM ?? null,
    level: r.info.level,
    mitteTiefen: v.mitteTiefen,
    fensterTiefen: v.fensterTiefen,
  });
  if (b.art === 'kein-treffer') return { art: 'kein-treffer', info: r.info };
  if (b.art === 'verdeckt') return { art: 'kein-treffer', info: r.info, grund: 'verdeckt' };
  if (b.art === 'eindeutig') return gesetzt(r);
  const w = await o.frage(b);
  if (w === null) return { art: 'abgebrochen', info: r.info };
  if (w === b.trefferSchicht) return gesetzt(r);
  if (o.ansichtUnveraendert && !o.ansichtUnveraendert()) return { art: 'abgebrochen', info: r.info, grund: 'ansicht' };
  return gesetzt(await o.quelle.pickAtScreen(o.x, o.y, { ...o.opts, tiefenFenster: suchfenster(b, w) }));
}
