import { describe, expect, it, vi } from 'vitest';
import { initialVisibility, layerForKey, lyParam, visibleBounds, createLayerRegistry } from '../src/layers';
import { parseManifest } from '../src/manifest';
import v1 from './fixtures/manifest-v1.json';
import v2 from './fixtures/manifest-v2.json';

const L1 = parseManifest(structuredClone(v1)).layers;
const L2 = parseManifest(structuredClone(v2)).layers;

describe('Ebenen-Register', () => {
  it('Voreinstellung: alle unterstützten sichtbar, nicht unterstützte nie', () => {
    expect(initialVisibility(L1, undefined)).toEqual({ punkte: true, standpunkte: true });
    expect(initialVisibility(L2, undefined)).toEqual({ punkte: true, mesh: false, splat: false });
  });
  it('ly= bestimmt die sichtbaren, unbekannte und nicht unterstützte IDs werden ignoriert', () => {
    expect(initialVisibility(L1, ['standpunkte', 'gibtsnicht'])).toEqual({ punkte: false, standpunkte: true });
    expect(initialVisibility(L2, ['mesh'])).toEqual({ punkte: false, mesh: false, splat: false });
    expect(initialVisibility(L1, [])).toEqual({ punkte: false, standpunkte: false });
  });
  it('ly= nur mit unbekannten IDs (keine passt) fällt auf die Voreinstellung zurück (M7)', () => {
    expect(initialVisibility(L1, ['gibtsnicht'])).toEqual(initialVisibility(L1, undefined));
    expect(initialVisibility(L2, ['auchnicht', 'undauchnicht'])).toEqual(initialVisibility(L2, undefined));
    // Bewusst leeres ly= ("alles aus") bleibt unverändert [] — keine Voreinstellung.
    expect(initialVisibility(L1, [])).not.toEqual(initialVisibility(L1, undefined));
  });
  it('lyParam schreibt nur Abweichungen von der Voreinstellung', () => {
    expect(lyParam(L1, { punkte: true, standpunkte: true })).toBeUndefined();
    expect(lyParam(L1, { punkte: false, standpunkte: true })).toEqual(['standpunkte']);
    expect(lyParam(L1, { punkte: false, standpunkte: false })).toEqual([]);
  });
  it('Taste n → n-te unterstützte Ebene', () => {
    expect(layerForKey(L2, 1)).toBe('punkte');
    expect(layerForKey(L2, 2)).toBeNull();
    expect(layerForKey(L1, 2)).toBe('standpunkte');
  });
});

const meshL = { id: 'mesh', kind: 'mesh', title: 'Mesh', format: 'gltf',
  url: 'model.glb', previewUrl: 'preview.glb', previewColor: 'vertex', triangleCount: 10,
  boundsWorld: [[-2, 0, 0], [1, 2, 1]], positionStepM: 0.001,
  provenance: { kind: 'other', interpolated: true, scaleVerified: false, scaleSource: 'cli' } };
const mp = parseManifest({ ...v2, layers: [meshL, v2.layers[0]] });
describe('Mesh im Ebenen-Register', () => {
  it('ist sichtbar und zählt vor der Wolke', () => {
    expect(initialVisibility(mp.layers, undefined)).toEqual({ mesh: true, punkte: true });
    expect(layerForKey(mp.layers, 1)).toBe('mesh');
    expect(layerForKey(mp.layers, 2)).toBe('punkte');
  });
  it('vereinigt ausschließlich sichtbare Grenzen', () => {
    expect(visibleBounds(mp, () => true)).toEqual([[-2, -1.5, 0], [7.5, 7.5, 4]]);
    expect(visibleBounds(mp, (id) => id === 'mesh')).toEqual(meshL.boundsWorld);
    expect(visibleBounds(mp, () => false)).toBeNull();
  });
  it('leitet Sichtbarkeit und Deckkraft weiter und rahmt das Mesh', () => {
    const h = { id: 'mesh', setVisible: vi.fn(), setOpacity: vi.fn() };
    const v = { cloud: { visible: true, opacity: 1 }, instance: { notifyChange() {}, view: { camera: { fov: 30 } } }, frameAt: vi.fn() };
    const reg = createLayerRegistry({ project: mp, viewer: v as unknown as import('../src/viewer').Viewer,
      stationLayer: null, initial: { punkte: true, mesh: true }, meshes: [h] });
    reg.setVisible('mesh', false); reg.setOpacity('mesh', 0.4); reg.frame('mesh');
    expect(h.setVisible).toHaveBeenLastCalledWith(false);
    expect(h.setOpacity).toHaveBeenLastCalledWith(0.4);
    expect(v.frameAt).toHaveBeenCalledWith([-0.5, 1, 0.5], expect.any(Number));
  });
  it('ausgeblendete Punktwolke wird eingefroren und lädt nicht weiter, eingeblendet wieder frei', () => {
    const v = { cloud: { visible: true, opacity: 1, frozen: false }, instance: { notifyChange() {}, view: { camera: { fov: 30 } } }, frameAt: vi.fn() };
    const reg = createLayerRegistry({ project: mp, viewer: v as unknown as import('../src/viewer').Viewer,
      stationLayer: null, initial: { punkte: true, mesh: true }, meshes: [] });
    reg.setVisible('punkte', false);
    expect(v.cloud).toMatchObject({ visible: false, frozen: true });
    reg.setVisible('punkte', true);
    expect(v.cloud).toMatchObject({ visible: true, frozen: false });
  });
});

