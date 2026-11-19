// Dritte Stufe der Eingabeschicht: was die Controller-Aktionen (gamepad.ts) in welcher Ansicht bedeuten, als Daten —
// Symbole je Controllertyp, die Belegungstabelle je Ansicht (Übersicht im Viewer, Dialog „Bedienung“) und die kurze
// Kontextleiste am Bildrand. Ein Schema für alle Ansichten: dieselbe Taste heißt überall möglichst dasselbe. Rein (Vitest).
import type { PadAktion, PadTyp } from './gamepad';
import { PAD_NICHT_BELEGT } from './gamepad';
import type { KeyAction } from './keymap';

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

/** Prüft, ob ein Kürzel im Ansichtsprofil wirkt (main.ts: aktionErlaubt aus profil.ts). */
export type PadErlaubt = (a: KeyAction) => boolean;

/** Ansichten, die die Hilfe zeigt: ohne erlaubtes „Begehen starten“ ist das Begehen nicht erreichbar. */
export function hilfeModi(erlaubt?: PadErlaubt): PadModus[] {
  const alle: PadModus[] = ['uebersicht', 'begehen', 'panorama', 'menue'];
  return erlaubt && !erlaubt({ type: 'begehen-start' }) ? alle.filter((m) => m !== 'begehen') : alle;
}

/** Belegung je Ansicht, vollständig (auch was nur mit Werkzeug wirkt). Nicht belegte Aktionen fehlen. Mit `erlaubt`
 *  (Ansichtsprofile) fehlen auch Zeilen, deren Kürzel das Profil sperrt (padSteuerung.ts → run → aktionErlaubt). */
export function belegungsTabelle(modus: PadModus, typ: PadTyp, b: Record<PadAktion, number>, erlaubt?: PadErlaubt): Zeile[] {
  const s = (a: PadAktion): string => (b[a] === PAD_NICHT_BELEGT ? '' : symbol(typ, b[a]));
  const paar = (a: PadAktion, c: PadAktion): string => [s(a), s(c)].filter(Boolean).join(' / ');
  const st = STICK_NAMEN[typ];
  // `wirkt`: das Kürzel, das der Knopf in dieser Ansicht über den Verteiler auslöst (padSteuerung.ts); ohne Angabe
  // wirkt er an run vorbei (Sticks, Menü, Setzen mit Werkzeug) und bleibt in jedem Profil stehen.
  const z = (eingabe: string, wirkung: string, wirkt?: KeyAction): Zeile | null =>
    (eingabe && !(wirkt && erlaubt && !erlaubt(wirkt)) ? { eingabe, wirkung } : null);
  const werkzeug: KeyAction = { type: 'werkzeug-wechsel', richtung: 1 };
  const standpunkt: KeyAction = { type: 'standpunkt', richtung: 1 };
  const fang: KeyAction = { type: 'toggle-snap' };
  const befund: KeyAction = { type: 'befund' };
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
        z(paar('werkzeug-voriges', 'werkzeug-naechstes'), 'Werkzeug wechseln', werkzeug),
        z(s('bestaetigen'), 'Punkt setzen (mit Werkzeug), Rückfrage bestätigen'),
        z(s('zurueck'), 'Zurück: Zeichnung abbrechen, sonst Begehen beenden'),
        z(s('zielen'), 'Zielen (fein, mit Fang)'),
        z(s('kontext'), 'Befund setzen', befund),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Standpunkt wechseln', standpunkt),
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
        z(paar('werkzeug-voriges', 'werkzeug-naechstes'), 'Werkzeug wechseln', werkzeug),
        z(s('bestaetigen'), 'Punkt setzen (mit Werkzeug)'),
        z(s('zurueck'), 'Panorama verlassen', { type: 'leave-panorama' }),
        z(s('zielen'), 'Fang ein/aus', fang),
        z(s('kontext'), 'Befund setzen (mit Werkzeug)', befund),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Standpunkt wechseln', standpunkt),
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
        z(paar('werkzeug-voriges', 'werkzeug-naechstes'), 'Werkzeug wechseln', werkzeug),
        z(s('bestaetigen'), 'Punkt in Bildmitte setzen (mit Werkzeug)'),
        z(s('zurueck'), 'Zeichnung abbrechen', { type: 'cancel-drawing' }),
        z(s('zielen'), 'Fang ein/aus', fang),
        z(s('kontext'), 'Begehen starten', { type: 'begehen-start' }),
        z(paar('standpunkt-voriger', 'standpunkt-naechster'), 'Standpunkt wechseln', standpunkt),
        z(s('schnell'), 'Schneller verschieben (halten)'),
        z(s('ausrichten'), 'Alles zeigen', { type: 'alles-zeigen' }),
        z(s('menue'), 'Menü'),
        z(s('belegung'), 'Belegung ein/aus'),
      ];
  }
  return zeilen.filter((x): x is Zeile => x !== null);
}

/** Kurze Leiste am Bildrand wie in Spielen: „✕ Punkt setzen · ○ Abbrechen“. Höchstens fünf Einträge, nur was jetzt wirkt
 *  (mit `erlaubt` auch nur, was das Ansichtsprofil zulässt, wie belegungsTabelle). */
export function kontextLeiste(k: PadKontext, typ: PadTyp, b: Record<PadAktion, number>, erlaubt?: PadErlaubt): { knopf: string; text: string }[] {
  const s = (a: PadAktion): string => (b[a] === PAD_NICHT_BELEGT ? '' : symbol(typ, b[a]));
  const e = (a: PadAktion, text: string, wirkt?: KeyAction): { knopf: string; text: string } =>
    ({ knopf: wirkt && erlaubt && !erlaubt(wirkt) ? '' : s(a), text });
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
      ...(k.werkzeug ? [e('bestaetigen', 'Punkt setzen'), e('kontext', 'Befund', { type: 'befund' })] : [e('werkzeug-naechstes', 'Werkzeug')]),
      e('zurueck', 'Panorama verlassen'),
      e('belegung', 'Belegung'),
    ];
  } else {
    liste = [
      ...(k.werkzeug ? [e('bestaetigen', 'Punkt setzen')] : []),
      ...(k.zeichnung ? [e('zurueck', 'Abbrechen')] : []),
      e('kontext', 'Begehen', { type: 'begehen-start' }),
      e('ausrichten', 'Alles zeigen'),
      e('belegung', 'Belegung'),
    ];
  }
  return liste.filter((x) => x.knopf !== '').slice(0, 5);
}

export const MODUS_TITEL: Record<PadModus, string> = {
  uebersicht: 'Übersicht', begehen: 'Begehen', panorama: 'Panorama', menue: 'Menü',
};
