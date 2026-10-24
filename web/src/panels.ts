// Panel-Host: rendert das Layout aus layoutModel.ts in Overlay-Leisten und schwebende
// Rahmen. Inhaltselemente (lil-gui, #measure-panel, #poi-panel, #minimap) werden nur umgehängt, nie neu gebaut.
// Ihr hidden-Attribut ist die Wahrheit über offen/zu: MutationObserver → setOpen; Schließen im Rahmen →
// content.hidden = true. Nach jeder Änderung: Layout speichern (layoutStore), Rahmen neu aufbauen (die Inhalte
// bleiben dieselben Knoten, nur die Hülle wird ersetzt).
import { istTouch } from './geraet';
import {
  activate, activeOf, applyDrop, clampFloat, clampLayout, defaultLayout, dropTargetAt, effectiveLayout, groupsOf,
  istMobil, resizeDock, setCollapsed, setOpen, tabsOf,
} from './layoutModel';
import type { Dock, DockSide, DropTarget, Layout, PanelDef } from './layoutModel';
import { clearLayout, loadLayout, saveLayout } from './layoutStore';
import type { KV } from './layoutStore';

export interface PanelSpec extends PanelDef { content: HTMLElement }
export interface PanelHost {
  readonly element: HTMLElement; // #panels
  layout(): Layout; // gespeichertes (nicht mobil umgerechnetes) Layout
  isOpen(id: string): boolean;
  open(id: string): void; // öffnet + aktiviert Reiter
  close(id: string): void;
  toggle(id: string): void;
  /** Inhalt vorübergehend außerhalb der Overlay-Rahmen anzeigen (geteilte Fotoansicht). */
  auslagern(id: string, ziel: HTMLElement | null, schwebend?: boolean): void;
  reset(): void; // Standard-Layout, Speicher gelöscht
  defs(): readonly PanelDef[];
  onChange(cb: (id: string, open: boolean) => void): void;
}

const DRAG_START_PX = 4;
const SPLITTER_STEP_PX = 16;
const DOCK_SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];
const SPLITTER_LABEL: Record<DockSide, string> = { left: 'Breite linke Leiste', right: 'Breite rechte Leiste', bottom: 'Höhe untere Leiste' };
/** Fester Standardplatz für „Schweben“ im Positionsmenü (nicht die zuletzt gemerkte Float-Größe des Panels). */
const MENU_FLOAT_RECT = { x: 200, y: 120, w: 320, h: 260 };

interface Wrap { content: HTMLElement; wrapper: HTMLDivElement }

interface DragState {
  id: string;
  sourceDock: Dock;
  sourceGroup: number;
  startX: number;
  startY: number;
  grabDx: number;
  grabDy: number;
  pointerId: number;
  started: boolean;
  ghost: HTMLDivElement | null;
  hoverGroup: { dock: DockSide; group: number } | null;
}

function headBtn(cls: string, glyph: string, label: string): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = cls;
  b.textContent = glyph;
  b.setAttribute('aria-label', label);
  b.title = label;
  // Fokusfalle wie toolbar.ts: ein Mausklick nimmt dem Canvas/Reiter den Fokus nicht.
  b.addEventListener('mousedown', (e) => e.preventDefault());
  return b;
}

function buildDockEl(side: DockSide): HTMLDivElement {
  const d = document.createElement('div');
  d.className = 'sv-dock';
  d.dataset.dock = side;
  return d;
}

