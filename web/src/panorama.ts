// Panorama-Modus: Kamera exakt in der Stationspose, nur Drehen/Zoomen (FOV).
// Je Würfelseite ein Viereck, aufgespannt aus Bildpose + Intrinsik (panoMath.faceQuad) — keine
// CubeTexture, keine Pixel-Umkopie. Stufen (panoLoader): erst die kleinste für alle sechs
// Seiten, dann die Zielstufe der sichtbaren Seiten nach Bildschirmauflösung unter dem Texturbudget.
// Überblendung = Deckkraft der Vierecke über der weiter gerenderten Punktwolke (Giro3D lädt
// und pickt nur sichtbare Punkte). Sprung: 400 ms Kamerafahrt durch die Wolke, dann
// Einblenden. Vom Sprung bis zum ersten Bild hält viewer.setStreamingPaused das Nachladen der
// Punktwolke an. Nachbarn als Bodenmarken (stationLayer).
//
// three/Giro3D-Fakten:
//  - ImageBitmap-Texturen: three ignoriert flipY → v = 0 am oberen Bildrand (faceQuad.uvs). Das Bitmap
//    wird nach dem Hochladen geschlossen (onUpdate) — nach einem Kontextverlust lädt reset() neu.
//  - transparent + depthTest:false → TRANSPARENT-Eimer nach dem Punktwolken-/EDL-Pass; renderOrder 500
//    (Standpunkte 900, Messmarken 998/999 liegen darüber). frustumCulled=false; der Abstand der
//    Vierecke folgt near/far (panoRadius) im 'before-render' (dann stehen near/far fest). Skaliert wird
//    je Viereck, nicht die Gruppe: der Abstand Bildposition ↔ Scannerzentrum bleibt so in Metern und
//    wächst nicht mit dem Radius (Referenzpaket Kirche: 0, aber der Vertrag erlaubt ihn).
//  - Giro3D ruft in jedem Frame controls.update(); MapControls bleibt registriert, aber deaktiviert, sein
//    target liegt 1 m voraus (viewer.ts leitet daraus die Near-Plane ab: nearPlaneFor(1 m) = 1 cm).
//  - Giro3D pflegt matrixWorld selbst → updateMatrixWorld(true) vor notifyChange.
import { GRENZEN } from './geraet';
import {
  BufferAttribute,
  BufferGeometry,
  DoubleSide,
  Group,
  LinearFilter,
  Mesh,
  MeshBasicMaterial,
  PerspectiveCamera,
  SRGBColorSpace,
  Texture,
  Vector3,
} from 'three';
import type { Vec3 } from './manifest';
import { PanoLoader } from './panoLoader';
import type { LevelRequest, LoaderEvent } from './panoLoader';
import {
  PANO_FOV_DEFAULT,
  angleDeg,
  clampFov,
  clampPitch,
  dirFromYawPitch,
  faceQuad,
  faceVisible,
  normalizeYaw,
  panoRadius,
  planFaceLevels,
  requiredLevel,
  smoothstep,
  yawPitchFromDir,
  yawPitchTo,
} from './panoMath';
import type { FaceQuad } from './panoMath';
import type { StationLayer } from './stationLayer';
import { FACES, levelUrl } from './stations';
import type { FaceName, Station } from './stations';
import type { Viewer } from './viewer';

export const TEXTURE_BUDGET_BYTES = GRENZEN.panoBytes;
/** Fahrt zum neuen Standpunkt; bei reduzierter Bewegung entfällt sie (Schnitt). */
export const TRANSITION_MS = 400;
export const FADE_IN_MS = 200;
/** Obergrenze, wie lange ein Sprung das Punktwolken-Nachladen anhält (kommt kein erstes Bild,
 *  z. B. alle Seiten 404, läuft die Wolke danach trotzdem weiter). */
export const STREAM_PAUSE_MAX_MS = 2000;
const WHEEL_ZOOM = 1.0015;
const bytesOf = (level: number): number => level * level * 4; // RGBA, ohne Mipmaps

export interface PanoView {
  yaw: number;
  pitch: number;
  fov: number;
}

export interface PanoState extends PanoView {
  station: string;
}

export interface PanoStats {
  jumps: number;
  jumpStartMs: number | null;
  firstImageMs: number | null;
  sharpMs: number | null;
  restores: number;
  failed: string[];
  shown: Partial<Record<FaceName, number>>;
  target: Partial<Record<FaceName, number>>;
}

