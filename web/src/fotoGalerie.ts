// Panel „Fotos“: orientierte Fotos als Galerie statt als Zeilen im Ebenenmenü (Rückmeldung iPhone 15.10.:
// im kleinen Menü nicht nützlich). Oben der Schalter für die Kameras in der 3D-Ansicht, darunter je Gruppe
// ein aufklappbarer Abschnitt mit Vorschaubildern (kleinste Stufe, erst beim Aufklappen geladen, `lazy`)
// und der Sichtbarkeit der Gruppe. Klick auf ein Bild öffnet es im Foto-Fenster. Sichtbarkeit läuft wie
// bisher über die Registry (Tasten, ly=, Hash bleiben gleich). Nur createElement/textContent (CSP).
import type { ImageSet } from './images';
import { attachFotoVorschau } from './fotoVorschau';
import type { LayerRegistry } from './layers';

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
};

export interface FotoGalerieOptionen {
  pkgBase: string;
  set: ImageSet;
  reg: LayerRegistry;
  /** ID der Fotos-Ebene in der Registry. */
  ebene: string;
  zeige(id: string): void;
  tiefe: number;
  rahmen(meter: number): void;
  suchen(): void;
}

export function attachFotoGalerie(root: HTMLElement, o: FotoGalerieOptionen): void {
  const { pkgBase, set, reg, ebene, zeige } = o;
  const kopf = el('label', undefined, 'sv-galerie-schalter');
  const alle = el('input');
  alle.type = 'checkbox';
  alle.checked = reg.isVisible(ebene);
  alle.addEventListener('change', () => reg.setVisible(ebene, alle.checked));
  kopf.append(alle, document.createTextNode(` Kameras in der Ansicht zeigen (${set.images.length})`));
  root.replaceChildren(kopf);
  const rahmen = el('label', 'Rahmengröße (m) ');
  const regler = el('input');
  regler.type = 'range'; regler.min = String(Math.log(0.1)); regler.max = String(Math.log(20)); regler.step = '0.01';
  regler.value = String(Math.log(o.tiefe)); regler.setAttribute('aria-label', 'Rahmengröße');
  const zahl = el('input');
  zahl.type = 'number'; zahl.min = '0.1'; zahl.max = '20'; zahl.step = '0.1'; zahl.value = String(o.tiefe);
  zahl.setAttribute('aria-label', 'Rahmengröße in Metern');
  const setzen = (x: number): void => {
    if (!Number.isFinite(x)) return;
    const m = Math.max(0.1, Math.min(20, x));
    zahl.value = String(Number(m.toFixed(2))); regler.value = String(Math.log(m)); o.rahmen(m);
  };
  regler.addEventListener('input', () => setzen(Math.exp(Number(regler.value))));
  zahl.addEventListener('change', () => setzen(zahl.valueAsNumber));
  rahmen.append(regler, zahl);
  const suchen = el('button', 'Fotos zu einer Stelle finden'); suchen.type = 'button';
  suchen.addEventListener('click', o.suchen);
  root.append(rahmen, suchen);

  const gruppenBoxen = new Map<string, HTMLInputElement>();
  for (const g of set.groups) {
    const bilder = set.images.filter((i) => i.group === g.id);
    if (bilder.length === 0) continue;
    const abschnitt = el('details', undefined, 'sv-galerie-gruppe');
    abschnitt.append(el('summary', `${g.title} (${bilder.length})`));
    const schalter = el('label', undefined, 'sv-galerie-schalter');
    const box = el('input');
    box.type = 'checkbox';
    box.setAttribute('aria-label', `Gruppe ${g.title} anzeigen`);
    box.addEventListener('change', () => reg.setGroupVisible(ebene, g.id, box.checked));
    schalter.append(box, document.createTextNode(' in der Ansicht zeigen'));
    gruppenBoxen.set(g.id, box);
    const raster = el('div', undefined, 'sv-galerie-raster');
    abschnitt.append(schalter, raster);
    raster.id = `foto-gruppe-${gruppenBoxen.size}`;
    const vorschau = attachFotoVorschau(raster, pkgBase, zeige);
    let geladen = false;
    abschnitt.addEventListener('toggle', () => {
      if (!abschnitt.open) return;
      if (!geladen) { geladen = true; vorschau.setze(bilder.map((bild) => ({ bild }))); }
      else vorschau.aktualisiere();
    });
    root.append(abschnitt);
  }

  const nachfuehren = (): void => {
    const e = reg.entries().find((x) => x.id === ebene);
    if (!e) return;
    if (alle.checked !== e.visible) alle.checked = e.visible;
    for (const g of e.groups ?? []) {
      const box = gruppenBoxen.get(g.id);
      if (box && box.checked !== g.visible) box.checked = g.visible;
    }
  };
  nachfuehren();
  reg.onChange(nachfuehren);
}
