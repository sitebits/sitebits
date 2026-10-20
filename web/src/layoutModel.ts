// Layout-Modell des Panel-Systems: rein, ohne DOM, unveränderlich. Panels liegen in
// einer Leiste (links/rechts/unten, mehrere Gruppen übereinander; offene Panels derselben Gruppe = Reiter) oder
// schweben frei. Gespeicherte Layouts sind fremde Daten (localStorage): mergeLayout prüft jedes Feld.
export type DockSide = 'left' | 'right' | 'bottom';
export type Dock = DockSide | 'float';
export interface FloatRect { x: number; y: number; w: number; h: number }
export interface PanelState { dock: Dock; group: number; open: boolean; collapsed: boolean; float: FloatRect }
export interface Layout {
  version: typeof LAYOUT_VERSION;
  /** Reihenfolge der Reiter/Stapel. */
  order: string[];
  panels: Record<string, PanelState>;
  /** Aktiver Reiter je groupKey. */
  activeTab: Record<string, string>;
  dockSize: Record<DockSide, number>;
}
export interface PanelDef { id: string; title: string; /** Kürzel (Anzeige im Fenster-Menü). */ key?: string; defaults: PanelState }
export type DropTarget =
  | { kind: 'float'; rect: FloatRect }
  | { kind: 'tab'; dock: DockSide; group: number }
  | { kind: 'dock'; dock: DockSide };

export const LAYOUT_VERSION = 1;
/** Höhe der Titelleiste — muss beim Klemmen sichtbar bleiben. */
export const TITLE_H = 28;
export const MIN_W = 200;
export const MIN_H = 120;
export const DOCK_MIN = 180;
export const DOCK_MAX_FRACTION = 0.6;
/** Abstand zum Fensterrand, ab dem ein gezogenes Panel andockt. */
export const EDGE_PX = 48;
export const MOBILE_MAX_W = 700;
/** Handy quer: breiter als MOBILE_MAX_W, aber so niedrig, dass schwebende Panels das Bild zudecken (17.10.). */
export const MOBILE_QUER_MAX_H = 500;

/** Mobiles Layout: schmal, oder ein Touch-Gerät im Querformat mit wenig Höhe. */
export function istMobil(vw: number, vh = Infinity, touch = false): boolean {
  return vw <= MOBILE_MAX_W || (touch && vh <= MOBILE_QUER_MAX_H);
}
export const DEFAULT_DOCK_SIZE: Record<DockSide, number> = { left: 300, right: 320, bottom: 240 };
const SIDES: readonly DockSide[] = ['left', 'right', 'bottom'];
const MAX_GROUP = 99;

export const groupKey = (dock: Dock, group: number): string => `${dock}:${group}`;
const clone = (l: Layout): Layout => structuredClone(l);
const clamp = (v: number, lo: number, hi: number): number => Math.min(Math.max(v, lo), hi);
const isObj = (x: unknown): x is Record<string, unknown> => typeof x === 'object' && x !== null && !Array.isArray(x);
const finite = (x: unknown): x is number => typeof x === 'number' && Number.isFinite(x);

function validState(x: unknown): x is PanelState {
  if (!isObj(x) || !isObj(x.float)) return false;
  const f = x.float;
  return (
    (x.dock === 'float' || SIDES.includes(x.dock as DockSide)) &&
    Number.isInteger(x.group) && (x.group as number) >= 0 && (x.group as number) <= MAX_GROUP &&
    typeof x.open === 'boolean' && typeof x.collapsed === 'boolean' &&
    finite(f.x) && finite(f.y) && finite(f.w) && finite(f.h)
  );
}

export function tabsOf(l: Layout, dock: DockSide, group: number): string[] {
  return l.order.filter((id) => {
    const p = l.panels[id];
    return p !== undefined && p.open && p.dock === dock && p.group === group;
  });
}

export function groupsOf(l: Layout, dock: DockSide): number[] {
  const gs = new Set<number>();
  for (const id of l.order) {
    const p = l.panels[id];
    if (p && p.open && p.dock === dock) gs.add(p.group);
  }
  return [...gs].sort((a, b) => a - b);
}

export function activeOf(l: Layout, dock: DockSide, group: number): string | null {
  const tabs = tabsOf(l, dock, group);
  const a = l.activeTab[groupKey(dock, group)];
  return a !== undefined && tabs.includes(a) ? a : (tabs[0] ?? null);
}

/** Verwaiste Einträge in activeTab entfernen (Panel geschlossen, verschoben, unbekannt). */
function normalizeTabs(l: Layout): Layout {
  for (const [k, id] of Object.entries(l.activeTab)) {
    const p = l.panels[id];
    if (!p || !p.open || p.dock === 'float' || groupKey(p.dock, p.group) !== k) delete l.activeTab[k];
  }
  return l;
}

export function defaultLayout(defs: readonly PanelDef[]): Layout {
  const panels: Record<string, PanelState> = {};
  for (const d of defs) panels[d.id] = structuredClone(d.defaults);
  return { version: LAYOUT_VERSION, order: defs.map((d) => d.id), panels, activeTab: {}, dockSize: { ...DEFAULT_DOCK_SIZE } };
}

