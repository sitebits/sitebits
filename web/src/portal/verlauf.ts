// Verlaufsseite /verlauf?p=<id>: wer hat wann was geändert, mit
// Vorher/Nachher je Feld; seitenweise („Ältere laden“), Download als JSON Lines. Nur textContent.
import { anfrage, PlattformFehler, projektPfad } from '../plattformApi';
import type { ProjektInfo } from '../plattformApi';
import { artText, bezeichnung, unterschiede, werText, zeitText } from './verlaufText';
import type { Ereignis, Seite } from './verlaufText';

function el<K extends keyof HTMLElementTagNameMap>(tag: K, klasse?: string, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (klasse) e.className = klasse;
  if (text !== undefined) e.textContent = text;
  return e;
}

function zeile(e: Ereignis, namen: ReadonlyMap<number, string>): HTMLTableRowElement {
  const tr = el('tr');
  const was = [artText(e.art), bezeichnung(e, namen)].filter(Boolean).join(' ');
  tr.append(el('td', undefined, zeitText(e.zeit)), el('td', undefined, werText(e)), el('td', undefined, was));
  const liste = el('ul');
  for (const u of unterschiede(e.vorher, e.nachher, e.art)) liste.append(el('li', undefined, `${u.feld}: ${u.vorher} → ${u.nachher}`));
  const td = el('td');
  td.append(liste);
  tr.append(td);
  return tr;
}

function fehlerText(e: unknown): string {
  if (e instanceof PlattformFehler && e.status === 403) return 'Den Verlauf sehen Bearbeiter, Projektleitung und Admins.';
  if (e instanceof PlattformFehler && e.status === 404) return 'Projekt nicht gefunden.';
  return e instanceof Error ? e.message : 'Der Verlauf konnte nicht geladen werden.';
}

export async function zeigeVerlauf(root: HTMLElement, pid: string): Promise<void> {
  root.replaceChildren();
  const seite = el('div', 'sv-seite sv-verlauf');
  const kopf = el('header', 'sv-kopf');
  const zurueck = el('a', undefined, '← Projekte');
  zurueck.href = '/';
  const titel = el('h1', 'sv-kopf-titel', 'Verlauf');
  kopf.append(zurueck, titel);
  const fehler = el('p', 'sv-fehler');
  fehler.hidden = true;
  const tabelle = el('table', 'sv-verlauf-tabelle');
  const kopfzeile = el('tr');
  for (const t of ['Zeit', 'Wer', 'Was', 'Änderungen']) kopfzeile.append(el('th', undefined, t));
  const thead = el('thead');
  thead.append(kopfzeile);
  const tbody = el('tbody');
  tabelle.append(thead, tbody);
  // Datentabellen dürfen laut WCAG 1.4.10 zweidimensional bleiben; die Seite selbst darf trotzdem
  // nicht waagerecht scrollen — eigener Scroll-Container mit Fokus und Namen statt eines Seiten-
  // umbruchs (axe-Regel scrollable-region-focusable).
  const tabelleRegion = el('div', 'sv-tabelle-region');
  tabelleRegion.setAttribute('role', 'region');
  tabelleRegion.setAttribute('aria-label', 'Verlauf');
  tabelleRegion.tabIndex = 0;
  tabelleRegion.append(tabelle);
  const mehr = el('button', undefined, 'Ältere laden');
  mehr.type = 'button';
  mehr.hidden = true;
  // Ein Fehler beim „Ältere laden“ darf die bereits angezeigten Zeilen nicht
  // wegräumen — eigene, kleine Fehlermeldung unter der Tabelle statt zeigeFehler() (die würde
  // Tabelle UND Knopf verstecken und damit jeden neuen Versuch verhindern).
  const mehrFehler = el('p', 'sv-fehler');
  mehrFehler.hidden = true;
  const download = el('a', undefined, 'Als JSON Lines herunterladen');
  download.href = `${projektPfad(pid)}/protokoll.jsonl`;
  download.setAttribute('download', '');
  seite.append(kopf, fehler, tabelleRegion, mehrFehler, mehr, download);
  root.append(seite);

  const zeigeFehler = (e: unknown): void => {
    fehler.textContent = fehlerText(e);
    fehler.hidden = false;
    tabelleRegion.hidden = true;
    mehr.hidden = true;
    download.hidden = true;
  };
  if (!pid) {
    zeigeFehler(new PlattformFehler(404, 'Projekt nicht gefunden.'));
    return;
  }
  try {
    const info = await anfrage<ProjektInfo>('GET', projektPfad(pid));
    titel.textContent = `Verlauf – ${info.titel}`;
  } catch (e) {
    zeigeFehler(e);
    return;
  }
  // Namen der Beteiligten für Konto-Bezüge; ohne Recht oder bei Fehlern bleibt es bei „Konto <Nummer>“.
  const namen = new Map<number, string>();
  try {
    const b = await anfrage<{ beteiligte: { nutzerId: number; nutzer: string }[] }>('GET', `${projektPfad(pid)}/beteiligte`);
    for (const x of b.beteiligte) namen.set(x.nutzerId, x.nutzer);
  } catch {
    /* Verlauf bleibt lesbar */
  }
  let vor: number | null = null;
  const laden = async (istErsteSeite: boolean): Promise<void> => {
    mehr.disabled = true;
    mehrFehler.hidden = true;
    try {
      const s = await anfrage<Seite>('GET', `${projektPfad(pid)}/protokoll?limit=50${vor === null ? '' : `&vor=${vor}`}`);
      if (s.ereignisse.length === 0 && vor === null) {
        const leer = el('tr');
        const td = el('td', undefined, 'Noch keine Änderungen.');
        td.colSpan = 4;
        leer.append(td);
        tbody.append(leer);
      }
      for (const e of s.ereignisse) tbody.append(zeile(e, namen));
      vor = s.weiter;
      mehr.hidden = vor === null;
    } catch (e) {
      // Erste Seite: noch nichts angezeigt — die volle Fehleransicht ersetzt die (leere) Tabelle.
      // Weitere Seiten: Tabelle und Knopf bleiben stehen, nur ein Hinweis für einen neuen Versuch.
      if (istErsteSeite) zeigeFehler(e);
      else {
        mehrFehler.textContent = fehlerText(e);
        mehrFehler.hidden = false;
      }
    } finally {
      mehr.disabled = false;
    }
  };
  mehr.addEventListener('click', () => void laden(false));
  await laden(true);
}
