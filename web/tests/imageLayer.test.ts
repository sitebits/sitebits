// Fotos-Ebene ohne WebGL: echte three-Objekte, Instanz und Bildschirm ersetzt.
import { describe, expect, it, vi } from 'vitest';
import { Group, LineSegments, PerspectiveCamera } from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import { attachImageLayer } from '../src/imageLayer';
import { parseImages } from '../src/images';
import { bild, imagesJson } from './images.test';

function instanz(): Instance {
  // Kamera 50 m über den Fotos, Blick nach unten; Bildschirm 1000 × 1000 px.
  const camera = new PerspectiveCamera(60, 1, 0.1, 1000);
  camera.position.set(400000, 5800000, 80);
  camera.lookAt(400000, 5800000, 0);
  camera.updateMatrixWorld();
  return { threeObjects: new Group(), notifyChange: vi.fn(), view: { camera },
    domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 1000, height: 1000 }) } } as unknown as Instance;
}

const set = parseImages(imagesJson([
  bild('F0001', { pose: { translation: [400000, 5800000, 30], rotationQuat: [1, 0, 0, 0] } }),
  bild('F0002', { group: 'Fassade', pose: { translation: [400005, 5800000, 30], rotationQuat: [1, 0, 0, 0] } }),
]));

describe('Fotos-Ebene', () => {
  it('eine LineSegments-Geometrie je Gruppe, Ursprung als Float64-Position', () => {
    const i = instanz();
    attachImageLayer(i, set, {});
    const linien = i.threeObjects.children[0]!.children
      .filter((c): c is LineSegments => c instanceof LineSegments && c.name !== 'fotos-aktiv');
    expect(linien.map((l) => l.name).sort()).toEqual(['fotos-Fassade', 'fotos-Nadir']);
    for (const l of linien) expect(l.position.x).toBeGreaterThan(399000);
  });
  it('trifft die Spitze unter dem Mauszeiger, nicht in ausgeblendeten Gruppen', () => {
    const i = instanz();
    const layer = attachImageLayer(i, set, {});
    const rechts = (5 / (Math.tan(Math.PI / 6) * 50)) * 500 + 500; // F0002 5 m östlich, 50 m unter der Kamera
    expect(layer.hitTest(500, 500)).toBe('F0001');
    expect(layer.hitTest(rechts, 500)).toBe('F0002');
    layer.setGroupVisible('Fassade', false);
    expect(layer.hitTest(rechts, 500)).toBeNull();
    expect(layer.groups().find((g) => g.id === 'Fassade')!.visible).toBe(false);
    layer.setVisible(false);
    expect(layer.hitTest(500, 500)).toBeNull();
  });
  it('aktive Kamera hervorgehoben, Positionen für das Einrahmen', () => {
    const i = instanz();
    const layer = attachImageLayer(i, set, {});
    layer.setActive('F0002');
    const aktiv = i.threeObjects.children[0]!.children.find((c) => c.name === 'fotos-aktiv')!;
    expect(aktiv.visible).toBe(true);
    layer.setActive(null);
    expect(aktiv.visible).toBe(false);
    expect(layer.positions()).toEqual([[400000, 5800000, 30], [400005, 5800000, 30]]);
  });
});

it('Rahmengröße tauscht Geometrie ohne Neuladen; Treffer dimmen andere Gruppen und respektieren Gruppensichtbarkeit', () => {
  const i = instanz(), layer = attachImageLayer(i, set, {});
  const root = i.threeObjects.children[0]!;
  const gruppe = root.children.find((c) => c.name === 'fotos-Nadir') as LineSegments;
  const geometrieAlt = gruppe.geometry, freigabe = vi.spyOn(geometrieAlt, 'dispose');
  const laenge = (): number => {
    const p = gruppe.geometry.getAttribute('position');
    return Math.hypot(p.getX(1) - p.getX(0), p.getY(1) - p.getY(0), p.getZ(1) - p.getZ(0));
  };
  layer.setTiefe(0.1); const klein = laenge(); layer.setTiefe(20);
  expect(laenge()).toBeCloseTo(klein * 200, 4); expect(freigabe).toHaveBeenCalledOnce();
  layer.setTreffer(new Set(['F0001']));
  const treffer = root.children.find((c) => c.name === 'fotos-treffer') as LineSegments;
  expect(treffer.geometry.getAttribute('position').count).toBe(16);
  expect((gruppe.material as { opacity: number }).opacity).toBeLessThan(0.2);
  layer.setGroupVisible('Nadir', false); expect(treffer.geometry.getAttribute('position').count).toBe(0);
  layer.setTreffer(null); expect((gruppe.material as { opacity: number }).opacity).toBe(0.9);
});
