// Reine Logik der Seite Projekt-Einstellungen: Typen der Projekt-API, Rollenauswahl je Aufrufer,
// Kategorienliste bearbeiten, Ergebnistexte. DOM in projektEinstellungen.ts. Der Server prüft jede
// Aktion selbst — das hier steuert nur, was angeboten wird.
import { rolleText } from './rechte';

export type VergebbareRolle = 'betrachter' | 'rueckmelder' | 'bearbeiter' | 'projektleitung';
export interface GewerkKurz { id: number; name: string; kurz: string }
export interface Gewerk extends GewerkKurz { bauherrenseite: boolean }
export interface Beteiligter {
  nutzerId: number;
  nutzer: string;
  rolle: string;
  gewerk: GewerkKurz | null;
  email?: string;
  letzteAnmeldung?: number | null;
  gesperrt?: boolean;
  admin?: boolean;
}
export interface ProjektEinladung { id: string; name: string; email: string; rolle: string | null; gewerk: number | null; erstelltAm: number; ablauf: number }
export interface Kategorie { name: string; aktiv: boolean }
export interface Einstellungen { vorlage: 'bestandsaufnahme' | 'bauprojekt' | null; rueckmeldungen: boolean; exportBearbeiter: boolean; befundFaeden?: boolean; nurLeseLinks?: boolean }
export type EinladungsAntwort =
  | { ergebnis: 'eingeladen'; link: string; einladung: ProjektEinladung }
  | { ergebnis: 'zugriff-erteilt' | 'zugriff-besteht'; gesperrt: boolean; text: string };

export const NUR_LEITUNG_TEXT = 'Diese Seite ist nur für die Projektleitung und Admins.';
const KATEGORIE_MAX = 60;
const KATEGORIEN_MAX = 30;
const LEITUNG_VERGIBT: readonly VergebbareRolle[] = ['betrachter', 'rueckmelder', 'bearbeiter'];

export const darfVerwalten = (rolle: string): boolean => rolle === 'projektleitung' || rolle === 'admin';

export function vergebbareRollen(eigeneRolle: string): { wert: VergebbareRolle; text: string }[] {
  const rollen: VergebbareRolle[] =
    eigeneRolle === 'admin' ? [...LEITUNG_VERGIBT, 'projektleitung'] : eigeneRolle === 'projektleitung' ? [...LEITUNG_VERGIBT] : [];
  return rollen.map((wert) => ({ wert, text: rolleText(wert) }));
}

/** Die Projektleitung ändert keine Admins und keine Projektleitung (auch nicht sich selbst); Admins alles. */
export const zeileAenderbar = (b: Beteiligter, eigeneRolle: string): boolean =>
  eigeneRolle === 'admin' || (eigeneRolle === 'projektleitung' && b.admin !== true && (LEITUNG_VERGIBT as readonly string[]).includes(b.rolle));

export function verschiebe(liste: readonly Kategorie[], index: number, richtung: -1 | 1): Kategorie[] {
  const neu = liste.map((k) => ({ ...k }));
  const ziel = index + richtung;
  if (index < 0 || index >= neu.length || ziel < 0 || ziel >= neu.length) return neu;
  [neu[index], neu[ziel]] = [neu[ziel]!, neu[index]!];
  return neu;
}

export const umschalten = (liste: readonly Kategorie[], index: number): Kategorie[] =>
  liste.map((k, i) => (i === index ? { ...k, aktiv: !k.aktiv } : { ...k }));

const vergleich = (s: string): string => s.normalize('NFC').trim().toLocaleLowerCase('de-DE');

export function hinzufuegen(liste: readonly Kategorie[], name: string): Kategorie[] | { fehler: string } {
  const n = name.normalize('NFC').trim();
  if (n === '') return { fehler: 'Bitte einen Namen eingeben.' };
  if (n.length > KATEGORIE_MAX) return { fehler: `Höchstens ${KATEGORIE_MAX} Zeichen.` };
  if (vergleich(n) === vergleich('Rückmeldung')) return { fehler: '„Rückmeldung“ ist für Rückmeldungen reserviert.' };
  if (liste.some((k) => vergleich(k.name) === vergleich(n))) return { fehler: 'Diese Kategorie gibt es schon.' };
  if (liste.length >= KATEGORIEN_MAX) return { fehler: `Höchstens ${KATEGORIEN_MAX} Kategorien.` };
  return [...liste.map((k) => ({ ...k })), { name: n, aktiv: true }];
}

export const einladungErgebnisText = (a: EinladungsAntwort): string =>
  a.ergebnis === 'eingeladen' ? 'Einladung angelegt. Den Link jetzt kopieren und selbst weitergeben.' : a.text;

export const VORLAGEN: readonly { wert: 'bestandsaufnahme' | 'bauprojekt'; text: string }[] = [
  { wert: 'bestandsaufnahme', text: 'Bestandsaufnahme (Befunde und Messen)' },
  { wert: 'bauprojekt', text: 'Bauprojekt (Gewerke, Kategorien für den Bau)' },
];
