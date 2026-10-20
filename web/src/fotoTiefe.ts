// Tiefenquelle für geladene Wolkenknoten/Meshes. CPU unabhängig von Sicht/LOD;
// eigene Kamera nur während des GPU-Ersatzes in View, im finally wiederhergestellt.
import { PerspectiveCamera, Quaternion, Raycaster, Vector2, Vector3 } from 'three';
import type { Viewer } from './viewer';
import type { MeshLayerHandle } from './meshLayer';
import type { OrientedImage } from './images';
import type { FotoProjektion } from './fotoZuordnung';
import type { Vec3 } from './manifest';
import { strahlSchnitte } from './meshStrahl';
import { punktStrahl } from './punktStrahl';

export function fotoTiefenquelle(v: Viewer, meshes: readonly MeshLayerHandle[], wolkeSichtbar: () => boolean): {
  tiefe(bild: OrientedImage, pixel: FotoProjektion): number | null;
  tiefeAsync(bild: OrientedImage, pixel: FotoProjektion, index: number, signal?: AbortSignal): Promise<number | null>;
  punkt(bild: OrientedImage, x: number, y: number): Vec3 | null;
} {
  const camera = new PerspectiveCamera();
  const caster = new Raycaster();
  function* strahl(bild: OrientedImage, x: number, y: number, far = 250, stichprobe?: number): Generator<void, Vec3 | null> {
    const { width, height, focalPx } = bild.intrinsics;
    if (x < 0 || y < 0 || x > width || y > height) return null;
    const { instance } = v;
    const alt = instance.view.camera;
    const [w, qx, qy, qz] = bild.pose.rotationQuat;
    camera.position.set(...bild.pose.translation);
    camera.quaternion.copy(new Quaternion(qx, qy, qz, w));
    // Foto-Sichtfeld unabhängig vom Seitenverhältnis der 3D-Leinwand.
    camera.fov = 2 * Math.atan(height / 2 / focalPx) * 180 / Math.PI;
    camera.aspect = width / height; camera.near = 0.01; camera.far = 250;
    camera.clearViewOffset(); camera.updateMatrixWorld(true);
    const ndc = new Vector2(x / width * 2 - 1, 1 - y / height * 2);
    caster.setFromCamera(ndc, camera);
    let treffer: Vector3 | null = null;
    if (v.cloud && wolkeSichtbar()) {
      const root = v.cloud.object3d;
      const cpu = root ? yield* punktStrahl(root, caster.ray, Math.max(width, height) / (1024 * focalPx), far, stichprobe) : null;
      treffer = cpu?.point ?? null;
      if (cpu === null) {
        try {
          instance.view.camera = camera;
          // Fixes virtuelles Fotobild (lange Seite 1024 px): das Pickfenster
          // muss unabhängig von voller/geteilter Leinwand denselben Winkel sehen.
          // Ohne ViewOffset streckt Giro3D das Foto auf die Engine-Größe; dann
          // kann dieselbe Rasterlücke je nach Panelzustand einen Zielpunkt hinter
          // der Wand statt der Wand liefern. Den Fotopixel mittig in den vorhandenen
          // Renderbuffer abbilden, ohne Engine, Leinwand oder LOD zu verändern.
          const size = instance.engine.getWindowSize();
          const scale = 1024 / Math.max(width, height);
          const px = Math.floor(size.width / 2), py = Math.floor(size.height / 2);
          camera.setViewOffset(width * scale, height * scale, x * scale - px, y * scale - py, size.width, size.height);
          const radius = Math.min(8, px, py, size.width - 1 - px, size.height - 1 - py,
            Math.floor(Math.min(x, width - x, y, height - y) * scale));
          const hits = instance.pickObjectsAt(new Vector2(px, py), { where: [v.cloud], radius, limit: (2 * radius + 1) ** 2 });
          for (const h of hits) if (!treffer || camera.position.distanceTo(h.point) < camera.position.distanceTo(treffer)) treffer = h.point.clone();
        } finally { instance.view.camera = alt; }
      }
    }
    for (const h of meshes.filter((m) => m.visible)) for (const hit of strahlSchnitte(caster, h.occluderMeshes)) {
      if (!treffer || camera.position.distanceTo(hit.point) < camera.position.distanceTo(treffer)) treffer = hit.point.clone();
    }
    return treffer ? [treffer.x, treffer.y, treffer.z] : null;
  }
  const punkt = (bild: OrientedImage, x: number, y: number): Vec3 | null => {
    const lauf = strahl(bild, x, y);
    let schritt = lauf.next();
    while (!schritt.done) schritt = lauf.next();
    return schritt.value;
  };
  const abstand = (bild: OrientedImage, p: Vec3 | null): number | null => p ? Math.hypot(...p.map((x, i) => x - bild.pose.translation[i]!)) : null;
  return {
    punkt,
    async tiefeAsync(bild, pixel, _index, signal) {
      const lauf = strahl(bild, pixel.x, pixel.y, pixel.abstand + Math.max(0.3, 0.02 * pixel.abstand), pixel.abstand);
      let beginn = performance.now();
      for (;;) {
        signal?.throwIfAborted();
        const schritt = lauf.next();
        if (schritt.done) return abstand(bild, schritt.value);
        if (performance.now() - beginn >= 4) {
          await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
          beginn = performance.now();
        }
      }
    },
    tiefe(bild, pixel) {
      const lauf = strahl(bild, pixel.x, pixel.y, pixel.abstand + Math.max(0.3, 0.02 * pixel.abstand), pixel.abstand);
      let schritt = lauf.next();
      while (!schritt.done) schritt = lauf.next();
      return abstand(bild, schritt.value);
    },
  };
}
