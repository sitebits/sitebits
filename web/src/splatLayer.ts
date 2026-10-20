// Spark wird erst bei vorhandener Sichtschicht importiert: WASM/CSP-Fehler bleiben lokal behandelbar.
import type Instance from '@giro3d/giro3d/core/Instance.js';
import type { MeshHandle } from './layers';
import type { SplatsLayer, Vec3 } from './manifest';
import { resolveUrl } from './manifest';

export const SPLAT_BUDGET_BYTES = 256 * 1024 * 1024;
export const SPLAT_MAX_COUNT = 1_000_000;
export interface SplatHandle extends MeshHandle { dispose(): void }

export function splatBudget(layer: Pick<SplatsLayer, 'splatCount' | 'sizeBytes'> & { shDegree?: number; format?: string; chunkCount?: number },
  weak: boolean): boolean {
  if (layer.format === 'rad') return Number.isSafeInteger(layer.chunkCount) && (layer.chunkCount ?? 0) > 0;
  // Rohdatei + dekodierte CPU/GPU/Sortierpuffer, konservativ 192 Byte/Gaussian + 48 je SH-Grad einschließlich LOD-Puffer.
  const budget = weak ? 64 * 1024 * 1024 : SPLAT_BUDGET_BYTES;
  return layer.splatCount <= SPLAT_MAX_COUNT && layer.sizeBytes + layer.splatCount * (192 + (layer.shDegree ?? 3) * 48) <= budget;
}

