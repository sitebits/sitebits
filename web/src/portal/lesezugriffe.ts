// Verwaltung, Abschnitt „Lesezugriffe“ (nur Admin): wer hat wann welches Projekt gelesen. Filter
// nach Projekt und Konto, seitenweise Nachladen, Download als JSON Lines. Die Darstellung (Texte,
// Abfragepfade) ist reine Logik (Vitest); der Abschnitt selbst baut nur mit createElement und
// textContent (strenge CSP). Geladen wird nie von selbst, sondern erst auf „Anzeigen“: das Öffnen
// der Verwaltung oder eine Admin-Aktion ist keine Einsicht in personenbezogene Daten. Serverseitig nur für Admins per Cookie; jede Einsicht wird selbst
// protokolliert.
import { anfrage } from '../plattformApi';
import { zeitText } from './verlaufText';
import type { AdminNutzer, AdminProjekt } from './verwaltungModell';

export interface Lesezugriff {
  id: number;
  zeit: number;
  nutzerId: number;
  /** Anonymisierte Konten erscheinen als „Gelöschtes Konto n“. */
  nutzer: string | null;
  kanal: 'web' | 'api';
  /** Name des API-Tokens bei kanal 'api' (nie der Wert). */
  token: string | null;
  projekt: string;
  art: 'oeffnen' | 'paket' | 'export' | 'api';
  objekt: string;
}

export interface LesezugriffSeite {
  lesezugriffe: Lesezugriff[];
  weiter: number | null;
}

export interface Filter {
  projekt?: string;
  konto?: number;
}

export const SEITE = 50;
export const LEER_TEXT = 'Keine Lesezugriffe im Aufbewahrungszeitraum.';
export const NICHT_GELADEN_TEXT = 'Die Lesezugriffe werden erst auf „Anzeigen“ geladen; jede Einsicht wird selbst protokolliert.';

const ARTEN: Record<string, string> = {
  oeffnen: 'Projekt geöffnet',
  paket: 'Projektdaten geladen',
  'export:csv': 'Export als CSV',
  'export:json': 'Export als JSON',
  'export:verlauf': 'Verlauf exportiert',
  'export:lesezugriffe': 'Lesezugriffe exportiert',
  'api:pois': 'POIs gelesen',
  'api:messungen': 'Messungen gelesen',
  'api:verlauf': 'Verlauf gelesen',
  'api:lesezugriffe': 'Lesezugriffe gelesen',
};

/** Lesbare Art; unbekannte Kombinationen erscheinen unverändert statt verschluckt zu werden. */
export function artText(e: Pick<Lesezugriff, 'art' | 'objekt'>): string {
  return ARTEN[`${e.art}:${e.objekt}`] ?? ARTEN[e.art] ?? [e.art, e.objekt].filter(Boolean).join(' ');
}

export const kontoText = (e: Pick<Lesezugriff, 'nutzer' | 'nutzerId'>): string => e.nutzer ?? `Konto ${e.nutzerId}`;

export function kanalText(e: Pick<Lesezugriff, 'kanal' | 'token'>): string {
  if (e.kanal !== 'api') return 'Web';
  return e.token ? `API, Token „${e.token}“` : 'API';
}

export function projektText(id: string, projekte: readonly AdminProjekt[]): string {
  return projekte.find((p) => p.id === id)?.titel ?? id;
}

function parameter(filter: Filter, extra: Record<string, string | number | null> = {}): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(extra)) if (v !== null) p.set(k, String(v));
  if (filter.projekt) p.set('projekt', filter.projekt);
  if (filter.konto !== undefined) p.set('nutzer', String(filter.konto));
  const s = p.toString();
  return s === '' ? '' : `?${s}`;
}

/** Pfad einer Seite; `vor` ist die `weiter`-Marke der vorigen Seite. */
export const seitenPfad = (filter: Filter, vor: number | null): string =>
  `/api/v1/lesezugriffe${parameter(filter, { limit: SEITE, vor })}`;

/** Pfad des Downloads (alle Einträge zum Filter, älteste zuerst). */
export const downloadPfad = (filter: Filter): string => `/api/v1/lesezugriffe.jsonl${parameter(filter)}`;

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function auswahl(beschriftung: string, id: string, optionen: { wert: string; text: string }[]): { label: HTMLLabelElement; select: HTMLSelectElement } {
  const select = el('select');
  select.id = id;
  for (const o of [{ wert: '', text: 'Alle' }, ...optionen]) {
    const option = el('option', undefined, o.text);
    option.value = o.wert;
    select.append(option);
  }
  const label = el('label', 'sv-feld');
  label.htmlFor = id;
  label.append(el('span', undefined, beschriftung), select);
  return { label, select };
}

function zeile(e: Lesezugriff, projekte: readonly AdminProjekt[]): HTMLTableRowElement {
  const tr = el('tr');
  for (const text of [zeitText(e.zeit), kontoText(e), projektText(e.projekt, projekte), artText(e), kanalText(e)]) tr.append(el('td', undefined, text));
  return tr;
}

