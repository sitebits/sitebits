// Portal-Rahmen (WCAG 2.4.2 Seitentitel, 3.2.6 einheitliche Hilfe): Seitentitel je Route, eine
// gemeinsame Fußzeile für jede Portal-Seite (auch ohne Anmeldung) und die Instanz-Angaben aus
// /api/barrierefreiheit. Reine Funktionen oben (Vitest), DOM unten — nur createElement/textContent.
import { ENTWURF_DB, kpEntwuerfeLoeschen } from '../entwurfKonstanten';
import { post } from './api';
import { fussLinks } from './karten';

export type Kontakt = { art: 'mailto' | 'formular'; ziel: string };
export type NichtBarrierefrei = { inhalt: string; grund: string; alternative: string };
export type Erklaerung = {
  schema: 1; stelle: string; geltungsbereich: string; stand: 'vollstaendig' | 'teilweise' | 'nicht';
  erstellt: string; methode: string; methode_text: string; nicht_barrierefrei: NichtBarrierefrei[];
  durchsetzung: { text: string; stelle: string; url: string };
};
export type Instanz = {
  kontakt: Kontakt | null; erklaerung: Erklaerung | null; produktEinschraenkungen: NichtBarrierefrei[];
  impressum: string | null; datenschutz: string | null;
};
export const OHNE_INSTANZ: Instanz = { kontakt: null, erklaerung: null, produktEinschraenkungen: [], impressum: null, datenschutz: null };

const TITEL: Record<string, string> = {
  '/anmelden': 'Anmelden',
  '/einladung': 'Einladung annehmen',
  '/teilen': '3D-Ansicht öffnen',
  '/abmelden': 'E-Mails abbestellen',
  '/verlauf': 'Verlauf',
  '/maengelliste': 'Mängelliste',
  '/befunde': 'Befunde',
  '/zu-pruefen': 'Zu prüfen',
  '/verwaltung': 'Verwaltung',
  '/uploads': 'Uploads',
  '/projekt-einstellungen': 'Projekt-Einstellungen',
  '/barrierefreiheit': 'Erklärung zur Barrierefreiheit',
};

export function seitenTitel(pfad: string): string {
  return `${TITEL[pfad] ?? 'Projekte'} – siteBits`;
}

