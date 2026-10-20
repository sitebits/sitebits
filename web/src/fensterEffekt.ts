// Fenster-Effekt (F32): Webcam → MediaPipe FaceLandmarker (lokal im Browser) → Augposition →
// asymmetrische Projektion (fensterProjektion.ts). Aus ab Werk; die Kamera startet nur auf ausdrücklichen Klick, das
// Bild verlässt den Browser nie (kein Upload, nichts gespeichert außer den Einstellungen ohne Bild). Tab verborgen
// → Kamera aus. MediaPipe wird erst beim Einschalten geladen (eigener Bundle-Teil, Wasm und Modell vom eigenen Host).
// Die Verschiebung gilt nur für das Zeichnen (before-render) und wird danach zurückgenommen (after-render): Steuerung,
// Picking und Ansichtslink sehen die Kamera unverändert. Messen sperrt main.ts, solange der Effekt an ist.
import { PerspectiveCamera, Vector3 } from 'three';
import type Instance from '@giro3d/giro3d/core/Instance.js';
import { EinEuroFilter, augeAusLandmarken, relativZurMitte } from './fensterKopf';
import type { Auge } from './fensterProjektion';
import { OHNE_VERSATZ, anwenden, fensterVersatz } from './fensterProjektion';

const SPEICHER = 'sv-fenster-effekt';
/** Augen länger als das ohne Gesicht → zurück zur neutralen Ansicht. */
const OHNE_GESICHT_MS = 1000;

export interface FensterEinstellungen {
  /** Bildschirmdiagonale in Zoll (16:10 angenommen) — bestimmt den Maßstab „Kopf-Meter → Szene“. */
  zoll: number;
  /** 1 = physikalisch richtig; 0–2. */
  staerke: number;
  /** Horizontaler Bildwinkel der Webcam in Grad. */
  bildwinkel: number;
  mitte: { x: number; y: number } | null;
}

const VORGABE: FensterEinstellungen = { zoll: 14, staerke: 1, bildwinkel: 60, mitte: null };

export function bildschirmBreiteM(zoll: number): number {
  return (zoll * 0.0254 * 16) / Math.hypot(16, 10);
}

function lade(): FensterEinstellungen {
  try {
    const roh = JSON.parse(localStorage.getItem(SPEICHER) ?? 'null') as Partial<FensterEinstellungen> | null;
    return { ...VORGABE, ...(roh && typeof roh === 'object' ? roh : {}) };
  } catch {
    return { ...VORGABE };
  }
}

export interface FensterEffekt {
  an(): boolean;
  setzeAn(an: boolean): Promise<void>;
  /** Aktuelle Kopfhaltung als neutral speichern. */
  mitteSetzen(): void;
  einstellungen(): FensterEinstellungen;
  setze(e: Partial<FensterEinstellungen>): void;
}

interface Landmarker {
  detectForVideo(v: HTMLVideoElement, t: number): { faceLandmarks: { x: number; y: number }[][] };
  close(): void;
}

