import { ausgelasseneVorschlaege, vorschlagText } from './vorschlagApi';
import { exportMitHinweis, pruefknoepfe } from './vorschlagBedienung';
import { anzeigeSprache, measurementNotes, sourceLabel } from './pointSources';
import type { SourceLayer } from './pointSources';
// Messliste: alle Messungen in Anlegereihenfolge (auch nach dem Neuladen — siehe
// hash.ts o=) mit Werten, Auswahl per Zeilenklick, Löschen je Zeile, CSV-/JSON-Download und „Alle löschen“
// (rückgängig machbar; im Plattformmodus mit Rückfrage, weil es am Server für alle löscht). Löst die
// lil-gui-Knöpfe „Messungen herunterladen/löschen“ ab. Texte nur über textContent.
import type { Project } from './manifest';
import { beschriftung } from './koordinaten';
import { formatM, measurementsCsv, num } from './measureCsv';
import type { MeasureTool } from './measure';
import { MEASURE_LABEL } from './measureStore';
import type { Measurement, MeasureStore } from './measureStore';

export interface MeasureList {
  refresh(): void;
  toggle(): void;
}

/** Rückfrage vor „Alle löschen“ im Plattformmodus (Final-Fix C/D): dort löscht es am Server für alle. */
export const alleLoeschenFrage = (n: number): string =>
  `Löscht alle ${n} Messungen des Projekts – für alle Beteiligten. Wirklich löschen?`;

/** Darf „Alle löschen“ n Messungen löschen? Nur gemeinsam genutzte (Server) brauchen eine Bestätigung. */
export function alleLoeschenErlaubt(n: number, gemeinsam: boolean, frage: (text: string) => boolean): boolean {
  return n === 0 || !gemeinsam || frage(alleLoeschenFrage(n));
}

function download(blob: Blob, name: string): void {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

/** Hauptwert und Nebenwerte einer Messung für die Liste. */
function values(m: Measurement): [string, string] {
  switch (m.kind) {
    case 'distance':
      return [formatM(m.distanceM), ''];
    case 'polyline':
      return [`Σ ${formatM(m.lengthM)}`, `${m.segmentsM.length} Teilstrecken`];
    case 'area':
      return [`${num(m.areaM2)} m²`, `U ${formatM(m.perimeterM)}${m.nonPlanar ? ' · ⚠ nicht eben' : ''}`];
    case 'height':
      return [`Δz ${m.dzM > 0 ? '+' : ''}${formatM(m.dzM)}`, `horiz. ${formatM(m.horizontalM)} · schräg ${formatM(m.slantM)}`];
  }
}

export function attachMeasureList(o: {
  root: HTMLElement;
  vorschlaegeSichtbar?: () => boolean;
  pruefen?: (id: string, a: 'annehmen' | 'verwerfen') => Promise<void>;
  sourceLayers?: readonly SourceLayer[];
  store: MeasureStore;
  tool: MeasureTool;
  /** `frame` beschriftet die Koordinatenspalte der CSV (F23); fehlt er, steht dort „[m]“. */
  manifest: Pick<Project, 'projectId' | 'revision'> & Partial<Pick<Project, 'frame'>>;
  /** Nach Änderungen am Speicher aus der Liste (Hash, Liste selbst). */
  onChange: () => void;
  onToggle: (open: boolean) => void;
  notify: (text: string) => void;
  /** true = Löschen wirkt am Server für alle Projektbeteiligten (Plattformmodus, Bearbeiter) → Rückfrage. */
  gemeinsam?: () => boolean;
}): MeasureList {
  const { root, store, tool, manifest: m } = o;
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  const table = el('table');
  const empty = el('p', 'Noch keine Messungen');
  empty.className = 'muted';
  const csv = el('button', 'CSV');
  const json = el('button', 'JSON');
  const clearAll = el('button', 'Alle löschen');
  for (const b of [csv, json, clearAll]) b.type = 'button';
  const bar = el('div');
  bar.append(csv, json, clearAll);
  root.replaceChildren(el('strong', 'Messungen'), empty, table, bar);

  const changed = (): void => {
    tool.sync();
    o.onChange();
  };

  const refresh = (): void => {
    const list = store.list().filter(m => !m.vorschlag || o.vorschlaegeSichtbar?.() !== false);
    const selected = tool.selection()?.id ?? null;
    empty.hidden = list.length > 0;
    table.replaceChildren(
      ...list.map((meas) => {
        const tr = el('tr');
        tr.setAttribute('aria-selected', String(meas.id === selected));
        const [main, extra] = values(meas);
        const value = el('td', main);
        if (extra) {
          const small = el('div', extra);
          small.className = 'muted';
          value.append(small);
        }
        const sources = el('div', meas.points.map((_,i) => `P${i+1}: ${sourceLabel(meas.pointSources?.[i],o.sourceLayers ?? [])}`).join(' · '));
        sources.className = 'muted'; value.append(sources);
        for (const note of measurementNotes(meas,o.sourceLayers ?? [],anzeigeSprache())) value.append(el('div',note));
        const del = el('button', '×');
        del.type = 'button';
        del.setAttribute('aria-label', `Messung ${meas.id} löschen`);
        del.title = `Messung ${meas.id} löschen`;
        del.addEventListener('click', (ev) => {
          ev.stopPropagation();
          store.remove(meas.id);
          changed();
        });
        const tdDel = el('td');
        if (meas.vorschlag) {
          value.append(el('strong',vorschlagText(meas.vorschlag))); tr.classList.add('sv-vorschlag');
          if (o.pruefen) pruefknoepfe(tdDel,a => o.pruefen!(meas.id,a),o.notify);
        } else tdDel.append(del);
        tr.append(el('td', meas.id), el('td', MEASURE_LABEL[meas.kind]), value, tdDel);
        tr.addEventListener('click', () => {
          tool.select({ id: meas.id });
          refresh();
        });
        return tr;
      }),
    );
  };

  const exportiere = (fn: () => void): void => exportMitHinweis(ausgelasseneVorschlaege(store.list()),fn);
  csv.addEventListener('click', () => exportiere(() =>
    download(new Blob([measurementsCsv(store.list(), o.sourceLayers, m.frame && beschriftung(m.frame.crs))], { type: 'text/csv;charset=utf-8' }), `measurements-${m.projectId}-r${m.revision}.csv`),
  ));
  json.addEventListener('click', () => exportiere(() =>
    download(new Blob([JSON.stringify(store.toJSON(m,o.sourceLayers), null, 2)], { type: 'application/json' }), `measurements-${m.projectId}-r${m.revision}.json`),
  ));
  clearAll.addEventListener('click', () => {
    // Auch eine offene Zeichnung (gelbe Vormarke) verwerfen — wie der frühere lil-gui-Knopf.
    tool.cancelDrawing();
    const n = store.list().filter(m => !m.vorschlag).length;
    if (!n) return;
    if (!alleLoeschenErlaubt(n, o.gemeinsam?.() ?? false, (t) => window.confirm(t))) return;
    store.clear(true);
    changed();
    o.notify(store.list().some(m => m.vorschlag) ? 'Messungen gelöscht – Strg+Z stellt sie wieder her; Vorschläge bleiben zum Prüfen erhalten.' : 'Alle Messungen gelöscht – Strg+Z stellt sie wieder her');
  });

  refresh();
  return {
    refresh,
    toggle: () => {
      root.hidden = !root.hidden;
      o.onToggle(!root.hidden);
      if (!root.hidden) refresh();
    },
  };
}
