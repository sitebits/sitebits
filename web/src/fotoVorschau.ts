// Eine virtualisierte Vorschaubild-Logik für Galerie und Bildleiste. Auch geöffnete große Gruppen
// halten nur sichtbare Zeilen (+ eine Zeile Puffer) im DOM; Tastatur kann jede Position erreichen.
import type { OrientedImage } from './images';
import { levelUrl } from './images';
export interface VorschauEintrag { bild: OrientedImage; zusatz?: string }
export function attachFotoVorschau(root: HTMLElement, basis: string, waehlen: (id: string) => void): {
  setze(bilder: readonly VorschauEintrag[], aktiv?: string | null): void;
  aktualisiere(): void;
} {
  root.classList.add('sv-vorschau');
  root.tabIndex = 0;
  root.setAttribute('role', 'group');
  root.setAttribute('aria-label', 'Fotos – Pfeiltasten wählen, Eingabe öffnet');
  const inhalt = document.createElement('div');
  inhalt.className = 'sv-vorschau-inhalt';
  root.replaceChildren(inhalt);
  let bilder: readonly VorschauEintrag[] = [], aktiv: string | null = null, fokus = 0;
  const zeichnen = (): void => {
    const hatteFokus = root.contains(document.activeElement) && document.activeElement !== root;
    const spalten = Math.max(1, Math.floor(root.clientWidth / 100));
    const zeile = 100;
    inhalt.style.height = `${Math.ceil(bilder.length / spalten) * zeile}px`;
    const anfang = Math.max(0, Math.floor(root.scrollTop / zeile) - 1) * spalten;
    const ende = Math.min(bilder.length, (Math.ceil((root.scrollTop + (root.clientHeight || 200)) / zeile) + 1) * spalten);
    inhalt.replaceChildren();
    for (let i = anfang; i < ende; i++) {
      const e = bilder[i]!;
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'sv-galerie-bild';
      b.style.cssText = `position:absolute;left:${i % spalten * 100 / spalten}%;top:${Math.floor(i / spalten) * zeile}px;width:${100 / spalten}%`;
      b.id = `${root.id}-bild-${i}`;
      b.setAttribute('aria-pressed', String(e.bild.id === aktiv));
      b.tabIndex = i === fokus ? 0 : -1;
      b.title = e.zusatz ? `${e.bild.label} · ${e.zusatz}` : e.bild.label;
      const img = document.createElement('img');
      img.loading = 'lazy'; img.decoding = 'async'; img.alt = e.bild.label;
      img.src = levelUrl(basis, e.bild, e.bild.levels[0]!);
      b.append(img);
      if (e.zusatz) { const text = document.createElement('span'); text.textContent = e.zusatz; b.append(text); }
      b.addEventListener('focus', () => { fokus = i; });
      b.addEventListener('click', () => { fokus = i; waehlen(e.bild.id); });
      inhalt.append(b);
    }
    if (hatteFokus) {
      const b = document.getElementById(`${root.id}-bild-${fokus}`);
      (b ?? root).focus({ preventScroll: true });
    }
  };
  root.addEventListener('scroll', zeichnen);
  new ResizeObserver(zeichnen).observe(root);
  root.addEventListener('keydown', (ev) => {
    const spalten = Math.max(1, Math.floor(root.clientWidth / 100));
    const delta = ({ ArrowRight: 1, ArrowLeft: -1, ArrowDown: spalten, ArrowUp: -spalten } as Record<string, number>)[ev.key];
    if (ev.key === 'Enter' || ev.key === ' ') { if (bilder[fokus]) waehlen(bilder[fokus]!.bild.id); }
    else if (delta !== undefined || ev.key === 'Home' || ev.key === 'End') {
      fokus = Math.max(0, Math.min(bilder.length - 1, ev.key === 'Home' ? 0 : ev.key === 'End' ? bilder.length - 1 : fokus + delta!));
      const oben = Math.floor(fokus / spalten) * 100;
      if (oben < root.scrollTop) root.scrollTop = oben;
      else if (oben + 100 > root.scrollTop + root.clientHeight) root.scrollTop = oben + 100 - root.clientHeight;
      zeichnen();
      document.getElementById(`${root.id}-bild-${fokus}`)?.focus({ preventScroll: true });
    } else return;
    ev.preventDefault(); ev.stopPropagation();
  });
  return { setze(b, a = null) { bilder = b; aktiv = a; fokus = Math.max(0, b.findIndex((e) => e.bild.id === a)); root.scrollTop = Math.floor(fokus / Math.max(1, Math.floor(root.clientWidth / 100))) * 100; zeichnen(); }, aktualisiere: zeichnen };
}
