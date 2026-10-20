// Herkunft bleibt optional: alte Daten sind unbekannt, niemals nachträglich geraten.
import { messhinweise } from './genauigkeit';
import type { Genauigkeit } from './genauigkeit';
import type { PickInfo } from './rayPick';
export interface PointSource { source: 'pointcloud' | 'mesh'; layerId: string }
export interface SourceLayer { id: string; title: string; provenance?: { scaleVerified: boolean; interpolated: boolean; scaleSource?: string; accuracy?: Genauigkeit } }
export function parsePointSource(value: unknown): PointSource | null {
  if (value === null) return null;
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Ungültige Punktquelle');
  const r = value as Record<string,unknown>;
  if (Object.keys(r).length !== 2 || (r.source !== 'mesh' && r.source !== 'pointcloud') || typeof r.layerId !== 'string' || !/^[A-Za-z0-9_-]{1,64}$/.test(r.layerId)) throw new Error('Ungültige Punktquelle');
  return { source:r.source,layerId:r.layerId };
}
export const sourceFromPick = (info: PickInfo): PointSource | null => info.source && info.layerId ? {source:info.source,layerId:info.layerId} : null;
export function sourceLabel(source: PointSource | null | undefined, layers: readonly SourceLayer[]): string {
  if (!source) return 'unbekannt';
  const title = layers.find((l) => l.id === source.layerId)?.title ?? source.layerId;
  return `${source.source === 'mesh' ? 'Mesh' : 'Punktwolke'}: ${title}`;
}
export function measurementNotes(m: { pointSources?: readonly (PointSource|null)[] }, layers: readonly SourceLayer[]): string[] {
  const notes = new Set<string>();
  for (const s of m.pointSources ?? []) {
    if (!s) continue; const p = layers.find((l) => l.id === s.layerId)?.provenance;
    // Mit Nachweis (F15) sagt der genauer, was am Maßstab belegt oder geprüft ist.
    if (p?.accuracy) for (const n of messhinweise(p.accuracy)) notes.add(n);
    else if (p?.scaleVerified === false) notes.add(`Modellmaß — Maßstab nicht belegt (Quelle: ${p.scaleSource ?? 'unbekannt'})`);
    if (s.source === 'mesh' && p?.interpolated) notes.add('Modelloberfläche (teils interpoliert)');
  }
  return [...notes];
}

/** Im Panorama dürfen gespeicherte Mesh-Eckpunkte den Wolken-Picker nicht umgehen. */
export const sourceReusable = (source: PointSource|null|undefined, panorama: boolean): boolean => !panorama || source?.source !== 'mesh';
