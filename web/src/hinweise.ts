// Tastenhinweise: kurze Zeilen „<Taste>: <Tätigkeit>“ unten in der 3D-Ansicht, je Zustand höchstens drei. Ein
// Hinweis verschwindet, nachdem seine Tätigkeit dreimal ausgeführt wurde (Zähler je Konto im Browser), bleibt mit
// „Hinweise immer zeigen“ stehen und zeigt immer die aktuelle Tastenbelegung; solange ein Controller benutzt wird, tritt sie zugunsten der Controller-Leiste zurück. Kein Zähler
// und keine Belohnung wird angezeigt. Rein (Vitest): Auswahl, Zähler, Beschriftung, Ausweichen der Leiste.
import type { KeyAction } from './keymap';
import type { AktionId } from './tastenbelegung';
import type { OrbitLinks } from './bedienEinstellungen';

export type HinweisId = 'begehen' | 'tastenhilfe' | 'setzen' | 'zielen' | 'befund' | 'beenden' | 'teilen' | 'drehen' | 'maus' | 'finger';
export interface HinweisKontext {
  begehen: boolean; panoramaZiel: boolean; werkzeug: string; auswahl: boolean; empfang: boolean; gamepad: boolean;
  /** Panorama offen (auch ohne Fadenkreuz): keine Zeile zu Maus und Finger, die gilt dem Orbit. */
  panorama?: boolean;
  /** Zeigergerät für die Zeile zu Maus bzw. Finger (Orbit der Werkzeugansicht); fehlt = keine solche Zeile. */
  zeiger?: 'maus' | 'finger' | null;
  /** Tastenhinweise zeigen; auf reinen Touch-Geräten erst, wenn eine Taste benutzt wurde (Vorgabe: ja). */
  tastatur?: boolean;
  /** Belegung der linken Maustaste im Orbit (bedienEinstellungen.ts) für den Text der Maus-Zeile. */
  orbitLinks?: OrbitLinks;
}
export interface HinweisZaehler { schema: 1; n: Partial<Record<HinweisId, number>> }
export const hinweisSchluessel = (nutzerId: number | null): string => (nutzerId === null ? 'sv-hinweise' : `sv-hinweise:${nutzerId}`);
/** Nach so vielen Ausführungen verschwindet ein Hinweis. */
export const HINWEIS_GRENZE = 3;
/** Höchstens so viele Hinweise zugleich. */
export const HINWEIS_HOECHSTENS = 3;

const IDS: readonly HinweisId[] = ['begehen', 'tastenhilfe', 'setzen', 'zielen', 'befund', 'beenden', 'teilen', 'drehen', 'maus', 'finger'];
export const TAETIGKEIT: Record<HinweisId, string> = {
  begehen: 'Begehen',
  tastenhilfe: 'Bedienung',
  setzen: 'Punkt setzen',
  zielen: 'zielen',
  befund: 'Befund setzen',
  beenden: 'Begehen beenden',
  teilen: 'Befundlink teilen',
  drehen: 'drehen',
  maus: 'Maus im Orbit',
  finger: 'Finger im Orbit',
};

/** Zeile zu Maus bzw. Finger im Orbit — wie die Steuerung es tut (drehpunkt.ts::mausTasten/fingerGesten): ein Finger
 *  wie links ziehen, zwei Finger zoomen und nehmen die andere Geste. */
export function gesteText(id: 'maus' | 'finger', links: OrbitLinks = 'drehen'): string {
  if (id === 'finger') {
    return links === 'drehen' ? 'Ein Finger: drehen · Zwei Finger: zoomen und verschieben'
      : 'Ein Finger: verschieben · Zwei Finger: zoomen und drehen';
  }
  return `Doppelklick: Drehpunkt · ${links === 'drehen' ? 'Ziehen' : 'Rechts ziehen'}: drehen · Rad: zoomen`;
}

export const zurueckgesetzt = (): HinweisZaehler => ({ schema: 1, n: {} });

