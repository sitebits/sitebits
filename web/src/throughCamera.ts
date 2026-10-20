// „Blick durch die Kamera“: die 3D-Kamera springt in die Fotopose, das entzerrte Foto liegt als
// Viereck deckungsgleich vor ihr (Ecken = Blickpyramide in 2 m Tiefe, imageMath.cameraFrustumCorners),
// die Deckkraft regelt der Überblendregler im Foto-Fenster.
//
// three/Giro3D-Fakten (vgl. panorama.ts):
//  - View.update() ruft in jedem Frame controls.update(); MapControls endet mit camera.lookAt(target)
//    und würde die Rollachse des Fotos zerstören. Deshalb hängt enter() die Controls ab
//    (view.setControls(null)) und leave() wieder an.
//  - ImageBitmap-Texturen: three ignoriert flipY → v = 0 am oberen Bildrand.
//  - transparent + depthTest:false, renderOrder 500 wie das Panorama; Messmarken liegen darüber.
import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  Quaternion,
  SRGBColorSpace,
  Texture,
  Vector3,
} from 'three';
import type { OrientedImage } from './images';
import { levelUrl } from './images';
import { cameraFrustumCorners, fotoFov, stufeFuer } from './imageMath';
import { smoothstep } from './panoMath';
import type { Viewer } from './viewer';

export const TRANSITION_MS = 400;
const QUAD_ABSTAND_M = 2;

export interface ThroughCamera {
  readonly active: boolean;
  readonly image: OrientedImage | null;
  enter(img: OrientedImage): void;
  leave(): void;
  setBlend(x: number): void;
  blend(): number;
  onChange(cb: () => void): void;
}

