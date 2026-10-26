// Messquelle der gekachelten Meshes. Gemessen wird ausschließlich auf Blättern (feinste Stufe, geometricError 0):
// Der Strahl geht durch den Kachelbaum (tilesMath.blattKandidaten), die Blatt-GLBs werden von vorn nach hinten
// geladen (tilesBlatt) und mit demselben Strahltest geschnitten wie die GLB-Meshes (meshStrahl). Was der Renderer
// gerade zeichnet — auch gröbere Stufen — dient nur als Verdeckung: Ist das Blatt nicht rechtzeitig da, gibt es
// keinen Wert („imprecise“, die Meldung „Feinste Stufe nicht rechtzeitig — erneut klicken“), nie einen Ersatzwert
// aus einer gröberen Stufe und nie den Wert der Fläche dahinter (CompositePickSource).
import { Raycaster, Vector2, Vector3 } from 'three';
import type { Camera, Intersection, Mesh } from 'three';
import { pointAllowed } from './clipBoxMath';
import type { ClipBoxState } from './clipBoxMath';
import { meshFeatureEdges, snapMeshHit } from './meshPickMath';
import { strahlSchnitte } from './meshStrahl';
import { FINE_BUDGET_MS } from './rayPick';
import type { PickInfo, PickOptions, PickSource, RayPickResult, Vorschau } from './rayPick';
import { BLATT_PARALLEL } from './tilesBlatt';
import type { BlattGeo, BlattSpeicher } from './tilesBlatt';
import { blattKandidaten } from './tilesMath';
import type { BlattKandidat, Tileset, TilesKnoten } from './tilesMath';

/** Was die Messquelle von einer gekachelten Ebene braucht (tilesLayer.ts liefert es). */
export interface KachelMessEbene {
  readonly id: string;
  readonly visible: boolean;
  readonly opacity: number;
  readonly tileset: Tileset;
  readonly blaetter: BlattSpeicher;
  /** Absolute Adresse des Blatt-GLB. */
  blattUrl(knoten: TilesKnoten): string;
  /** Gerade gezeichnete Kachelnetze (alle Stufen): nur Verdeckung und Vorschau, nie Messwert. */
  readonly occluderMeshes: readonly Mesh[];
}

type Treffer = { hit: Intersection; mesh: Mesh; h: KachelMessEbene; uri: string };

const warte = (ms: number): Promise<'zeit'> => new Promise((ok) => setTimeout(() => ok('zeit'), Math.max(0, ms)));

export class KachelPickSource implements PickSource {
  constructor(private readonly o: {
    camera: () => Camera;
    rect: () => { left: number; top: number; width: number; height: number };
    layers: () => readonly KachelMessEbene[];
    clip: () => ClipBoxState | undefined;
  }) {}
  warm(): void { /* Blätter lädt der erste Klick; es gibt nichts Teures vorzubereiten. */ }
  pickAtEvent(ev: MouseEvent, opts?: PickOptions): Promise<RayPickResult> { return this.pickAtScreen(ev.clientX, ev.clientY, opts); }
  async pickAtScreen(x: number, y: number, opts: PickOptions = {}): Promise<RayPickResult> {
    const t0 = performance.now(); const clip = this.o.clip();
    const sichtbar = this.o.layers().filter((h) => h.visible && h.opacity > 0);
    const info: PickInfo = { source: 'mesh', level: 'none', ms: 0, wideCandidates: 0, nearCandidates: 0 };
    const fertig = (p: Vector3 | null): RayPickResult => ({ point: p ? [p.x, p.y, p.z] : null, info: { ...info, ms: performance.now() - t0 } });
    if (!sichtbar.length) { info.reason = 'hidden'; return fertig(null); }
    const rect = this.o.rect(); const camera = this.o.camera().clone();
    camera.matrixWorldInverse.copy(camera.matrixWorld).invert();
    const raycaster = new Raycaster();
    raycaster.setFromCamera(new Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), camera);
    const accept = (p: Vector3): boolean => pointAllowed(clip, [p.x, p.y, p.z]);
    const imFenster = (d: number): boolean => !opts.tiefenFenster || (d >= opts.tiefenFenster.min && d < opts.tiefenFenster.max);
    const gueltig = (hit: Intersection): boolean => accept(hit.point) && imFenster(hit.distance);

    const kandidaten = sichtbar.flatMap((h) => blattKandidaten(h.tileset, raycaster.ray, opts.tiefenFenster?.max)
      .map((k): BlattKandidat & { h: KachelMessEbene } => ({ ...k, h })))
      .sort((a, b) => a.ein - b.ein || a.knoten.id - b.knoten.id);
    // Sichtbare gezeichnete Fläche (gleich welche Stufe) vor dem ersten Messtreffer verdeckt nur.
    const verdeckung = sichtbar.flatMap((h) => strahlSchnitte(raycaster, h.occluderMeshes)).filter(gueltig)
      .reduce((d, hit) => Math.min(d, hit.distance), Infinity);

