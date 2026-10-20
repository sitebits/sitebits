// Nächste sichtbare Datenfläche; innerhalb 5 mm entscheidet die Ebenenpräferenz.
import { surfaceGapM } from './pickMath';
import type { PickOptions, PickSource, RayPickResult, Vorschau } from './rayPick';
export type PickPreference = 'auto' | 'pointcloud' | 'mesh';
export const PICK_TIE_M = 0.005;
export class CompositePickSource implements PickSource {
  constructor(private readonly o: { pointcloud: PickSource; cloudLayerId: string; meshes: readonly PickSource[];
    preference: () => PickPreference; panorama: () => boolean }) {}
  warm(): void { this.o.pointcloud.warm(); this.o.meshes.forEach((m) => m.warm()); }
  pickAtEvent(ev: MouseEvent, opts?: PickOptions): Promise<RayPickResult> { return this.pickAtScreen(ev.clientX,ev.clientY,opts); }
  async pickAtScreen(x: number, y: number, opts?: PickOptions): Promise<RayPickResult> {
    const cloud = (r: RayPickResult): RayPickResult => ({ ...r, info: { ...r.info, source: 'pointcloud', layerId: this.o.cloudLayerId } });
    // Getter sofort erfassen: ein Wechsel während des asynchronen Wolken-Picks ändert den Klick nicht.
    const pref = this.o.preference(); const panorama = this.o.panorama();
    const pending = this.o.pointcloud.pickAtScreen(x,y,opts).then(cloud);
    if (panorama) return pending;
    const results = await Promise.all([pending, ...this.o.meshes.map((m) => m.pickAtScreen(x,y,opts))]);
    const hits = results.filter((r) => r.point !== null && Number.isFinite(r.info.tiefeM));
    hits.sort((a,b) => a.info.tiefeM!-b.info.tiefeM! || (a.info.layerId ?? '').localeCompare(b.info.layerId ?? ''));
    // Ein noch nicht messbares Mesh, das sichtbar vor dem nächsten Treffer liegt, verdeckt ihn:
    // kein Ersatzwert von der Fläche dahinter (M5). Ohne Tiefe (alte Quelle) gilt es als davor.
    const blockiert = results.filter((r) => r.info.reason === 'imprecise')
      .find((r) => !hits.length || (r.info.frontDepthM ?? -Infinity) < hits[0]!.info.tiefeM! - PICK_TIE_M);
    if (blockiert) return blockiert;
    if (!hits.length) return results[0]!;
    const near = hits.filter((r) => r.info.tiefeM! <= hits[0]!.info.tiefeM! + PICK_TIE_M);
    const wanted = pref === 'mesh' ? 'mesh' : 'pointcloud';
    return near.find((r) => r.info.source === wanted) ?? near[0]!;
  }
  vorschauAtScreen(x: number, y: number): Vorschau {
    const cloud = this.o.pointcloud.vorschauAtScreen(x,y); if (this.o.panorama()) return cloud;
    const results = [cloud, ...this.o.meshes.map((m) => m.vorschauAtScreen(x,y))];
    const near = results.filter((r) => r.tiefeM !== null).sort((a,b) => a.tiefeM!-b.tiefeM!);
    const first = near[0] ?? cloud;
    // Ein Mesh ist undurchsichtig: Tiefen deutlich dahinter sind verdeckt, keine zweite Schicht.
    const meshVorn = Math.min(...results.slice(1).map((r) => r.tiefeM ?? Infinity));
    const sichtbar = (t: number): boolean => !Number.isFinite(meshVorn) || t <= meshVorn + surfaceGapM(meshVorn);
    return { ...first, mitteTiefen: results.flatMap((r) => r.mitteTiefen).filter(sichtbar),
      fensterTiefen: results.flatMap((r) => r.fensterTiefen).filter(sichtbar), ms: results.reduce((n,r) => n+r.ms,0) };
  }
}
const STORAGE_KEY = 'scanview-messen-auf';
export function loadPickPreference(): PickPreference {
  try { const s = localStorage.getItem(STORAGE_KEY); if (s === 'mesh' || s === 'pointcloud') return s; } catch { /* gesperrter Browser-Speicher */ }
  return 'auto';
}
export function attachPickPreference(root: HTMLElement, initial: PickPreference, changed: (value: PickPreference) => void): void {
  const label = document.createElement('label'); label.className = 'sv-messen-auf'; label.textContent = 'Messen auf';
  const select = document.createElement('select'); select.setAttribute('aria-label','Messen auf');
  for (const [value,text] of [['auto','Automatisch'],['pointcloud','Punktwolke'],['mesh','Mesh']]) {
    const option = document.createElement('option'); option.value=value!; option.textContent=text!; select.append(option);
  }
  select.value=initial; select.title='Bei gleich nahen Flächen entscheidet diese Auswahl; automatisch: Punktwolke vor Mesh';
  select.addEventListener('change', () => {
    const value = select.value as PickPreference; changed(value);
    try { localStorage.setItem(STORAGE_KEY,value); } catch { /* Auswahl bleibt in dieser Sitzung */ }
  });
  label.append(select); root.append(label);
}
