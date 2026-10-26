import { describe, expect, it } from 'vitest';
import { parseManifest, ManifestError } from '../src/manifest';
import v2 from './fixtures/manifest-v2.json';

const kachel = {
  id: 'kacheln', kind: 'mesh-tiles', title: 'Gesamtmodell', format: '3dtiles',
  url: 'layers/kacheln/tileset.json', triangleCount: 9_600_000,
  boundsWorld: [[364990, 5805990, 60], [365010, 5806010, 80]], positionStepM: 0.00025,
  crs: 'EPSG:25833', heightSystem: 'DHHN2016',
  provenance: { kind: 'photogrammetry', interpolated: false, scaleVerified: true, scaleSource: 'gcp' },
};
const schema2 = (extra: object) => ({ ...v2, layers: [v2.layers[0], extra] });

describe('Ebene „Mesh gekachelt“ (mesh-tiles)', () => {
  it('liest die Ebene und behält dasselbe Objekt im Bequemlichkeitsverweis tiles', () => {
    const p = parseManifest(schema2(kachel));
    expect(p.tiles.map((t) => t.id)).toEqual(['kacheln']);
    expect(p.tiles[0]).toBe(p.layers[1]);
    expect(p.tiles[0]).toMatchObject({ kind: 'mesh-tiles', format: '3dtiles', url: 'layers/kacheln/tileset.json', crs: 'EPSG:25833' });
    expect(p.meshes).toEqual([]);
  });
  it('alte Pakete: keine gekachelte Ebene, tiles leer', () => {
    expect(parseManifest(v2).tiles).toEqual([]);
  });
  it('ohne format bleibt die Ebene nicht unterstützt (wie beim Mesh), anderes Format wird abgelehnt', () => {
    expect(parseManifest(schema2({ ...kachel, format: undefined })).layers[1]).toMatchObject({ unsupported: true });
    expect(() => parseManifest(schema2({ ...kachel, format: 'cesium' }))).toThrow(/format/);
  });
  it('kind mesh mit format 3dtiles bleibt unverändert nicht unterstützt', () => {
    expect(parseManifest(schema2({ ...kachel, kind: 'mesh' })).layers[1]).toMatchObject({ unsupported: true });
  });
  it.each([
    { url: 5 }, { url: '' }, { url: 'https://fremd.example/tileset.json' }, { url: '//fremd.example/tileset.json' },
    { url: '/etc/tileset.json' }, { url: '../draussen/tileset.json' }, { url: 'layers/../../x/tileset.json' },
    { url: 'layers\\kacheln\\tileset.json' }, { url: 'layers/kacheln/tileset.glb' }, { url: 'data:application/json,{}' },
    { triangleCount: -1 }, { triangleCount: 1.5 }, { boundsWorld: [[0, 0, 0]] }, { boundsWorld: [[0, 0, 0], [-1, 1, 1]] },
    { positionStepM: 0 }, { positionStepM: Infinity }, { crs: '' }, { crs: undefined }, { heightSystem: undefined },
    { provenance: undefined },
  ])('lehnt kaputte Felder ab: %j', (patch) => {
    expect(() => parseManifest(schema2({ ...kachel, ...patch }))).toThrow(ManifestError);
  });
  it('startVisible wird wie bei anderen Ebenen gelesen', () => {
    expect(parseManifest(schema2({ ...kachel, startVisible: false })).tiles[0]?.startVisible).toBe(false);
    expect(() => parseManifest(schema2({ ...kachel, startVisible: 'nein' }))).toThrow(/startVisible/);
  });
  it('zählt als darstellbare Ebene für initialView.layers', () => {
    const p = parseManifest({ ...schema2(kachel), initialView: { target: [0, 0, 0], distance: 5, layers: ['kacheln'] } });
    expect(p.initialView.layers).toEqual(['kacheln']);
  });
});
