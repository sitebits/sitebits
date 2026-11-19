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
  /** Ansichtsprofil (P08-S6); fehlt bei einem älteren Server. */
  profil?: ProfilWahl;
}
export interface TeilenListe { erlaubt: boolean; links: TeilenLink[] }
export interface TeilenAngelegt { link: TeilenLink; token: string }

// Ansichtsprofile der Nur-Lese-Links (P08-S6). Der Viewer kennt dieselben drei (web/src/profil.ts); hier eigene
// Texte, damit Portal und Viewer keinen gemeinsamen Chunk bekommen.
export type ProfilWahl = 'ansehen' | 'messen' | 'arbeiten';
export const PROFIL_TEXT: Record<ProfilWahl, string> = { ansehen: 'Ansehen', messen: 'Messen', arbeiten: 'Arbeiten' };
export const PROFIL_HILFE =
  'Ansehen: nur das 3D-Modell mit wenigen Einstellungen (Grau, Hintergrund, Punktgröße, Vollbild) und einer Strecke zum Messen. ' +
  'Messen: dazu Linienzug, Fläche und Höhe. Arbeiten: die volle Gast-Oberfläche mit Werkzeugleiste und Panels. ' +
  'Gäste können die Ansicht nicht wechseln; was sie laden dürfen, ist in allen drei gleich.';
export function profilWahl(roh: unknown): ProfilWahl {
  return roh === 'messen' || roh === 'arbeiten' ? roh : 'ansehen';
}

export const TAGE_STANDARD = 14;
export const TAGE_MAX = 90;
export const PASSWORT_MIN = 4;

// Startansicht im Gast-Link (ADR 0016, Nachtrag 2026-11): nur Kamera `cam`, Ziel `tgt` und sichtbare Ebenen `ly`
// reisen mit, und nur im Fragment (nie an Server, DB oder Logs). Befundlinks, Messungen, Standpunkte, Filter usw.
// (b, e, m, g, o, st, yaw, pitch, fov, ko, kx, ec, kv, rev) bleiben draußen. Zeichen nur aus einer Positivliste, damit
// die Ansicht hinter dem geprüften Ziel aus gastZiel keine Adresse bilden kann (SAST open-redirect, teilen.ts).
// `ly=` (leer) ist gültig: so schreibt der Viewer „alle Ebenen aus“ (hash.ts, layers.ts lyParam).
const ANSICHT_SCHLUESSEL = ['cam', 'tgt', 'ly'] as const;
const ANSICHT_WERT = /^[0-9A-Za-z_.,;:-]+$/;
const passt = (k: (typeof ANSICHT_SCHLUESSEL)[number], v: string): boolean => (k === 'ly' && v === '') || ANSICHT_WERT.test(v);
export const ANSICHT_MAX = 512;

/** Ansichtsteil aus einer Viewer-Adresse (`…/ansicht?p=…#cam=…&tgt=…&ly=…`) oder einem Hash; behält nur cam/tgt/ly
 *  mit zulässigen Zeichen, verwirft andere Werte einzeln. `cam=…&tgt=…&ly=…` (höchstens ANSICHT_MAX Zeichen) oder null. */
export function gastAnsicht(eingabe: string): string | null {
  return ansichtLesen(eingabe).ansicht;
}

/** Wie gastAnsicht, nennt zusätzlich die Schlüssel cam/tgt/ly, die die Eingabe hatte, aber nicht durften (Hinweis im
 *  Formular, wenn ein eingefügter Link nur teilweise übernommen wird), und ob die Ansicht nur zu lang war. */
export function ansichtLesen(eingabe: string): { ansicht: string | null; verworfen: string[]; zuLang: boolean } {
  const roh = eingabe.trim();
  const hash = roh.indexOf('#');
  const q = new URLSearchParams(hash >= 0 ? roh.slice(hash + 1) : roh);
  const verworfen: string[] = [];
  const teile = ANSICHT_SCHLUESSEL.flatMap((k) => {
    const v = q.get(k);
    if (v === null) return [];
    if (passt(k, v)) return [`${k}=${v}`];
    verworfen.push(k);
    return [];
  });
  const ansicht = teile.join('&');
  const zuLang = ansicht.length > ANSICHT_MAX;
  return { ansicht: ansicht !== '' && !zuLang ? ansicht : null, verworfen, zuLang };
}

/** Token und Ansicht aus `#t=<43>[&<ansicht>]`; null ohne gültiges Token. Eine unbrauchbare Ansicht → ansicht null. */
export function linkTeile(hash: string): { token: string; ansicht: string | null } | null {
  const m = /^#t=([A-Za-z0-9_-]{43})(?:&(.*))?$/.exec(hash);
  if (!m) return null;
  return { token: m[1]!, ansicht: m[2] ? gastAnsicht(m[2]) : null };
}

/** Das Token steht im Fragment: der Browser schickt es nie an Server oder Proxy-Logs; ebenso die Startansicht. */
export function linkAdresse(origin: string, token: string, ansicht?: string | null): string {
  const a = ansicht ? gastAnsicht(ansicht) : null;
  return `${origin}/teilen#t=${token}${a ? `&${a}` : ''}`;
}

/** Viewer-Adresse der Gast-Ansicht; unbrauchbare Projekt-ID → Startseite. `zeigen`: Show-Modus (`/teilen?zeigen=1#t=…`,
 *  für QR-Codes am Stand) an den Viewer weiterreichen. `ansicht` wird erneut geprüft und nur hinter einem gültigen Ziel
 *  als Fragment angehängt. */
export function gastZiel(projekt: unknown, zeigen = false, ansicht?: string | null): string {
  if (typeof projekt !== 'string' || !/^[a-z0-9][a-z0-9-]{0,63}$/.test(projekt)) return '/';
  const a = ansicht ? gastAnsicht(ansicht) : null;
  return `/ansicht?p=/data/${projekt}/&gast=1${zeigen ? '&zeigen=1' : ''}${a ? `#${a}` : ''}`;
}

export function zustand(l: Pick<TeilenLink, 'ablauf' | 'widerrufen'>, jetztS: number): 'aktiv' | 'abgelaufen' | 'zurueckgezogen' {
  if (l.widerrufen !== null) return 'zurueckgezogen';
  return l.ablauf <= jetztS ? 'abgelaufen' : 'aktiv';
}

export const ZUSTAND_TEXT = { aktiv: 'aktiv', abgelaufen: 'abgelaufen', zurueckgezogen: 'zurückgezogen' } as const;

/** Ehrlich: keine Download-Funktion, aber gezeigte 3D-Daten sind technisch kopierbar; Texturen und Sichtschicht
 *  stammen aus Fotos (ADR 0016, Restrisiko ohne Anonymisierung). */
export const EHRLICH_TEXT =
  'Gäste sehen nur Punktwolke, Meshes und Sichtschicht (Splat) dieses Projekts – keine Panoramen, Fotos, Befunde, Messungen oder Exporte. ' +
  'Fototexturen und Sichtschicht entstehen aus Fotos und können Personen zeigen. ' +
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
