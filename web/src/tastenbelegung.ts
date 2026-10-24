// Tastenbelegung als Daten (Anforderungen an die Bedienung): jede Aktion
// hat eine umbelegbare Taste in genau einer Ebene. „global" wirkt seitenweit (außer Textfeldern/Dialogen),
// „begehen" nur im Kamerazustand Begehen mit Fokus auf der Leinwand und hat dort Vorrang. Rein (Vitest).
import { PAD_AKTIONEN, PAD_KNOPF_MAX, PAD_NICHT_BELEGT, PAD_VORGABE } from './gamepad';
import type { PadAktion } from './gamepad';

export type Ebene = 'global' | 'begehen';
export interface Akkord { taste: string; strg: boolean; alt: boolean }

export type AktionId =
  | 'werkzeug.navigate' | 'werkzeug.distance' | 'werkzeug.polyline' | 'werkzeug.area' | 'werkzeug.height' | 'werkzeug.poi'
  | 'panel.ansicht' | 'panel.pois' | 'panel.karte' | 'panel.ebenen'
  | 'messliste' | 'kantenfang' | 'alles-zeigen' | 'schnittbox.verschieben' | 'schnittbox.skalieren'
  | 'begehen' | 'tastenhilfe' | 'standpunkt.voriger' | 'standpunkt.naechster' | 'teilen'
  | 'koordinaten' | 'koordinate.kopieren'
  | 'begehen.vor' | 'begehen.zurueck' | 'begehen.links' | 'begehen.rechts' | 'begehen.runter' | 'begehen.hoch'
  | 'begehen.schnell' | 'begehen.zielen' | 'begehen.setzen'
  | 'begehen.drehen-links' | 'begehen.drehen-rechts' | 'begehen.neigen-hoch' | 'begehen.neigen-runter'
  | 'begehen.werkzeug-voriges' | 'begehen.werkzeug-naechstes' | 'begehen.ansage' | 'begehen.befund';

export type HalteAktion =
  | 'begehen.vor' | 'begehen.zurueck' | 'begehen.links' | 'begehen.rechts' | 'begehen.runter' | 'begehen.hoch'
  | 'begehen.schnell' | 'begehen.zielen' | 'begehen.setzen'
  | 'begehen.drehen-links' | 'begehen.drehen-rechts' | 'begehen.neigen-hoch' | 'begehen.neigen-runter';

export const HALTE_AKTIONEN: readonly HalteAktion[] = [
  'begehen.vor', 'begehen.zurueck', 'begehen.links', 'begehen.rechts', 'begehen.runter', 'begehen.hoch',
  'begehen.schnell', 'begehen.zielen', 'begehen.setzen',
  'begehen.drehen-links', 'begehen.drehen-rechts', 'begehen.neigen-hoch', 'begehen.neigen-runter',
];

export interface AktionDef { id: AktionId; ebene: Ebene; titel: string; art: 'einmal' | 'halten'; vorgabe: Akkord | null }

const t = (taste: string): Akkord => ({ taste, strg: false, alt: false });
const def = (id: AktionId, ebene: Ebene, titel: string, taste: string, art: 'einmal' | 'halten' = 'einmal'): AktionDef =>
  ({ id, ebene, titel, art, vorgabe: t(taste) });

