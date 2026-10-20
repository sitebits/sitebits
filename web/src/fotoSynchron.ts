import { BufferAttribute, BufferGeometry, Line, LineBasicMaterial, Vector3, PerspectiveCamera, Quaternion } from 'three';
import type { Viewer } from './viewer';
import type { PickSource } from './rayPick';
import type { OrientedImage } from './images';

/** Fotozeiger → bestehende PickSource aus Fotopose → echter Oberflächenpunkt, Strahl und Fadenkreuz. */
export function attachFotoSynchron(v: Viewer, picker: PickSource): {
  zeige(bild: OrientedImage, x: number, y: number): void; aus(): void;
} {
  const kreuz = document.createElement('span'); kreuz.className = 'sv-foto-kreuz'; kreuz.textContent = '+'; kreuz.hidden = true;
  kreuz.setAttribute('aria-hidden', 'true'); document.body.append(kreuz);
  const geometry = new BufferGeometry();
  const position = new BufferAttribute(new Float32Array(6), 3); geometry.setAttribute('position', position);
  const linie = new Line(geometry, new LineBasicMaterial({ color: 0xffcc00, depthTest: false }));
  linie.name = 'scanview-foto-strahl';
  linie.visible = false; linie.frustumCulled = false; linie.renderOrder = 902; v.instance.threeObjects.add(linie);
  let frame = 0, generation = 0;
  let treffpunkt: Vector3 | null = null;
  let pending: { bild: OrientedImage; x: number; y: number } | null = null;
  const aus = (): void => { generation++; pending = null; treffpunkt = null; cancelAnimationFrame(frame); frame = 0; kreuz.hidden = true; linie.visible = false; v.instance.notifyChange(linie); };
  const kreuzNachfuehren = (): void => {
    if (!treffpunkt) return;
    const proj = treffpunkt.clone().project(v.instance.view.camera);
    const r = v.instance.domElement.getBoundingClientRect();
    kreuz.hidden = proj.z < -1 || proj.z > 1 || proj.x < -1 || proj.x > 1 || proj.y < -1 || proj.y > 1;
    kreuz.style.left = `${r.left + (proj.x + 1) / 2 * r.width}px`;
    kreuz.style.top = `${r.top + (1 - proj.y) / 2 * r.height}px`;
  };
  v.controls.addEventListener('change', kreuzNachfuehren);
  window.addEventListener('resize', kreuzNachfuehren);
  const render = (): void => {
    frame = 0;
    const p = pending; pending = null;
    if (!p) return;
    const { bild, x, y } = p;
    if (x < 0 || y < 0 || x > bild.intrinsics.width || y > bild.intrinsics.height) { aus(); return; }
    const { instance } = v;
    const alt = instance.view.camera;
    const camera = new PerspectiveCamera(2 * Math.atan(bild.intrinsics.height / 2 / bild.intrinsics.focalPx) * 180 / Math.PI, bild.intrinsics.width / bild.intrinsics.height, 0.01, 250);
    const [w, qx, qy, qz] = bild.pose.rotationQuat;
    camera.position.set(...bild.pose.translation); camera.quaternion.copy(new Quaternion(qx, qy, qz, w)); camera.updateMatrixWorld(true);
    const r = instance.domElement.getBoundingClientRect();
    // pickAtScreen erfasst den Strahl synchron vor seinem ersten await (PickSource-Vertrag).
    const gen = ++generation;
    try {
      instance.view.camera = camera;
      const result = picker.pickAtScreen(r.left + x / bild.intrinsics.width * r.width, r.top + y / bild.intrinsics.height * r.height, { budgetMs: 80 });
      void result.then((hit) => {
        if (gen !== generation) return;
        if (!hit.point) { aus(); return; }
        const ziel = new Vector3(...hit.point);
        treffpunkt = ziel;
        kreuzNachfuehren();
        linie.position.set(...bild.pose.translation);
        position.setXYZ(0, 0, 0, 0); position.setXYZ(1, ziel.x - camera.position.x, ziel.y - camera.position.y, ziel.z - camera.position.z);
        position.needsUpdate = true; linie.visible = true; linie.updateMatrixWorld(true); instance.notifyChange(linie);
      }).catch(() => { if (gen === generation) aus(); });
    } finally { instance.view.camera = alt; }
  };
  return { aus, zeige(bild, x, y) { pending = { bild, x, y }; if (!frame) frame = requestAnimationFrame(render); } };
}
