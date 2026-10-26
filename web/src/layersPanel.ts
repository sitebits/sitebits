// Panel „Ebenen": eine Zeile je Ebene mit Sichtbarkeits-Kontrollkästchen, Titel, Art,
// bei Punktwolken einem Deckkraft-Regler, und einem Knopf „Einrahmen". Nicht unterstützte Ebenen (Review
// Focus 4) sind gesperrt, nicht versteckt — sichtbar bleibt, dass scanview das Paket vollständig gelesen
// hat. Fotos stehen nicht hier, sondern im Panel „Fotos“ (fotoGalerie.ts). Ohne Giro3D-Import (nur Typen aus
// layers.ts), Texte nur über textContent/Attribute wie
// measureList.ts/pois.ts — deshalb ohne eigenen Vitest-Test (DOM-Verhalten über layers.spec.ts, E2E).
// Zeilen werden EINMAL gebaut (die Ebenenliste eines Projekts ändert sich nie zur Laufzeit) und bei
// reg.onChange() nur an den zwei Feldern (Kontrollkästchen, Regler) nachgeführt, die sich von außen ändern
// können (Kürzel 1–9, ly=-Deep-Link, ein anderes Panel). Ein Neuaufbau bei jeder Änderung (wie zuerst
// gebaut) hätte einen laufenden Regler-Zug mitten in der Geste abgebrochen und die Tastaturauswahl
// verloren — dasselbe Problem, das panels.ts mit captureFocus() für seine eigenen Rahmen löst.
import type { LayerEntry, LayerRegistry } from './layers';
import { MESH_FLAECHEN } from './meshMath';
import type { MeshFlaeche } from './meshMath';

const KIND_LABEL: Record<string, string> = { pointcloud: 'Punktwolke', panoramas: 'Standpunkte', mesh: 'Mesh', 'mesh-tiles': 'Mesh (gekachelt)', splats: 'Sichtschicht', images: 'Fotos' };
const artLabel = (kind: string): string => KIND_LABEL[kind] ?? 'nicht unterstützt';

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};

interface Row {
  li: HTMLLIElement;
  checkbox: HTMLInputElement;
  range: HTMLInputElement | null;
  shade: HTMLInputElement | null;
  flaeche: HTMLSelectElement | null;
  draht: HTMLInputElement | null;
}

const FLAECHE_LABEL: Record<MeshFlaeche, string> = { textur: 'Textur', grau: 'Grau', normalen: 'Normalen', aus: 'Aus' };

