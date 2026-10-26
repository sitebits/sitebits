import { describe, expect, it, vi } from 'vitest';
import { createLayerRegistry, initialVisibility, layerForKey, visibleBounds } from '../src/layers';
import { parseManifest } from '../src/manifest';
import v2 from './fixtures/manifest-v2.json';

const kachel = {
  id: 'kacheln', kind: 'mesh-tiles', title: 'Gesamtmodell', format: '3dtiles', url: 'layers/kacheln/tileset.json', triangleCount: 100,
  boundsWorld: [[-2, 0, 0], [1, 2, 1]], positionStepM: 0.00025, crs: 'EPSG:25833', heightSystem: 'DHHN2016',
  provenance: { kind: 'photogrammetry', interpolated: true, scaleVerified: false, scaleSource: 'cli' },
};
const p = parseManifest({ ...v2, layers: [kachel, v2.layers[0]] });
const viewer = () => ({ cloud: { visible: true, opacity: 1, frozen: false }, instance: { notifyChange() {}, view: { camera: { fov: 30 } } }, frameAt: vi.fn() });

describe('Gekachelte Mesh-Ebene im Ebenen-Register', () => {
  it('ist sichtbar, zählt für Tasten und Grenzen wie ein Mesh', () => {
    expect(initialVisibility(p.layers, undefined)).toEqual({ kacheln: true, punkte: true });
    expect(layerForKey(p.layers, 1)).toBe('kacheln');
    expect(visibleBounds(p, (id) => id === 'kacheln')).toEqual(kachel.boundsWorld);
    expect(visibleBounds(p, () => false)).toBeNull();
  });
  it('leitet Sichtbarkeit und Deckkraft weiter und rahmt auf boundsWorld', () => {
    const h = { id: 'kacheln', setVisible: vi.fn(), setOpacity: vi.fn() }; const v = viewer();
    const reg = createLayerRegistry({ project: p, viewer: v as unknown as import('../src/viewer').Viewer, stationLayer: null,
      initial: { punkte: true, kacheln: true }, meshes: [h] });
    reg.setVisible('kacheln', false); reg.setOpacity('kacheln', 0.4); reg.frame('kacheln');
    expect(h.setVisible).toHaveBeenLastCalledWith(false); expect(h.setOpacity).toHaveBeenLastCalledWith(0.4);
    expect(v.frameAt).toHaveBeenCalledWith([-0.5, 1, 0.5], expect.any(Number));
  });
  it('Eintrag nennt Herkunft (Maßstab, Interpolation) und dass nur auf der feinsten Stufe gemessen wird', () => {
    const reg = createLayerRegistry({ project: p, viewer: viewer() as unknown as import('../src/viewer').Viewer, stationLayer: null,
      initial: { punkte: true, kacheln: true }, meshes: [{ id: 'kacheln', setVisible() {}, setOpacity() {} }] });
    const e = reg.entries().find((x) => x.id === 'kacheln')!;
    expect(e.kind).toBe('mesh-tiles'); expect(e.supported).toBe(true);
    expect(e.notes).toEqual(['Modellmaß – Maßstab nicht belegt', 'Oberfläche teils interpoliert', 'Gekachelt – Messen nur auf der feinsten Stufe']);
    expect(e.genauigkeit).toBeDefined(); expect(e.flaeche).toBeUndefined(); expect(e.shading).toBeUndefined();
  });
  it('gesperrt, wenn die Ebene nicht ladbar war (disabled)', () => {
    const reg = createLayerRegistry({ project: p, viewer: viewer() as unknown as import('../src/viewer').Viewer, stationLayer: null,
      initial: { punkte: true, kacheln: true }, meshes: [{ id: 'kacheln', disabled: true, setVisible() {}, setOpacity() {} }] });
    expect(reg.entries().find((x) => x.id === 'kacheln')).toMatchObject({ supported: false, visible: false });
  });
});
