// Schattenkoordinaten ohne float32-Fehler in UTM (Etappe S2).
// three.js rechnet im Vertex-Shader `worldPosition = modelMatrix * position` und danach
// `directionalShadowMatrix * worldPosition` — beides float32. Bei Weltkoordinaten um 5,8 Mio. m liegt das
// float32-Raster bei 0,5 m: Schattenkoordinaten springen um bis zu ±0,25 m, Schattenkanten zittern, Akne.
// Abhilfe: von mvPosition ausgehen (relativ zur Kamera, three rechnet modelViewMatrix auf der CPU in double)
// und eine je Bild auf der CPU (double) gebildete Matrix `schatten.matrix × kamera.matrixWorld` nehmen, die
// direkt vom Sichtraum in die Schattentextur führt. Ihre Translation ist die Schattenkoordinate der Kamera —
// eine kleine Zahl, die float32 genau trägt. Gilt für das eine gerichtete Schattenlicht (die Sonne).
// Der Tiefenpass der Schattenkarte selbst ist unkritisch: auch dort rechnet three modelViewMatrix in double.
import { Matrix4, ShaderChunk } from 'three';
import type { Camera, Material, WebGLProgramParametersWithUniforms } from 'three';

/** Sichtraum → Schattentextur, in double: ziel = schatten × kameraWelt. */
export function schattenAusSicht(schatten: Matrix4, kameraWelt: Matrix4, ziel = new Matrix4()): Matrix4 {
  return ziel.multiplyMatrices(schatten, kameraWelt);
}

// Ersatz für die Schleife über NUM_DIR_LIGHT_SHADOWS in shadowmap_vertex. Normal-Bias im Sichtraum: dieselbe
// Verschiebung wie three in Weltkoordinaten (Drehung erhält Längen). Ohne Normalen-Attribut (flach schattierte
// Meshes) ist transformedNormal null: dann ohne Normal-Bias statt NaN.
const ORIGINAL = /for \( int i = 0; i < NUM_DIR_LIGHT_SHADOWS; i \+\+ \) \{\s*shadowWorldPosition = worldPosition \+ vec4\( shadowWorldNormal \* directionalLightShadows\[ i \]\.shadowNormalBias, 0 \);\s*vDirectionalShadowCoord\[ i \] = directionalShadowMatrix\[ i \] \* shadowWorldPosition;\s*\}/;
const ERSATZ = `for ( int i = 0; i < NUM_DIR_LIGHT_SHADOWS; i ++ ) {
			float svNl = length( transformedNormal );
			vec3 svVersatz = svNl > 0.0 ? transformedNormal / svNl * directionalLightShadows[ i ].shadowNormalBias : vec3( 0.0 );
			vDirectionalShadowCoord[ i ] = svSchattenAusSicht * vec4( mvPosition.xyz + svVersatz, 1.0 );
		}`;

/** Ersetzt den Directional-Teil von shadowmap_vertex; wirft, wenn three den Chunk geändert hat (dann lieber
 *  laut scheitern als still unpräzise schattieren). */
export function patchSchattenVertex(chunk: string): string {
  if (!ORIGINAL.test(chunk)) throw new Error('shadowmap_vertex: Directional-Schleife nicht gefunden (three.js geändert?)');
  return chunk.replace(ORIGINAL, ERSATZ);
}

/** Gemeinsame Uniform aller Sonnen-Materialien; `aktualisieren` je Zeichnen mit der zeichnenden Kamera. */
export interface SchattenUniform {
  readonly uniform: { value: Matrix4 };
  aktualisieren(kamera: Camera): void;
}

export function schattenUniform(schattenMatrix: () => Matrix4): SchattenUniform {
  const uniform = { value: new Matrix4() };
  return { uniform, aktualisieren(kamera) { schattenAusSicht(schattenMatrix(), kamera.matrixWorld, uniform.value); } };
}

/** Material auf präzise Schattenkoordinaten umstellen. Eigener Programmschlüssel, sonst nähme three ein
 *  gleich parametrisiertes ungepatchtes Programm. onBeforeRender läuft nur im Hauptpass, nach dem
 *  Schattenpass desselben Bildes — schatten.matrix ist dann aktuell. */
export function sonnenMaterial<M extends Material>(m: M, s: SchattenUniform): M {
  m.onBeforeCompile = (shader: WebGLProgramParametersWithUniforms) => {
    shader.uniforms.svSchattenAusSicht = s.uniform;
    shader.vertexShader = shader.vertexShader
      .replace('#include <shadowmap_pars_vertex>', '#include <shadowmap_pars_vertex>\nuniform mat4 svSchattenAusSicht;')
      .replace('#include <shadowmap_vertex>', patchSchattenVertex(ShaderChunk.shadowmap_vertex));
  };
  m.customProgramCacheKey = () => 'scanview-sonne-schatten';
  m.onBeforeRender = (_r, _s, kamera) => s.aktualisieren(kamera);
  return m;
}