export function attachThroughCamera(
  v: Viewer,
  pkgBase: string,
  o: { reduziert(): boolean; notify(text: string): void; fetchFn?: typeof fetch; vorher?(): void },
): ThroughCamera {
  const { instance, controls } = v;
  const camera = instance.view.camera as PerspectiveCamera;
  const fetchFn = o.fetchFn ?? ((u: string) => fetch(u));
  const material = new MeshBasicMaterial({ transparent: true, opacity: 0.85, depthTest: false, depthWrite: false, side: DoubleSide });
  const geometry = new BufferGeometry();
  const quad = new Mesh(geometry, material);
  quad.renderOrder = 500;
  quad.frustumCulled = false;
  quad.visible = false;
  quad.name = 'scanview-foto-blick';
  instance.threeObjects.add(quad);
  const listeners: (() => void)[] = [];
  let image: OrientedImage | null = null;
  let blend = 0.85;
  let saved: { position: Vector3; quaternion: Quaternion; fov: number; near: number; target: Vector3 } | null = null;
  let anim: { from: Vector3; fromQ: Quaternion; t0: number } | null = null;
  let generation = 0;

  const emit = (): void => {
    for (const cb of listeners) cb();
  };
  const render = (): void => {
    quad.updateMatrixWorld(true);
    instance.notifyChange(camera);
  };

  const target = (img: OrientedImage): { p: Vector3; q: Quaternion } => {
    const [w, x, y, z] = img.pose.rotationQuat;
    return { p: new Vector3(...img.pose.translation), q: new Quaternion(x, y, z, w) };
  };

  const step = (): void => {
    if (!anim || !image) return;
    const { p, q } = target(image);
    const t = smoothstep(Math.min(1, (performance.now() - anim.t0) / TRANSITION_MS));
    camera.position.lerpVectors(anim.from, p, t);
    camera.quaternion.slerpQuaternions(anim.fromQ, q, t);
    camera.updateMatrixWorld();
    render();
    if (t < 1) requestAnimationFrame(step);
    else anim = null;
  };

  const setQuad = (img: OrientedImage): void => {
    // Relativ zur Kamera (Float32-Puffer): Ecken minus Fotoposition, Mesh an der Fotoposition.
    const t = img.pose.translation;
    const ecken = cameraFrustumCorners(img, QUAD_ABSTAND_M);
    const pos = new Float32Array(4 * 3);
    ecken.forEach((e, i) => pos.set([e[0] - t[0], e[1] - t[1], e[2] - t[2]], i * 3));
    geometry.setAttribute('position', new BufferAttribute(pos, 3));
    // unten links, unten rechts, oben rechts, oben links; v = 0 oben (ImageBitmap).
    geometry.setAttribute('uv', new BufferAttribute(new Float32Array([0, 1, 1, 1, 1, 0, 0, 0]), 2));
    geometry.setIndex([0, 1, 2, 0, 2, 3]);
    quad.position.set(t[0], t[1], t[2]);
  };

  const loadTexture = async (img: OrientedImage, gen: number): Promise<void> => {
    let url = img.label;
    try {
      const r = instance.domElement.getBoundingClientRect();
      const benoetigt = Math.max(r.width, r.height) * (globalThis.devicePixelRatio || 1);
      url = levelUrl(pkgBase, img, stufeFuer(img.levels, benoetigt));
      const res = await fetchFn(url);
      if (!res.ok) throw new Error(`Status ${res.status}`);
      const bitmap = await createImageBitmap(await res.blob());
      if (gen !== generation) {
        bitmap.close();
        return;
      }
      const tex = new Texture(bitmap);
      tex.colorSpace = SRGBColorSpace;
      tex.needsUpdate = true;
      tex.onUpdate = () => bitmap.close();
      material.map?.dispose();
      material.map = tex;
      material.needsUpdate = true;
      quad.visible = true;
      render();
    } catch (e) {
      if (gen === generation) o.notify(`Foto ${img.label} nicht ladbar (${e instanceof Error ? e.message : String(e)})`);
    }
  };

  const api: ThroughCamera = {
    get active() {
      return image !== null;
    },
    get image() {
      return image;
    },
    enter(img) {
      generation++;
      if (!saved) {
        o.vorher?.();
        saved = { position: camera.position.clone(), quaternion: camera.quaternion.clone(), fov: camera.fov,
          near: instance.view.minNearPlane, target: controls.target.clone() };
        controls.enabled = false;
        instance.view.setControls(null);
      }
      image = img;
      material.map?.dispose();
      material.map = null;
      quad.visible = false;
      setQuad(img);
      instance.view.minNearPlane = 0.01;
      camera.fov = fotoFov(img.intrinsics, camera.aspect);
      camera.updateProjectionMatrix();
      if (o.reduziert()) {
        const { p, q } = target(img);
        camera.position.copy(p);
        camera.quaternion.copy(q);
        anim = null;
        render();
      } else {
        anim = { from: camera.position.clone(), fromQ: camera.quaternion.clone(), t0: performance.now() };
        requestAnimationFrame(step);
      }
      void loadTexture(img, generation);
      emit();
    },
    leave() {
      if (!saved) return;
      generation++;
      anim = null;
      image = null;
      quad.visible = false;
      material.map?.dispose();
      material.map = null;
      camera.position.copy(saved.position);
      camera.quaternion.copy(saved.quaternion);
      camera.fov = saved.fov;
      camera.updateProjectionMatrix();
      instance.view.minNearPlane = saved.near;
      // Alter Drehpunkt zurück (sichtbar seit orbit-drehpunkt): Kamera und Ziel stehen wie vorher.
      controls.target.copy(saved.target);
      instance.view.setControls(controls);
      controls.enabled = true;
      controls.update();
      saved = null;
      render();
      emit();
    },
    setBlend(x) {
      blend = Math.min(1, Math.max(0, x));
      material.opacity = blend;
      render();
      emit();
    },
    blend: () => blend,
    onChange(cb) {
      listeners.push(cb);
    },
  };
  material.opacity = blend;
  return api;
}
