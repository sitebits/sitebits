// Reine Logik der Verwaltungsseite: Typen der Admin-API, Rechte-Matrix, Anzeige-Formate. DOM in
// verwaltung.ts.
import { ROLLEN_NAME } from './rechte';

/** Vergebbare Projektrollen (Admin ist ein Kontomerkmal, keine Projektrolle). */
export const ROLLEN = ['betrachter', 'rueckmelder', 'bearbeiter', 'projektleitung'] as const;
export type Rolle = (typeof ROLLEN)[number];

export const istRolle = (wert: string): wert is Rolle => (ROLLEN as readonly string[]).includes(wert);

export interface AdminNutzer {
  id: number;
  name: string;
  email: string;
  admin: boolean;
  gesperrt: boolean;
  letzteAnmeldung: number | null;
  rechte: Record<string, Rolle>;
  /** Endgültig anonymisiert (DSGVO): keine Aktionen, keine Rechte mehr (Server lehnt ab). */
  anonymisiert: boolean;
}

export interface AdminProjekt {
  id: string;
  titel: string;
}

export interface OffeneEinladung {
  id: string;
  name: string;
  email: string;
  zweck: 'neu' | 'zuruecksetzen';
  admin: boolean;
  rechte: Record<string, Rolle>;
  erstelltAm: number;
  ablauf: number;
}

export interface StatistikTag {
  tag: string;
  oeffnen: number;
  standpunkt: number;
  messung: number;
  poi: number;
}

export interface StatistikProjekt {
  id: string;
  titel: string;
  summen: Omit<StatistikTag, 'tag'>;
  verlauf: StatistikTag[];
  zuletztGeoeffnet: string | null;
}

export const ROLLEN_AUSWAHL: { wert: '' | Rolle; text: string }[] = [
  { wert: '', text: '—' },
  ...ROLLEN.map((wert) => ({ wert, text: ROLLEN_NAME[wert] })),
];

export const zelle = (n: AdminNutzer, pid: string): '' | Rolle | 'admin' => (n.admin ? 'admin' : (n.rechte[pid] ?? ''));

/** Eigenes Konto (E-Mail aus der Sitzung, Vergleich ohne Groß-/Kleinschreibung wie die Datenbank) — dafür
 *  zeigt die Verwaltung weder Sperren noch Überall abmelden noch Zugang zurücksetzen (Final-Fix C/D). */
export const istEigenesKonto = (n: Pick<AdminNutzer, 'email'>, eigeneEmail: string): boolean =>
  n.email.trim().toLowerCase() === eigeneEmail.trim().toLowerCase();

export const statusText = (n: AdminNutzer): string => (n.anonymisiert ? 'anonymisiert' : n.gesperrt ? 'gesperrt' : 'aktiv');

export const datumText = (ts: number | null): string =>
  ts === null
    ? '–'
    : new Intl.DateTimeFormat('de-DE', { dateStyle: 'medium', timeStyle: 'short', timeZone: 'Europe/Berlin' }).format(new Date(ts * 1000));

export function tagText(tag: string | null): string {
  if (!tag) return '–';
  const [j, m, t] = tag.split('-');
  return `${t}.${m}.${j}`;
}

export function rechteAusAuswahl(auswahl: Record<string, string>): Record<string, Rolle> {
  const out: Record<string, Rolle> = {};
  for (const [pid, wert] of Object.entries(auswahl)) if (istRolle(wert)) out[pid] = wert;
  return out;
}

const BLOECKE = '▁▂▃▄▅▆▇█';

export function sparkline(werte: number[]): string {
  const max = Math.max(0, ...werte);
  return werte.map((v) => BLOECKE[max === 0 ? 0 : Math.round((v / max) * 7)] ?? '▁').join('');
}

/** Vorlage zum Weitergeben des Einladungslinks (die Plattform verschickt selbst keine Mails). */
export function einladungsText(link: string, name: string, zuruecksetzen = false): string {
  const vorname = name.trim().split(/\s+/)[0] ?? '';
  const anrede = vorname ? `Hallo ${vorname},` : 'Hallo,';
  const kern = zuruecksetzen
    ? 'hier ist der Link, mit dem du deinen Zugang zur 3D-Ansicht der Projektdaten neu einrichtest:'
    : 'hier ist dein Zugang zur 3D-Ansicht der Projektdaten:';
  return [
    anrede,
    '',
    kern,
    link,
    '',
    'Der Link gilt 7 Tage und funktioniert nur einmal. Beim Öffnen legst du einen Passkey an (Fingerabdruck, ' +
      'Gesichtserkennung oder Geräte-PIN) – ein Passwort brauchst du nicht; wo das nicht geht, bietet die Seite ' +
      'ein Passwort an. Wenn etwas nicht klappt, melde dich einfach.',
  ].join('\n');
}
