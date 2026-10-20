// KTX2-Texturen aus dem GLB-Puffer ohne blob:-URL. Der eingebaute GLTFLoader-Weg legt für
// eingebettete Bilder eine blob:-URL an und lädt sie per fetch — die CSP (connect-src 'self')
// blockiert das, und GLTFLoader verwirft die Textur dann stumm (Mesh zeigt nur Vertexfarben).
// Gleicher Name wie das eingebaute Plugin: ersetzt es (GLTFLoader.parse, plugins[name]).
import {
  LinearFilter, LinearMipmapLinearFilter, LinearMipmapNearestFilter, NearestFilter,
  NearestMipmapLinearFilter, NearestMipmapNearestFilter, RepeatWrapping, ClampToEdgeWrapping,
  MirroredRepeatWrapping,
} from 'three';
import type { CompressedTexture, MagnificationTextureFilter, MinificationTextureFilter, Texture, Wrapping } from 'three';
import type { GLTFLoaderPlugin, GLTFParser } from 'three/examples/jsm/loaders/GLTFLoader.js';
import type { KTX2Loader } from 'three/examples/jsm/loaders/KTX2Loader.js';

const FILTER: Record<number, MinificationTextureFilter> = {
  9728: NearestFilter, 9729: LinearFilter, 9984: NearestMipmapNearestFilter,
  9985: LinearMipmapNearestFilter, 9986: NearestMipmapLinearFilter, 9987: LinearMipmapLinearFilter,
};
const WRAP: Record<number, Wrapping> = { 33071: ClampToEdgeWrapping, 33648: MirroredRepeatWrapping, 10497: RepeatWrapping };

type Sampler = { magFilter?: number; minFilter?: number; wrapS?: number; wrapT?: number };
interface Json {
  textures?: { sampler?: number; name?: string; extensions?: Record<string, { source?: number } | undefined> }[];
  images?: { bufferView?: number; uri?: string; name?: string }[];
  samplers?: Sampler[];
}

/**
 * Sampler wie GLTFParser.loadTextureImage (glTF-Standardwerte bei fehlendem Sampler). Ohne Mip-Stufen
 * in der KTX2-Datei wäre ein Mipmap-Filter unvollständig (schwarze Textur) → linear.
 */
export function samplerAnwenden(t: Texture, s: Sampler, mipStufen: number): void {
  t.flipY = false;
  t.magFilter = (FILTER[s.magFilter ?? -1] ?? LinearFilter) as MagnificationTextureFilter;
  const min = FILTER[s.minFilter ?? -1] ?? LinearMipmapLinearFilter;
  t.minFilter = mipStufen > 1 || min === NearestFilter || min === LinearFilter ? min : LinearFilter;
  t.wrapS = WRAP[s.wrapS ?? -1] ?? RepeatWrapping;
  t.wrapT = WRAP[s.wrapT ?? -1] ?? RepeatWrapping;
  t.generateMipmaps = false;
}

export function ktx2EingebettetPlugin(ktx2: KTX2Loader): (parser: GLTFParser) => GLTFLoaderPlugin {
  return (parser) => {
    const cache = new Map<number, Promise<Texture>>();
    return {
      name: 'KHR_texture_basisu',
      loadTexture(textureIndex: number): Promise<Texture> | null {
        const json = parser.json as Json;
        const def = json.textures?.[textureIndex];
        const source = def?.extensions?.KHR_texture_basisu?.source;
        if (def === undefined || source === undefined) return null;
        const bild = json.images?.[source];
        // prepareMeshGlb lässt nur eingebettete Bilder zu; ohne bufferView gibt es nichts zu tun.
        if (bild?.bufferView === undefined) return null;
        const vorhanden = cache.get(textureIndex);
        if (vorhanden) return vorhanden;
        const geladen = (parser.getDependency('bufferView', bild.bufferView) as Promise<ArrayBuffer>)
          .then((puffer) => new Promise<CompressedTexture>((ok, fehler) => {
            ktx2.parse(puffer, (t) => ok(t as CompressedTexture), fehler);
          }))
          .then((t) => {
            samplerAnwenden(t, json.samplers?.[def.sampler ?? -1] ?? {}, t.mipmaps?.length ?? 0);
            t.name = def.name ?? bild.name ?? '';
            parser.associations.set(t, { textures: textureIndex });
            return t;
          });
        cache.set(textureIndex, geladen);
        return geladen;
      },
    };
  };
}
