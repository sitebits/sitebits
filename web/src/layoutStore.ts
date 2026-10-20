// Layout je Projekt im localStorage des Betrachters. Speicher kann fehlen oder werfen (privates
// Fenster, Kontingent): dann Standard-Layout bzw. still nicht speichern — nie eine Ausnahme an den Aufrufer.
import { defaultLayout, mergeLayout } from './layoutModel';
import type { Layout, PanelDef } from './layoutModel';

export interface KV { getItem(k: string): string | null; setItem(k: string, v: string): void; removeItem(k: string): void }
export const layoutKey = (projectId: string): string => `scanview.layout.${projectId}`;

export function browserStorage(): KV | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

export function loadLayout(s: KV | null, projectId: string, defs: readonly PanelDef[]): Layout {
  let raw: string | null = null;
  try {
    raw = s?.getItem(layoutKey(projectId)) ?? null;
  } catch {
    raw = null;
  }
  if (raw === null) return defaultLayout(defs);
  try {
    return mergeLayout(JSON.parse(raw), defs);
  } catch {
    return defaultLayout(defs);
  }
}

export function saveLayout(s: KV | null, projectId: string, l: Layout): boolean {
  try {
    if (!s) return false;
    s.setItem(layoutKey(projectId), JSON.stringify(l));
    return true;
  } catch {
    return false;
  }
}

export function clearLayout(s: KV | null, projectId: string): void {
  try {
    s?.removeItem(layoutKey(projectId));
  } catch {
    /* Speicher nicht verfügbar — nichts zu löschen */
  }
}

/** Foto-Darstellung nutzt denselben fehlertoleranten Projektspeicher, einen eigenen Untereintrag. */
export interface FotoLayout { tiefe: number; hoch: number; quer: number }
export const fotoLayoutVorgabe = (): FotoLayout => ({ tiefe: 1.5, hoch: 0.5, quer: 0.5 });
export function loadFotoLayout(s: KV | null, projekt: string): FotoLayout {
  const v = fotoLayoutVorgabe();
  try {
    const r: unknown = JSON.parse(s?.getItem(`${layoutKey(projekt)}.fotos`) ?? 'null');
    if (!r || typeof r !== 'object') return v;
    for (const key of ['tiefe', 'hoch', 'quer'] as const) {
      const x = (r as Record<string, unknown>)[key];
      if (typeof x === 'number' && Number.isFinite(x)) v[key] = Math.min(key === 'tiefe' ? 20 : 0.75, Math.max(key === 'tiefe' ? 0.1 : 0.25, x));
    }
  } catch { /* Gesperrter Speicher oder beschädigter Eintrag: Vorgabe. */ }
  return v;
}
export function saveFotoLayout(s: KV | null, projekt: string, wert: FotoLayout): void {
  try { s?.setItem(`${layoutKey(projekt)}.fotos`, JSON.stringify(wert)); } catch { /* Darstellung bleibt bedienbar. */ }
}