const fotoL = (count: number) => ({ id: 'fotos', kind: 'images', title: 'Fotos', url: 'images.json', count });
describe('startVisible: Ebene beim Start aus', () => {
  const ohneWolke = (wert: unknown) => parseManifest({ ...v2, layers: [{ ...v2.layers[0], startVisible: wert }, fotoL(3)] });
  it('false blendet die Ebene in der Voreinstellung aus, true/fehlend nicht', () => {
    expect(initialVisibility(ohneWolke(false).layers, undefined)).toEqual({ punkte: false, fotos: true });
    expect(initialVisibility(ohneWolke(true).layers, undefined)).toEqual({ punkte: true, fotos: true });
    expect(ohneWolke(true).layers[0]).toHaveProperty('startVisible', true);
    expect(L2[0]).not.toHaveProperty('startVisible');
  });
  it('ly= schlägt startVisible; lyParam schreibt nichts, solange die Voreinstellung gilt', () => {
    const l = ohneWolke(false).layers;
    expect(initialVisibility(l, ['punkte', 'fotos'])).toEqual({ punkte: true, fotos: true });
    expect(lyParam(l, { punkte: false, fotos: true })).toBeUndefined();
    expect(lyParam(l, { punkte: true, fotos: true })).toEqual(['punkte', 'fotos']);
  });
  it('kein Wahrheitswert → Manifestfehler', () => {
    expect(() => ohneWolke('nein')).toThrow(/startVisible/);
  });
});

describe('Fotos im Ebenen-Register', () => {
  it('Manifest liest kind images (nicht mehr „nicht unterstützt“)', () => {
    const p = parseManifest({ ...v2, layers: [v2.layers[0], fotoL(3)] });
    expect(p.images).toEqual({ id: 'fotos', kind: 'images', title: 'Fotos', url: 'images.json', count: 3 });
    expect(p.layers[1]).not.toHaveProperty('unsupported');
  });
  it('Manifest weist kaputte Fotos-Ebene ab', () => {
    expect(() => parseManifest({ ...v2, layers: [v2.layers[0], { ...fotoL(3), url: 5 }] })).toThrow(/url/);
    expect(() => parseManifest({ ...v2, layers: [v2.layers[0], { ...fotoL(-1) }] })).toThrow(/count/);
  });
  it('bis 500 Fotos sichtbar, darüber zunächst ausgeblendet (ly= kann sie einschalten)', () => {
    const wenige = parseManifest({ ...v2, layers: [v2.layers[0], fotoL(500)] }).layers;
    const viele = parseManifest({ ...v2, layers: [v2.layers[0], fotoL(501)] }).layers;
    expect(initialVisibility(wenige, undefined)).toEqual({ punkte: true, fotos: true });
    expect(initialVisibility(viele, undefined)).toEqual({ punkte: true, fotos: false });
    expect(initialVisibility(viele, ['fotos'])).toEqual({ punkte: false, fotos: true });
    expect(lyParam(viele, { punkte: true, fotos: false })).toBeUndefined();
  });
  it('leitet Sichtbarkeit und Gruppen an die Fotos-Ebene weiter', () => {
    const p = parseManifest({ ...v2, layers: [v2.layers[0], fotoL(3)] });
    const handle = { setVisible: vi.fn(), setGroupVisible: vi.fn(),
      groups: () => [{ id: 'Nadir', title: 'Nadir', count: 2, visible: true }, { id: 'Fassade', title: 'Fassade', count: 1, visible: false }],
      positions: () => [[0, 0, 0], [2, 4, 6]] as [number, number, number][] };
    const v = { cloud: { visible: true, opacity: 1 }, instance: { notifyChange() {}, view: { camera: { fov: 30 } } }, frameAt: vi.fn() };
    const reg = createLayerRegistry({ project: p, viewer: v as unknown as import('../src/viewer').Viewer,
      stationLayer: null, initial: { punkte: true, fotos: true }, images: handle });
    expect(handle.setVisible).toHaveBeenLastCalledWith(true);
    reg.setVisible('fotos', false);
    expect(handle.setVisible).toHaveBeenLastCalledWith(false);
    const fotos = reg.entries().find((e) => e.id === 'fotos')!;
    expect(fotos.groups?.map((g) => g.id)).toEqual(['Nadir', 'Fassade']);
    reg.setGroupVisible('fotos', 'Fassade', true);
    expect(handle.setGroupVisible).toHaveBeenLastCalledWith('Fassade', true);
    reg.frame('fotos');
    expect(v.frameAt).toHaveBeenCalledWith([1, 2, 3], expect.any(Number));
  });
  it('ohne Handle (images.json 404) ist die Ebene gesperrt', () => {
    const p = parseManifest({ ...v2, layers: [v2.layers[0], fotoL(3)] });
    const v = { cloud: { visible: true, opacity: 1 }, instance: { notifyChange() {}, view: { camera: { fov: 30 } } }, frameAt: vi.fn() };
    const reg = createLayerRegistry({ project: p, viewer: v as unknown as import('../src/viewer').Viewer,
      stationLayer: null, initial: { punkte: true, fotos: true } });
    expect(reg.entries().find((e) => e.id === 'fotos')!.supported).toBe(false);
    expect(reg.isVisible('fotos')).toBe(false);
  });
});
