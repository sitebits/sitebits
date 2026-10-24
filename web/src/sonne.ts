// Live-Sonne im Viewer (Etappe S2, Schaufenster): Sonnenstand zu Datum/Uhrzeit, gerichtetes Licht mit
// Schattenkarte auf den Mesh-Ebenen, schwaches Himmelslicht, Sonnenbahn als Überlagerung.
//   - Rechnen: sonneMath (NOAA/Meeus, gegen NREL SPA geprüft), Ort/Ortszeit: sonneOrt.
//   - Weltkoordinaten Z oben, X Gitter-Ost; geografischer Azimut wird um die Meridiankonvergenz gedreht.
//   - Präzision: Schattenkoordinaten aus dem Sichtraum (sonneSchatten.ts), sonst ±0,25 m Fehler in UTM.
//   - Punktwolken und Sichtschichten werfen keine Schatten: Giro3D 2.0 zeichnet Punktwolken (mit EDL) in einem
//     eigenen Durchgang, die Schattenkarte der Meshes sieht sie nie; auch castShadow auf allen Points-Knoten
//     ändert kein Pixel (Testpunkt 21.10., Pilotpaket). Ein Ersatz-Mesh für den Schatten folgt.
//   - Zustand und `setzen` sind die Stelle für Ansichtszustand/MCP (API-Parität), Panel nur eine Bedienung davon.
import {
  Box3, BufferGeometry, DirectionalLight, Float32BufferAttribute, Group, HemisphereLight, Line, LineBasicMaterial,
  Mesh, MeshBasicMaterial, SphereGeometry, Vector3,
} from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import { GRENZEN } from './geraet';
import type { MeshLayerHandle } from './meshLayer';
import { sonnenRichtung, sonnenstand, tagesbahn } from './sonneMath';
import type { Sonnenstand } from './sonneMath';
import { aufUntergang, ausOrtszeit, ortszeit } from './sonneOrt';
import type { SonnenOrt } from './sonneOrt';
import { schattenUniform } from './sonneSchatten';

const SONNE_STAERKE = 2.4;
const HIMMEL_STAERKE = 0.55;
/** Zeitraffer: Minuten Sonnenzeit je Sekunde (ein Sommertag in ~17 s). */
export const ZEITRAFFER_MIN_JE_S = 60;
const BAHN_FARBE = 0xffd27a;

export interface SonneZustand {
  an: boolean;
  /** ISO-Zeitpunkt (UTC). */
  zeit: string;
  /** Sonnenstand zur Zeit am Paketort; null ohne Georeferenz. */
  stand: Sonnenstand | null;
  bahn: boolean;
  spielt: boolean;
}

export interface Sonne {
  /** null: Paket ohne Georeferenz, Sonne nicht verfügbar. */
  readonly ort: SonnenOrt | null;
  readonly an: boolean;
  readonly zeit: Date;
  readonly stand: Sonnenstand | null;
  readonly bahn: boolean;
  readonly spielt: boolean;
  readonly aufUnter: { auf: Date | null; unter: Date | null } | null;
  /** Für Ansichtszustand/MCP und Panel: alle Felder optional, nur Gesetztes ändert sich. */
  setzen(z: { an?: boolean; zeit?: Date | string; bahn?: boolean; spielt?: boolean }): void;
  zustand(): SonneZustand;
  onChange(cb: () => void): void;
  dispose(): void;
}