export function mergeLayout(saved: unknown, defs: readonly PanelDef[]): Layout {
  const l = defaultLayout(defs);
  if (!isObj(saved) || saved.version !== LAYOUT_VERSION || !isObj(saved.panels)) return l;
  const known = new Set(defs.map((d) => d.id));
  for (const d of defs) {
    const sp = saved.panels[d.id];
    if (validState(sp)) l.panels[d.id] = structuredClone(sp);
  }
  const savedOrder = Array.isArray(saved.order) ? saved.order.filter((x): x is string => typeof x === 'string' && known.has(x)) : [];
  const uniq = [...new Set(savedOrder)];
  l.order = [...uniq, ...defs.map((d) => d.id).filter((id) => !uniq.includes(id))];
  if (isObj(saved.activeTab)) for (const [k, v] of Object.entries(saved.activeTab)) if (typeof v === 'string' && known.has(v)) l.activeTab[k] = v;
  if (isObj(saved.dockSize)) for (const s of SIDES) { const v = saved.dockSize[s]; if (finite(v)) l.dockSize[s] = v; }
  return normalizeTabs(l);
}

function update(l: Layout, id: string, f: (p: PanelState, n: Layout) => void): Layout {
  if (!l.panels[id]) return l;
  const n = clone(l);
  f(n.panels[id]!, n);
  return normalizeTabs(n);
}

export const setOpen = (l: Layout, id: string, open: boolean): Layout =>
  update(l, id, (p, n) => {
    p.open = open;
    if (open && p.dock !== 'float') n.activeTab[groupKey(p.dock, p.group)] = id;
  });

export const activate = (l: Layout, id: string): Layout =>
  update(l, id, (p, n) => {
    if (p.dock !== 'float') n.activeTab[groupKey(p.dock, p.group)] = id;
  });

export const setCollapsed = (l: Layout, id: string, collapsed: boolean): Layout => update(l, id, (p) => void (p.collapsed = collapsed));

export function clampFloat(r: FloatRect, vw: number, vh: number): FloatRect {
  const w = clamp(r.w, MIN_W, Math.max(vw, MIN_W));
  const h = clamp(r.h, MIN_H, Math.max(vh, MIN_H));
  return { x: clamp(r.x, 0, Math.max(vw - w, 0)), y: clamp(r.y, 0, Math.max(vh - TITLE_H, 0)), w: Math.min(w, vw), h: Math.min(h, vh) };
}

const dockLimit = (dock: DockSide, vw: number, vh: number): number => Math.round((dock === 'bottom' ? vh : vw) * DOCK_MAX_FRACTION);

export function resizeDock(l: Layout, dock: DockSide, size: number, vw: number, vh: number): Layout {
  const n = clone(l);
  n.dockSize[dock] = clamp(Math.round(size), DOCK_MIN, Math.max(dockLimit(dock, vw, vh), DOCK_MIN));
  return n;
}

export function clampLayout(l: Layout, vw: number, vh: number): Layout {
  let n = clone(l);
  for (const p of Object.values(n.panels)) p.float = clampFloat(p.float, vw, vh);
  for (const s of SIDES) n = resizeDock(n, s, n.dockSize[s], vw, vh);
  return n;
}

export function dropTargetAt(
  p: { x: number; y: number },
  view: { w: number; h: number },
  hoverGroup: { dock: DockSide; group: number } | null,
  rect: FloatRect,
): DropTarget {
  if (hoverGroup) return { kind: 'tab', ...hoverGroup };
  if (p.x <= EDGE_PX) return { kind: 'dock', dock: 'left' };
  if (p.x >= view.w - EDGE_PX) return { kind: 'dock', dock: 'right' };
  if (p.y >= view.h - EDGE_PX) return { kind: 'dock', dock: 'bottom' };
  return { kind: 'float', rect: clampFloat(rect, view.w, view.h) };
}

export function applyDrop(l: Layout, id: string, t: DropTarget): Layout {
  return update(l, id, (p, n) => {
    p.open = true;
    if (t.kind === 'float') {
      p.dock = 'float';
      p.group = 0;
      p.float = { ...t.rect };
      return;
    }
    if (t.kind === 'tab') {
      p.dock = t.dock;
      p.group = t.group;
    } else {
      p.dock = 'float'; // sich selbst beim Zählen der Gruppen nicht mitnehmen
      const gs = groupsOf(n, t.dock);
      p.dock = t.dock;
      p.group = Math.min(gs.length ? gs.at(-1)! + 1 : 0, MAX_GROUP);
    }
    n.activeTab[groupKey(p.dock, p.group)] = id;
  });
}

export function effectiveLayout(l: Layout, vw: number, vh = Infinity, touch = false): Layout {
  if (!istMobil(vw, vh, touch)) return l;
  const n = clone(l);
  // Aktiv bleibt der erste bisher aktive Reiter (Reihenfolge wie order), sonst das erste offene Panel.
  const wasActive = n.order.find((id) => Object.values(l.activeTab).includes(id) && l.panels[id]?.open);
  for (const p of Object.values(n.panels)) {
    p.dock = 'bottom';
    p.group = 0;
  }
  n.activeTab = wasActive ? { [groupKey('bottom', 0)]: wasActive } : {};
  return normalizeTabs(n);
}
