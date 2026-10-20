// Reine Logik der Nur-Lese-Links (F28): Typen der API, Link-Adresse, Zustandstext. DOM in teilen.ts.
// Der Server prüft alles selbst (Projektleitung, Schalter, Ablauf) — hier nur Anzeige.

export interface TeilenLink {
  id: number;
  name: string;
  erstellt: number;
  ablauf: number;
  widerrufen: number | null;
  abrufe: number;
  letzterAbruf: number | null;
  passwort: boolean;
}
export interface TeilenListe { erlaubt: boolean; links: TeilenLink[] }
export interface TeilenAngelegt { link: TeilenLink; token: string }

export const TAGE_STANDARD = 14;
export const TAGE_MAX = 90;
export const PASSWORT_MIN = 4;

/** Das Token steht im Fragment: der Browser schickt es nie an Server oder Proxy-Logs. */
export const linkAdresse = (origin: string, token: string): string => `${origin}/teilen#t=${token}`;

/** Viewer-Adresse der Gast-Ansicht; unbrauchbare Projekt-ID → Startseite. */
export function gastZiel(projekt: unknown): string {
  return typeof projekt === 'string' && /^[a-z0-9][a-z0-9-]{0,63}$/.test(projekt) ? `/ansicht?p=/data/${projekt}/&gast=1` : '/';
}

/** Token aus `#t=…` (token_urlsafe(32): genau 43 Zeichen aus [A-Za-z0-9_-]); sonst null. */
export function tokenAusFragment(hash: string): string | null {
  return /^#t=([A-Za-z0-9_-]{43})$/.exec(hash)?.[1] ?? null;
}

export function zustand(l: Pick<TeilenLink, 'ablauf' | 'widerrufen'>, jetztS: number): 'aktiv' | 'abgelaufen' | 'zurueckgezogen' {
  if (l.widerrufen !== null) return 'zurueckgezogen';
  return l.ablauf <= jetztS ? 'abgelaufen' : 'aktiv';
}

export const ZUSTAND_TEXT = { aktiv: 'aktiv', abgelaufen: 'abgelaufen', zurueckgezogen: 'zurückgezogen' } as const;

/** Ehrlich: keine Download-Funktion, aber gezeigte 3D-Daten sind technisch kopierbar. */
export const EHRLICH_TEXT =
  'Gäste sehen nur Punktwolke und Meshes dieses Projekts – keine Panoramen, Fotos, Befunde, Messungen oder Exporte. ' +
  'Es gibt keine Download-Funktion; was im Browser angezeigt wird, ist technisch aber kopierbar. Nur an Menschen geben, die es sehen dürfen.';

/** Fertiger Text zum Einfügen in Mail oder Messenger (die Plattform verschickt keine Mails). */
export function teilenText(adresse: string, name: string, ablaufText: string, passwort: boolean): string {
  return [
    `Hallo ${name},`,
    '',
    `hier ist der Link zur 3D-Ansicht (nur ansehen, gültig bis ${ablaufText}):`,
    adresse,
    ...(passwort ? ['', 'Das Passwort schicke ich dir getrennt.'] : []),
    '',
    'Am besten am Computer oder Tablet in einem aktuellen Browser öffnen.',
  ].join('\n');
}