/** Nur bekannte Hinweise mit ganzer Zahl 0 … Grenze; alles andere (fremdes Schema, kaputtes JSON) = leer. */
export function ladeHinweise(raw: string | null): HinweisZaehler {
  let roh: unknown;
  try { roh = raw === null ? null : JSON.parse(raw); } catch { return zurueckgesetzt(); }
  if (typeof roh !== 'object' || roh === null || (roh as { schema?: unknown }).schema !== 1) return zurueckgesetzt();
  const quelle = (roh as { n?: unknown }).n;
  if (typeof quelle !== 'object' || quelle === null || Array.isArray(quelle)) return zurueckgesetzt();
  const n: Partial<Record<HinweisId, number>> = {};
  for (const id of IDS) {
    const w = (quelle as Record<string, unknown>)[id];
    if (typeof w === 'number' && Number.isInteger(w) && w >= 0 && w <= HINWEIS_GRENZE) n[id] = w;
  }
  return { schema: 1, n };
}

export const speichereHinweise = (z: HinweisZaehler): string => JSON.stringify(z);

/** Eine Ausführung mehr; gekappt bei der Grenze (der Speicher wächst nicht). */
export function gezaehlt(z: HinweisZaehler, id: HinweisId): HinweisZaehler {
  const alt = z.n[id] ?? 0;
  if (alt >= HINWEIS_GRENZE) return z;
  return { schema: 1, n: { ...z.n, [id]: alt + 1 } };
}

/** Kandidaten je Zustand in fester Reihenfolge. */
function kandidaten(k: HinweisKontext): HinweisId[] {
  if (k.empfang) return ['tastenhilfe', 'drehen'];
  if (k.begehen || k.panoramaZiel) {
    const out: HinweisId[] = [];
    if (k.werkzeug !== 'navigate') out.push('setzen');
    out.push('befund');
    if (k.begehen) out.push('zielen');
    if (k.panoramaZiel && !k.begehen) out.push('drehen');
    if (k.begehen) out.push('beenden');
    return out;
  }
  return k.auswahl ? ['begehen', 'teilen', 'tastenhilfe'] : ['begehen', 'tastenhilfe'];
}

export function sichtbareHinweise(k: HinweisKontext, z: HinweisZaehler, o: {
  immer: boolean; taste: (id: HinweisId) => string;   // aktuelle Beschriftung (Tastatur oder Gamepad), '' = nicht belegt
}): { id: HinweisId; text: string }[] {
  const out: { id: HinweisId; text: string }[] = [];
  // Maus oder Finger: eine eigene Zeile vorab, nur im Orbit der Werkzeugansicht; zählt nicht zu den drei Tastenhinweisen.
  const geste = k.zeiger ?? null;
  if (geste && !k.begehen && !k.panorama && !k.panoramaZiel && !k.empfang && (o.immer || (z.n[geste] ?? 0) < HINWEIS_GRENZE)) {
    out.push({ id: geste, text: gesteText(geste, k.orbitLinks) });
  }
  if (k.tastatur === false) return out;
  const vorab = out.length;
  for (const id of kandidaten(k)) {
    if (out.length - vorab >= HINWEIS_HOECHSTENS) break;
    if (!o.immer && (z.n[id] ?? 0) >= HINWEIS_GRENZE) continue;
    const taste = o.taste(id);
    if (!taste) continue;
    out.push({ id, text: `${taste}: ${TAETIGKEIT[id]}` });
  }
  return out;
}

/** Welche Tastenaktion zählt für welchen Hinweis. Setzen und Befund zählen dort, wo sie wirklich ausgeführt werden
 *  (Fadenkreuz-Steuerung), Begehen/Beenden am Zustandswechsel, Tastenhilfe und Teilen beim Öffnen — deshalb hier
 *  nur, was allein über eine Taste läuft: Zielen im Begehen, Drehen im Panorama-Fadenkreuz und in der
 *  Empfängeransicht (dort als Schritt). */
