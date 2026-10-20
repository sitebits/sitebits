import { anfrage, projektPfad } from '../plattformApi';
import type { ProjektInfo } from '../plattformApi';
import { rechte } from '../rechte';
import { fadenLesen, namenLaden } from '../fadenApi';
import { namenAus, OHNE_NAMEN, zeitText } from '../fadenModell';
import { annehmen, verwerfen, fadenAnnehmen, fadenVerwerfen, zuPruefen } from '../vorschlagApi';
import type { Namen } from '../fadenModell';
import type { Pruefzeile } from './zuPruefenModell';
import type { Pruefliste } from '../vorschlagApi';
import { gesammelt, ergebnisText, anzahl, pruefgruppen } from './zuPruefenModell';
function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (text !== undefined)
    e.textContent = text;
  return e;
}
function knopf(text: string, fn: () => void): HTMLButtonElement {
  const b = el('button', text);
  b.type = 'button';
  b.addEventListener('click', fn);
  return b;
}
/** Native Modal mit Fokusführung und Escape; kein blockierendes confirm(). */
function bestaetigen(text: string, start: () => void): void {
  const d = el('dialog');
  const h = el('h2', text);
  h.id = 'sv-pruefen-dialog';
  d.setAttribute('aria-labelledby', h.id);
  d.append(h, knopf('Bestätigen', () => { d.close(); start(); }), knopf('Abbrechen', () => d.close()));
  d.addEventListener('close', () => d.remove());
  document.body.append(d);
  d.showModal();
}
export async function zeigeZuPruefen(root: HTMLElement, pid: string): Promise<void> {
  const seite = el('div');
  seite.className = 'sv-seite';
  const zurueck = el('a', '← Projekte');
  zurueck.href = '/';
  const titel = el('h1', 'Zu prüfen');
  const status = el('p');
  status.setAttribute('role', 'status');
  status.tabIndex = -1;
  const fehler = el('p');
  fehler.setAttribute('role', 'alert');
  const inhalt = el('div');
  seite.append(zurueck, titel, status, fehler, inhalt);
  root.replaceChildren(seite);
  let liste: Pruefliste;
  let sendet = false;
  let namen: Namen = OHNE_NAMEN;
  const entscheide = async (z: Pruefzeile, a: 'annehmen' | 'verwerfen', text?: string): Promise<unknown> => {
    const ziel = z.ziel;
    if (ziel.tabelle === 'faden') {
      return a === 'verwerfen' ? fadenVerwerfen(pid, ziel.poi, ziel.schluessel, text)
        : fadenAnnehmen(pid, ziel.poi, ziel.schluessel, (await fadenLesen(pid, ziel.poi)).stand.version);
    }
    return (a === 'annehmen' ? annehmen : verwerfen)(ziel.tabelle, pid, ziel.id, ziel.version);
  };
  const zeichne = (): void => {
    inhalt.replaceChildren();
    if (!anzahl(liste)) {
      inhalt.append(el('p', 'Nichts zu prüfen.'));
      return;
    }
    const gruppen = pruefgruppen(liste, pid, namen);
    const pruefe = async (zeilen: Pruefzeile[], a: 'annehmen' | 'verwerfen', text?: string): Promise<void> => {
      if (sendet)
        return;
      sendet = true;
      fehler.textContent = '';
      for (const b of inhalt.querySelectorAll('button'))
        b.disabled = true;
      try {
        const r = await gesammelt(zeilen, z => entscheide(z, a, text));
        status.textContent = ergebnisText(r, a);
        await laden();
        status.focus();
      }
      catch (e) {
        fehler.textContent = e instanceof Error ? e.message : String(e);
      }
      finally {
        sendet = false;
        for (const b of inhalt.querySelectorAll('button'))
          b.disabled = false;
      }
    };
    for (const { name, zeilen } of gruppen) {
      const s = el('section');
      s.append(el('h2', name));
      if (zeilen.length)
        for (const a of ['annehmen', 'verwerfen'] as const)
          s.append(knopf(`Alle ${a}`, () => bestaetigen(`${zeilen.length} Vorschläge ${a}?`, () => { void pruefe(zeilen, a); })));
      const ul = el('ul');
      for (const z of zeilen) {
        const li = el('li');
        li.className = 'sv-pruefzeile';
        const link = el('a', 'An der Stelle öffnen');
        link.href = z.link;
        li.append(el('h3', z.text), el('p', `${z.wer} · ${zeitText(z.wann)}`), link);
        const grund = el('input');
        grund.maxLength = 4000;
        grund.setAttribute('aria-label', 'Begründung (optional)');
        if (z.ziel.tabelle === 'faden')
          li.append(grund);
        li.append(knopf('Annehmen', () => { void pruefe([z], 'annehmen'); }), knopf('Verwerfen', () => { void pruefe([z], 'verwerfen', grund.value); }));
        ul.append(li);
      }
      s.append(ul);
      inhalt.append(s);
    }
  };
  async function laden(): Promise<void> { liste = await zuPruefen(pid); zeichne(); }
  try {
    const info = await anfrage<ProjektInfo>('GET', projektPfad(pid));
    if (!rechte(info.rolle).alleAendern) {
      fehler.textContent = 'Prüfen ist ab der Rolle Bearbeiter möglich.';
      return;
    }
    titel.textContent = `Zu prüfen – ${info.titel}`;
    const n = await namenLaden(pid);
    namen = namenAus(n.gewerke, n.beteiligte);
    await laden();
  }
  catch (e) {
    fehler.textContent = e instanceof Error ? e.message : String(e);
  }
}