export function lesezugriffeAbschnitt(projekte: readonly AdminProjekt[], nutzer: readonly AdminNutzer[]): HTMLElement {
  const abschnitt = el('section', 'sv-abschnitt sv-lesezugriffe');
  abschnitt.append(el('h2', undefined, 'Lesezugriffe'));
  abschnitt.append(
    el(
      'p',
      'sv-hinweis',
      'Wer hat wann welches Projekt gelesen? Nur für Admins sichtbar; jede Einsicht wird selbst protokolliert. Die Einträge enthalten Konto, Zeit, Projekt und Art, keine IP-Adresse und kein Gerät, und werden nach der Aufbewahrungsfrist gelöscht.',
    ),
  );

  const formular = el('form', 'sv-filter');
  const projektAuswahl = auswahl('Projekt', 'sv-lese-projekt', projekte.map((p) => ({ wert: p.id, text: p.titel })));
  const kontoAuswahl = auswahl('Konto', 'sv-lese-konto', nutzer.map((n) => ({ wert: String(n.id), text: n.name })));
  const anzeigen = el('button', undefined, 'Anzeigen');
  anzeigen.type = 'submit';
  formular.append(projektAuswahl.label, kontoAuswahl.label, anzeigen);

  const fehler = el('p', 'sv-fehler');
  fehler.hidden = true;
  fehler.setAttribute('aria-live', 'polite');
  const nichtGeladen = el('p', 'sv-hinweis', NICHT_GELADEN_TEXT);
  const leer = el('p', undefined, LEER_TEXT);
  leer.hidden = true;

  const kopfzeile = el('tr');
  for (const t of ['Zeit', 'Konto', 'Projekt', 'Art', 'Kanal']) kopfzeile.append(el('th', undefined, t));
  const thead = el('thead');
  thead.append(kopfzeile);
  const tbody = el('tbody');
  const tabelle = el('table', 'sv-tabelle sv-lesezugriffe-tabelle');
  tabelle.append(thead, tbody);
  // Wie die übrigen Datentabellen: eigener Scroll-Container mit Fokus und Namen.
  const region = el('div', 'sv-tabelle-region');
  region.setAttribute('role', 'region');
  region.setAttribute('aria-label', 'Lesezugriffe');
  region.tabIndex = 0;
  region.append(tabelle);
  region.hidden = true;

  const mehr = el('button', undefined, 'Ältere laden');
  mehr.type = 'button';
  mehr.hidden = true;
  const download = el('a', undefined, 'Als JSON Lines herunterladen');
  download.setAttribute('download', '');
  download.hidden = true;
  abschnitt.append(formular, nichtGeladen, fehler, leer, region, mehr, download);

  let filter: Filter = {};
  let vor: number | null = null;
  // Zählt Filterwechsel: eine späte Antwort zum alten Filter darf die neue Liste nicht ergänzen.
  let lauf = 0;
  const laden = async (ersteSeite: boolean): Promise<void> => {
    const dieserLauf = lauf;
    mehr.disabled = true;
    anzeigen.disabled = true;
    fehler.hidden = true;
    nichtGeladen.hidden = true;
    try {
      const s = await anfrage<LesezugriffSeite>('GET', seitenPfad(filter, vor));
      if (dieserLauf !== lauf) return;
      for (const e of s.lesezugriffe) tbody.append(zeile(e, projekte));
      vor = s.weiter;
      const leerListe = ersteSeite && s.lesezugriffe.length === 0;
      leer.hidden = !leerListe;
      region.hidden = leerListe;
      download.hidden = false;
      download.href = downloadPfad(filter);
      mehr.hidden = vor === null;
    } catch (e) {
      if (dieserLauf !== lauf) return;
      fehler.textContent = e instanceof Error ? e.message : 'Die Lesezugriffe konnten nicht geladen werden.';
      fehler.hidden = false;
      // Erste Seite: nichts Brauchbares zu zeigen; weitere Seiten: Liste und Knopf bleiben für einen neuen Versuch.
      if (ersteSeite) {
        region.hidden = true;
        leer.hidden = true;
        mehr.hidden = true;
        download.hidden = true;
      }
    } finally {
      if (dieserLauf === lauf) {
        mehr.disabled = false;
        anzeigen.disabled = false;
      }
    }
  };
  const neuLaden = (): void => {
    lauf += 1;
    tbody.replaceChildren();
    vor = null;
    filter = {
      projekt: projektAuswahl.select.value || undefined,
      konto: kontoAuswahl.select.value ? Number(kontoAuswahl.select.value) : undefined,
    };
    void laden(true);
  };
  formular.addEventListener('submit', (ev) => {
    ev.preventDefault();
    neuLaden();
  });
  mehr.addEventListener('click', () => void laden(false));
  // Bewusst kein Laden beim Aufbau: Die Einsicht personenbezogener Daten geschieht nur auf Anforderung.
  return abschnitt;
}