export function erstelleSonne(o: { instance: Instance; ort: SonnenOrt | null; meshes: readonly MeshLayerHandle[]; start?: Date }): Sonne {
  const { instance, ort } = o;
  const listeners: (() => void)[] = [];
  let an = false; let bahn = true; let spielt = false;
  let zeit = new Date(Math.round((o.start ?? new Date()).getTime() / 300_000) * 300_000);
  let stand: Sonnenstand | null = ort ? sonnenstand(zeit, ort.breiteGrad, ort.laengeGrad) : null;
  let aufUnterTag = ''; let aufUnter: { auf: Date | null; unter: Date | null } | null = null;

  // Eigene, ungedrehte Gruppe im Weltursprung: Position und Ziel des Lichts sind Weltkoordinaten (Z oben).
  // three rechnet Licht- und Schattenmatrizen auf der CPU in double; groß sind die Zahlen also nur dort.
  const gruppe = new Group(); gruppe.name = 'scanview-sonne'; gruppe.visible = false;
  const licht = new DirectionalLight(0xfff4e5, SONNE_STAERKE);
  licht.castShadow = true;
  licht.shadow.mapSize.set(GRENZEN.schattenKarte, GRENZEN.schattenKarte);
  // Himmel von oben (Z), nicht von Gitter-Nord (three-Vorgabe Y).
  const himmel = new HemisphereLight(0xdde8f5, 0x5d574c, HIMMEL_STAERKE); himmel.position.set(0, 0, 1);
  gruppe.add(licht, licht.target, himmel);
  // Sonnenbahn: Bogen um die Szenenmitte. Große Verschiebung in position, Ecken relativ (float32).
  const bahnGruppe = new Group(); bahnGruppe.name = 'scanview-sonnenbahn';
  const bahnMaterial = new LineBasicMaterial({ color: BAHN_FARBE, transparent: true, opacity: 0.9, depthWrite: false });
  const wendeMaterial = new LineBasicMaterial({ color: BAHN_FARBE, transparent: true, opacity: 0.3, depthWrite: false });
  const linien = { tag: new Line(new BufferGeometry(), bahnMaterial), sommer: new Line(new BufferGeometry(), wendeMaterial),
    winter: new Line(new BufferGeometry(), wendeMaterial) };
  const punkt = new Mesh(new SphereGeometry(1, 16, 8), new MeshBasicMaterial({ color: 0xffe08a }));
  for (const l of [...Object.values(linien), punkt]) { l.raycast = () => undefined; bahnGruppe.add(l); }
  gruppe.add(bahnGruppe);
  instance.threeObjects.add(gruppe);
  const uniform = schattenUniform(() => licht.shadow.matrix);
  // Schattenkarte nur neu zeichnen, wenn die Mesh-Ebenen im Pass sichtbar sind. Giro3D zeichnet mit
  // Tiefenschattierung (EDL) in Eimern — erst alles unsichtbar, dann Meshes, dann Punktwolken in mehreren
  // Durchgängen, dann Transparentes — und three würde die Karte bei jedem dieser render()-Aufrufe neu
  // zeichnen, meist leer. scene.onBeforeRender läuft vor dem Schattenpass jedes Aufrufs.
  licht.shadow.autoUpdate = false;
  const werfenSichtbar = (): boolean => o.meshes.some((h) => h.occluderMeshes.some((m) =>
    (Array.isArray(m.material) ? m.material : [m.material]).some((x) => x.visible)));
  const szene3 = instance.scene;
  const vorher = szene3.onBeforeRender;
  szene3.onBeforeRender = function (this: typeof szene3, ...args: Parameters<typeof vorher>) {
    vorher.apply(this, args);
    if (an && licht.visible && werfenSichtbar()) licht.shadow.needsUpdate = true;
  };

  const box = new Box3(); const mitte = new Vector3(); let radius = 1; let bahnJahr = NaN; let bahnTag = NaN;
  const szene = (): boolean => {
    box.makeEmpty();
    for (const h of o.meshes) {
      const b = h.visible ? h.worldBox : null;
      if (b) box.union(new Box3(new Vector3(...b[0]), new Vector3(...b[1])));
    }
    if (box.isEmpty()) return false;
    box.getCenter(mitte); radius = Math.max(5, box.getSize(new Vector3()).length() / 2);
    return true;
  };
  const bahnPunkte = (l: Line, tag: Date): void => {
    if (!ort) return;
    const p: number[] = [];
    for (const { stand: s } of tagesbahn(tag, ort.breiteGrad, ort.laengeGrad, 5)) {
      const r = sonnenRichtung(s, ort.konvergenzGrad); p.push(r[0] * radius, r[1] * radius, r[2] * radius);
    }
    l.geometry.dispose(); l.geometry = new BufferGeometry(); l.geometry.setAttribute('position', new Float32BufferAttribute(p, 3));
  };
  const bahnBauen = (): void => {
    const oz = ortszeit(zeit);
    if (oz.jahr === bahnJahr && oz.tagImJahr === bahnTag) return;
    bahnJahr = oz.jahr; bahnTag = oz.tagImJahr;
    // Tagesbahn des UTC-Tags um die Berliner Mittagszeit: deckt in Mitteleuropa den ganzen lichten Tag ab.
    bahnPunkte(linien.tag, ausOrtszeit(oz.jahr, oz.tagImJahr, 12 * 60));
    bahnPunkte(linien.sommer, new Date(Date.UTC(oz.jahr, 5, 21, 12)));
    bahnPunkte(linien.winter, new Date(Date.UTC(oz.jahr, 11, 21, 12)));
  };

  const anwenden = (): void => {
    if (!ort) return;
    stand = sonnenstand(zeit, ort.breiteGrad, ort.laengeGrad);
    const oz = ortszeit(zeit); const tagKey = `${oz.jahr}-${oz.tagImJahr}`;
    if (tagKey !== aufUnterTag) { aufUnterTag = tagKey; aufUnter = aufUntergang(oz.jahr, oz.tagImJahr, ort); }
    gruppe.visible = an;
    if (an && szene()) {
      const r = sonnenRichtung(stand, ort.konvergenzGrad);
      const ueber = stand.hoeheScheinbarGrad > 0;
      // Sonne unter dem Horizont: kein Direktlicht, nur Himmel.
      licht.visible = ueber;
      const d = new Vector3(...r);
      licht.target.position.copy(mitte);
      licht.position.copy(mitte).addScaledVector(d, radius + 10);
      licht.updateMatrixWorld(); licht.target.updateMatrixWorld();
      // Schattenkamera orthografisch, knapp um die Mesh-Bounds im Lichtraum (Texeldichte).
      const kam = licht.shadow.camera;
      kam.position.copy(licht.position); kam.lookAt(mitte); kam.updateMatrixWorld();
      const lichtBox = box.clone().applyMatrix4(kam.matrixWorldInverse);
      const rand = 1;
      kam.left = lichtBox.min.x - rand; kam.right = lichtBox.max.x + rand;
      kam.bottom = lichtBox.min.y - rand; kam.top = lichtBox.max.y + rand;
      kam.near = Math.max(0.1, -lichtBox.max.z - rand); kam.far = -lichtBox.min.z + rand;
      kam.updateProjectionMatrix();
      // Bias in Metern, umgerechnet: Tiefe 2 cm, Normale ~1,5 Texel.
      const texel = Math.max(kam.right - kam.left, kam.top - kam.bottom) / licht.shadow.mapSize.x;
      licht.shadow.bias = -0.02 / (kam.far - kam.near);
      licht.shadow.normalBias = 1.5 * texel;
      bahnGruppe.visible = bahn;
      bahnGruppe.position.copy(mitte); bahnGruppe.updateMatrixWorld(true);
      bahnBauen();
      punkt.visible = ueber; punkt.scale.setScalar(radius * 0.025);
      punkt.position.set(r[0] * radius, r[1] * radius, r[2] * radius);
      gruppe.updateMatrixWorld(true);
    }
    instance.notifyChange();
    listeners.forEach((cb) => cb());
  };
  const meshesSchalten = (): void => {
    // Schattenkarte einmal anschalten und dann lassen: Umschalten verlangte neu übersetzte Shader überall.
    if (an) instance.renderer.shadowMap.enabled = true;
    for (const h of o.meshes) h.setSonne(an ? uniform : null);
  };
  // Mesh-Wechsel (Vollstufe, Sichtbarkeit) ändern die Bounds der Schattenkamera.
  for (const h of o.meshes) h.onChange(() => { if (an) anwenden(); });

  let rahmen = 0; let letzt = 0;
  const schritt = (jetzt: number): void => {
    if (!spielt) return;
    const dt = Math.min(0.1, (jetzt - letzt) / 1000); letzt = jetzt;
    let t = zeit.getTime() + dt * ZEITRAFFER_MIN_JE_S * 60_000;
    // Nur der lichte Tag: nach Untergang wieder beim Aufgang desselben Tages.
    if (aufUnter?.auf && aufUnter.unter && t > aufUnter.unter.getTime()) t = aufUnter.auf.getTime();
    zeit = new Date(t); anwenden();
    rahmen = requestAnimationFrame(schritt);
  };

  const sonne: Sonne = {
    ort,
    get an() { return an; }, get zeit() { return zeit; }, get stand() { return stand; },
    get bahn() { return bahn; }, get spielt() { return spielt; }, get aufUnter() { return aufUnter; },
    setzen(z) {
      if (!ort) return;
      if (z.zeit !== undefined) { const t = new Date(z.zeit); if (Number.isFinite(t.getTime())) zeit = t; }
      if (z.bahn !== undefined) bahn = z.bahn;
      if (z.an !== undefined && z.an !== an) {
        an = z.an; meshesSchalten();
        // Schattenkarte (bis 128 MiB) beim Ausschalten freigeben; three legt sie beim nächsten Einschalten neu an.
        if (!an) { licht.shadow.map?.dispose(); licht.shadow.map = null; }
      }
      if (z.spielt !== undefined && z.spielt !== spielt) {
        spielt = z.spielt && an;
        cancelAnimationFrame(rahmen);
        if (spielt) { letzt = performance.now(); rahmen = requestAnimationFrame(schritt); }
      }
      if (!an && spielt) { spielt = false; cancelAnimationFrame(rahmen); }
      anwenden();
    },
    zustand: () => ({ an, zeit: zeit.toISOString(), stand, bahn, spielt }),
    onChange(cb) { listeners.push(cb); },
    dispose() {
      cancelAnimationFrame(rahmen); gruppe.removeFromParent(); licht.dispose(); szene3.onBeforeRender = vorher;
      for (const l of Object.values(linien)) l.geometry.dispose();
      bahnMaterial.dispose(); wendeMaterial.dispose(); punkt.geometry.dispose(); punkt.material.dispose();
    },
  };
  if (ort) anwenden();
  return sonne;
}