export function createPanelHost(o: { root: HTMLElement; panels: readonly PanelSpec[]; projectId: string; storage: KV | null }): PanelHost {
  const { root, panels, projectId, storage } = o;
  const specs = new Map(panels.map((p) => [p.id, p] as const));
  const wraps = new Map<string, Wrap>();
  const changeListeners: ((id: string, open: boolean) => void)[] = [];
  let layout = clampLayout(loadLayout(storage, projectId, panels), innerWidth, innerHeight);
  const ausgelagert = new Map<string, HTMLElement>();
  let drag: DragState | null = null;
  // Nur für das mobile Blatt: effectiveLayout wählt bei mehreren offenen Gruppen die Position in
  // `order`, nicht die zuletzt angetippte — hier zusätzlich merken, welcher Reiter zuletzt aktiviert wurde.
  // Nicht Teil des gespeicherten Layouts (L6: das gespeicherte Layout bleibt im Mobil-Modus unverändert).
  let lastActive: string | null = null;

  root.replaceChildren();
  const docks: Record<DockSide, HTMLDivElement> = { left: buildDockEl('left'), right: buildDockEl('right'), bottom: buildDockEl('bottom') };
  const floatLayer = document.createElement('div');
  floatLayer.className = 'sv-float-layer';
  const dropHint = document.createElement('div');
  dropHint.className = 'sv-drop-hint';
  dropHint.hidden = true;
  // Geschlossene Panels stehen in keinem Rahmen — ohne dieses Depot verließe ihr content-Knoten den
  // Dokumentbaum (er hängt sonst nur noch im entfernten alten Rahmen), was #poi-panel & Co. für main.ts/E2E
  // unsichtbar macht (constraints.md: DOM-Verträge gelten auch geschlossen).
  const stash = document.createElement('div');
  stash.hidden = true;
  const moveMenu = document.createElement('div');
  moveMenu.className = 'sv-move-menu';
  moveMenu.hidden = true;
  root.append(docks.left, docks.right, docks.bottom, floatLayer, dropHint, stash, moveMenu);

  // Ein einziges Positionsmenü für alle Rahmen (statt eines je Rahmen): `.sv-frame` hat `overflow: hidden`
  // (panels.css) — im Rahmen selbst verankert würde es an dessen Kante abgeschnitten. Es lebt in `root` und
  // wird beim Öffnen an den anklickenden Knopf gesetzt.
  let moveMenuTarget: string | null = null;
  const moveMenuEntries: { label: string; target: (id: string) => DropTarget }[] = [
    { label: 'Links andocken', target: () => ({ kind: 'dock', dock: 'left' }) },
    { label: 'Rechts andocken', target: () => ({ kind: 'dock', dock: 'right' }) },
    { label: 'Unten andocken', target: () => ({ kind: 'dock', dock: 'bottom' }) },
    { label: 'Schweben', target: () => ({ kind: 'float', rect: clampFloat(MENU_FLOAT_RECT, innerWidth, innerHeight) }) },
  ];
  function closeMoveMenu(): void {
    moveMenu.hidden = true;
    moveMenuTarget = null;
    document.removeEventListener('keydown', onMoveMenuKey, true);
    document.removeEventListener('pointerdown', onMoveMenuOutside, true);
  }
  function onMoveMenuKey(ev: KeyboardEvent): void {
    if (ev.key === 'Escape') {
      ev.stopPropagation();
      closeMoveMenu();
      return;
    }
    // Positionsmenü per Tastatur bedienbar (wie panelMenu.ts' Fenster-Menü): ↑/↓ zwischen den Einträgen,
    // Enter/Space aktiviert den fokussierten (native Knopf-Aktivierung, kein eigener Handler nötig).
    if (ev.key !== 'ArrowDown' && ev.key !== 'ArrowUp') return;
    const items = [...moveMenu.querySelectorAll<HTMLButtonElement>('button')];
    const i = items.indexOf(document.activeElement as HTMLButtonElement);
    if (i < 0) return;
    ev.preventDefault();
    const next = ev.key === 'ArrowDown' ? items[(i + 1) % items.length] : items[(i - 1 + items.length) % items.length];
    next?.focus();
  }
  function onMoveMenuOutside(ev: PointerEvent): void {
    const t = ev.target;
    if (!(t instanceof Node)) return;
    if (!moveMenu.contains(t) && !(t instanceof HTMLElement && t.closest('.sv-move'))) closeMoveMenu();
  }
  function openMoveMenu(btn: HTMLButtonElement, id: string): void {
    if (moveMenuTarget === id && !moveMenu.hidden) {
      closeMoveMenu();
      return;
    }
    moveMenuTarget = id;
    const r = btn.getBoundingClientRect();
    moveMenu.style.left = `${r.left}px`;
    moveMenu.style.top = `${r.bottom + 4}px`;
    moveMenu.hidden = false;
    document.addEventListener('keydown', onMoveMenuKey, true);
    document.addEventListener('pointerdown', onMoveMenuOutside, true);
    // Erster Eintrag bekommt den Fokus (ARIA „Menu Button“-Muster, wie panelMenu.ts) — sonst wäre das Menü
    // nach dem Öffnen per Tastatur nur über wiederholtes Tab erreichbar.
    moveMenu.querySelector<HTMLButtonElement>('button')?.focus();
  }
  for (const entry of moveMenuEntries) {
    const b = document.createElement('button');
    b.type = 'button';
    b.textContent = entry.label;
    b.addEventListener('mousedown', (e) => e.preventDefault());
    b.addEventListener('click', () => {
      const id = moveMenuTarget;
      if (!id) return;
      layout = applyDrop(layout, id, entry.target(id));
      closeMoveMenu();
      persist();
      render();
    });
    moveMenu.append(b);
  }

  function persist(): void {
    saveLayout(storage, projectId, layout);
  }

  /** Handy-Layout (schmal, oder Touch quer mit wenig Höhe) — wie render() es bestimmt, auch vor dem ersten render(). */
  function handy(): boolean {
    return istMobil(innerWidth, innerHeight, istTouch());
  }

  function isMobile(): boolean {
    return root.dataset.mobile === 'true';
  }

  /** Öffnen/Schließen ist immer diese Abfolge, egal ob durch die API oder durch den MutationObserver ausgelöst. */
  function applyOpenChange(id: string, open: boolean): void {
    layout = setOpen(layout, id, open);
    // Handy: immer nur ein Panel im Blatt — die übrigen schließen (ausgelagerte bleiben, sie liegen neben dem Bild).
    const verdraengt = open && handy() ? layout.order.filter((x) => x !== id && layout.panels[x]?.open && !ausgelagert.has(x)) : [];
    for (const x of verdraengt) {
      layout = setOpen(layout, x, false);
      wraps.get(x)!.content.hidden = true;
    }
    if (open) {
      layout = activate(layout, id);
      lastActive = id;
    }
    persist();
    render();
    for (const x of verdraengt) for (const cb of changeListeners) cb(x, false);
    for (const cb of changeListeners) cb(id, open);
  }

  /** „Der Rahmen folgt als Geist“ — sieht wie ein echter .sv-frame/.sv-frame-head aus (Klassen aus panels.css),
      ist aber ein eigenes, klickdurchlässiges Element: den echten (u. U. mehrreitrigen) Quell-Rahmen selbst zu
      verschieben wäre falsch, sobald nur ein einzelner Reiter aus einer Gruppe herausgezogen wird. */
  function createGhost(title: string): HTMLDivElement {
    const g = document.createElement('div');
    g.className = 'sv-frame';
    g.style.position = 'fixed';
    g.style.left = '0';
    g.style.top = '0';
    g.style.zIndex = '1000';
    g.style.pointerEvents = 'none';
    g.style.width = 'max-content';
    g.style.opacity = '0.85';
    const head = document.createElement('div');
    head.className = 'sv-frame-head';
    head.style.padding = '0 10px';
    head.style.color = 'var(--sv-fg)';
    head.textContent = title;
    g.append(head);
    return g;
  }

  function moveGhost(g: HTMLDivElement, x: number, y: number): void {
    g.style.transform = `translate(${x + 12}px, ${y + 12}px)`;
  }

  function positionHint(r: { left: number; top: number; width: number; height: number }, target: string): void {
    dropHint.dataset.target = target;
    dropHint.style.left = `${r.left}px`;
    dropHint.style.top = `${r.top}px`;
    dropHint.style.width = `${r.width}px`;
    dropHint.style.height = `${r.height}px`;
    dropHint.hidden = false;
  }

  function showDropHint(target: DropTarget, hover: { dock: DockSide; group: number } | null): void {
    if (target.kind === 'dock') {
      const r = docks[target.dock].getBoundingClientRect();
      // Leere untere Leiste: `left: 50%` ohne Breite schrumpft auf 0px (links/rechts haben immer eine per JS
      // gesetzte Breite) — dann die tatsächliche CSS-Fläche (60% Breite, mittig, dockSize.bottom hoch) zeigen.
      if (target.dock === 'bottom' && r.width < 1) {
        const w = innerWidth * 0.6;
        const h = layout.dockSize.bottom;
        positionHint({ left: (innerWidth - w) / 2, top: innerHeight - 8 - h, width: w, height: h }, target.dock);
        return;
      }
      positionHint(r, target.dock);
      return;
    }
    if (target.kind === 'tab' && hover) {
      const frameEl = [...docks[hover.dock].querySelectorAll<HTMLElement>('.sv-frame')].find((f) => Number(f.dataset.group) === hover.group);
      if (frameEl) positionHint(frameEl.getBoundingClientRect(), 'tab');
      return;
    }
    if (target.kind === 'float') {
      dropHint.dataset.target = 'float';
      dropHint.style.left = `${target.rect.x}px`;
      dropHint.style.top = `${target.rect.y}px`;
      dropHint.style.width = `${target.rect.w}px`;
      dropHint.style.height = `${target.rect.h}px`;
      dropHint.hidden = false;
    }
  }

  function findHoverGroup(x: number, y: number, sourceDock: Dock, sourceGroup: number): { dock: DockSide; group: number } | null {
    for (const el of document.elementsFromPoint(x, y)) {
      const headEl = el.closest('.sv-frame-head');
      if (!headEl) continue;
      const frameEl = headEl.closest<HTMLElement>('.sv-frame');
      const fdock = frameEl?.dataset.dock;
      if (!frameEl || !fdock || fdock === 'float') continue;
      const fgroup = Number(frameEl.dataset.group);
      if (fdock === sourceDock && fgroup === sourceGroup) continue;
      return { dock: fdock as DockSide, group: fgroup };
    }
    return null;
  }

  function dragRect(id: string, x: number, y: number, grabDx: number, grabDy: number): { x: number; y: number; w: number; h: number } {
    const size = layout.panels[id]!.float;
    return { x: x - grabDx, y: y - grabDy, w: size.w, h: size.h };
  }

  // render() ersetzt bei jeder Änderung ALLE Rahmen (dockEl.replaceChildren())
  // — auch die gerade gezogene Kopfzeile. Waren move/up/cancel auf dem Kopfzeilen-Knoten selbst registriert
  // (mitsamt setPointerCapture darauf), riss dessen Entfernen die Erfassung implizit ab (Browser feuern dabei
  // kein pointercancel), die Listener liefen ins Leere, `drag` blieb für immer gesetzt und der Geist blieb
  // stehen. render() kann während einer Ziehgeste jederzeit von außen laufen (Resize-Listener; auch
  // host.open/close/toggle per Tastenkürzel während gehaltener Maustaste). Fix: move/up/cancel
  // hängen auf `window` (das render() nie entfernt) statt am Rahmen — die Geste überlebt jeden dazwischen
  // laufenden render() unbeeinflusst, kein „Geste abbrechen“ nötig. `pointerId`-Filter statt Capture wahrt die
  // Exklusivität gegenüber einem zweiten Zeiger (Multitouch). Siehe auch attachSplitterDrag/attachGripDrag
  // unten — derselbe Fix, dieselbe Begründung.
  function onHeadMove(ev: PointerEvent): void {
    if (!drag || ev.pointerId !== drag.pointerId) return;
    const dx = ev.clientX - drag.startX;
    const dy = ev.clientY - drag.startY;
    if (!drag.started) {
      if (Math.hypot(dx, dy) < DRAG_START_PX) return;
      drag.started = true;
      drag.ghost = createGhost(specs.get(drag.id)!.title);
      root.append(drag.ghost);
    }
    ev.preventDefault();
    moveGhost(drag.ghost!, ev.clientX, ev.clientY);
    const hover = findHoverGroup(ev.clientX, ev.clientY, drag.sourceDock, drag.sourceGroup);
    drag.hoverGroup = hover;
    const target = dropTargetAt({ x: ev.clientX, y: ev.clientY }, { w: innerWidth, h: innerHeight }, hover, dragRect(drag.id, ev.clientX, ev.clientY, drag.grabDx, drag.grabDy));
    showDropHint(target, hover);
  }

  function cleanupDragListeners(): void {
    window.removeEventListener('pointermove', onHeadMove);
    window.removeEventListener('pointerup', onHeadUp);
    window.removeEventListener('pointercancel', onHeadCancel);
    window.removeEventListener('keydown', onDragEscape, true);
    window.removeEventListener('blur', onDragBlur);
  }

  // Ohne setPointerCapture liefert der Browser kein pointerup/-cancel mehr, wenn die Maustaste
  // außerhalb des Fensters losgelassen wird (bzw. der Zeiger das Fenster verlässt und dort losgelassen wird)
  // — das Ereignis erreicht die Seite dann schlicht nicht. Ein window-blur (typischerweise die Folge, wenn
  // der Fokus so an eine andere App/Fenster geht) bricht die Geste stattdessen ab, statt sie hängen zu lassen.
  function onDragBlur(): void {
    if (!drag) return;
    cleanupDragListeners();
    endDrag();
  }

  function endDrag(): void {
    drag?.ghost?.remove();
    dropHint.hidden = true;
    drag = null;
  }

  function onHeadUp(ev: PointerEvent): void {
    if (!drag || ev.pointerId !== drag.pointerId) return;
    const d = drag;
    cleanupDragListeners();
    if (!d.started) {
      // Kein Ziehen: früher (mit setPointerCapture auf der Kopfzeile) leitete der Browser den click vom
      // Reiter weg um, weshalb hier statt dessen aktiviert wurde. Capture ist mit dem Wechsel auf
      // window-Listener oben entfallen — ein unbewegter Klick löst den nativen click meist wieder zuverlässig
      // am Reiter selbst aus (dessen eigener Listener ruft ebenfalls activateTab), diese Absicherung bleibt
      // aber als Fallback für den Fall, dass er es nicht tut (ohne Browser/DOM-Testumgebung
      // nicht verifizierbar). Beide Aufrufer treffen sich in activateTab, das selbst No-op ist, wenn `id`
      // schon der sichtbare Reiter ist — hier also unbedingt aufrufen, kein doppeltes
      // Prüfen/Filtern mehr nötig.
      endDrag();
      activateTab(d.id);
      return;
    }
    const target = dropTargetAt({ x: ev.clientX, y: ev.clientY }, { w: innerWidth, h: innerHeight }, d.hoverGroup, dragRect(d.id, ev.clientX, ev.clientY, d.grabDx, d.grabDy));
    layout = applyDrop(layout, d.id, target);
    endDrag();
    persist();
    render();
  }

  function onHeadCancel(ev: PointerEvent): void {
    if (!drag || ev.pointerId !== drag.pointerId) return;
    cleanupDragListeners();
    endDrag();
    // Layout wurde nie verändert (nur der Geist bewegt sich) — kein render() nötig.
  }

  function onDragEscape(ev: KeyboardEvent): void {
    if (!drag || ev.key !== 'Escape') return;
    ev.preventDefault();
    ev.stopPropagation();
    cleanupDragListeners();
    endDrag();
  }

  function attachHeadDrag(head: HTMLElement, dock: Dock, group: number, activeId: string, ids: readonly string[]): void {
    head.addEventListener('pointerdown', (ev) => {
      if (isMobile() || ev.button !== 0) return;
      const target = ev.target as HTMLElement;
      if (target.closest('.sv-collapse, .sv-move, .sv-close')) return;
      // Defensiv: eine nie zu Ende gekommene vorige Geste (sollte mit den window-Listenern oben nicht mehr
      // vorkommen, aber der Geist darf unter keinen Umständen die Geste überleben) vor dem Start aufräumen.
      if (drag) {
        cleanupDragListeners();
        endDrag();
      }
      const tabBtn = target.closest<HTMLButtonElement>('[role="tab"]');
      const id = tabBtn && ids.includes(tabBtn.dataset.panel ?? '') ? tabBtn.dataset.panel! : activeId;
      const frameEl = head.closest<HTMLElement>('.sv-frame')!;
      const r = frameEl.getBoundingClientRect();
      drag = {
        id, sourceDock: dock, sourceGroup: group, startX: ev.clientX, startY: ev.clientY,
        grabDx: ev.clientX - r.left, grabDy: ev.clientY - r.top, pointerId: ev.pointerId, started: false, ghost: null, hoverGroup: null,
      };
      window.addEventListener('pointermove', onHeadMove);
      window.addEventListener('pointerup', onHeadUp);
      window.addEventListener('pointercancel', onHeadCancel);
      window.addEventListener('keydown', onDragEscape, true);
      window.addEventListener('blur', onDragBlur);
    });
  }

  function attachSplitterDrag(handle: HTMLElement, side: DockSide): void {
    handle.addEventListener('pointerdown', (down) => {
      down.preventDefault();
      const startSize = layout.dockSize[side];
      const start = side === 'bottom' ? down.clientY : down.clientX;
      const sign = side === 'left' ? 1 : -1;
      const live = (cur: number): number => startSize + sign * (cur - start);
      // move/up/cancel auf `window` statt auf `handle` (kein setPointerCapture) — render() kann
      // während dieser Geste laufen (z. B. Resize) und baut den Splitter neu auf; window bleibt bestehen, die
      // Geste läuft ungestört weiter. `docks[side].querySelector` statt des geschlossenen `handle`, damit die
      // Live-Vorschau auch am ggf. neu aufgebauten Splitter landet, nicht am entfernten alten Knoten.
      const onMove = (mv: PointerEvent): void => {
        if (mv.pointerId !== down.pointerId) return;
        const cur = side === 'bottom' ? mv.clientY : mv.clientX;
        const size = Math.max(live(cur), 1);
        if (side === 'bottom') docks.bottom.style.height = `${size}px`;
        else docks[side].style.width = `${size}px`;
        docks[side].querySelector('.sv-splitter')?.setAttribute('aria-valuenow', String(Math.round(size)));
      };
      const cleanup = (): void => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onCancel);
        window.removeEventListener('blur', onBlur);
      };
      const onUp = (up: PointerEvent): void => {
        if (up.pointerId !== down.pointerId) return;
        cleanup();
        const cur = side === 'bottom' ? up.clientY : up.clientX;
        layout = resizeDock(layout, side, live(cur), innerWidth, innerHeight);
        persist();
        render();
      };
      const onCancel = (ev: PointerEvent): void => {
        if (ev.pointerId !== down.pointerId) return;
        cleanup();
        render(); // verwirft die nur visuelle Live-Vorschau, `layout` wurde nie verändert
      };
      // Ohne Capture kommt außerhalb des Fensters losgelassen kein pointerup/-cancel mehr an —
      // window-blur bricht die Geste ersatzweise ab (siehe onDragBlur oben, gleiche Begründung).
      const onBlur = (): void => {
        cleanup();
        render();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onCancel);
      window.addEventListener('blur', onBlur);
    });
  }

  function buildSplitter(side: DockSide, size: number): HTMLDivElement {
    const s = document.createElement('div');
    s.className = 'sv-splitter';
    s.setAttribute('role', 'separator');
    s.setAttribute('aria-orientation', side === 'bottom' ? 'horizontal' : 'vertical');
    s.setAttribute('aria-label', SPLITTER_LABEL[side]);
    s.setAttribute('aria-valuenow', String(size));
    s.tabIndex = 0;
    attachSplitterDrag(s, side);
    s.addEventListener('keydown', (ev) => {
      const delta =
        side === 'bottom'
          ? ev.key === 'ArrowUp' ? SPLITTER_STEP_PX : ev.key === 'ArrowDown' ? -SPLITTER_STEP_PX : 0
          : ev.key === 'ArrowRight' ? SPLITTER_STEP_PX : ev.key === 'ArrowLeft' ? -SPLITTER_STEP_PX : 0;
      if (delta === 0) return;
      ev.preventDefault();
      layout = resizeDock(layout, side, layout.dockSize[side] + delta, innerWidth, innerHeight);
      persist();
      // Kein render(): das baut den fokussierten Splitter selbst neu auf (Fokus fiele auf <body>, eine
      // zweite Pfeiltaste bliebe wirkungslos) — die Leiste direkt aktualisieren reicht hier.
      const size = layout.dockSize[side];
      if (side === 'bottom') docks.bottom.style.height = `${size}px`;
      else docks[side].style.width = `${size}px`;
      s.setAttribute('aria-valuenow', String(size));
    });
    return s;
  }

  /** Für die Live-Vorschau während attachGripDrag: der beim Gestenstart gegriffene Rahmen kann durch einen
      dazwischen laufenden render() ersetzt worden sein — frisch über den Panel-Reiter suchen statt eine
      Referenz auf einen ggf. schon entfernten Knoten zu halten. */
  function currentFloatFrame(id: string): HTMLElement | null {
    return [...floatLayer.querySelectorAll<HTMLElement>('.sv-frame')].find((f) => f.querySelector<HTMLElement>('[role="tab"]')?.dataset.panel === id) ?? null;
  }

  function attachGripDrag(grip: HTMLElement, id: string): void {
    grip.addEventListener('pointerdown', (down) => {
      down.preventDefault();
      const start = layout.panels[id]!.float;
      // move/up/cancel auf `window` statt auf `grip` (kein setPointerCapture) — dieselbe
      // Begründung wie bei attachHeadDrag/attachSplitterDrag oben.
      const onMove = (mv: PointerEvent): void => {
        if (mv.pointerId !== down.pointerId) return;
        const w = Math.max(start.w + (mv.clientX - down.clientX), 1);
        const h = Math.max(start.h + (mv.clientY - down.clientY), 1);
        const frame = currentFloatFrame(id);
        if (frame) {
          frame.style.width = `${w}px`;
          frame.style.height = `${h}px`;
        }
      };
      const cleanup = (): void => {
        window.removeEventListener('pointermove', onMove);
        window.removeEventListener('pointerup', onUp);
        window.removeEventListener('pointercancel', onCancel);
        window.removeEventListener('blur', onBlur);
      };
      const onUp = (up: PointerEvent): void => {
        if (up.pointerId !== down.pointerId) return;
        cleanup();
        const w = start.w + (up.clientX - down.clientX);
        const h = start.h + (up.clientY - down.clientY);
        layout = applyDrop(layout, id, { kind: 'float', rect: clampFloat({ ...start, w, h }, innerWidth, innerHeight) });
        persist();
        render();
      };
      const onCancel = (ev: PointerEvent): void => {
        if (ev.pointerId !== down.pointerId) return;
        cleanup();
        render();
      };
      // Siehe attachSplitterDrag — ohne Capture bräuchte ein außerhalb des Fensters loslassen
      // sonst kein Ende.
      const onBlur = (): void => {
        cleanup();
        render();
      };
      window.addEventListener('pointermove', onMove);
      window.addEventListener('pointerup', onUp);
      window.addEventListener('pointercancel', onCancel);
      window.addEventListener('blur', onBlur);
    });
  }

  function attachTabsRoving(tablist: HTMLElement, tabs: readonly HTMLButtonElement[]): void {
    const focusTab = (b: HTMLButtonElement): void => {
      for (const x of tabs) x.tabIndex = x === b ? 0 : -1;
      b.focus();
    };
    tablist.addEventListener('focusin', (ev) => {
      if (ev.target instanceof HTMLButtonElement) for (const x of tabs) x.tabIndex = x === ev.target ? 0 : -1;
    });
    tablist.addEventListener('keydown', (ev) => {
      const i = tabs.indexOf(document.activeElement as HTMLButtonElement);
      if (i < 0) return;
      const next =
        ev.key === 'ArrowRight' ? tabs[(i + 1) % tabs.length]
        : ev.key === 'ArrowLeft' ? tabs[(i - 1 + tabs.length) % tabs.length]
        : ev.key === 'Home' ? tabs[0]
        : ev.key === 'End' ? tabs.at(-1)
        : undefined;
      if (!next) return;
      ev.preventDefault();
      focusTab(next);
    });
  }

  /** Ist `id` gerade der sichtbare Reiter seiner Gruppe? Im Mobil-Modus gilt dieselbe lastActive-Regel wie in
      render() (effectiveLayout wählt sonst nach order-Position statt nach der zuletzt angetippten). Geteilt
      von activateTab (Idempotenz) und toggle() (offen+aktiv → schließen, sonst → aktivieren). */
  function isDisplayedActive(id: string): boolean {
    const p = layout.panels[id];
    if (!p) return false;
    if (isMobile()) {
      const basis = structuredClone(layout);
      for (const id of ausgelagert.keys()) if (basis.panels[id]) basis.panels[id]!.open = false;
      const eff = effectiveLayout(basis, innerWidth, innerHeight, istTouch());
      const ids = tabsOf(eff, 'bottom', 0);
      if (!ids.includes(id)) return false;
      return (lastActive && ids.includes(lastActive) ? lastActive : activeOf(eff, 'bottom', 0)) === id;
    }
    return p.dock === 'float' || activeOf(layout, p.dock, p.group) === id;
  }

  // Ohne setPointerCapture landet ein Mausklick auf einem inaktiven Reiter
  // sowohl im Nicht-Ziehen-Zweig von onHeadUp (der Fallback dort) als auch — nach dessen render() — im
  // nativen click des frisch aufgebauten Reiter-Knopfs; beide riefen bisher activateTab auf und lösten je ein
  // eigenes persist()+render() aus. activateTab selbst ist jetzt die Wache: ist `id` schon der sichtbare
  // Reiter, ist es ein No-op — robust unabhängig davon, welcher der beiden Aufrufer zuerst dran ist.
  function activateTab(id: string): void {
    if (isDisplayedActive(id)) return;
    layout = activate(layout, id);
    lastActive = id;
    persist();
    render();
  }

  function buildFrame(dock: Dock, group: number, ids: readonly string[], active: string, collapsed: boolean): HTMLElement {
    const frame = document.createElement('section');
    frame.className = 'sv-frame';
    frame.dataset.dock = dock;
    frame.dataset.group = String(group);
    frame.dataset.collapsed = String(collapsed);
    frame.setAttribute('role', 'region');
    frame.setAttribute('aria-label', specs.get(active)!.title);

    const head = document.createElement('div');
    head.className = 'sv-frame-head';

    const tablist = document.createElement('div');
    tablist.setAttribute('role', 'tablist');
    const tabBtns: HTMLButtonElement[] = [];
    for (const id of ids) {
      const b = document.createElement('button');
      b.type = 'button';
      b.setAttribute('role', 'tab');
      b.dataset.panel = id;
      const selected = id === active;
      b.setAttribute('aria-selected', String(selected));
      b.tabIndex = selected ? 0 : -1;
      b.textContent = specs.get(id)!.title;
      b.addEventListener('mousedown', (e) => e.preventDefault());
      b.addEventListener('click', () => activateTab(id));
      tablist.append(b);
      tabBtns.push(b);
    }
    attachTabsRoving(tablist, tabBtns);

    const title = specs.get(active)!.title;
    const collapseBtn = headBtn('sv-collapse', collapsed ? '▸' : '▾', `${title} ${collapsed ? 'ausklappen' : 'einklappen'}`);
    collapseBtn.setAttribute('aria-expanded', String(!collapsed));
    collapseBtn.addEventListener('click', () => {
      layout = setCollapsed(layout, active, !collapsed);
      persist();
      render();
    });

    const moveBtn = headBtn('sv-move', '⠿', `${title} verschieben`);
    moveBtn.setAttribute('aria-haspopup', 'menu');
    moveBtn.addEventListener('click', () => openMoveMenu(moveBtn, active));
    const closeBtn = headBtn('sv-close', '×', `${title} schließen`);
    closeBtn.addEventListener('click', () => {
      // Beobachter-Pfad: so feuern bestehende Listener (z. B. toolbar.setListOpen) nur über onChange.
      wraps.get(active)!.content.hidden = true;
    });

    head.append(tablist, collapseBtn, moveBtn, closeBtn);
    attachHeadDrag(head, dock, group, active, ids);
    frame.append(head);

    const body = document.createElement('div');
    body.className = 'sv-frame-body';
    for (const id of ids) {
      const w = wraps.get(id)!.wrapper;
      w.hidden = id !== active;
      body.append(w);
    }
    frame.append(body);
    frame.style.flex = '0 1 auto';

    if (dock === 'float') {
      const grip = document.createElement('div');
      grip.className = 'sv-grip';
      attachGripDrag(grip, active);
      frame.append(grip);
    }
    return frame;
  }

  /** dockEl.replaceChildren() entfernt den alten Rahmen aus dem Dokument, bevor der neue eingehängt wird —
      jeder dort fokussierte Knoten verliert dabei den Fokus (Entfernen eines fokussierten Vorfahren schiebt
      ihn auf <body>), auch wenn er anschließend wieder eingehängt wird. Ohne Gegenmaßnahme würde z. B. das
      Öffnen der Messliste (Observer → render()) den Fokus aus lil-gui reißen (Vertrag
      `document.activeElement.closest('.lil-gui')`, constraints.md). Inhaltsknoten (lil-gui, #poi-panel, …)
      werden nie neu erzeugt — dieselbe Referenz nach dem Aufbau erneut fokussieren reicht. Eigene
      Steuerelemente (Reiter, die drei Kopf-Knöpfe, Splitter) werden pro render() frisch erzeugt — dafür die
      Entsprechung im neuen Rahmen anhand ihrer Attribute wiederfinden. */
  function captureFocus(): (() => void) | null {
    const el = document.activeElement;
    if (!(el instanceof HTMLElement) || !root.contains(el)) return null;
    if ([...wraps.values()].some((w) => w.wrapper.contains(el))) return () => el.focus({ preventScroll: true });
    const tab = el.closest<HTMLElement>('[role="tab"]');
    if (tab?.dataset.panel) {
      const panel = tab.dataset.panel;
      return () => root.querySelector<HTMLElement>(`[role="tab"][data-panel="${panel}"]`)?.focus({ preventScroll: true });
    }
    const ctrlCls = (['sv-collapse', 'sv-move', 'sv-close'] as const).find((c) => el.classList.contains(c));
    const frameEl = el.closest<HTMLElement>('.sv-frame');
    if (ctrlCls && frameEl) {
      const panel = frameEl.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.dataset.panel;
      if (panel) {
        return () => {
          const t = root.querySelector<HTMLElement>(`[role="tab"][data-panel="${panel}"]`);
          t?.closest<HTMLElement>('.sv-frame')?.querySelector<HTMLElement>(`.${ctrlCls}`)?.focus({ preventScroll: true });
        };
      }
    }
    if (el.classList.contains('sv-splitter')) {
      const side = el.closest<HTMLElement>('.sv-dock')?.dataset.dock as DockSide | undefined;
      if (side) return () => docks[side].querySelector<HTMLElement>('.sv-splitter')?.focus({ preventScroll: true });
    }
    return null;
  }

  /** .sv-frame-body (overflow: auto) wird bei jedem render() komplett neu aufgebaut — ohne Gegenmaßnahme
      springt eine lange Messungs-/POI-Liste beim nächsten Öffnen/Schließen irgendeines Panels an den Anfang
      zurück, auch wenn nur ein ganz anderes Panel betroffen ist. Merkt sich die Bildlaufposition je aktuell
      angezeigtem Panel (Schlüssel: die Panel-ID des sichtbaren Reiters, dieselbe Logik wie captureFocus()). */
  function captureScrollTops(): Map<string, number> {
    const out = new Map<string, number>();
    for (const frame of root.querySelectorAll<HTMLElement>('.sv-frame')) {
      const id = frame.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.dataset.panel;
      const body = frame.querySelector<HTMLElement>('.sv-frame-body');
      if (id && body) out.set(id, body.scrollTop);
    }
    return out;
  }

  /** Muss NACH dem Wiedereinhängen der neuen Rahmen laufen (dockEl.replaceChildren()/floatLayer.replaceChildren()):
      scrollTop an einem noch nicht im Dokument hängenden Element wird auf 0 geklemmt (scrollHeight ist dort 0). */
  function restoreScrollTops(saved: Map<string, number>): void {
    for (const frame of root.querySelectorAll<HTMLElement>('.sv-frame')) {
      const id = frame.querySelector<HTMLElement>('[role="tab"][aria-selected="true"]')?.dataset.panel;
      const top = id ? saved.get(id) : undefined;
      const body = frame.querySelector<HTMLElement>('.sv-frame-body');
      if (body && top !== undefined) body.scrollTop = top;
    }
  }

  function render(): void {
    closeMoveMenu(); // Ziel könnte ein gleich verschwindender Knopf sein — nie über einen render() hinweg offen lassen
    const restoreFocus = captureFocus();
    const scrollTops = captureScrollTops();
    const basis = structuredClone(layout);
    for (const id of ausgelagert.keys()) if (basis.panels[id]) basis.panels[id]!.open = false;
    const eff = effectiveLayout(basis, innerWidth, innerHeight, istTouch());
    const mobile = istMobil(innerWidth, innerHeight, istTouch());
    root.dataset.mobile = String(mobile);
    // Quer: das Blatt liegt rechts statt unten (panels.css), sonst bliebe vom Bild kaum etwas.
    root.dataset.quer = String(mobile && innerWidth > innerHeight);
    // Für Leisten außerhalb des Panel-Hosts (toolbar.css, bedienung.css): Handy hoch bzw. quer.
    document.body.classList.toggle('sv-handy', mobile);
    document.body.classList.toggle('sv-handy-quer', mobile && innerWidth > innerHeight);
    for (const side of DOCK_SIDES) {
      const dockEl = docks[side];
      dockEl.replaceChildren();
      if (side === 'bottom') dockEl.style.height = `${eff.dockSize[side]}px`;
      else dockEl.style.width = `${eff.dockSize[side]}px`;
      const groups = groupsOf(eff, side);
      for (const g of groups) {
        const ids = tabsOf(eff, side, g);
        // effectiveLayout wählt bei mehreren gemergten Gruppen (Mobil) die order-Position, nicht den zuletzt
        // angetippten Reiter (siehe lastActive oben) — dessen Wert hat hier Vorrang, wenn er noch offen ist.
        const active = mobile && lastActive && ids.includes(lastActive) ? lastActive : activeOf(eff, side, g)!;
        const collapsed = eff.panels[active]!.collapsed;
        dockEl.append(buildFrame(side, g, ids, active, collapsed));
      }
      if (groups.length > 0 && !mobile) dockEl.append(buildSplitter(side, eff.dockSize[side]));
    }
    floatLayer.replaceChildren();
    if (!mobile) {
      for (const [id, st] of Object.entries(eff.panels)) {
        if (!st.open || st.dock !== 'float') continue;
        const frame = buildFrame('float', 0, [id], id, st.collapsed);
        frame.style.left = `${st.float.x}px`;
        frame.style.top = `${st.float.y}px`;
        frame.style.width = `${st.float.w}px`;
        // Eingeklappt: Höhe weglassen, sonst bliebe unter der 28px-Kopfzeile eine leere Box stehen
        // (Rumpf/Griff sind über data-collapsed ausgeblendet, die Rahmenhöhe würde sie trotzdem freihalten).
        if (!st.collapsed) frame.style.height = `${st.float.h}px`;
        floatLayer.append(frame);
      }
    }
    // Geschlossene Panels stecken sonst nirgends — ihr content-Knoten bliebe außerhalb des Dokumentbaums.
    // Bezug ist `root`, nicht `document` (isConnected wäre falsch, solange root selbst noch nicht eingehängt ist).
    for (const [id, w] of wraps) {
      const ziel = ausgelagert.get(id);
      if (ziel && layout.panels[id]?.open) {
        w.wrapper.hidden = false;
        ziel.append(w.wrapper);
      } else if (!root.contains(w.wrapper)) stash.append(w.wrapper);
    }
    restoreScrollTops(scrollTops);
    restoreFocus?.();
  }

  // Handy: Start mit freiem Modell — kein Panel offen (nur für diese Sitzung, gespeichert wird erst bei einer Änderung).
  if (handy()) for (const p of panels) layout = setOpen(layout, p.id, false);

  for (const p of panels) {
    const wrapper = document.createElement('div');
    wrapper.className = 'sv-panel-content';
    wrapper.dataset.panel = p.id;
    wrapper.setAttribute('role', 'tabpanel');
    wrapper.append(p.content);
    wraps.set(p.id, { content: p.content, wrapper });
    p.content.hidden = !layout.panels[p.id]!.open; // gespeichertes Layout gewinnt beim Start
    const obs = new MutationObserver(() => {
      const isOpen = !p.content.hidden;
      if (isOpen === layout.panels[p.id]!.open) return; // kein Abweichen — z. B. unsere eigene open()/close()
      applyOpenChange(p.id, isOpen);
    });
    obs.observe(p.content, { attributes: true, attributeFilter: ['hidden'] });
  }

  function open(id: string): void {
    const w = wraps.get(id);
    if (!w || !layout.panels[id]) return;
    w.content.hidden = false;
    applyOpenChange(id, true);
  }

  function close(id: string): void {
    const w = wraps.get(id);
    if (!w || !layout.panels[id]) return;
    w.content.hidden = true;
    applyOpenChange(id, false);
  }

  function toggle(id: string): void {
    const p = layout.panels[id];
    if (!p) return;
    if (!p.open) {
      open(id);
      return;
    }
    if (ausgelagert.has(id) || isDisplayedActive(id)) {
      close(id);
      return;
    }
    activateTab(id);
  }

  function reset(): void {
    const before = layout;
    clearLayout(storage, projectId);
    layout = defaultLayout(panels);
    // Handy: wie beim Start freies Modell statt dreier Panels im Blatt.
    if (handy()) for (const p of panels) layout = setOpen(layout, p.id, false);
    lastActive = null;
    for (const p of panels) wraps.get(p.id)!.content.hidden = !layout.panels[p.id]!.open;
    render();
    persist(); // damit das Zurücksetzen einen Reload überlebt
    // Kein applyOpenChange (das würde erneut layout/rendern) — nur Beobachter benachrichtigen, deren
    // offen/zu sich durch den Reset tatsächlich geändert hat (z. B. toolbar.setListOpen in main.ts).
    for (const p of panels) {
      const was = before.panels[p.id]!.open;
      const now = layout.panels[p.id]!.open;
      if (was !== now) for (const cb of changeListeners) cb(p.id, now);
    }
  }

  window.addEventListener('resize', () => {
    const next = clampLayout(layout, innerWidth, innerHeight);
    const changed = JSON.stringify(next) !== JSON.stringify(layout);
    layout = next;
    render();
    if (changed) persist();
  });

  render();

  return {
    auslagern(id, ziel, schwebend = false) {
      if (ziel) ausgelagert.set(id, ziel);
      else ausgelagert.delete(id);
      if (schwebend && layout.panels[id]) {
        layout = applyDrop(layout, id, { kind: 'float', rect: clampFloat(layout.panels[id]!.float, innerWidth, innerHeight) });
        persist();
      }
      render();
    },
    element: root,
    layout: () => layout,
    isOpen: (id) => layout.panels[id]?.open ?? false,
    open,
    close,
    toggle,
    reset,
    defs: () => panels,
    onChange: (cb) => {
      changeListeners.push(cb);
    },
  };
}