export function attachFensterEffekt(o: {
  instance: Instance;
  /** Orbit-Drehpunkt; null im Panorama (dort keine Tiefe — der Effekt pausiert). */
  drehpunkt: () => Vector3 | null;
  wasmBasis: string;
  melde: (text: string) => void;
  onAnAus: (an: boolean) => void;
}): FensterEffekt {
  let ein = false;
  let stand = lade();
  let video: HTMLVideoElement | null = null;
  let strom: MediaStream | null = null;
  let landmarker: Landmarker | null = null;
  let lauf = 0;
  let roh: Auge | null = null;
  let zuletztGesehen = 0;
  const filter = [new EinEuroFilter(), new EinEuroFilter(), new EinEuroFilter(0.5)];
  let gemerkt: Vector3 | null = null;

  const hinweis = document.createElement('div');
  hinweis.className = 'sv-fenster-hinweis';
  hinweis.setAttribute('role', 'status');
  hinweis.hidden = true;
  const text = document.createElement('span');
  text.textContent = 'Kamera an (Fenster-Effekt, bleibt im Browser)';
  const aus = document.createElement('button');
  aus.type = 'button';
  aus.textContent = 'Aus';
  aus.addEventListener('click', () => void setzeAn(false));
  hinweis.append(text, aus);
  document.body.append(hinweis);

  const speichern = (): void => {
    try {
      localStorage.setItem(SPEICHER, JSON.stringify(stand));
    } catch {
      /* privater Modus: Einstellungen gelten nur für diese Sitzung */
    }
  };

  const kamera = (): PerspectiveCamera | null => {
    const c = o.instance.view.camera;
    return c instanceof PerspectiveCamera ? c : null;
  };

  const vorDemZeichnen = (): void => {
    const c = kamera();
    const ziel = o.drehpunkt();
    if (!ein || !c || !ziel || !roh) return;
    const auge = relativZurMitte(roh, stand.mitte);
    const breite = o.instance.domElement.clientWidth;
    const hoehe = o.instance.domElement.clientHeight;
    const v = performance.now() - zuletztGesehen > OHNE_GESICHT_MS ? OHNE_VERSATZ : fensterVersatz(auge, {
      ebene: c.position.distanceTo(ziel), fovDeg: c.fov, aspect: c.aspect,
      bildschirmBreiteM: bildschirmBreiteM(stand.zoll), staerke: stand.staerke,
    });
    gemerkt = c.position.clone();
    c.updateMatrixWorld();
    anwenden(c, gemerkt, v, breite, hoehe);
    c.updateProjectionMatrix();
  };
  const nachDemZeichnen = (): void => {
    const c = kamera();
    if (!c || !gemerkt) return;
    c.position.copy(gemerkt);
    gemerkt = null;
    c.clearViewOffset(); // setzt die Projektion neu: Picking zwischen zwei Bildern rechnet ohne Versatz
    c.updateMatrixWorld();
  };
  o.instance.addEventListener('before-render', vorDemZeichnen);
  o.instance.addEventListener('after-render', nachDemZeichnen);

  const schritt = (n: number): void => {
    if (n !== lauf || !video || !landmarker) return;
    if (video.readyState >= 2) {
      const t = performance.now();
      const lm = landmarker.detectForVideo(video, t).faceLandmarks[0];
      const a = lm && augeAusLandmarken(lm, { breitePx: video.videoWidth, hoehePx: video.videoHeight, bildwinkelDeg: stand.bildwinkel });
      if (a) {
        const s = t / 1000;
        roh = { x: filter[0]!.filter(a.x, s), y: filter[1]!.filter(a.y, s), z: filter[2]!.filter(a.z, s) };
        zuletztGesehen = t;
      }
      o.instance.notifyChange();
    }
    requestAnimationFrame(() => schritt(n));
  };

  const stoppen = (): void => {
    lauf++;
    strom?.getTracks().forEach((t) => t.stop());
    strom = null;
    landmarker?.close();
    landmarker = null;
    video = null;
    roh = null;
    filter.forEach((f) => f.zuruecksetzen());
    hinweis.hidden = true;
    o.instance.notifyChange();
  };

  async function setzeAn(an: boolean): Promise<void> {
    if (an === ein) return;
    ein = an;
    o.onAnAus(an);
    if (!an) {
      stoppen();
      o.melde('Fenster-Effekt aus, Kamera aus');
      return;
    }
    const n = ++lauf;
    try {
      strom = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: 640, height: 480 }, audio: false });
      if (n !== lauf) return stoppen();
      video = document.createElement('video');
      video.muted = true;
      video.playsInline = true;
      video.srcObject = strom;
      await video.play();
      const { FaceLandmarker, FilesetResolver } = await import('@mediapipe/tasks-vision');
      const dateien = await FilesetResolver.forVisionTasks(`${o.wasmBasis}mediapipe`);
      landmarker = await FaceLandmarker.createFromOptions(dateien, {
        baseOptions: { modelAssetPath: `${o.wasmBasis}mediapipe/face_landmarker.task`, delegate: 'GPU' },
        runningMode: 'VIDEO',
        numFaces: 1,
      });
      if (n !== lauf) return stoppen();
      hinweis.hidden = false;
      o.melde('Fenster-Effekt an: Kopf bewegen, „Mitte setzen“ bei gerader Haltung');
      schritt(n);
    } catch (e) {
      const name = e instanceof DOMException ? e.name : '';
      ein = false;
      o.onAnAus(false);
      stoppen();
      o.melde(name === 'NotAllowedError' ? 'Kamera nicht freigegeben — Fenster-Effekt aus'
        : name === 'NotFoundError' ? 'Keine Kamera gefunden — Fenster-Effekt aus'
          : 'Fenster-Effekt nicht möglich (Kamera oder Gesichtserkennung)');
    }
  }

  document.addEventListener('visibilitychange', () => {
    if (document.hidden && ein) void setzeAn(false);
  });

  return {
    an: () => ein,
    setzeAn,
    mitteSetzen: () => {
      if (!roh) return o.melde('Kein Gesicht erkannt');
      stand = { ...stand, mitte: { x: roh.x, y: roh.y } };
      speichern();
      o.melde('Mitte gesetzt');
    },
    einstellungen: () => stand,
    setze: (e) => {
      stand = { ...stand, ...e };
      speichern();
      o.instance.notifyChange();
    },
  };
}
