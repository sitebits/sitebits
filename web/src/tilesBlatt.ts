// Blattabruf der gekachelten Meshes für das Messen. Der Renderer (Tiles3D) entscheidet nach Bildfehler, welche Kacheln
// geladen sind; REPLACE wartet dabei auf alle Geschwister, die feinste Stufe ist am Klick oft nicht da. Gemessen wird
// deshalb nie auf dem, was gerade gezeichnet wird, sondern auf Blatt-GLBs, die hier gezielt geladen werden: nur
// Positionen und Indizes (Texturen bleiben außen vor), Lage aus der Kachelkette des tileset.json.
import type { BufferGeometry, Material, Mesh } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { istMesh } from './kachelMaterial';
import { glbOhneTexturen } from './meshLayer';
import { GLTF_ZU_Z_OBEN } from './tilesMath';
import type { TilesKnoten } from './tilesMath';
import { PICK_CACHE_BYTES } from './rayPick';

/** Ein Blatt-GLB über dieser Größe wird nicht geladen (Vertrag: < 3 MB; großzügig, aber begrenzt). */
export const BLATT_MAX_BYTES = 16 * 2 ** 20;
/** Zahl der Blätter, die der Messabruf gleichzeitig lädt (je Welle). */
export const BLATT_PARALLEL = 6;
/** Dekodierte Blätter im Speicher: gleiche Grenze wie die Punkt-Knoten des Messens (rayPick.PICK_CACHE_BYTES). */
export const BLATT_CACHE_BYTES = PICK_CACHE_BYTES;

export interface BlattGeo {
  uri: string;
  /** Meshes mit gesetzter matrixWorld (Kachelkette · Y→Z · Knotentransformationen); Material zählt nur für die Seite. */
  meshes: Mesh<BufferGeometry, Material | Material[]>[];
  bytes: number;
  dreiecke: number;
}

export type LadeBytes = (url: string) => Promise<ArrayBuffer>;

/** Standard: gleicher Ursprung, kein Weiterleiten auf fremde Server (connect-src 'self' trägt es zusätzlich). */
export const holeBytes: LadeBytes = async (url) => {
  const antwort = await fetch(url, { credentials: 'same-origin' });
  if (!antwort.ok) { void antwort.body?.cancel().catch(() => undefined); throw new Error(`HTTP ${antwort.status}`); }
  const laenge = Number(antwort.headers.get('content-length'));
  if (Number.isFinite(laenge) && laenge > BLATT_MAX_BYTES) { void antwort.body?.cancel().catch(() => undefined); throw new Error('Blatt-GLB zu groß'); }
  const bytes = await antwort.arrayBuffer();
  if (bytes.byteLength > BLATT_MAX_BYTES) throw new Error('Blatt-GLB zu groß');
  return bytes;
};

const dreieckeVon = (g: BufferGeometry): number => Math.floor((g.index?.count ?? g.attributes.position?.count ?? 0) / 3);

/** GLB → nur Geometrie, in Weltlage. Wirft bei ungültigem GLB, externen Puffern oder fehlender Geometrie. */
export async function ladeBlattGeo(bytes: ArrayBuffer, knoten: Pick<TilesKnoten, 'welt'>, uri: string): Promise<BlattGeo> {
  const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
  const daten = glbOhneTexturen(bytes, true);
  const gltf = await loader.parseAsync(daten, '');
  const szene = gltf.scene;
  // Kachel → Welt, davor die GLB-Achsen (Y oben → Z oben); darunter hängen die Knoten des GLB (Verschiebung der Wurzel).
  szene.matrixAutoUpdate = false;
  szene.matrix.copy(knoten.welt).multiply(GLTF_ZU_Z_OBEN);
  szene.updateMatrixWorld(true);
  const meshes: BlattGeo['meshes'] = []; let dreiecke = 0; const arrays = new Set<ArrayBufferLike>();
  szene.traverse((o) => {
    if (!istMesh(o)) return;
    const g = o.geometry as BufferGeometry;
    // Messen braucht nur Position und Index; alles andere (UV, Farben, Normalen) gleich wieder freigeben.
    for (const name of Object.keys(g.attributes)) if (name !== 'position') g.deleteAttribute(name);
    const pos = g.attributes.position!;
    arrays.add(('data' in pos ? pos.data.array : pos.array).buffer);
    if (g.index) arrays.add(g.index.array.buffer);
    dreiecke += dreieckeVon(g); meshes.push(o);
  });
  if (!meshes.length) throw new Error('Blatt ohne Geometrie');
  return { uri, meshes, bytes: [...arrays].reduce((n, a) => n + a.byteLength, 0), dreiecke };
}

/**
 * Geladene Blätter mit Byte-Grenze (zuletzt benutzt bleibt) und gemeinsamen Abrufen: Läuft ein Abruf in ein Zeitlimit,
 * lädt er weiter und landet im Speicher — der nächste Klick findet das Blatt fertig vor, statt erneut zu starten.
 */
export class BlattSpeicher {
  private readonly fertig = new Map<string, BlattGeo>();
  private readonly laufend = new Map<string, Promise<BlattGeo>>();
  private bytes = 0;
  constructor(private readonly o: { lade: LadeBytes; maxBytes?: number }) {}
  get belegt(): number { return this.bytes; }
  get anzahl(): number { return this.fertig.size; }
  /** Schon geladen (ohne Netz); markiert als zuletzt benutzt. */
  vorhanden(uri: string): BlattGeo | null {
    const g = this.fertig.get(uri);
    if (g) { this.fertig.delete(uri); this.fertig.set(uri, g); }
    return g ?? null;
  }
  laeuft(uri: string): boolean { return this.laufend.has(uri); }
  hole(url: string, uri: string, knoten: Pick<TilesKnoten, 'welt'>): Promise<BlattGeo> {
    const da = this.vorhanden(uri); if (da) return Promise.resolve(da);
    const l = this.laufend.get(uri); if (l) return l;
    const p = this.o.lade(url).then((b) => ladeBlattGeo(b, knoten, uri)).then((g) => { this.ablegen(g); return g; })
      .finally(() => { this.laufend.delete(uri); });
    this.laufend.set(uri, p);
    return p;
  }
  private ablegen(g: BlattGeo): void {
    this.fertig.set(g.uri, g); this.bytes += g.bytes;
    const max = this.o.maxBytes ?? BLATT_CACHE_BYTES;
    // Das neueste Blatt bleibt immer, auch wenn es allein über der Grenze liegt.
    for (const [uri, alt] of this.fertig) {
      if (this.bytes <= max || uri === g.uri) break;
      this.fertig.delete(uri); this.bytes -= alt.bytes;
      for (const m of alt.meshes) m.geometry.dispose();
    }
  }
  leeren(): void {
    for (const g of this.fertig.values()) for (const m of g.meshes) m.geometry.dispose();
    this.fertig.clear(); this.bytes = 0;
  }
}
