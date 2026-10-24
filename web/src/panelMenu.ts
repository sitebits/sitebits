// Fenster-Menü: eigener Knopf #panel-menu neben der Werkzeugleiste — Liste aller Panels (an/aus)
// plus „Layout zurücksetzen". #toolbar bleibt dadurch frei von Fenster-Knöpfen. Folgt dem Knopf-Muster aus
// toolbar.ts (mousedown-Fokusfalle, Texte nur als textContent/Attribute).
import type { PanelHost } from './panels';

const menueText = (titel: string, key: string): string => (key ? `${titel} (${key.toUpperCase()})` : titel);

export interface PanelMenu {
  /** Kürzel nach einer Umbelegung neu beschriften: `f(panelId)` liefert das Kürzel oder `''`. */
  setKuerzel(f: (panelId: string) => string): void;
}

export function attachPanelMenu(root: HTMLElement, host: PanelHost): PanelMenu {
  const button = document.createElement('button');
  button.type = 'button';
  button.setAttribute('aria-haspopup', 'menu');
  button.setAttribute('aria-expanded', 'false');
  // Auf dem Handy nur das Symbol (Text bleibt für Hilfstechnik lesbar, panels.css), am Desktop Symbol und Text.
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  const pfad = document.createElementNS(ns, 'path');
  pfad.setAttribute('d', 'M4 5h16v14H4zM4 13h16M12 13v6');
  svg.append(pfad);
  const text = document.createElement('span');
  text.className = 'sv-knopf-text';
  text.textContent = 'Fenster';
  button.append(svg, text);
  button.addEventListener('mousedown', (e) => e.preventDefault());

  const menu = document.createElement('div');
  menu.setAttribute('role', 'menu');
  menu.hidden = true;

  const items = new Map<string, HTMLButtonElement>();
  for (const def of host.defs()) {
    const item = document.createElement('button');
    item.type = 'button';
    item.setAttribute('role', 'menuitemcheckbox');
    item.dataset.panel = def.id;
    item.setAttribute('aria-checked', String(host.isOpen(def.id)));
    item.textContent = menueText(def.title, def.key ?? '');
    item.addEventListener('mousedown', (e) => e.preventDefault());
    item.addEventListener('click', () => {
      host.toggle(def.id);
      // ARIA-Menü-Muster (APG „Menu Button“): eine Auswahl schließt das Menü, Fokus zurück zum Knopf —
      // wie ein Häkchen-Menüpunkt in einer Desktop-App, nicht wie eine Mehrfachauswahl-Liste.
      closeMenu();
      button.focus();
    });
    menu.append(item);
    items.set(def.id, item);
  }

  const sep = document.createElement('div');
  sep.setAttribute('role', 'separator');
  menu.append(sep);

  const resetItem = document.createElement('button');
  resetItem.type = 'button';
  resetItem.setAttribute('role', 'menuitem');
  resetItem.textContent = 'Layout zurücksetzen';
  resetItem.addEventListener('mousedown', (e) => e.preventDefault());
  resetItem.addEventListener('click', () => {
    host.reset();
    // reset() feuert onChange nur für Panels, deren offen/zu sich ändert;
    // die Häkchen hier zusätzlich von Hand nachführen deckt auch den unveränderten Rest ab (idempotent,
    // da host.onChange oben ohnehin auf dieselbe Weise aktualisiert).
    for (const [id, item] of items) item.setAttribute('aria-checked', String(host.isOpen(id)));
    closeMenu();
    button.focus();
  });
  menu.append(resetItem);

  const focusables = (): HTMLButtonElement[] => [...items.values(), resetItem];

  function closeMenu(): void {
    menu.hidden = true;
    button.setAttribute('aria-expanded', 'false');
    document.removeEventListener('keydown', onKey, true);
    document.removeEventListener('pointerdown', onOutside, true);
  }

  function openMenu(): void {
    menu.hidden = false;
    button.setAttribute('aria-expanded', 'true');
    document.addEventListener('keydown', onKey, true);
    document.addEventListener('pointerdown', onOutside, true);
    focusables()[0]?.focus();
  }

  function onKey(ev: KeyboardEvent): void {
    if (ev.key === 'Escape') {
      ev.stopPropagation();
      closeMenu();
      button.focus();
      return;
    }
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    const list = focusables();
    const i = list.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    ev.preventDefault();
    const next = ev.key === 'ArrowDown' ? list[(i + 1) % list.length] : list[(i - 1 + list.length) % list.length];
    next?.focus();
  }

  function onOutside(ev: PointerEvent): void {
    if (!menu.contains(ev.target as Node) && ev.target !== button) closeMenu();
  }

  button.addEventListener('click', () => {
    if (menu.hidden) openMenu();
    else closeMenu();
  });

  host.onChange((id, open) => {
    items.get(id)?.setAttribute('aria-checked', String(open));
  });

  root.replaceChildren(button, menu);
  return {
    setKuerzel: (f) => {
      for (const def of host.defs()) {
        const item = items.get(def.id);
        if (item) item.textContent = menueText(def.title, f(def.id));
      }
    },
  };
}
