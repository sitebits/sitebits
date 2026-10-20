import { describe, expect, it } from 'vitest';
import {
  DOCK_MIN, EDGE_PX, MIN_H, MIN_W, TITLE_H, activate, activeOf, applyDrop, clampFloat, clampLayout, defaultLayout,
  dropTargetAt, effectiveLayout, istMobil, groupsOf, mergeLayout, resizeDock, setCollapsed, setOpen, tabsOf,
} from '../src/layoutModel';
import type { PanelDef, PanelState } from '../src/layoutModel';

const st = (dock: PanelState['dock'], group: number, open: boolean): PanelState => ({
  dock, group, open, collapsed: false, float: { x: 100, y: 100, w: 320, h: 240 },
});
const DEFS: PanelDef[] = [
  { id: 'ansicht', title: 'Ansicht', key: 'i', defaults: st('left', 0, true) },
  { id: 'messungen', title: 'Messungen', key: 'm', defaults: st('right', 0, false) },
  { id: 'pois', title: 'POIs', key: 'o', defaults: st('right', 0, false) },
  { id: 'karte', title: 'Minikarte', key: 'k', defaults: st('right', 1, true) },
];

describe('defaultLayout / Reiter', () => {
  it('legt jedes Panel mit seinen Vorgaben an, Reihenfolge wie die Definitionen', () => {
    const l = defaultLayout(DEFS);
    expect(l.version).toBe(1);
    expect(l.order).toEqual(['ansicht', 'messungen', 'pois', 'karte']);
    expect(l.panels.karte).toEqual(st('right', 1, true));
  });
  it('Reiter = offene Panels derselben Leiste und Gruppe, geschlossene zählen nicht', () => {
    let l = defaultLayout(DEFS);
    expect(tabsOf(l, 'right', 0)).toEqual([]);
    expect(groupsOf(l, 'right')).toEqual([1]);
    l = setOpen(setOpen(l, 'messungen', true), 'pois', true);
    expect(tabsOf(l, 'right', 0)).toEqual(['messungen', 'pois']);
    expect(groupsOf(l, 'right')).toEqual([0, 1]);
    expect(activeOf(l, 'right', 0)).toBe('pois'); // zuletzt geöffnet = aktiv
    expect(activeOf(activate(l, 'messungen'), 'right', 0)).toBe('messungen');
  });
  it('schließt man den aktiven Reiter, wird der erste verbleibende aktiv', () => {
    const l = setOpen(setOpen(setOpen(defaultLayout(DEFS), 'messungen', true), 'pois', true), 'pois', false);
    expect(activeOf(l, 'right', 0)).toBe('messungen');
  });
  it('verändert die Eingabe nicht', () => {
    const l = defaultLayout(DEFS);
    const before = JSON.stringify(l);
    setOpen(l, 'messungen', true);
    setCollapsed(l, 'karte', true);
    applyDrop(l, 'karte', { kind: 'dock', dock: 'left' });
    expect(JSON.stringify(l)).toBe(before);
  });
});

describe('mergeLayout', () => {
  it('Unsinn, fremde Version oder kein Objekt → Standard', () => {
    for (const x of [null, 42, 'x', [], { version: 2, panels: {} }, { version: 1 }]) expect(mergeLayout(x, DEFS)).toEqual(defaultLayout(DEFS));
  });
  it('übernimmt gültige Panel-Zustände, ungültige Einträge fallen einzeln auf den Standard', () => {
    const saved = {
      version: 1,
      order: ['karte', 'ansicht'],
      panels: {
        karte: { dock: 'float', group: 0, open: true, collapsed: true, float: { x: 10, y: 20, w: 300, h: 200 } },
        ansicht: { dock: 'oben', group: 0, open: true, collapsed: false, float: { x: 0, y: 0, w: 1, h: 1 } },
        messungen: { dock: 'left', group: 0.5, open: true, collapsed: false, float: { x: 0, y: 0, w: 300, h: 200 } },
      },
      activeTab: { 'right:0': 'pois', 'left:0': 'gibtsnicht' },
      dockSize: { left: 400, right: 'breit', bottom: 200 },
    };
    const l = mergeLayout(saved, DEFS);
    expect(l.panels.karte).toEqual(saved.panels.karte);
    expect(l.panels.ansicht).toEqual(DEFS[0]!.defaults);   // Leiste 'oben' ungültig
    expect(l.panels.messungen).toEqual(DEFS[1]!.defaults); // group 0.5 ungültig
    expect(l.dockSize).toEqual({ left: 400, right: 320, bottom: 200 });
    expect(l.activeTab['left:0']).toBeUndefined();
  });
  it('neues Panel (fehlt im Gespeicherten) bekommt seinen Standard, unbekanntes wird verworfen', () => {
    const saved = { ...defaultLayout(DEFS.slice(0, 3)), order: ['ansicht', 'alt', 'messungen', 'pois'] };
    (saved.panels as Record<string, PanelState>).alt = st('left', 0, true);
    const l = mergeLayout(saved, DEFS);
    expect(l.panels.karte).toEqual(DEFS[3]!.defaults);
    expect(l.panels.alt).toBeUndefined();
    expect(l.order).toEqual(['ansicht', 'messungen', 'pois', 'karte']);
  });
});