export const AKTIONEN: readonly AktionDef[] = [
  def('werkzeug.navigate', 'global', 'Navigieren', 'v'),
  def('werkzeug.distance', 'global', 'Strecke messen', 'd'),
  def('werkzeug.polyline', 'global', 'Polylinie messen', 'l'),
  def('werkzeug.area', 'global', 'Fläche messen', 'a'),
  def('werkzeug.height', 'global', 'Höhe messen', 'h'),
  def('werkzeug.poi', 'global', 'Markieren (Befund setzen per Klick)', 'p'),
  def('panel.ansicht', 'global', 'Panel Ansicht', 'i'),
  def('panel.pois', 'global', 'Panel POIs', 'o'),
  def('panel.karte', 'global', 'Panel Karte', 'k'),
  def('panel.ebenen', 'global', 'Panel Ebenen', 'n'),
  def('messliste', 'global', 'Messliste ein/aus', 'm'),
  def('kantenfang', 'global', 'Fang ein/aus (Kanten, Mesh-Ecken)', 'e'),
  def('alles-zeigen', 'global', 'Alles zeigen', 'f'),
  def('schnittbox.verschieben', 'global', 'Schnittbox verschieben', 'g'),
  def('schnittbox.skalieren', 'global', 'Schnittbox Größe ändern', 's'),
  def('begehen', 'global', 'Begehen starten', 'w'),
  def('tastenhilfe', 'global', 'Bedienung (Hilfe)', '?'),
  def('standpunkt.voriger', 'global', 'Voriger Standpunkt', '['),
  def('standpunkt.naechster', 'global', 'Nächster Standpunkt', ']'),
  // Neue Aktionen stehen am Ende ihrer Ebene: bei einem Konflikt mit einer schon umbelegten Taste weicht beim
  // Laden die spätere Aktion (ladeBelegung), die bestehende Belegung bleibt.
  def('teilen', 'global', 'Befundlink teilen', 't'),
  // K ist das Panel Karte: Koordinatenanzeige auf C, Kopieren auf U (Strg+Umschalt+C gehört in Chrome den
  // Entwicklerwerkzeugen).
  def('koordinaten', 'global', 'Koordinate am Zeiger ein/aus', 'c'),
  def('koordinate.kopieren', 'global', 'Festgehaltene Koordinate kopieren', 'u'),
  def('begehen.vor', 'begehen', 'Vorwärts', 'w', 'halten'),
  def('begehen.zurueck', 'begehen', 'Rückwärts', 's', 'halten'),
  def('begehen.links', 'begehen', 'Nach links', 'a', 'halten'),
  def('begehen.rechts', 'begehen', 'Nach rechts', 'd', 'halten'),
  def('begehen.runter', 'begehen', 'Abwärts', 'q', 'halten'),
  def('begehen.hoch', 'begehen', 'Aufwärts', 'e', 'halten'),
  def('begehen.schnell', 'begehen', 'Schnell bewegen', 'Shift', 'halten'),
  def('begehen.zielen', 'begehen', 'Zielen', 'z', 'halten'),
  def('begehen.setzen', 'begehen', 'Punkt setzen', ' ', 'halten'),
  def('begehen.drehen-links', 'begehen', 'Nach links drehen', 'ArrowLeft', 'halten'),
  def('begehen.drehen-rechts', 'begehen', 'Nach rechts drehen', 'ArrowRight', 'halten'),
  def('begehen.neigen-hoch', 'begehen', 'Nach oben neigen', 'ArrowUp', 'halten'),
  def('begehen.neigen-runter', 'begehen', 'Nach unten neigen', 'ArrowDown', 'halten'),
  def('begehen.werkzeug-voriges', 'begehen', 'Voriges Werkzeug', 'x'),
  def('begehen.werkzeug-naechstes', 'begehen', 'Nächstes Werkzeug', 'c'),
  def('begehen.ansage', 'begehen', 'Wert ansagen', 'r'),
  // Im Begehen ist f frei: dort setzt es einen Befund am Fadenkreuz statt „Alles zeigen“.
  def('begehen.befund', 'begehen', 'Befund setzen am Fadenkreuz (statt „Alles zeigen“)', 'f'),
];
const NACH_ID = new Map(AKTIONEN.map((a) => [a.id, a] as const));

/** `pad`: umbelegte Controller-Knöpfe (0–17, −1 = nicht belegt); fehlende Aktionen gelten mit der Vorgabe.
 *  `padSchema` 2 = ansichtsübergreifende Aktionen (seit der neuen Controller-Steuerung); Umbelegungen des alten
 *  Schemas (nur Begehen, andere Bedeutungen derselben Namen) werden beim Laden verworfen. */
export interface Belegung {
  schema: 1;
  einzeltastenAus: boolean;
  tasten: Partial<Record<AktionId, Akkord | null>>;
  pad?: Partial<Record<PadAktion, number>>;
  padSchema?: 2;
}
export const BELEGUNG_SCHLUESSEL = 'sv-tastenbelegung';
export const vorgabeBelegung = (): Belegung => ({ schema: 1, einzeltastenAus: false, tasten: {} });

