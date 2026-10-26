import { expect, it } from 'vitest';
import { glbOhneTexturen, prepareMeshGlb } from '../src/meshLayer';
function glb(json: object): ArrayBuffer {
  const bytes = new TextEncoder().encode(JSON.stringify(json));
  const n = Math.ceil(bytes.length / 4) * 4;
  const buffer = new ArrayBuffer(n + 20);
  const view = new DataView(buffer);
  [0x46546c67, 2, buffer.byteLength, n, 0x4e4f534a].forEach((x, i) => view.setUint32(i * 4, x, true));
  new Uint8Array(buffer, 20).fill(32); new Uint8Array(buffer, 20, bytes.length).set(bytes);
  return buffer;
}
it.each(['image/png', 'image/jpeg'])('weist %s vor Blob-Dekodierung zurück', (mimeType) => {
  expect(() => prepareMeshGlb(glb({ images: [{ mimeType, bufferView: 0 }] }))).toThrow('Mesh-Textur nicht KTX2');
});
it('lässt eingebettetes KTX2 und texturfreie Vorschauen durch', () => {
  const buffer = glb({ images: [{ mimeType: 'image/ktx2', bufferView: 0 }] });
  expect(prepareMeshGlb(buffer)).toBe(buffer);
  expect(() => prepareMeshGlb(glb({}))).not.toThrow();
});
it('lädt keine externen Bilder/Geometriepuffer', () => {
  expect(() => prepareMeshGlb(glb({ images: [{ uri: 'https://fremd/texture.ktx2' }] }))).toThrow('eingebettet');
  expect(() => prepareMeshGlb(glb({ buffers: [{ uri: 'buffer.bin' }] }))).toThrow('eingebettet');
});
it('Vorschau ohne GPU-Kompression erhält Geometrie und entfernt Materialtexturen', () => {
  const b = prepareMeshGlb(glb({ meshes: [{ primitives: [{ attributes: { POSITION: 0, COLOR_0: 1 } }] }],
    materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 } } }] }), true);
  const v = new DataView(b);
  const j = JSON.parse(new TextDecoder().decode(new Uint8Array(b, 20, v.getUint32(12, true))));
  expect(j.materials).toEqual([{ extensions: { KHR_materials_unlit: {} } }]);
  expect(j.meshes[0].primitives[0].attributes.COLOR_0).toBe(1);
});
it('weist ungültige GLB-Header zurück', () => {
  expect(() => prepareMeshGlb(new ArrayBuffer(20))).toThrow('Ungültiges GLB');
});

const jsonVon = (b: ArrayBuffer): Record<string, unknown> => JSON.parse(new TextDecoder().decode(new Uint8Array(b, 20, new DataView(b).getUint32(12, true)))) as Record<string, unknown>;
it('glbOhneTexturen erklärt KHR_materials_unlit als benutzt (sonst ignoriert der GLTFLoader es)', () => {
  const j = jsonVon(glbOhneTexturen(glb({ materials: [{ pbrMetallicRoughness: { baseColorTexture: { index: 0 } } }], extensionsUsed: ['KHR_texture_basisu'] })));
  expect(j.extensionsUsed).toEqual(['KHR_texture_basisu', 'KHR_materials_unlit']);
  expect(jsonVon(glbOhneTexturen(glb({}))).extensionsUsed).toEqual(['KHR_materials_unlit']);
});
it('glbOhneTexturen: Seite nur auf Wunsch erhalten (Vorschau bleibt wie bisher einseitig)', () => {
  const quelle = glb({ materials: [{ doubleSided: true }, { doubleSided: false }, {}] });
  expect(jsonVon(glbOhneTexturen(quelle)).materials).toEqual([{ extensions: { KHR_materials_unlit: {} } }, { extensions: { KHR_materials_unlit: {} } }, { extensions: { KHR_materials_unlit: {} } }]);
  expect(jsonVon(glbOhneTexturen(quelle, true)).materials).toEqual([
    { doubleSided: true, extensions: { KHR_materials_unlit: {} } }, { extensions: { KHR_materials_unlit: {} } }, { extensions: { KHR_materials_unlit: {} } }]);
});
it('glbOhneTexturen weist ungültige GLB-Header zurück', () => {
  expect(() => glbOhneTexturen(new ArrayBuffer(20))).toThrow('Ungültiges GLB');
});