describe('clampFloat / clampLayout', () => {
  it('holt ein außerhalb gespeichertes Panel ins Fenster, Titelleiste greifbar', () => {
    expect(clampFloat({ x: 2400, y: 1500, w: 320, h: 240 }, 1280, 720)).toEqual({ x: 960, y: 720 - TITLE_H, w: 320, h: 240 });
    expect(clampFloat({ x: -50, y: -10, w: 320, h: 240 }, 1280, 720)).toEqual({ x: 0, y: 0, w: 320, h: 240 });
  });
  it('Mindest- und Höchstgröße', () => {
    expect(clampFloat({ x: 0, y: 0, w: 10, h: 10 }, 1280, 720)).toEqual({ x: 0, y: 0, w: MIN_W, h: MIN_H });
    expect(clampFloat({ x: 0, y: 0, w: 5000, h: 5000 }, 1280, 720)).toEqual({ x: 0, y: 0, w: 1280, h: 720 });
  });
  it('klemmt auch Leistenbreiten', () => {
    const l = clampLayout({ ...defaultLayout(DEFS), dockSize: { left: 5000, right: 10, bottom: 300 } }, 1000, 800);
    expect(l.dockSize).toEqual({ left: 600, right: DOCK_MIN, bottom: 300 });
  });
  it('resizeDock klemmt auf [DOCK_MIN, 60 %]', () => {
    expect(resizeDock(defaultLayout(DEFS), 'bottom', 900, 1920, 1000).dockSize.bottom).toBe(600);
  });
});

describe('dropTargetAt / applyDrop', () => {
  const view = { w: 1920, h: 1080 };
  const rect = { x: 800, y: 400, w: 320, h: 240 };
  it('über einer Gruppenkopfzeile → Reiter, sonst Kante → Leiste, sonst schweben', () => {
    expect(dropTargetAt({ x: 900, y: 500 }, view, { dock: 'right', group: 1 }, rect)).toEqual({ kind: 'tab', dock: 'right', group: 1 });
    expect(dropTargetAt({ x: EDGE_PX, y: 500 }, view, null, rect)).toEqual({ kind: 'dock', dock: 'left' });
    expect(dropTargetAt({ x: 1920 - EDGE_PX, y: 500 }, view, null, rect)).toEqual({ kind: 'dock', dock: 'right' });
    expect(dropTargetAt({ x: 900, y: 1080 - EDGE_PX }, view, null, rect)).toEqual({ kind: 'dock', dock: 'bottom' });
    expect(dropTargetAt({ x: 900, y: 500 }, view, null, rect)).toEqual({ kind: 'float', rect });
  });
  it('Leiste → neue Gruppe unten im Stapel; Reiter → Gruppe übernehmen und aktiv', () => {
    let l = applyDrop(defaultLayout(DEFS), 'karte', { kind: 'dock', dock: 'left' });
    expect(l.panels.karte).toMatchObject({ dock: 'left', group: 1, open: true });
    l = applyDrop(l, 'messungen', { kind: 'tab', dock: 'left', group: 1 });
    expect(tabsOf(l, 'left', 1)).toEqual(['messungen', 'karte']);
    expect(activeOf(l, 'left', 1)).toBe('messungen');
  });
  it('schweben übernimmt das Rechteck', () => {
    const l = applyDrop(defaultLayout(DEFS), 'karte', { kind: 'float', rect });
    expect(l.panels.karte).toMatchObject({ dock: 'float', float: rect, open: true });
  });
  it('unbekannte ID ändert nichts', () => {
    const l = defaultLayout(DEFS);
    expect(applyDrop(l, 'gibtsnicht', { kind: 'dock', dock: 'left' })).toEqual(l);
  });
});

describe('effectiveLayout (mobil, L6)', () => {
  it('≤ 700 px: alle offenen Panels als Reiter eines Blatts unten, Rest unverändert', () => {
    const l = applyDrop(defaultLayout(DEFS), 'karte', { kind: 'float', rect: { x: 1, y: 1, w: 300, h: 200 } });
    const e = effectiveLayout(l, 390);
    expect(tabsOf(e, 'bottom', 0)).toEqual(['ansicht', 'karte']);
    expect(effectiveLayout(l, 1920)).toEqual(l);
    expect(l.panels.karte!.dock).toBe('float'); // Eingabe unverändert
  });
});

describe('istMobil (Handy quer, 17.10.)', () => {
  it('schmal immer, quer nur mit Touch und wenig Höhe', () => {
    expect(istMobil(390, 844)).toBe(true);
    expect(istMobil(750, 342, true)).toBe(true);
    expect(istMobil(750, 342, false)).toBe(false);
    expect(istMobil(1024, 768, true)).toBe(false);
    const l = defaultLayout(DEFS);
    expect(effectiveLayout(l, 750, 342, true)).not.toEqual(l);
    expect(effectiveLayout(l, 750, 342, false)).toEqual(l);
  });
});
