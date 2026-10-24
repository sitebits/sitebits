// Dritte Stufe der Eingabeschicht: was die Controller-Aktionen (gamepad.ts) in welcher Ansicht bedeuten, als Daten —
// Symbole je Controllertyp, die Belegungstabelle je Ansicht (Übersicht im Viewer, Dialog „Bedienung“) und die kurze
// Kontextleiste am Bildrand. Ein Schema für alle Ansichten: dieselbe Taste heißt überall möglichst dasselbe. Rein (Vitest).
import type { PadAktion, PadTyp } from './gamepad';
import { PAD_NICHT_BELEGT } from './gamepad';

export type PadModus = 'uebersicht' | 'begehen' | 'panorama' | 'menue';
export interface PadKontext {
  modus: PadModus;
  /** Ein Werkzeug außer „Navigieren“ ist gewählt. */
  werkzeug: boolean;
  /** Eine Zeichnung (Messung) ist angefangen. */
  zeichnung: boolean;
  /** Rückfrage am Fadenkreuz offen (vorn/hinten). */
  rueckfrage: boolean;
}

const PS: readonly string[] = ['✕', '○', '□', '△', 'L1', 'R1', 'L2', 'R2', 'Create', 'Options', 'L3', 'R3', 'Kreuz ↑', 'Kreuz ↓', 'Kreuz ←', 'Kreuz →', 'PS', 'Touchpad'];
const XBOX: readonly string[] = ['A', 'B', 'X', 'Y', 'LB', 'RB', 'LT', 'RT', 'Ansicht', 'Menü', 'LS', 'RS', 'Kreuz ↑', 'Kreuz ↓', 'Kreuz ←', 'Kreuz →', 'Xbox', 'Knopf 17'];
const NEUTRAL: readonly string[] = [
  'Unten', 'Rechts', 'Links', 'Oben', 'L-Schulter', 'R-Schulter', 'L-Auslöser', 'R-Auslöser', 'Auswahl', 'Start', 'L-Stick',
  'R-Stick', 'Kreuz ↑', 'Kreuz ↓', 'Kreuz ←', 'Kreuz →', 'Home', 'Touchpad',
];
export const STICK_NAMEN: Record<PadTyp, { links: string; rechts: string }> = {
  playstation: { links: 'Linker Stick', rechts: 'Rechter Stick' },
  xbox: { links: 'Linker Stick', rechts: 'Rechter Stick' },
  neutral: { links: 'Linker Stick', rechts: 'Rechter Stick' },
};
export const TYP_NAME: Record<PadTyp, string> = { playstation: 'PlayStation', xbox: 'Xbox', neutral: 'Controller' };

/** Kurzname eines Knopfs für den Controllertyp; nicht belegt (−1) oder unbekannt = ''. */
export function symbol(typ: PadTyp, knopf: number): string {
  const t = typ === 'playstation' ? PS : typ === 'xbox' ? XBOX : NEUTRAL;
  return Number.isInteger(knopf) && knopf >= 0 ? (t[knopf] ?? '') : '';
}

export interface Zeile { eingabe: string; wirkung: string }

