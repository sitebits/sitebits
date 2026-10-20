// „Blick durch die Kamera“ ohne WebGL: echte three-Kamera, Viewer und Netz ersetzt.
import { describe, expect, it, vi } from 'vitest';
import { Group, PerspectiveCamera, Quaternion, Vector3 } from 'three';
import { attachThroughCamera } from '../src/throughCamera';
import { parseImages } from '../src/images';
import { bild, imagesJson } from './images.test';
import type { Viewer } from '../src/viewer';

function viewer() {
  const camera = new PerspectiveCamera(50, 2, 0.1, 1000);
  camera.position.set(400010, 5800010, 60);
  camera.lookAt(400000, 5800000, 0);
  const controls = { enabled: true, target: new Vector3(), update: vi.fn() };
  const view = { camera, minNearPlane: 0.5, setControls: vi.fn() };
  const instance = { threeObjects: new Group(), notifyChange: vi.fn(), view,
    domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 500 }) } };
  return { v: { instance, controls } as unknown as Viewer, camera, controls, view, instance };
}

const q = new Quaternion().setFromAxisAngle(new Vector3(0.3, 0.2, 1).normalize(), 0.9);
const img = parseImages(imagesJson([bild('F0001', {
  pose: { translation: [400000, 5800000, 30], rotationQuat: [q.w, q.x, q.y, q.z] } })])).images[0]!;

describe('Blick durch die Kamera', () => {
  it('Kamera exakt in der Fotopose (mit Rollwinkel), Controls abgehängt, danach alles zurück', async () => {
    const { v, camera, controls, view } = viewer();
    controls.target.set(400001, 5800002, 3);
    const vorher = { p: camera.position.clone(), q: camera.quaternion.clone(), fov: camera.fov };
    const fetchFn = vi.fn(async () => new Response('x', { status: 404 }));
    const notify = vi.fn();
    const d = attachThroughCamera(v, 'https://x/data/p/', { reduziert: () => true, notify, fetchFn: fetchFn as unknown as typeof fetch });
    d.enter(img);
    expect(d.active).toBe(true);
    expect(camera.position.toArray()).toEqual([400000, 5800000, 30]);
    expect(camera.quaternion.angleTo(q)).toBeLessThan(1e-12);
    expect(view.setControls).toHaveBeenLastCalledWith(null);
    expect(controls.enabled).toBe(false);
    // Foto passt vollständig ins 2:1-Fenster: vertikales FOV aus der Fotohöhe (1040 / 2800).
    expect(camera.fov).toBeCloseTo((2 * Math.atan(1040 / 2800) * 180) / Math.PI, 9);
    await vi.waitFor(() => expect(notify).toHaveBeenCalledWith(expect.stringContaining('nicht ladbar')));
    expect(fetchFn).toHaveBeenCalledWith('https://x/data/p/fotos/F0001/2048.jpg');
    d.leave();
    expect(d.active).toBe(false);
    expect(camera.position.distanceTo(vorher.p)).toBeLessThan(1e-9);
    expect(camera.quaternion.angleTo(vorher.q)).toBeLessThan(1e-9);
    expect(camera.fov).toBe(vorher.fov);
    expect(view.setControls).toHaveBeenLastCalledWith(controls);
    expect(controls.enabled).toBe(true);
    expect(view.minNearPlane).toBe(0.5);
    // Der Drehpunkt (sichtbar seit orbit-drehpunkt) springt nicht.
    expect(controls.target.toArray()).toEqual([400001, 5800002, 3]);
  });
  it('Überblendung begrenzt auf 0 … 1, zweites enter behält die Ausgangsansicht', () => {
    const { v, camera } = viewer();
    const vorher = camera.position.clone();
    const d = attachThroughCamera(v, 'https://x/data/p/', { reduziert: () => true, notify: vi.fn(),
      fetchFn: (async () => new Response('x', { status: 404 })) as unknown as typeof fetch });
    d.setBlend(2);
    expect(d.blend()).toBe(1);
    d.setBlend(-1);
    expect(d.blend()).toBe(0);
    d.enter(img);
    d.enter(img);
    d.leave();
    expect(camera.position.distanceTo(vorher)).toBeLessThan(1e-9);
  });
});