export async function createSplatLayer(o: { instance: Instance; layer: SplatsLayer;
  originWorld: Vec3; pkgBase: string; notify: (text: string) => void; loading?: (active: boolean) => void }): Promise<SplatHandle> {
  const { instance, layer, notify } = o;
  const weak = matchMedia('(pointer: coarse)').matches ||
    ((navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8) <= 4;
  if (!instance.renderer.capabilities.isWebGL2 || !splatBudget(layer, weak)) {
    notify('Sichtschicht überschreitet das Gerätebudget – bitte Punktwolke oder Mesh nutzen.');
    return { id: layer.id, disabled: true, setVisible: () => undefined,
      setOpacity: () => undefined, dispose: () => undefined };
  }
  // CSP-Prüfung vor Spark: dessen Modul startet sonst eine ungebundene WASM-Promise.
  await WebAssembly.compile(new Uint8Array([0, 97, 115, 109, 1, 0, 0, 0]));
  const { SparkRenderer, SplatMesh, SplatFileType } = await import('@sparkjsdev/spark');
  const spark = new SparkRenderer({ renderer: instance.renderer, onDirty: () => instance.notifyChange(),
    enableLod: true, pagedExtSplats: true, maxPagedSplats: weak ? 131_072 : 1_048_576, lodSplatCount: weak ? 100_000 : 500_000, lodRenderScale: weak ? 2 : 1 });
  let mesh: InstanceType<typeof SplatMesh> | undefined;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30_000);
  let firstFrame: (() => void) | undefined;
  let firstFrameTimer: ReturnType<typeof setTimeout> | undefined;
  let armFirstFrame: (() => void) | undefined;
  try {
    o.loading?.(true);
    notify('Lade Sichtschicht …');
    if (layer.format === 'rad') {
      mesh = new SplatMesh({ url: resolveUrl(o.pkgBase, layer.url), paged: true,
        raycastable: false, enableLod: true });
      const timeout = new Promise<never>((_, reject) => controller.signal.addEventListener('abort',
        () => reject(new Error('RAD-Ladezeit überschritten')), { once: true }));
      const metadata = await Promise.race([mesh.paged!.getRadMeta(), timeout]);
      if (!metadata.meta.lodTree || metadata.meta.chunks.length !== layer.chunkCount ||
          metadata.meta.count < layer.splatCount || metadata.meta.maxSh !== layer.shDegree)
        throw new Error('RAD-Metadaten widersprechen Manifest');
      mesh.paged!.setMaxSh(layer.shDegree);
      // Auch die Initialisierung (erster Chunk) unter dieselbe Frist, sonst hängt die Ladeanzeige.
      await Promise.race([mesh.initialized, timeout]);
    } else {
      const response = await fetch(resolveUrl(o.pkgBase, layer.url), { signal: controller.signal });
      if (!response.ok || !response.body) throw new Error('Splat-Datei nicht erreichbar');
      const reader = response.body.getReader();
      // Vorallokation aus geprüftem Manifest; niemals unbeschränkt arrayBuffer() lesen.
      const bytes = new Uint8Array(layer.sizeBytes);
      let received = 0;
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        if (received + value.length > bytes.length) { await reader.cancel(); throw new Error('Splat-Datei größer als Manifest'); }
        bytes.set(value, received); received += value.length;
      }
      if (received !== bytes.length) throw new Error('Splat-Datei unvollständig');
      mesh = new SplatMesh({ fileBytes: bytes, fileType: layer.format === 'spz' ? SplatFileType.SPZ : SplatFileType.PLY, maxSplats: layer.splatCount,
        raycastable: false, extSplats: true, nonLod: true, enableLod: true, lod: true, lodAbove: 100_000 });
      if (controller.signal.aborted) throw new Error('Splat-Ladezeit überschritten');
      const timeout = new Promise<never>((_, reject) => controller.signal.addEventListener('abort',
        () => reject(new Error('Splat-Ladezeit überschritten')), { once: true }));
      await Promise.race([mesh.initialized, timeout]);
      // Keep source buffers (nonLod) to validate the file count independently of LoD parents.
      if (mesh.extSplats?.getNumSplats() !== layer.splatCount) throw new Error('Gaussian-Anzahl widerspricht Manifest');
      mesh.extSplats.setMaxSh(layer.shDegree);
      mesh.extSplats.lodSplats?.setMaxSh(layer.shDegree);
      // SPZ stores RUB; our import converts the project axes by Rx(pi) on disk.
      // Spark consumes SPZ coordinates directly (unlike our source reader), so undo that here.
      if (layer.format === 'spz') mesh.rotation.x = Math.PI;
    }
    mesh.position.set(...o.originWorld);
    instance.scene.add(spark, mesh);
    instance.notifyChange();
    const loaded = mesh;
    if (layer.format === 'rad') {
      firstFrame = () => {
        if (!loaded.visible || (loaded.paged?.getNumSplats() ?? 0) === 0) return;
        instance.removeEventListener('after-render', firstFrame!);
        firstFrame = undefined;
        clearTimeout(firstFrameTimer);
        o.loading?.(false);
        performance.mark('scanview-splats-first-frame');
        notify('Sichtschicht geladen – Details werden nachgeladen. Messen auf Punktwolke oder Mesh.');
      };
      instance.addEventListener('after-render', firstFrame);
      armFirstFrame = () => {
        clearTimeout(firstFrameTimer);
        firstFrameTimer = setTimeout(() => {
          if (!loaded.visible || !firstFrame) return;
          loaded.visible = false;
          instance.notifyChange();
          o.loading?.(false);
          notify('RAD lieferte kein erstes Bild – Sichtschicht ausgeblendet.');
        }, 60_000);
      };
      armFirstFrame();
    } else { o.loading?.(false); notify('Sichtschicht geladen – Messen auf Punktwolke oder Mesh.'); }
    const contextLost = (): void => { loaded.visible = false; o.loading?.(false); notify('Grafikspeicher verloren – Sichtschicht ausgeblendet.'); };
    instance.renderer.domElement.addEventListener('webglcontextlost', contextLost);
    return { id: layer.id, setVisible: (on) => { loaded.visible = on; o.loading?.(on && firstFrame !== undefined);
        if (on && firstFrame) armFirstFrame?.();
        instance.notifyChange(); },
      setOpacity: (opacity) => { loaded.opacity = opacity; instance.notifyChange(); },
      dispose: () => { o.loading?.(false); if (firstFrame) instance.removeEventListener('after-render', firstFrame);
        clearTimeout(firstFrameTimer);
        instance.renderer.domElement.removeEventListener('webglcontextlost', contextLost);
        instance.scene.remove(loaded, spark); loaded.dispose(); spark.dispose(); } };
  } catch (error) {
    o.loading?.(false);
    controller.abort();
    if (mesh) { const failed = mesh; failed.dispose();
      void failed.initialized.then(() => failed.dispose(), () => undefined); }
    spark.dispose();
    throw error;
  } finally { clearTimeout(timer); }
}
