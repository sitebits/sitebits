// Seite /befunde?p=<id>: Liste „Fällig/überfällig“ eines Projekts mit Befund-Fäden — überfällig zuerst (Reihenfolge
// des Servers), Filter Status, Zuständig, Frist und Kategorie. Je Befund Kennzeichen als Text, „an der Stelle öffnen“
// (Befundlink in den Viewer) und der Verlauf zum Lesen und Kommentieren ohne 3D, eine Spalte (Handy, schwache Geräte).
// Nur createElement/textContent (strenge CSP). Reine Teile: ../fadenModell.ts.
import { createFadenAnsicht } from '../fadenAnsicht';
import { befundeLesen, namenLaden } from '../fadenApi';
import {
  befundlinkPfad, FILTER_LEER, FRIST_TEXT, FRIST_WERTE, kategorienAus, kennzeichen, namenAus, OHNE_NAMEN, STATUS_WERTE,
  statusText, ZUSTAENDIG_TEXT, ZUSTAENDIG_WERTE, zustaendigText,
} from '../fadenModell';
import type { BefundZeile, Filter, Namen, Stand } from '../fadenModell';
import { anfrage, PlattformFehler, projektPfad } from '../plattformApi';
import type { ProjektInfo } from '../plattformApi';

export const AUS_TEXT = 'Befund-Fäden sind in diesem Projekt ausgeschaltet.';
const NICHT_GEFUNDEN = 'Projekt nicht gefunden.';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function auswahl(id: string, text: string, optionen: readonly (readonly [string, string])[]): { label: HTMLDivElement; select: HTMLSelectElement } {
  const select = el('select');
  select.id = id;
  for (const [wert, t] of optionen) {
    const o = el('option', undefined, t);
    o.value = wert;
    select.append(o);
  }
  // Beschriftung neben (nicht um) die Liste: sonst trüge ihr Name auch die gewählte Option.
  const label = el('label', undefined, text);
  label.htmlFor = id;
  const feld = el('div', 'sv-feld');
  feld.append(label, select);
  return { label: feld, select };
}

const fehlerText = (e: unknown): string =>
  e instanceof PlattformFehler && e.status === 404 ? NICHT_GEFUNDEN : e instanceof Error ? e.message : 'Die Befunde konnten nicht geladen werden.';

const anzahlText = (n: number): string => (n === 0 ? 'Keine Befunde für diese Auswahl.' : n === 1 ? '1 Befund.' : `${n} Befunde.`);