/** ``keyNumber``: Position unter den Tasten 1–9 (layerForKey), null ohne Taste (nicht unterstützt). */
function buildRow(reg: LayerRegistry, e: LayerEntry, keyNumber: number | null): Row {
  const li = el('li');
  li.dataset.layer = e.id;
  if (!e.supported) li.setAttribute('aria-disabled', 'true');

  const checkbox = document.createElement('input');
  checkbox.type = 'checkbox';
  checkbox.checked = e.visible;
  checkbox.disabled = !e.supported;
  checkbox.setAttribute('aria-label', `${e.title} anzeigen${keyNumber !== null ? ` (${keyNumber})` : ''}`);
  checkbox.addEventListener('change', () => reg.setVisible(e.id, checkbox.checked));

  const titel = el('span', e.title); titel.className = 'sv-ebene-titel';
  const art = el('span', e.supported ? artLabel(e.kind) : 'nicht unterstützt'); art.className = 'sv-ebene-art';
  li.append(checkbox, titel, art);

  for (const note of e.notes ?? []) { const s = el('small', note); s.className = 'sv-ebene-hinweis'; li.append(s); }
  // Genauigkeit (F15): offen, damit auch „kein Genauigkeitsnachweis“ ohne Klick zu sehen ist; einklappbar.
  if (e.genauigkeit?.length) {
    const d = el('details'); d.className = 'sv-ebene-genauigkeit'; d.open = true;
    const kopf = el('summary', 'Genauigkeit');
    d.append(kopf);
    for (const z of e.genauigkeit) d.append(el('small', z));
    li.append(d);
  }
  let shade: HTMLInputElement | null = null;
  let flaeche: HTMLSelectElement | null = null;
  let draht: HTMLInputElement | null = null;
  if (e.kind === 'mesh' && e.supported) {
    // Darstellung wie Sketchfab: Fläche (Textur/Grau/Normalen/Aus) und Drahtgitter darüber.
    const zeile = el('span'); zeile.className = 'sv-ebene-schalter';
    const fl = el('label', 'Fläche ');
    flaeche = el('select');
    for (const f of MESH_FLAECHEN) { const o = el('option', FLAECHE_LABEL[f]); o.value = f; flaeche.append(o); }
    flaeche.value = e.flaeche ?? 'textur';
    flaeche.setAttribute('aria-label', `${e.title} Fläche`);
    flaeche.addEventListener('change', () => reg.setFlaeche(e.id, flaeche!.value as MeshFlaeche));
    fl.append(flaeche);
    const dl = el('label', 'Drahtgitter');
    draht = el('input'); draht.type = 'checkbox'; draht.checked = e.draht ?? false;
    draht.setAttribute('aria-label', `${e.title} Drahtgitter`);
    draht.addEventListener('change', () => reg.setDraht(e.id, draht!.checked));
    dl.prepend(draht);
    zeile.append(fl, ' ', dl); li.append(zeile);
    const label = el('label', 'Schattierung'); label.className = 'sv-ebene-schalter';
    shade = el('input'); shade.type = 'checkbox'; shade.checked = e.shading ?? false;
    shade.setAttribute('aria-label', `${e.title} Schattierung`);
    shade.addEventListener('change', () => reg.setShading(e.id, shade!.checked));
    label.prepend(shade); li.append(label);
  }
  let range: HTMLInputElement | null = null;
  if (e.supported && (e.kind === 'pointcloud' || e.kind === 'mesh' || e.kind === 'mesh-tiles' || e.kind === 'splats')) {
    range = document.createElement('input');
    range.type = 'range';
    range.min = '0';
    range.max = '1';
    range.step = '0.05';
    range.value = String(e.opacity);
    range.setAttribute('aria-label', 'Deckkraft');
    range.addEventListener('input', () => reg.setOpacity(e.id, Number(range!.value)));
    li.append(range);
  }

  const frameBtn = el('button', 'Einrahmen');
  frameBtn.type = 'button';
  frameBtn.disabled = !e.supported;
  frameBtn.addEventListener('click', () => reg.frame(e.id));
  li.append(frameBtn);

  return { li, checkbox, range, shade, flaeche, draht };
}

export function attachLayersPanel(root: HTMLElement, reg: LayerRegistry, vorschlaege?: { an: boolean; aendern(an: boolean): void }): void {
  // Kein eigenes <strong>Ebenen</strong> hier (M2): der Rahmen trägt den Titel schon (main.ts registriert
  // das Panel mit title: 'Ebenen', panels.ts zeigt ihn in der Kopfzeile) — eine zweite Überschrift im Inhalt
  // wäre doppelt.
  const list = el('ul');
  list.className = 'sv-ebenen';
  root.replaceChildren(list);
  if (vorschlaege) {
    const label = el('label'); const cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = vorschlaege.an;
    cb.addEventListener('change',() => vorschlaege.aendern(cb.checked)); label.append(cb, document.createTextNode('KI-Vorschläge zeigen')); root.append(label);
  }

  const rows = new Map<string, Row>();
  let n = 0;
  for (const e of reg.entries()) {
    const taste = e.keyNumber ?? (e.supported ? ++n : null);
    // Fotos stehen im eigenen Panel „Fotos“ (fotoGalerie.ts), nicht hier; ihre Taste zählt trotzdem mit.
    if (e.kind === 'images') continue;
    const row = buildRow(reg, e, taste);
    rows.set(e.id, row);
    list.append(row.li);
  }

  // Nachführen statt neu bauen (siehe Kopfkommentar): .checked/.value per Skript lösen kein 'change'/
  // 'input' aus, also keine Rückkopplung auf reg.setVisible/setOpacity.
  reg.onChange(() => {
    for (const e of reg.entries()) {
      const row = rows.get(e.id);
      if (!row) continue;
      if (row.checkbox.checked !== e.visible) row.checkbox.checked = e.visible;
      if (row.shade) row.shade.checked = e.shading ?? false;
      if (row.flaeche && row.flaeche.value !== (e.flaeche ?? 'textur')) row.flaeche.value = e.flaeche ?? 'textur';
      if (row.draht) row.draht.checked = e.draht ?? false;
      if (row.range && Number(row.range.value) !== e.opacity) row.range.value = String(e.opacity);
    }
  });
}