    const grenze = t0 + (opts.budgetMs ?? FINE_BUDGET_MS);
    let bester: Treffer | null = null; let anzahl = 0; let grund: 'timeout' | 'error' | null = null; let i = 0;
    while (i < kandidaten.length) {
      // Exakt: ein späteres Blatt beginnt erst hinter dem besten Treffer, kann ihn also nicht unterbieten.
      if (bester && bester.hit.distance <= kandidaten[i]!.ein) break;
      const welle = kandidaten.slice(i, i + BLATT_PARALLEL);
      const laden = Promise.all(welle.map((k) => k.h.blaetter.hole(k.h.blattUrl(k.knoten), k.knoten.uri!, k.knoten)));
      // Ein Abruf nach dem Zeitlimit läuft weiter und liegt beim nächsten Klick fertig bereit.
      laden.catch(() => undefined);
      let geos: BlattGeo[] | 'zeit';
      try { geos = await Promise.race([laden, warte(grenze - performance.now())]); }
      catch { grund = 'error'; break; }
      if (geos === 'zeit') { grund = 'timeout'; break; }
      i += welle.length;
      for (const [j, geo] of geos.entries()) {
        const h = welle[j]!.h;
        for (const hit of strahlSchnitte(raycaster, geo.meshes)) {
          if (!gueltig(hit)) continue;
          anzahl++;
          if (!bester || hit.distance < bester.hit.distance) bester = { hit, mesh: hit.object as Mesh, h, uri: geo.uri };
        }
      }
    }
    info.wideCandidates = info.nearCandidates = anzahl;
    if (grund !== null && !bester) {
      // Nicht rechtzeitig/kaputt, und kein früherer Treffer: kein Wert. frontDepthM = was dort sichtbar im Weg steht
      // (sonst Eintritt des ersten ungeladenen Blattes), damit der Verbund die Fläche dahinter nicht einsetzt.
      info.reason = 'imprecise'; info.fineReason = grund;
      info.frontDepthM = Number.isFinite(verdeckung) ? verdeckung : kandidaten[i]?.ein ?? 0;
      return fertig(null);
    }
    if (grund !== null && bester && kandidaten[i] && bester.hit.distance > kandidaten[i]!.ein) {
      // Ein noch nicht geladenes Blatt liegt vor dem besten Treffer und könnte ihn unterbieten: kein Wert.
      info.reason = 'imprecise'; info.fineReason = grund; info.frontDepthM = kandidaten[i]!.ein;
      return fertig(null);
    }
    if (!bester) {
      // Strahl trifft kein Blatt, doch ein gezeichnetes Netz steht im Weg: verdeckt, nicht „leer“ (kein Ersatzwert dahinter).
      if (Number.isFinite(verdeckung)) { info.reason = 'imprecise'; info.fineReason = 'error'; info.frontDepthM = verdeckung; return fertig(null); }
      info.reason = 'empty'; return fertig(null);
    }
    const { hit, mesh, h, uri } = bester; const dreieck = hit.faceIndex ?? 0;
    const lokal = mesh.worldToLocal(hit.point.clone());
    const einrasten = opts.snapEdges ? snapMeshHit({ geometry: mesh.geometry, triangle: dreieck, hitLocal: lokal, matrixWorld: mesh.matrixWorld,
      camera, width: rect.width, height: rect.height, x: x - rect.left, y: y - rect.top, edges: true, featureEdges: meshFeatureEdges(mesh), accept })
      : { kind: 'none' as const, point: hit.point.clone() };
    if (performance.now() - t0 > (opts.budgetMs ?? FINE_BUDGET_MS)) {
      info.reason = 'imprecise'; info.fineReason = 'timeout'; info.frontDepthM = hit.distance; return fertig(null);
    }
    const tiefe = einrasten.point.clone().sub(raycaster.ray.origin).dot(raycaster.ray.direction);
    const eingerastetImFenster = !opts.tiefenFenster || (tiefe >= opts.tiefenFenster.min && tiefe < opts.tiefenFenster.max);
    const punkt = eingerastetImFenster ? einrasten.point : hit.point;
    info.level = 'fine'; info.layerId = h.id; info.triangle = { tile: uri, index: dreieck };
    info.snap = { result: eingerastetImFenster ? einrasten.kind : 'none', original: einrasten.kind === 'vertex', shiftMm: punkt.distanceTo(hit.point) * 1000 };
    info.tiefeM = punkt.clone().sub(raycaster.ray.origin).dot(raycaster.ray.direction);
    info.nearDepthM = hit.distance; info.frontDepthM = hit.distance;
    return fertig(punkt);
  }
  vorschauAtScreen(x: number, y: number): Vorschau {
    const t0 = performance.now(); const rect = this.o.rect(); const caster = new Raycaster();
    caster.setFromCamera(new Vector2((x - rect.left) / rect.width * 2 - 1, 1 - (y - rect.top) / rect.height * 2), this.o.camera());
    // Nur Anzeige (nie ein Messwert): was gerade gezeichnet wird, gleich welche Stufe.
    const treffer = this.o.layers().filter((h) => h.visible && h.opacity > 0).flatMap((h) => strahlSchnitte(caster, h.occluderMeshes))
      .filter((h) => pointAllowed(this.o.clip(), [h.point.x, h.point.y, h.point.z])).sort((a, b) => a.distance - b.distance);
    const h = treffer[0]; const tiefen = h ? [h.distance] : [];
    return { punktWelt: h ? [h.point.x, h.point.y, h.point.z] : null, tiefeM: h?.distance ?? null,
      mitteTiefen: tiefen, fensterTiefen: tiefen, ms: performance.now() - t0 };
  }
}