export function normalisiere(e: { key: string; ctrlKey: boolean; metaKey: boolean; altKey: boolean; altGraph?: boolean }): Akkord {
  const taste = e.key.length === 1 ? e.key.toLowerCase() : e.key;
  if (e.altGraph) return { taste, strg: false, alt: false };
  return { taste, strg: e.ctrlKey || e.metaKey, alt: e.altKey };
}

function kanonisiere(a: Akkord): Akkord {
  const taste = a.taste.length === 1 ? a.taste.toLowerCase() : a.taste;
  return { taste, strg: a.strg, alt: a.alt };
}

const gleich = (a: Akkord, b: Akkord): boolean => a.taste === b.taste && a.strg === b.strg && a.alt === b.alt;
const FEST_OHNE = new Set(['Tab', 'Escape', 'Enter', 'Delete', 'Backspace']);
const REINE_MODIFIKATOREN = new Set(['Control', 'Alt', 'Meta', 'AltGraph']);
export function istFest(a: Akkord): boolean {
  if (FEST_OHNE.has(a.taste)) return true;
  if (!a.strg && !a.alt && /^[1-9]$/.test(a.taste)) return true;
  if (a.strg && !a.alt && a.taste === 'z') return true;
  if (REINE_MODIFIKATOREN.has(a.taste)) return true;
  return false;
}

/** In der Ebene `global` zusätzlich fest (auch mit Strg/Alt): Pfeiltasten, Leertaste, Pos1, Ende bedienen dort
 *  Regler, Listen, Knöpfe und das Blättern der Seite. Im Begehen sind sie belegbar (Blick, Setzen). */
const FEST_GLOBAL = new Set(['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', ' ', 'Home', 'End']);
export function istFestIn(a: Akkord, ebene: Ebene): boolean {
  return istFest(a) || (ebene === 'global' && FEST_GLOBAL.has(a.taste));
}

export function akkordVon(b: Belegung, a: AktionId): Akkord | null {
  return a in b.tasten ? (b.tasten[a] ?? null) : (NACH_ID.get(a)?.vorgabe ?? null);
}

const EBENE_VON = new Map(AKTIONEN.map((a) => [a.id, a.ebene] as const));
/** Beschriftung einer Aktion für Hinweise und Hilfe: leer, wenn keine Taste belegt ist oder „Einzeltasten aus“ sie
 *  abschaltet — das betrifft nur die Ebene „global“ (Strg-/Alt-Akkorde und die Begehen-Tasten bleiben aktiv). */
export function kuerzelFuer(b: Belegung, a: AktionId): string {
  const k = akkordVon(b, a);
  if (k && b.einzeltastenAus && EBENE_VON.get(a) === 'global' && !k.strg && !k.alt) return '';
  return beschriftung(k);
}

export function aktionFuer(eingabe: Akkord, ebene: Ebene, b: Belegung): AktionId | null {
  if (ebene === 'global' && b.einzeltastenAus && !eingabe.strg && !eingabe.alt) return null;
  for (const a of AKTIONEN) {
    if (a.ebene !== ebene) continue;
    const k = akkordVon(b, a.id);
    if (k && gleich(k, eingabe)) return a.id;
  }
  return null;
}

export type BelegeErgebnis = { ok: true; belegung: Belegung } | { ok: false; grund: 'fest' | 'konflikt' | 'ungueltig'; mit?: AktionId };
// Tastennamen landen in Beschriftungen (lil-gui setzt Optionsnamen als HTML) und kommen aus dem Speicher
// des Browsers: erlaubt ist genau ein Zeichen oder ein benannter Wert wie ArrowLeft, Shift, F5.
// Tote und unbestimmte Tasten (auf deutscher Tastatur ^ und ´ = „Dead“) sind keine brauchbaren Kürzel.
const UNBRAUCHBAR = new Set(['Dead', 'Unidentified', 'Process']);
export const gueltigerTastenname = (t: string): boolean =>
  !UNBRAUCHBAR.has(t) && (t.length === 1 || /^[A-Za-z][A-Za-z0-9]*$/.test(t));