export function hinweisFuerAktion(a: KeyAction, k: { begehen: boolean; panoramaZiel: boolean }): HinweisId | null {
  if (a.type === 'drehen-schritt') return 'drehen';
  if (a.type !== 'halten') return null;
  if (a.aktion === 'begehen.zielen' && k.begehen) return 'zielen';
  if ((a.aktion === 'begehen.drehen-links' || a.aktion === 'begehen.drehen-rechts') && k.panoramaZiel && !k.begehen) return 'drehen';
  return null;
}

// --- Beschriftung ---------------------------------------------------------------------------------------------
const AKTION: Partial<Record<HinweisId, AktionId>> = {
  begehen: 'begehen', tastenhilfe: 'tastenhilfe', setzen: 'begehen.setzen', zielen: 'begehen.zielen', befund: 'begehen.befund', teilen: 'teilen',
};
/** Aktuelle Beschriftung eines Hinweises (Tasten; mit Controller zeigt padSteuerung.ts eine eigene Leiste).
 *  Beenden ist immer Esc. `kuerzel` liefert '' für nicht belegte oder abgeschaltete Tasten. */
export function hinweisTaste(id: HinweisId, o: { kuerzel: (a: AktionId) => string }): string {
  if (id === 'beenden') return 'Esc';
  if (id === 'drehen') return [o.kuerzel('begehen.drehen-links'), o.kuerzel('begehen.drehen-rechts')].filter(Boolean).join(' ');
  const a = AKTION[id];
  return a ? o.kuerzel(a) : '';
}

// --- Ausweichen -----------------------------------------------------------------------------------------------
export interface Rechteck { left: number; top: number; right: number; bottom: number }
const leer = (r: Rechteck): boolean => r.right <= r.left || r.bottom <= r.top;
export const schneidet = (a: Rechteck, b: Rechteck): boolean =>
  !leer(a) && !leer(b) && a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom;
/** Verdeckt die Leiste das fokussierte Element? Ein Element, das die Leiste ganz umschließt (die vollflächige
 *  3D-Ansicht), wird nicht verdeckt: die Leiste liegt darauf. Leere Rechtecke (unsichtbar) zählen nicht. */
export function ueberdeckt(leiste: Rechteck, fokus: Rechteck): boolean {
  const umschliesst = fokus.left <= leiste.left && fokus.top <= leiste.top && fokus.right >= leiste.right && fokus.bottom >= leiste.bottom;
  return schneidet(leiste, fokus) && !umschliesst;
}

/** Seite der Leiste. Rechts ist die Vorgabe; links nur, wenn es dort frei ist. Verdeckt sie auf beiden Seiten etwas —
 *  das fokussierte Element oder ein Panel (z. B. die Minikarte unten rechts) —, tritt sie zurück (`keine`): dieselben
 *  Angaben stehen im Dialog „Bedienung“. `rechts` ist die Leiste an ihrem rechten Platz, `breite` die Breite des
 *  Fensters; links liegt sie mit demselben Randabstand. */
export function seiteFuer(rechts: Rechteck, breite: number, fokus: Rechteck | null, panels: readonly Rechteck[]): 'rechts' | 'links' | 'keine' {
  const rand = breite - rechts.right;
  const links = { left: rand, right: rand + (rechts.right - rechts.left), top: rechts.top, bottom: rechts.bottom };
  const frei = (r: Rechteck): boolean => !(fokus && ueberdeckt(r, fokus)) && !panels.some((p) => schneidet(r, p));
  return frei(rechts) ? 'rechts' : frei(links) ? 'links' : 'keine';
}

/**
 * Zeigt dieser Tastendruck eine echte Tastatur an? Nicht bei einer Bildschirmtastatur: Android meldet ihre Zeichen
 * als „Unidentified“ bzw. Code 229 (auch während einer Eingabe-Komposition); Eingaben in Textfelder zählen gar
 * nicht, weil dort Enter und Rücktaste auch von der Bildschirmtastatur echt ankommen.
 */
export function echteTastatur(ev: { key: string; keyCode?: number; isComposing?: boolean }, imTextfeld: boolean): boolean {
  if (imTextfeld || ev.isComposing === true) return false;
  return ev.key !== 'Unidentified' && ev.key !== 'Process' && ev.keyCode !== 229;
}