export interface Panorama {
  readonly active: boolean;
  readonly state: PanoState | null;
  readonly blend: number;
  readonly stats: PanoStats;
  readonly textureBytes: number;
  textureKeys(): string[];
  enter(id: string, view?: Partial<PanoView>): boolean;
  leave(): void;
  setView(view: Partial<PanoView>): void;
  lookAt(world: Vec3): void;
  /** Umsehen per Ziehen sperren (Eckpunkt ziehen); leave() hebt die Sperre auf. */
  setLookLocked(on: boolean): void;
  setBlend(alpha: number): void;
  onChange(cb: () => void): void;
}

export function attachPanorama(
  v: Viewer,
  pkgBase: string,
  stations: readonly Station[],
  layer: StationLayer,
  notify: (text: string) => void,
  /** reduziert: true = Bewegung reduzieren — ein Standpunktwechsel springt als Schnitt statt als Fahrt. */
  o: { reduziert?: () => boolean; onOverlay?: (opacity: number) => void } = {},
): Panorama {
  const { instance, controls } = v;
  const cam = instance.view.camera;
  if (!(cam instanceof PerspectiveCamera)) throw new Error('Panorama-Modus braucht eine Perspektivkamera.');
  const camera: PerspectiveCamera = cam; // Typ bleibt auch in den (gehobenen) inneren Funktionen eingeengt
  const byId = new Map(stations.map((s) => [s.id, s]));

  const group = new Group();
  group.name = 'scanview-panorama';
  group.visible = false;
  instance.threeObjects.add(group);
  const meshes = new Map<FaceName, Mesh<BufferGeometry, MeshBasicMaterial>>();
  for (const f of FACES) {
    const mat = new MeshBasicMaterial({
      transparent: true,
      opacity: 0,
      depthTest: false,
      depthWrite: false,
      side: DoubleSide,
      toneMapped: false,
    });
    mat.visible = false;
    const mesh = new Mesh(new BufferGeometry(), mat);
    mesh.frustumCulled = false;
    mesh.renderOrder = 500;
    mesh.name = `pano-${f}`;
    group.add(mesh);
    meshes.set(f, mesh);
  }

  let state: PanoState | null = null;
  let blend = 1;
  let savedFov = camera.fov;
  let anim: { from: Vector3; t0: number } | null = null;
  let arrivedAt: number | null = null;
  let raf: number | null = null;
  const quads = new Map<FaceName, FaceQuad>();
  const listeners: (() => void)[] = [];
  const stats: PanoStats = {
    jumps: 0,
    jumpStartMs: null,
    firstImageMs: null,
    sharpMs: null,
    restores: 0,
    failed: [],
    shown: {},
    target: {},
  };

  // Vom Sprung bis zum ersten Bild gehen keine neuen COPC-Anfragen ans Netz — sonst teilen
  // sich die Panorama-Seiten die (gedrosselte) Bandbreite mit dem Nachladen der Punktwolke entlang der
  // Fahrt. Freigabe beim ersten Bild, beim Verlassen und spätestens nach STREAM_PAUSE_MAX_MS.
  let pauseTimer: ReturnType<typeof setTimeout> | null = null;
  const resumeStreaming = (): void => {
    if (pauseTimer !== null) clearTimeout(pauseTimer);
    pauseTimer = null;
    v.setStreamingPaused(false);
  };
  const pauseStreaming = (): void => {
    if (pauseTimer !== null) clearTimeout(pauseTimer);
    v.setStreamingPaused(true);
    pauseTimer = setTimeout(resumeStreaming, STREAM_PAUSE_MAX_MS);
  };

  const station = (): Station | null => (state ? (byId.get(state.station) ?? null) : null);
  const emit = (): void => listeners.forEach((cb) => cb());

  const loader: PanoLoader<ImageBitmap, Texture> = new PanoLoader<ImageBitmap, Texture>(
    {
      fetchImage: async (url, signal) => {
        const res = await fetch(url, { signal, priority: 'high' });
        if (!res.ok) {
          void res.body?.cancel().catch(() => undefined);
          throw new Error(`HTTP ${res.status}`);
        }
        return createImageBitmap(await res.blob());
      },
      makeTexture: (bmp) => {
        const t = new Texture(bmp);
        t.colorSpace = SRGBColorSpace;
        t.flipY = false;
        t.generateMipmaps = false;
        t.minFilter = LinearFilter;
        t.magFilter = LinearFilter;
        t.onUpdate = () => bmp.close(); // nach dem Hochladen nur noch im Grafikspeicher
        t.needsUpdate = true;
        return t;
      },
      discardImage: (bmp) => bmp.close(),
      disposeTexture: (t) => {
        t.dispose();
        (t.image as ImageBitmap | null)?.close();
      },
      bytesOf,
    },
    TEXTURE_BUDGET_BYTES,
    (e: LoaderEvent) => {
      if (e.type === 'failed') {
        stats.failed.push(`${e.station}/${e.face}/${e.level}: ${e.reason}`);
        notify(`Panorama ${e.station}: Bild ${e.face}/${e.level} nicht ladbar (${e.reason})`);
      }
      applyTextures();
      kick();
    },
  );

  const cameraPosition = (s: Station): Vector3 => {
    const to = new Vector3(...s.pose.translation);
    if (!anim) return to;
    return anim.from.clone().lerp(to, smoothstep((performance.now() - anim.t0) / TRANSITION_MS));
  };

  const placeCamera = (): void => {
    const s = station();
    if (!s || !state) return;
    const pos = cameraPosition(s);
    const d = dirFromYawPitch(state.yaw, state.pitch);
    camera.up.set(0, 0, 1);
    camera.position.copy(pos);
    camera.lookAt(pos.x + d[0], pos.y + d[1], pos.z + d[2]);
    camera.fov = state.fov;
    camera.updateProjectionMatrix();
    controls.target.set(pos.x + d[0], pos.y + d[1], pos.z + d[2]);
    instance.notifyChange(camera);
  };

  const buildGeometry = (s: Station): void => {
    quads.clear();
    for (const f of s.panorama!.faces) {
      const q = faceQuad(f.image);
      quads.set(f.name, q);
      const g = new BufferGeometry();
      g.setAttribute('position', new BufferAttribute(new Float32Array(q.corners.flat()), 3));
      g.setAttribute('uv', new BufferAttribute(new Float32Array(q.uvs.flat()), 2));
      g.setIndex([0, 3, 2, 0, 2, 1]);
      const mesh = meshes.get(f.name)!;
      mesh.geometry.dispose();
      mesh.geometry = g;
      const t = f.image.pose.translation;
      const o = s.pose.translation;
      mesh.position.set(t[0] - o[0], t[1] - o[1], t[2] - o[2]);
    }
    group.position.set(...s.pose.translation);
    group.updateMatrixWorld(true);
  };

  const visibleFaces = (): FaceName[] => Object.keys(stats.target) as FaceName[];

  const updateOpacity = (): void => {
    const f = anim ? 0 : arrivedAt === null ? 0 : smoothstep((performance.now() - arrivedAt) / FADE_IN_MS);
    for (const m of meshes.values()) m.material.opacity = blend * f;
    group.visible = state !== null;
    o.onOverlay?.(state ? blend * f : 0);
    group.updateMatrixWorld(true);
    instance.notifyChange(group);
  };

  const applyTextures = (): void => {
    const s = station();
    for (const f of FACES) {
      const mesh = meshes.get(f)!;
      const best = s?.panorama ? loader.best(s.id, f, s.panorama.levels) : null;
      const map = best?.texture ?? null;
      if (mesh.material.map !== map) {
        mesh.material.map = map;
        mesh.material.needsUpdate = true;
      }
      mesh.material.visible = map !== null;
      stats.shown[f] = best?.level ?? 0;
    }
    updateOpacity();
  };

  const plan = (): void => {
    const s = station();
    if (!s?.panorama || !state) return;
    const p = s.panorama;
    const d = dirFromYawPitch(state.yaw, state.pitch);
    const el = instance.domElement;
    const aspect = el.clientWidth / Math.max(1, el.clientHeight);
    const vis = p.faces
      .filter((f) => faceVisible(d, quads.get(f.name)!.center, state!.fov, aspect))
      .map((f) => ({ face: f.name, angleDeg: angleDeg(d, quads.get(f.name)!.center) }))
      .sort((a, b) => a.angleDeg - b.angleDeg);
    const target = requiredLevel(p.levels, state.fov, el.height);
    const levels = planFaceLevels(vis, target, p.levels, bytesOf, TEXTURE_BUDGET_BYTES);
    const base = p.levels[0]!;
    // Alle sechs Seiten in der kleinsten Stufe, dann die Zielstufen der sichtbaren. Eine Umordnung
    // „sichtbare 512er zuerst“ brachte gedrosselt keinen durchgängigen Vorteil beim ersten Bild.
    const reqs: LevelRequest[] = p.faces.map((f) => ({
      station: s.id,
      face: f.name,
      level: base,
      url: levelUrl(pkgBase, p, f, base),
    }));
    for (const { face } of vis) {
      const lv = levels.get(face)!;
      const f = p.faces.find((x) => x.name === face)!;
      if (lv > base) reqs.push({ station: s.id, face, level: lv, url: levelUrl(pkgBase, p, f, lv) });
    }
    stats.target = Object.fromEntries(vis.map(({ face }) => [face, levels.get(face)!]));
    loader.want(reqs);
    applyTextures();
  };

  const tick = (): void => {
    raf = null;
    const now = performance.now();
    if (anim && now - anim.t0 >= TRANSITION_MS) anim = null;
    placeCamera();
    const vis = visibleFaces();
    if (!anim && arrivedAt === null && vis.length > 0 && vis.every((f) => (stats.shown[f] ?? 0) > 0)) arrivedAt = now;
    updateOpacity();
    if (anim || (arrivedAt !== null && now - arrivedAt < FADE_IN_MS)) kick();
  };
  const kick = (): void => {
    if (raf === null && state) raf = requestAnimationFrame(tick);
  };

  // Zeitmessung nach jedem tatsächlich gezeichneten Frame.
  instance.addEventListener('after-render', () => {
    const s = station();
    if (!s || stats.jumpStartMs === null) return;
    const vis = visibleFaces();
    if (vis.length === 0) return;
    const now = performance.now();
    // Einblenden begonnen (nicht Deckkraft > 0): auch bei Überblendung 0 zählen erstes Bild/scharf.
    const drawn = !anim && arrivedAt !== null && vis.every((f) => (stats.shown[f] ?? 0) > 0);
    if (stats.firstImageMs === null && drawn) {
      stats.firstImageMs = now - stats.jumpStartMs;
      resumeStreaming();
    }
    const failures = new Set(loader.failures());
    const sharp = vis.every(
      (f) => (stats.shown[f] ?? 0) >= stats.target[f]! || failures.has(`${s.id}/${f}/${stats.target[f]}`),
    );
    if (stats.sharpMs === null && stats.firstImageMs !== null && sharp) stats.sharpMs = now - stats.jumpStartMs;
  });

  // Abstand der Vierecke in [near, far) halten — near/far stehen erst vor dem Zeichnen fest.
  instance.addEventListener('before-render', () => {
    if (!group.visible) return;
    const r = panoRadius(camera.near, camera.far);
    for (const m of meshes.values()) m.scale.setScalar(r);
    group.updateMatrixWorld(true);
  });

  // Steuerung im Panorama: Ziehen dreht („anfassen“), Mausrad ändert den Blickwinkel.
  let drag: { x: number; y: number; yaw: number; pitch: number } | null = null;
  // Gesperrt, solange ein Messungs-Eckpunkt gezogen wird (measure.ts).
  let lookLocked = false;
  const el = instance.domElement;
  el.addEventListener('pointerdown', (ev) => {
    if (!state || ev.button !== 0 || lookLocked) return;
    drag = { x: ev.clientX, y: ev.clientY, yaw: state.yaw, pitch: state.pitch };
  });
  window.addEventListener('pointermove', (ev) => {
    if (!drag || !state) return;
    const k = state.fov / Math.max(1, el.clientHeight);
    setView({ yaw: drag.yaw + (ev.clientX - drag.x) * k, pitch: drag.pitch + (ev.clientY - drag.y) * k });
  });
  window.addEventListener('pointerup', () => {
    drag = null;
  });
  el.addEventListener(
    'wheel',
    (ev) => {
      if (!state) return;
      ev.preventDefault();
      setView({ fov: state.fov * Math.pow(WHEEL_ZOOM, ev.deltaY) });
    },
    { passive: false },
  );
  // Esc (Panorama verlassen) läuft über den Verteiler keymap.ts: Vorrang Ziehen → Zeichnung →
  // Panorama an einer Stelle.

  // Kontextverlust: Giro3D stellt seine Entities selbst her, unsere Texturen nicht.
  el.addEventListener('webglcontextlost', () => notify('Grafikkontext verloren – Anzeige wird wiederhergestellt …'));
  el.addEventListener('webglcontextrestored', () => {
    stats.restores++;
    stats.failed = [];
    stats.shown = {};
    loader.reset();
    for (const m of meshes.values()) {
      m.material.map = null;
      m.material.visible = false;
      m.material.needsUpdate = true;
    }
    if (!state) return;
    stats.jumpStartMs = performance.now();
    stats.firstImageMs = null;
    stats.sharpMs = null;
    arrivedAt = null;
    plan();
    kick();
  });

  function setView(view: Partial<PanoView>): void {
    if (!state) return;
    state = {
      ...state,
      yaw: normalizeYaw(view.yaw ?? state.yaw),
      pitch: clampPitch(view.pitch ?? state.pitch),
      fov: clampFov(view.fov ?? state.fov),
    };
    placeCamera();
    plan();
    emit();
  }

  function enter(id: string, view: Partial<PanoView> = {}): boolean {
    const s = byId.get(id);
    if (!s) {
      notify(`Standpunkt ${id} nicht im Paket`);
      return false;
    }
    if (!s.panorama) {
      notify(
        s.panoramaIssue === 'legacy'
          ? `Standpunkt ${id}: Panorama im alten Paketformat – „scanview-import panos“ neu ausführen`
          : s.panoramaIssue === 'gesperrt'
            ? `Standpunkt ${id}: Panorama für dieses Konto nicht freigegeben`
            : `Standpunkt ${id}: kein Panorama`,
      );
      return false;
    }
    const from = camera.position.clone();
    let keep: PanoView;
    if (state) keep = { yaw: state.yaw, pitch: state.pitch, fov: state.fov };
    else {
      savedFov = camera.fov;
      controls.enabled = false;
      const d = camera.getWorldDirection(new Vector3());
      keep = { ...yawPitchFromDir([d.x, d.y, d.z]), fov: PANO_FOV_DEFAULT };
    }
    state = {
      station: id,
      yaw: normalizeYaw(view.yaw ?? keep.yaw),
      pitch: clampPitch(view.pitch ?? keep.pitch),
      fov: clampFov(view.fov ?? keep.fov),
    };
    stats.jumps++;
    pauseStreaming();
    stats.jumpStartMs = performance.now();
    stats.firstImageMs = null;
    stats.sharpMs = null;
    stats.failed = [];
    stats.shown = {};
    stats.target = {};
    loader.setStation(id);
    for (const m of meshes.values()) {
      m.material.map = null;
      m.material.visible = false;
    }
    buildGeometry(s);
    anim = o.reduziert?.() ? null : { from, t0: performance.now() };
    arrivedAt = null;
    layer.showNeighboursOf(id);
    layer.setActive(id);
    placeCamera();
    plan();
    kick();
    emit();
    return true;
  }

  function leave(): void {
    if (!state) return;
    lookLocked = false;
    drag = null;
    const d = dirFromYawPitch(state.yaw, state.pitch);
    const p = camera.position.clone();
    state = null;
    anim = null;
    arrivedAt = null;
    resumeStreaming();
    stats.target = {};
    loader.setStation(null);
    for (const m of meshes.values()) {
      m.material.map = null;
      m.material.visible = false;
    }
    group.visible = false;
    o.onOverlay?.(0);
    camera.fov = savedFov;
    camera.updateProjectionMatrix();
    controls.target.set(p.x + 5 * d[0], p.y + 5 * d[1], p.z + 5 * d[2]);
    controls.enabled = true;
    controls.update();
    layer.showAll();
    layer.setActive(null);
    instance.notifyChange(camera);
    emit();
  }

  return {
    get active() {
      return state !== null;
    },
    get state() {
      return state ? { ...state } : null;
    },
    get blend() {
      return blend;
    },
    stats,
    get textureBytes() {
      return loader.usedBytes;
    },
    textureKeys: () => loader.keys(),
    enter,
    leave,
    setView,
    lookAt: (world) => {
      const s = station();
      if (s) setView(yawPitchTo(s.pose.translation, world));
    },
    setLookLocked: (on) => {
      lookLocked = on;
      if (on) drag = null;
    },
    setBlend: (alpha) => {
      blend = Math.min(1, Math.max(0, alpha));
      updateOpacity();
      emit(); // u. a. Regler der Bedienleiste nachführen
    },
    onChange: (cb) => {
      listeners.push(cb);
    },
  };
}
