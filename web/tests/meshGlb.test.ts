import { expect, it } from 'vitest';
import { prepareMeshGlb } from '../src/meshLayer';
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