export async function zeigeBefunde(root: HTMLElement, pid: string, nutzerId: number | null, oeffnen: string | null = null): Promise<void> {
  root.replaceChildren();
  const seite = el('div', 'sv-seite sv-befunde');
  const kopf = el('header', 'sv-kopf');
  const zurueck = el('a', undefined, '← Projekte');
  zurueck.href = '/';
  const titel = el('h1', 'sv-kopf-titel', 'Befunde');
  kopf.append(zurueck, titel);
  const fehler = el('p', 'sv-fehler');
  fehler.setAttribute('role', 'alert');
  fehler.hidden = true;
  const inhalt = el('div', 'sv-befunde-inhalt');
  seite.append(kopf, fehler, inhalt);
  root.append(seite);
  const zeigeFehler = (t: string): void => {
    fehler.textContent = t;
    fehler.hidden = false;
    inhalt.replaceChildren();
  };

  if (!pid) return zeigeFehler(NICHT_GEFUNDEN);
  let info: ProjektInfo;
  try {
    info = await anfrage<ProjektInfo>('GET', projektPfad(pid));
  } catch (e) {
    return zeigeFehler(fehlerText(e));
  }
  titel.textContent = `Befunde – ${info.titel}`;
  if (info.befund_faeden !== true) return zeigeFehler(AUS_TEXT);

  const filterForm = el('form', 'sv-befunde-filter');
  filterForm.setAttribute('role', 'search');
  filterForm.setAttribute('aria-label', 'Befunde filtern');
  const status = auswahl('sv-bf-status', 'Status', [['', 'alle'], ...STATUS_WERTE.map((s) => [s, statusText(s)] as const)]);
  const zust = auswahl('sv-bf-zustaendig', 'Zuständig', ZUSTAENDIG_WERTE.map((z) => [z, ZUSTAENDIG_TEXT[z]] as const));
  const frist = auswahl('sv-bf-frist', 'Frist', [['', 'alle'], ...FRIST_WERTE.map((f) => [f, FRIST_TEXT[f]] as const)]);
  const kategorie = auswahl('sv-bf-kategorie', 'Kategorie', [['', 'alle'], ...kategorienAus(info.kategorien).map((k) => [k, k] as const)]);
  filterForm.append(status.label, zust.label, frist.label, kategorie.label);
  const anzahl = el('p', 'sv-status');
  anzahl.setAttribute('role', 'status');
  const liste = el('ul', 'sv-befunde-liste');
  inhalt.append(filterForm, anzahl, liste);

  let namen: Namen = OHNE_NAMEN;
  const namenP = namenLaden(pid).then((n) => {
    namen = namenAus(n.gewerke, n.beteiligte);
  });

  const zeile = (b: BefundZeile, i: number): HTMLLIElement => {
    const li = el('li', b.ueberfaellig ? 'sv-befund-zeile sv-ueberfaellig' : 'sv-befund-zeile');
    const artikel = el('article');
    const h = el('h2', 'sv-befund-titel', b.poi.daten.title);
    h.id = `sv-bf-${i}-titel`;
    artikel.setAttribute('aria-labelledby', h.id);
    const angaben = el('dl', 'sv-befund-angaben');
    /** Angaben der Zeile; nach einer eigenen Änderung aus dem Stand der Antwort, ohne die Liste neu zu laden. */
    const fuelle = (st: Stand, ueberfaellig: boolean): void => {
      angaben.replaceChildren();
      const feld = (name: string, wert: string): void => {
        if (!wert) return;
        const paar = el('div');
        paar.append(el('dt', undefined, name), el('dd', undefined, wert));
        angaben.append(paar);
      };
      feld('Stand', kennzeichen(st, ueberfaellig));
      feld('Zuständig', st.zustaendig ? zustaendigText(st.zustaendig, namen) : '');
      feld('Kategorie', b.poi.daten.category);
      li.classList.toggle('sv-ueberfaellig', ueberfaellig);
    };
    fuelle(b.stand, b.ueberfaellig);
    const aktionen = el('div', 'sv-befund-aktionen');
    const stelle = el('a', undefined, 'an der Stelle öffnen');
    stelle.href = befundlinkPfad(pid, b.poi.id);
    stelle.setAttribute('aria-label', `„${b.poi.daten.title}“ an der Stelle öffnen`);
    const oeffner = el('button', undefined, 'Verlauf anzeigen');
    oeffner.type = 'button';
    oeffner.setAttribute('aria-expanded', 'false');
    oeffner.setAttribute('aria-label', `Verlauf von „${b.poi.daten.title}“ anzeigen`);
    aktionen.append(stelle, oeffner);
    // Ein geänderter Stand (Status, Frist, Zuständig) erscheint sofort in der Zeile, die Reihenfolge erst beim nächsten
    // Laden der Liste — der Verlauf bleibt offen, damit der Fokus nicht verloren geht.
    const faden = createFadenAnsicht({
      pruefen: ['bearbeiter','projektleitung','admin'].includes(info.rolle),
      pid, id: `sv-bf-${i}-faden`, nutzerId, ebene: 3, kommentar: { label: 'Kommentar', senden: 'Senden' },
      onStand: (_poi, st) => fuelle(st, st.ueberfaellig === true),
    });
    faden.element.id = `sv-bf-${i}-verlauf`;
    oeffner.setAttribute('aria-controls', faden.element.id);
    oeffner.dataset['poi'] = b.poi.id;
    oeffner.addEventListener('click', () => {
      const auf = oeffner.getAttribute('aria-expanded') !== 'true';
      oeffner.setAttribute('aria-expanded', String(auf));
      oeffner.textContent = auf ? 'Verlauf ausblenden' : 'Verlauf anzeigen';
      oeffner.setAttribute('aria-label', `Verlauf von „${b.poi.daten.title}“ ${auf ? 'ausblenden' : 'anzeigen'}`);
      if (auf) void faden.zeige(b.poi.id);
      else void faden.zeige(null);
    });
    artikel.append(h, angaben, aktionen, faden.element);
    li.append(artikel);
    return li;
  };

  let lauf = 0;
  const laden = async (): Promise<void> => {
    const n = ++lauf;
    const f: Filter = {
      ...FILTER_LEER,
      status: status.select.value as Filter['status'],
      zustaendig: zust.select.value as Filter['zustaendig'],
      frist: frist.select.value as Filter['frist'],
      kategorie: kategorie.select.value,
    };
    try {
      const [antwort] = await Promise.all([befundeLesen(pid, f), namenP]);
      if (n !== lauf) return;
      fehler.hidden = true;
      liste.replaceChildren(...antwort.befunde.map(zeile));
      anzahl.textContent = anzahlText(antwort.befunde.length);
    } catch (e) {
      if (n !== lauf) return;
      fehler.textContent = fehlerText(e);
      fehler.hidden = false;
      liste.replaceChildren();
      anzahl.textContent = '';
    }
  };
  for (const s of [status.select, zust.select, frist.select, kategorie.select]) s.addEventListener('change', () => void laden());
  filterForm.addEventListener('submit', (ev) => ev.preventDefault());
  await laden();
  // Link aus einer Benachrichtigung (?b=<Befund>): Verlauf dieses Befunds öffnen und hinspringen.
  if (oeffnen) {
    const knopf = [...liste.querySelectorAll<HTMLButtonElement>('button[data-poi]')].find((k) => k.dataset['poi'] === oeffnen);
    if (knopf) {
      knopf.click();
      knopf.closest('li')?.scrollIntoView({ block: 'start' });
      knopf.focus({ preventScroll: true });
    } else {
      anzahl.textContent = `${anzahl.textContent ?? ''} Der Befund aus dem Link ist mit diesen Filtern nicht in der Liste oder nicht mehr vorhanden.`;
    }
  }
}