/** Belegung je Ansicht, vollständig (auch was nur mit Werkzeug wirkt). Nicht belegte Aktionen fehlen. */
export function belegungsTabelle(modus: PadModus, typ: PadTyp, b: Record<PadAktion, number>): Zeile[] {
  const s = (a: PadAktion): string => (b[a] === PAD_NICHT_BELEGT ? '' : symbol(typ, b[a]));
  const paar = (a: PadAktion, c: PadAktion): string => [s(a), s(c)].filter(Boolean).join(' / ');
  const st = STICK_NAMEN[typ];
  const z = (eingabe: string, wirkung: string): Zeile | null => (eingabe ? { eingabe, wirkung } : null);
  let zeilen: (Zeile | null)[];
  switch (modus) {
    case 'menue':
      zeilen = [
        z(`${st.links}, ${paar('tempo-hoch', 'tempo-runter')}`, 'Auswahl bewegen'),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Auswahl bewegen, Wert ändern'),
        z(s('bestaetigen'), 'Auswählen'),
        z(s('zurueck'), 'Schließen'),
        z(s('menue'), 'Menü schließen'),
      ];
      break;
    case 'begehen':
      zeilen = [
        z(st.links, 'Gehen'),
        z(st.rechts, 'Umschauen'),
        z(paar('runter', 'hoch'), 'Abwärts / aufwärts'),
        z(paar('werkzeug-voriges', 'werkzeug-naechstes'), 'Werkzeug wechseln'),
        z(s('bestaetigen'), 'Punkt setzen (mit Werkzeug), Rückfrage bestätigen'),
        z(s('zurueck'), 'Zurück: Zeichnung abbrechen, sonst Begehen beenden'),
        z(s('zielen'), 'Zielen (fein, mit Fang)'),
        z(s('kontext'), 'Befund setzen'),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Standpunkt wechseln'),
        z(paar('tempo-hoch', 'tempo-runter'), 'Tempo schneller / langsamer'),
        z(s('schnell'), 'Sprinten (bis der Stick ruht)'),
        z(s('ausrichten'), 'Blick waagerecht'),
        z(s('menue'), 'Menü'),
        z(s('belegung'), 'Belegung ein/aus'),
      ];
      break;
    case 'panorama':
      zeilen = [
        z(st.rechts, 'Umsehen'),
        z(paar('runter', 'hoch'), 'Heraus- / hineinzoomen'),
        z(paar('werkzeug-voriges', 'werkzeug-naechstes'), 'Werkzeug wechseln'),
        z(s('bestaetigen'), 'Punkt setzen (mit Werkzeug)'),
        z(s('zurueck'), 'Panorama verlassen'),
        z(s('zielen'), 'Fang ein/aus'),
        z(s('kontext'), 'Befund setzen (mit Werkzeug)'),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Standpunkt wechseln'),
        z(s('ausrichten'), 'Blick waagerecht'),
        z(s('menue'), 'Menü'),
        z(s('belegung'), 'Belegung ein/aus'),
      ];
      break;
    default:
      zeilen = [
        z(st.links, 'Verschieben'),
        z(st.rechts, 'Um den Drehpunkt drehen'),
        z(paar('runter', 'hoch'), 'Heraus- / hineinzoomen'),
        z(paar('werkzeug-voriges', 'werkzeug-naechstes'), 'Werkzeug wechseln'),
        z(s('bestaetigen'), 'Punkt in Bildmitte setzen (mit Werkzeug)'),
        z(s('zurueck'), 'Zeichnung abbrechen'),
        z(s('zielen'), 'Fang ein/aus'),
        z(s('kontext'), 'Begehen starten'),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Standpunkt wechseln'),
        z(s('schnell'), 'Schneller verschieben (halten)'),
        z(s('ausrichten'), 'Alles zeigen'),
        z(s('menue'), 'Menü'),
        z(s('belegung'), 'Belegung ein/aus'),
      ];
  }
  return zeilen.filter((x): x is Zeile => x !== null);
}

/** Kurze Leiste am Bildrand wie in Spielen: „✕ Punkt setzen · ○ Abbrechen“. Höchstens fünf Einträge, nur was jetzt wirkt. */
export function kontextLeiste(k: PadKontext, typ: PadTyp, b: Record<PadAktion, number>): { knopf: string; text: string }[] {
  const s = (a: PadAktion): string => (b[a] === PAD_NICHT_BELEGT ? '' : symbol(typ, b[a]));
  const e = (a: PadAktion, text: string): { knopf: string; text: string } => ({ knopf: s(a), text });
  // Zwei Richtungen des Steuerkreuzes zusammen: „Kreuz ↑↓“ statt „Kreuz ↑ / Kreuz ↓“.
  const hochRunter = (): string => {
    const t = [s('tempo-hoch'), s('tempo-runter')].filter(Boolean);
    return t.every((x) => x.startsWith('Kreuz ')) && t.length === 2 ? `Kreuz ${t.map((x) => x.slice(6)).join('')}` : t.join('/');
  };
  let liste: { knopf: string; text: string }[];
  if (k.modus === 'menue') {
    liste = [e('bestaetigen', 'Auswählen'), e('zurueck', 'Schließen'), { knopf: hochRunter(), text: 'Auswahl' }];
  } else if (k.rueckfrage) {
    liste = [e('bestaetigen', 'Bestätigen'), { knopf: hochRunter(), text: 'Wechseln' }, e('zurueck', 'Abbrechen')];
  } else if (k.modus === 'begehen') {
    liste = [
      ...(k.werkzeug ? [e('bestaetigen', 'Punkt setzen')] : [e('werkzeug-naechstes', 'Werkzeug')]),
      e('kontext', 'Befund'),
      e('zielen', 'Zielen'),
      e('zurueck', k.zeichnung ? 'Abbrechen' : 'Beenden'),
      e('belegung', 'Belegung'),
    ];
  } else if (k.modus === 'panorama') {
    liste = [
      ...(k.werkzeug ? [e('bestaetigen', 'Punkt setzen'), e('kontext', 'Befund')] : [e('werkzeug-naechstes', 'Werkzeug')]),
      e('zurueck', 'Panorama verlassen'),
      e('belegung', 'Belegung'),
    ];
  } else {
    liste = [
      ...(k.werkzeug ? [e('bestaetigen', 'Punkt setzen')] : []),
      ...(k.zeichnung ? [e('zurueck', 'Abbrechen')] : []),
      e('kontext', 'Begehen'),
      e('ausrichten', 'Alles zeigen'),
      e('belegung', 'Belegung'),
    ];
  }
  return liste.filter((x) => x.knopf !== '').slice(0, 5);
}

export const MODUS_TITEL: Record<PadModus, string> = {
  uebersicht: 'Übersicht', begehen: 'Begehen', panorama: 'Panorama', menue: 'Menü',
};