export function belege(b: Belegung, a: AktionId, akkord: Akkord | null): BelegeErgebnis {
  const kan = akkord ? kanonisiere(akkord) : null;
  if (kan && !gueltigerTastenname(kan.taste)) return { ok: false, grund: 'ungueltig' };
  const d = NACH_ID.get(a);
  if (!d) throw new Error('unbekannte Aktion ' + a);
  const ebene = d.ebene;
  if (kan && istFestIn(kan, ebene)) return { ok: false, grund: 'fest' };
  if (kan) {
    for (const andere of AKTIONEN) {
      if (andere.id === a || andere.ebene !== ebene) continue;
      const k = akkordVon(b, andere.id);
      if (k && gleich(k, kan)) return { ok: false, grund: 'konflikt', mit: andere.id };
    }
  }
  return { ok: true, belegung: { ...b, tasten: { ...b.tasten, [a]: kan } } };
}

export function konflikte(b: Belegung): { aktion: AktionId; mit: AktionId }[] {
  const out: { aktion: AktionId; mit: AktionId }[] = [];
  AKTIONEN.forEach((x, i) => {
    const kx = akkordVon(b, x.id);
    if (!kx) return;
    for (const y of AKTIONEN.slice(i + 1)) {
      const ky = akkordVon(b, y.id);
      if (ky && x.ebene === y.ebene && gleich(kx, ky)) out.push({ aktion: x.id, mit: y.id });
    }
  });
  return out;
}

const NAMEN: Record<string, string> = {
  ' ': 'Leertaste', ArrowLeft: '←', ArrowRight: '→', ArrowUp: '↑', ArrowDown: '↓', Shift: 'Umschalt',
};
export function beschriftung(a: Akkord | null): string {
  if (!a) return '';
  const name = NAMEN[a.taste] ?? (a.taste.length === 1 ? a.taste.toUpperCase() : a.taste);
  return [a.strg ? 'Strg' : '', a.alt ? 'Alt' : '', name].filter(Boolean).join('+');
}

const gueltigerKnopf = (n: unknown): n is number =>
  typeof n === 'number' && Number.isInteger(n) && n >= PAD_NICHT_BELEGT && n <= PAD_KNOPF_MAX;

/** Vollständige Knopfbelegung: Umbelegtes aus `pad`, sonst die Vorgabe. */
export function padBelegung(b: Belegung): Record<PadAktion, number> {
  return { ...PAD_VORGABE, ...b.pad };
}

/** Zwei Aktionen auf demselben Knopf; nicht belegte Aktionen kollidieren nie. */
export function padKonflikte(b: Belegung): { aktion: PadAktion; mit: PadAktion }[] {
  const k = padBelegung(b);
  const out: { aktion: PadAktion; mit: PadAktion }[] = [];
  PAD_AKTIONEN.forEach((x, i) => {
    if (k[x] === PAD_NICHT_BELEGT) return;
    for (const y of PAD_AKTIONEN.slice(i + 1)) if (k[y] === k[x]) out.push({ aktion: x, mit: y });
  });
  return out;
}

export type BelegePadErgebnis = { ok: true; belegung: Belegung } | { ok: false; grund: 'konflikt' | 'ungueltig'; mit?: PadAktion };
export function belegePad(b: Belegung, a: PadAktion, knopf: number): BelegePadErgebnis {
  if (!gueltigerKnopf(knopf)) return { ok: false, grund: 'ungueltig' };
  if (knopf !== PAD_NICHT_BELEGT) {
    const k = padBelegung(b);
    const mit = PAD_AKTIONEN.find((x) => x !== a && k[x] === knopf);
    if (mit) return { ok: false, grund: 'konflikt', mit };
  }
  return { ok: true, belegung: { ...b, pad: { ...b.pad, [a]: knopf }, padSchema: 2 } };
}

export const speichereBelegung = (b: Belegung): string => JSON.stringify(b);

const istAkkord = (x: unknown): x is Akkord =>
  typeof x === 'object' && x !== null &&
  typeof (x as Akkord).taste === 'string' && (x as Akkord).taste.length <= 20 && gueltigerTastenname((x as Akkord).taste) &&
  typeof (x as Akkord).strg === 'boolean' && typeof (x as Akkord).alt === 'boolean';

