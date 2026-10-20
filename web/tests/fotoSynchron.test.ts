import { expect, it, vi } from 'vitest';
import { Group, PerspectiveCamera, Vector3 } from 'three';
import { attachFotoSynchron } from '../src/fotoSynchron';
import type { Viewer } from '../src/viewer';
import type { PickSource } from '../src/rayPick';
import type { OrientedImage } from '../src/images';

it('Fotozeiger nutzt reale Pick-Treffer und lässt sich nach abgebrochenem Frame erneut starten', async () => {
  const frames = new Map<number, FrameRequestCallback>(); let nummer = 0;
  const span = { style: {} as Record<string, string>, hidden: true, setAttribute: vi.fn() };
  vi.stubGlobal('document', { createElement: () => span, body: { append: vi.fn() } });
  vi.stubGlobal('window', { addEventListener: vi.fn() });
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frames.set(++nummer, cb); return nummer; });
  vi.stubGlobal('cancelAnimationFrame', (n: number) => frames.delete(n));
  try {
    const camera = new PerspectiveCamera(); camera.position.set(0, 0, 30); camera.lookAt(0, 0, 0); camera.updateMatrixWorld(true);
    const view = { camera }, threeObjects = new Group();
    const v = { instance: { view, threeObjects, notifyChange: vi.fn(), domElement: { getBoundingClientRect: () => ({ left: 0, top: 0, width: 800, height: 600 }) } }, controls: { addEventListener: vi.fn() } } as unknown as Viewer;
    const info = { level: 'fine' as const, ms: 0, wideCandidates: 1, nearCandidates: 1 };
    const pick = vi.fn<PickSource['pickAtScreen']>(() => Promise.resolve({ point: [0, 0, 0], info }));
    const picker = { pickAtScreen: pick } as unknown as PickSource;
    const bild: OrientedImage = { id: 'F0001', label: 'Test', group: 'g', pose: { translation: [0, 0, 10], rotationQuat: [1, 0, 0, 0] }, intrinsics: { width: 1000, height: 800, focalPx: 1000 }, levels: [512], rev: null, tiles: null };
    const s = attachFotoSynchron(v, picker);
    s.zeige(bild, 500, 400); s.aus(); expect(frames.size).toBe(0);
    s.zeige(bild, 500, 400); expect(frames.size).toBe(1);
    for (const cb of frames.values()) cb(0); frames.clear();
    await Promise.resolve();
    expect(pick).toHaveBeenCalledOnce(); expect(view.camera).toBe(camera);
    const strahl = threeObjects.children[0]!;
    expect(strahl.visible).toBe(true); expect(strahl.position.equals(new Vector3(0, 0, 10))).toBe(true);
    s.aus(); expect(strahl.visible).toBe(false); expect(span.hidden).toBe(true);
    pick.mockResolvedValue({ point: null, info });
    s.zeige(bild, 500, 400); for (const cb of frames.values()) cb(0); frames.clear(); await Promise.resolve();
    expect(strahl.visible).toBe(false); // Kein Ersatzpunkt in geschätzter Tiefe.
  } finally { vi.unstubAllGlobals(); }
});