// Dieselben Regeln wie der Server (barrierefreiheit.py) — hier noch einmal, weil das Portal einer
// Antwort nie blind einen Link-Wert glaubt (javascript:, Zusatzempfänger).
const MAILTO = /^mailto:[A-Za-z0-9._+-]+@[A-Za-z0-9-]+(\.[A-Za-z0-9-]+)+$/;
const HTTPS = /^https:\/\/[^\s/?#]+([/?#]\S*)?$/;
const STAENDE = ['vollstaendig', 'teilweise', 'nicht'] as const;
// Impressum/Datenschutz (rechtliches.py): https ohne Zugangsdaten oder Pfad derselben Origin (nicht //),
// nur druckbares ASCII ohne Backslash, höchstens 2000 Zeichen.
const RECHT_ZEICHEN = /^[\x21-\x5b\x5d-\x7e]{1,2000}$/;
const RECHT_HTTPS = /^https:\/\/[^/?#@]+([/?#].*)?$/;
const RECHT_PFAD = /^\/(?!\/)/;

function rechtsLink(roh: unknown): string | null {
  if (typeof roh !== 'string' || !RECHT_ZEICHEN.test(roh)) return null;
  return RECHT_HTTPS.test(roh) || RECHT_PFAD.test(roh) ? roh : null;
}

/** Links zu Impressum und Datenschutzhinweisen der Instanz (Rechtstext stellt der Betreiber); fehlende entfallen. */
export function rechtsLinks(impressum: string | null, datenschutz: string | null): { href: string; text: string }[] {
  return [
    ...(impressum ? [{ href: impressum, text: 'Impressum' }] : []),
    ...(datenschutz ? [{ href: datenschutz, text: 'Datenschutz' }] : []),
  ];
}

export function nurPfad(pfad: string): string {
  return pfad.split(/[?#]/, 1)[0] || '/';
}

export function feedbackHref(kontakt: Kontakt | null, seite: { titel: string; origin: string; pfad: string }): string {
  if (!kontakt) return '/barrierefreiheit#melden';
  if (kontakt.art === 'formular') return kontakt.ziel;
  const betreff = `Barriere melden: ${seite.titel}`;
  const text = `Seite: ${seite.origin}${nurPfad(seite.pfad)}\n\nWas hat nicht funktioniert, und womit (Browser, Hilfsmittel)?\n`;
  return `${kontakt.ziel}?subject=${encodeURIComponent(betreff)}&body=${encodeURIComponent(text)}`;
}

const istText = (v: unknown): v is string => typeof v === 'string' && v.trim() !== '';

function kontaktAus(roh: unknown): Kontakt | null {
  if (typeof roh !== 'object' || roh === null) return null;
  const { art, ziel } = roh as Record<string, unknown>;
  if (art === 'mailto' && typeof ziel === 'string' && MAILTO.test(ziel)) return { art, ziel };
  if (art === 'formular' && typeof ziel === 'string' && HTTPS.test(ziel)) return { art, ziel };
  return null;
}

function eintraegeAus(roh: unknown): NichtBarrierefrei[] {
  if (!Array.isArray(roh)) return [];
  return roh.filter((e): e is NichtBarrierefrei => typeof e === 'object' && e !== null
    && istText((e as Record<string, unknown>).inhalt) && istText((e as Record<string, unknown>).grund)
    && istText((e as Record<string, unknown>).alternative));
}

function erklaerungAus(roh: unknown): Erklaerung | null {
  if (typeof roh !== 'object' || roh === null) return null;
  const e = roh as Record<string, unknown>;
  const d = e.durchsetzung as Record<string, unknown> | null | undefined;
  const liste = eintraegeAus(e.nicht_barrierefrei);
  const gueltig = e.schema === 1 && istText(e.stelle) && typeof e.geltungsbereich === 'string' && HTTPS.test(e.geltungsbereich)
    && (STAENDE as readonly unknown[]).includes(e.stand) && typeof e.erstellt === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(e.erstellt)
    && istText(e.methode) && istText(e.methode_text) && liste.length > 0
    && Array.isArray(e.nicht_barrierefrei) && liste.length === e.nicht_barrierefrei.length
    && typeof d === 'object' && d !== null && istText(d.text) && istText(d.stelle) && typeof d.url === 'string' && HTTPS.test(d.url);
  return gueltig ? (roh as Erklaerung) : null;
}

export function pruefeInstanz(daten: unknown): Instanz {
  if (typeof daten !== 'object' || daten === null) return OHNE_INSTANZ;
  const d = daten as Record<string, unknown>;
  return {
    kontakt: kontaktAus(d.kontakt),
    erklaerung: erklaerungAus(d.erklaerung),
    produktEinschraenkungen: eintraegeAus(d.produkt_einschraenkungen),
    impressum: rechtsLink(d.impressum),
    datenschutz: rechtsLink(d.datenschutz),
  };
}

/** Zeitgrenze für die Instanzangaben: ohne sie hinge die Fußzeile (Abmelden, Lizenzen, Quelltext) an der Anfrage. */
export const INSTANZ_ZEITGRENZE_MS = 5000;

export async function ladeInstanz(): Promise<Instanz | null> {
  const abbruch = new AbortController();
  const timer = setTimeout(() => abbruch.abort(), INSTANZ_ZEITGRENZE_MS);
  try {
    const antwort = await fetch('/api/barrierefreiheit', { credentials: 'same-origin', signal: abbruch.signal });
    if (!antwort.ok) return null;
    return pruefeInstanz(await antwort.json());
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

/** Lokale Befundentwürfe dieses Browsers löschen (alle Konten): fertig nach Erfolg, Fehler, Blockade oder
 *  spätestens nach einer Sekunde — Abmelden hängt nie daran. Ein offener Viewer gibt seine Verbindung dabei frei.
 *  Dazu die Kontrollpunkt-Entwürfe im localStorage (synchron, vorab). */
export function entwuerfeLoeschen(idb: IDBFactory | undefined = globalThis.indexedDB, speicher: () => Storage | undefined = () => globalThis.localStorage): Promise<void> {
  try {
    kpEntwuerfeLoeschen(speicher());
  } catch {
    /* Zugriff auf localStorage gesperrt */
  }
  return new Promise((fertig) => {
    const frist = setTimeout(fertig, 1000);
    const ende = (): void => {
      clearTimeout(frist);
      fertig();
    };
    try {
      if (!idb) return ende();
      const r = idb.deleteDatabase(ENTWURF_DB);
      r.onsuccess = ende;
      r.onerror = ende;
      r.onblocked = ende;
    } catch {
      ende();
    }
  });
}

export function baueFusszeile(
  fuss: HTMLElement,
  opt: { kontakt: Kontakt | null; quelltextUrl: string | null; angemeldet: boolean; impressum: string | null; datenschutz: string | null },
): void {
  fuss.replaceChildren();
  const links = [
    { href: '/barrierefreiheit', text: 'Barrierefreiheit' },
    { href: feedbackHref(opt.kontakt, { titel: document.title, origin: location.origin, pfad: location.pathname }), text: 'Barriere melden' },
    ...fussLinks(opt.quelltextUrl),
    ...rechtsLinks(opt.impressum, opt.datenschutz),
  ];
  for (const { href, text } of links) {
    const a = document.createElement('a');
    a.href = href;
    a.textContent = text;
    fuss.appendChild(a);
  }
  if (!opt.angemeldet) return;
  const abmelden = document.createElement('button');
  abmelden.type = 'button';
  abmelden.textContent = 'Abmelden';
  abmelden.addEventListener('click', () => {
    abmelden.disabled = true;
    // Erst die Entwürfe löschen, dann abmelden: danach gibt es im Browser keine Inhalte dieses Kontos mehr.
    void entwuerfeLoeschen()
      .then(() => post('/auth/abmelden', {}))
      .catch(() => undefined)
      .finally(() => {
        location.href = '/anmelden';
      });
  });
  fuss.appendChild(abmelden);
}