export function ladeBelegung(raw: string | null): Belegung {
  let roh: unknown;
  try { roh = raw === null ? null : JSON.parse(raw); } catch { return vorgabeBelegung(); }
  if (typeof roh !== 'object' || roh === null || (roh as { schema?: unknown }).schema !== 1) return vorgabeBelegung();
  const r = roh as { einzeltastenAus?: unknown; tasten?: unknown };
  const tasten = typeof r.tasten === 'object' && r.tasten !== null ? (r.tasten as Record<string, unknown>) : {};

  // Erster Durchgang: alle syntaktisch gültigen, nicht festen Einträge laden
  const geladen = new Map<AktionId, Akkord | null>();
  for (const [id, wert] of Object.entries(tasten)) {
    const aktion = NACH_ID.get(id as AktionId);
    if (!aktion) continue;
    if (wert !== null && !istAkkord(wert)) continue;
    const kan = wert === null ? null : kanonisiere({ taste: wert.taste, strg: wert.strg, alt: wert.alt });
    if (kan && istFestIn(kan, aktion.ebene)) continue;
    geladen.set(id as AktionId, kan);
  }

  // Zweiter Durchgang: alle geladenen Einträge übernehmen, dann Konflikte auflösen
  let b: Belegung = { ...vorgabeBelegung(), einzeltastenAus: r.einzeltastenAus === true };
  for (const [id, kan] of geladen) {
    b = { ...b, tasten: { ...b.tasten, [id]: kan } };
  }

  // Wiederholt Konflikte auflösen, bis keine mehr vorhanden sind
  while (true) {
    const conflicts = konflikte(b);
    if (conflicts.length === 0) break;

    for (const { aktion, mit } of conflicts) {
      const aktionIdx = AKTIONEN.findIndex(a => a.id === aktion);
      const mitIdx = AKTIONEN.findIndex(a => a.id === mit);

      // Die Aktion, die später in AKTIONEN steht, wird auf Vorgabe zurückgesetzt
      const toReset = aktionIdx > mitIdx ? aktion : mit;
      const def = NACH_ID.get(toReset)?.vorgabe ?? null;

      // Wenn die Vorgabe auch konfliktiert, auf null setzen
      const checkConflict = belege(b, toReset, def);
      if (checkConflict.ok) {
        b = checkConflict.belegung;
      } else {
        b = { ...b, tasten: { ...b.tasten, [toReset]: null } };
      }
    }
  }

  return { ...b, ...ladePad(roh) };
}

/** Knopfbelegung laden: nur gültige Ganzzahlen, Konflikte lösen wie bei den Tasten (spätere Aktion fällt auf
 *  ihre Vorgabe zurück, kollidiert auch die, wird sie abgeschaltet). */
function ladePad(roh: unknown): { pad?: Partial<Record<PadAktion, number>>; padSchema?: 2 } {
  if ((roh as { padSchema?: unknown }).padSchema !== 2) return {};
  const quelle = (roh as { pad?: unknown }).pad;
  if (typeof quelle !== 'object' || quelle === null || Array.isArray(quelle)) return {};
  const pad: Partial<Record<PadAktion, number>> = {};
  for (const a of PAD_AKTIONEN) {
    const w = (quelle as Record<string, unknown>)[a];
    if (gueltigerKnopf(w)) pad[a] = w;
  }
  let b: Belegung = { ...vorgabeBelegung(), pad };
  for (let runde = 0; runde <= 2 * PAD_AKTIONEN.length; runde++) {
    const k = padKonflikte(b);
    if (k.length === 0) break;
    const { aktion, mit } = k[0]!;
    const zurueck = PAD_AKTIONEN.indexOf(aktion) > PAD_AKTIONEN.indexOf(mit) ? aktion : mit;
    const vorgabe = PAD_VORGABE[zurueck];
    const frei = !PAD_AKTIONEN.some((x) => x !== zurueck && padBelegung(b)[x] === vorgabe);
    b = { ...b, pad: { ...b.pad, [zurueck]: frei ? vorgabe : PAD_NICHT_BELEGT } };
  }
  return Object.keys(b.pad ?? {}).length > 0 ? { pad: b.pad, padSchema: 2 } : {};
}
