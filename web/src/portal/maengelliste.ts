// Eine geschützte Quelle für Kopf und Befunde; Server entscheidet über Exportrechte.
import { namenLaden } from '../fadenApi';
import { namenAus, OHNE_NAMEN } from '../fadenModell';
import type { Stand } from '../fadenModell';
import { anfrage, PlattformFehler, projektPfad } from '../plattformApi';
import type { PoiDaten, ServerEintrag } from '../plattformApi';
import { spaltenFuer, zeitUTC, zeilen } from './maengellisteModell';

type Liste = { titel: string; revision: number | null; stand: number; pois: (ServerEintrag<PoiDaten> & { stand?: Stand })[] };

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
}

export async function zeigeMaengelliste(root: HTMLElement, pid: string): Promise<void> {
  const seite = el('div');
  seite.className = 'sv-seite sv-maengelliste';
  const nav = el('nav');
  nav.className = 'sv-druck-aktionen';
  nav.setAttribute('aria-label', 'Mängellisten-Aktionen');
  const zurueck = el('a', '← Projekte');
  zurueck.href = '/';
  nav.append(zurueck);
  const titel = el('h1', 'Mängelliste');
  const meldung = el('p', 'Mängelliste wird geladen.');
  meldung.setAttribute('role', 'status');
  seite.append(nav, titel, meldung);
  root.replaceChildren(seite);
  try {
    if (!pid) throw new PlattformFehler(404, 'Projekt nicht gefunden.');
    const liste = await anfrage<Liste>('GET', `${projektPfad(pid)}/maengelliste`);
    titel.textContent = `Mängelliste – ${liste.titel}`;
    meldung.textContent = `Paket-Revision: ${liste.revision ?? '—'} · Stand (UTC): ${zeitUTC(liste.stand)}`;
    const drucken = el('button', 'Drucken / als PDF speichern');
    drucken.type = 'button';
    drucken.addEventListener('click', () => window.print());
    nav.append(drucken);
    const tabelle = el('table');
    tabelle.className = 'sv-maengelliste-tabelle';
    tabelle.append(el('caption', 'Befunde des Projekts (Koordinaten in Metern)'));
    const head = el('thead');
    const kopf = el('tr');
    // Befund-Fäden an: Status, Zuständig, Frist; Zuständig braucht die Namen der Gewerke und Beteiligten.
    const mitFaden = liste.pois.some((p) => p.stand !== undefined);
    const n = mitFaden ? await namenLaden(pid) : null;
    const namen = n ? namenAus(n.gewerke, n.beteiligte) : OHNE_NAMEN;
    for (const spalte of spaltenFuer(liste.pois)) {
      const th = el('th', spalte);
      th.scope = 'col';
      kopf.append(th);
    }
    head.append(kopf);
    const body = el('tbody');
    for (const werte of zeilen(liste.pois, namen)) {
      const tr = el('tr');
      for (const wert of werte) tr.append(el('td', wert));
      body.append(tr);
    }
    tabelle.append(head, body);
    const region = el('div');
    region.className = 'sv-tabelle-region';
    region.setAttribute('role', 'region');
    region.setAttribute('aria-label', 'Befunde');
    region.tabIndex = 0;
    region.append(tabelle);
    seite.append(region);
    if (liste.pois.length === 0) seite.append(el('p', 'Noch keine Befunde.'));
  } catch (e) {
    meldung.className = 'sv-fehler';
    meldung.textContent = e instanceof PlattformFehler && e.status === 404 ? 'Projekt nicht gefunden.'
      : e instanceof Error ? e.message : 'Die Mängelliste konnte nicht geladen werden.';
  }
}
