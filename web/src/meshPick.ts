// Messquelle für sichtbare Vollstufen. three-Raycaster rechnet jeden Weltstrahl vor
// dem Dreiecksschnitt in das lokale Mesh-System und das Ergebnis in Float64 zurück.
import { Raycaster, Vector2, Vector3 } from 'three';
import type { Camera, Mesh } from 'three';
import { pointAllowed } from './clipBoxMath';
import type { ClipBoxState } from './clipBoxMath';
import type { MeshLayerHandle } from './meshLayer';
import { meshFeatureEdges, snapMeshHit } from './meshPickMath';
import { strahlSchnitte } from './meshStrahl';
import { FINE_BUDGET_MS } from './rayPick';
import type { PickInfo, PickOptions, PickSource, RayPickResult, Vorschau } from './rayPick';
export class MeshPickSource implements PickSource {
  constructor(private readonly o: {
    camera: () => Camera;
    rect: () => { left: number; top: number; width: number; height: number };
    layers: () => readonly MeshLayerHandle[];
    clip: () => ClipBoxState | undefined;
  }) {}
  warm(): void { for (const h of this.o.layers()) for (const m of h.pickMeshes) meshFeatureEdges(m); }
  pickAtEvent(ev: MouseEvent, opts?: PickOptions): Promise<RayPickResult> { return this.pickAtScreen(ev.clientX, ev.clientY, opts); }
  async pickAtScreen(x: number, y: number, opts: PickOptions = {}): Promise<RayPickResult> {
    const t0 = performance.now(); const clip = this.o.clip();
    const visible = this.o.layers().filter((h) => h.visible);
    const info: PickInfo = { source: 'mesh', level: 'none', ms: 0, wideCandidates: 0, nearCandidates: 0 };
    const finish = (point: Vector3 | null): RayPickResult => ({ point: point ? [point.x,point.y,point.z] : null, info: { ...info, ms: performance.now()-t0 } });
    if (!visible.length) { info.reason = 'hidden'; return finish(null); }
    // Ebenen, die noch laden, fordern die Vollstufe an und messen nicht. Ebenen mit Ladefehler
    // kommen nie über die Vorschau hinaus — sie sperren die übrigen Ebenen nicht.
    const pending = visible.filter((h) => h.stage !== 'full');
    pending.filter((h) => h.failure === null).forEach((h) => h.requestFull());
    const rect = this.o.rect(); const camera = this.o.camera().clone();
    // Weltpose einschließlich Elterntransformationen übernehmen. Neu berechnen
    // würde die elternlose Kopie aus ihrer lokalen Pose anders ausrichten.
    camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
    const raycaster = new Raycaster(); raycaster.setFromCamera(new Vector2((x-rect.left)/rect.width*2-1, 1-(y-rect.top)/rect.height*2), camera);
    const accept = (p: Vector3): boolean => pointAllowed(clip, [p.x,p.y,p.z]);
    const inWindow = (d: number): boolean => !opts.tiefenFenster || (d >= opts.tiefenFenster.min && d < opts.tiefenFenster.max);
    const results = visible.flatMap((h) => strahlSchnitte(raycaster, h.pickMeshes).map((hit) => ({ h,hit })))
      .filter(({hit}) => accept(hit.point) && inWindow(hit.distance))
      .sort((a,b) => a.hit.distance-b.hit.distance || a.h.id.localeCompare(b.h.id) || (a.hit.faceIndex ?? 0)-(b.hit.faceIndex ?? 0));
    // Sichtbare, nicht messbare Fläche vor dem ersten Messtreffer: kein Ersatzwert dahinter.
    // frontDepthM lässt den Verbund prüfen, ob sie auch vor einem Wolkentreffer liegt.
    const block = pending.flatMap((h) => strahlSchnitte(raycaster, h.occluderMeshes))
      .filter((hit) => accept(hit.point) && inWindow(hit.distance)).reduce((d, hit) => Math.min(d, hit.distance), Infinity);
    const first = results[0];
    if (block < (first?.hit.distance ?? Infinity)) { info.reason = 'imprecise'; info.frontDepthM = block; return finish(null); }
    if (!first) { info.reason = 'empty'; return finish(null); }
    const {h,hit} = first; const mesh = hit.object as Mesh; const triangle = hit.faceIndex ?? 0;
    const local = mesh.worldToLocal(hit.point.clone());
    // Einrasten (Vertex, Merkmalskante) nur mit eingeschaltetem Fang (Taste E): Auf dem vereinfachten
    // Modell liegen Vertices bis mehrere Zentimeter neben dem Klick (A.4 am Reiter: bis 46 mm).
    const snapped = opts.snapEdges ? snapMeshHit({ geometry: mesh.geometry, triangle, hitLocal: local, matrixWorld: mesh.matrixWorld,
      camera, width: rect.width, height: rect.height, x: x-rect.left, y: y-rect.top, edges: true, featureEdges:meshFeatureEdges(mesh), accept })
      : { kind: 'none' as const, point: hit.point.clone() };
    if (performance.now()-t0 > (opts.budgetMs ?? FINE_BUDGET_MS)) { info.reason = 'imprecise'; info.frontDepthM = hit.distance; return finish(null); }
    // Fang darf ein explizites Tiefenfenster nicht verlassen.
    const depth = snapped.point.clone().sub(raycaster.ray.origin).dot(raycaster.ray.direction);
    const snappedInWindow = !opts.tiefenFenster || (depth >= opts.tiefenFenster.min && depth < opts.tiefenFenster.max);
    const point = snappedInWindow ? snapped.point : hit.point;
    info.level = 'fine'; info.layerId = h.id; info.triangle = { tile: `full/${h.pickMeshes.indexOf(mesh)}`, index: triangle };
    info.snap = { result: snappedInWindow ? snapped.kind : 'none', original: snapped.kind === 'vertex', shiftMm: point.distanceTo(hit.point)*1000 };
    info.tiefeM = point.clone().sub(raycaster.ray.origin).dot(raycaster.ray.direction);
    info.nearDepthM = hit.distance; info.frontDepthM = hit.distance; info.wideCandidates = results.length; info.nearCandidates = results.length;
    return finish(point);
  }
  vorschauAtScreen(x: number, y: number): Vorschau {
    const t0 = performance.now(); const rect = this.o.rect(); const caster = new Raycaster();
    caster.setFromCamera(new Vector2((x-rect.left)/rect.width*2-1, 1-(y-rect.top)/rect.height*2), this.o.camera());
    const hits = this.o.layers().filter((h) => h.visible && h.stage === 'full').flatMap((h) => strahlSchnitte(caster, h.pickMeshes))
      .filter((h) => pointAllowed(this.o.clip(), [h.point.x,h.point.y,h.point.z])).sort((a,b) => a.distance-b.distance);
    // Nur die vorderste Fläche ist sichtbar; Strahlschnitte dahinter (Rückseiten, verdeckte Teile)
    // wären sonst eine zweite „Schicht“ und lösten die Rückfrage vorn/hinten aus.
    const h = hits[0]; const depths = h ? [h.distance] : [];
    return { punktWelt: h ? [h.point.x,h.point.y,h.point.z] : null, tiefeM: h?.distance ?? null,
      mitteTiefen: depths, fensterTiefen: depths, ms: performance.now()-t0 };
  }
}
