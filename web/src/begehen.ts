// Kamerazustand Begehen: freie Bewegung durch die Punktwolke mit Tastatur, Maus (unter Pointer-Lock) und
// später Gamepad. Die Mathematik liegt rein in walkControls.ts; hier nur der Adapter an Kamera, Leinwand
// und Panorama. Solange das Begehen läuft, schreibt nur diese Schleife in die Kamera: MapControls sind
// gesperrt, ihr Ziel läuft aber fortlaufend vor der Kamera mit — Giro3D ruft controls.update() in jedem
// Bild auf (auch gesperrt) und zöge die Kamera sonst zurück; außerdem richtet sich die Near-Plane nach
// dem Abstand Kamera–Ziel (viewer.ts, updateNearFloor).
import { PerspectiveCamera, Vector3 } from 'three';
import { bewegungReduziert, drehenInSchritten } from './bedienEinstellungen';
import type { BedienEinstellungen } from './bedienEinstellungen';
import type { Panorama } from './panorama';
import type { AktionId, HalteAktion } from './tastenbelegung';
import type { Viewer } from './viewer';
import { FORMULAR_OFFEN } from './zielpunkt';
import {
  blick, drehSchritt, drehe, Haltezustand, SetzZaehler, mausBlick, neigSchritt, PITCH_MAX, schritt, tempoAusRad, tempoText, vignette, yawPitch, zielBeimAustritt,
} from './walkControls';
import type { V3, WalkEingabe, WalkParameter, WalkZustand } from './walkControls';

/** Drehrate der Pfeiltasten (bzw. des Sticks) beim stetigen Drehen. */
const DREHRATE_RAD_S = 1.5;
const SCHNELL_FAKTOR = 4;
const ZEITKONSTANTE_S = 0.15;
/** Abstand des mitlaufenden Kameraziels. Er bestimmt die Near-Plane (Abstand/100): bei 2 m liegt sie bei
 *  2 cm, eine Fläche 0,5 m vor der Kamera wird nicht abgeschnitten. Beim Beenden gilt wieder der
 *  Austrittsabstand aus walkControls (10 m bzw. der Treffer). */
const ZIEL_ABSTAND_M = 2;
/** Zielen halbiert die Mausempfindlichkeit. */
const ZIELEN_FAKTOR = 0.5;
/** Linke Maustaste unter Pointer-Lock: lief zwischen Drücken und Loslassen mehr Mausweg auf, wird nicht
 *  gesetzt (Zeigerabbruch). */
export const ABBRUCH_PX = 24;
/** Esc, das unmittelbar nach dem Verlust des Pointer-Locks ankommt, gehört noch zum Lösen des Locks. */
const ESC_NACH_LOCK_MS = 150;
/** Ruhe nach der letzten Raste, bevor das neue Tempo angesagt wird (eine Ansage je Drehbewegung). */
const TEMPO_ANSAGE_MS = 400;

export interface EingabeQuelle {
  eingabe: WalkEingabe;
  dreh: number;
  neig: number;
  schritte: { dreh: -1 | 0 | 1; neig: -1 | 0 | 1 };
}

export interface Begehen {
  readonly aktiv: boolean;
  /** false (mit Meldung), wenn ein Panorama offen ist. */
  starten(): boolean;
  /** `treffer` = lokaler Zielpunkt unter dem Fadenkreuz; ohne ihn liegt das Ziel 10 m in Blickrichtung. */
  beenden(treffer?: V3 | null): void;
  /** Esc im Begehen: unter Pointer-Lock (oder gleich nachdem der Browser ihn gelöst hat) nur den Lock
   *  freigeben, sonst beenden. */
  esc(treffer?: V3 | null): void;
  halte(a: HalteAktion, gedrueckt: boolean): void;
  /** Position in lokalen Koordinaten (Paket-Ursprung), Winkel in rad, Lauftempo in m/s. */
  zustand(): { pos: V3; yaw: number; pitch: number; tempo: number };
  setzeEingabeQuelle(q: ((dt: number) => EingabeQuelle | null) | null): void;
  /** Pointer-Lock auf der Leinwand aktiv: bestehende Klick-Handler ignorieren Klicks (eingefrorene Position). */
  gesperrt(): boolean;
  /** Im Begehen: Pointer-Lock besteht oder ist eben erst (< 150 ms) verloren gegangen. Ein Esc in diesem
   *  Moment gehört dem Lösen des Locks, nicht einer offenen Zeichnung. */
  zeigerGesperrt(): boolean;
  onChange(cb: (aktiv: boolean) => void): void;
  onBild(cb: () => void): void;
  /** Zielen aktiv (halbe Empfindlichkeit, Kantenfang beim nächsten Setzen). */
  zielt(): boolean;
  /** Pointer-Lock freigeben und alles Gehaltene loslassen, ohne das Begehen zu beenden (z. B. vor einem
   *  Formular, das Tasten braucht). */
  zeigerFreigeben(): void;
  /** Befund setzen am Fadenkreuz: dieselbe Warteschlange wie das Setzen (Kamera steht, Eingabereihenfolge
   *  bleibt), aber unabhängig vom Werkzeug. Außerhalb des Begehens wirkungslos. */
  befund(): Promise<void>;
  /** Nur für E2E: Pointer-Lock lässt sich ohne Fenster nicht erzwingen; `on` lässt gesperrt() so antworten. */
  __testLock(on: boolean): void;
}

/** Rückfrage „vorn/hinten“, soweit das Begehen sie bedient (Mausrad, linke Maustaste). */
export interface RueckfrageSteuerung {
  offen(): boolean;
  wechsle(): void;
  bestaetige(): void;
  /** Fokus wandert in die Rückfrage: die Leinwand verliert ihn, der Pointer-Lock bleibt (Mausrad, Klick). */
  enthaelt(n: Node | null): boolean;
}

const DREH_TASTEN: ReadonlySet<HalteAktion> = new Set([
  'begehen.drehen-links', 'begehen.drehen-rechts', 'begehen.neigen-hoch', 'begehen.neigen-runter',
]);
const klemme1 = (x: number): number => Math.max(-1, Math.min(1, x));

export function attachBegehen(o: {
  v: Viewer;
  pano: Panorama;
  /** Weitere Sperre (z. B. Blick durch die Kamera): Meldungstext oder null. */
  gesperrt?(): string | null;
  einstellungen: () => BedienEinstellungen;
  systemReduziert: () => boolean;
  melde: (t: string) => void;
  kuerzel: (a: AktionId) => string;
  vignetteEl: HTMLElement;
  /** Setzen am Fadenkreuz. Startet sofort (Strahl und Bild der Eingabe), wendet das Ergebnis aber erst
   *  nach `vorher` an — so bleibt die Eingabereihenfolge. `zielen` = Zielen war beim Auslösen aktiv;
   *  `befund` = einen Befund setzen (Formular), unabhängig vom gewählten Werkzeug. */
  setzen?: (s: { zielen: boolean; vorher: Promise<void>; befund?: boolean }) => Promise<void>;
  rueckfrage?: RueckfrageSteuerung;
  /** Ein Formular braucht die Eingaben (z. B. ein Befund wird erfasst): die Kamera steht, auch für das Gamepad. */
  formularOffen?: () => boolean;
  /** Fokus ins offene Formular (ein Klick in die 3D-Ansicht holt dann keinen Zeiger, sondern führt dorthin). */
  formularFokussieren?: () => void;
  /** Zusatz an „Begehen beendet“, wenn es zurück in die Punktwolke geht (Satz zur Ansicht, bedienhilfe.ts). */
  endeZusatz?: () => string;
}): Begehen {
  const { v, pano } = o;
  const { instance, controls } = v;
  const cam = instance.view.camera;
  if (!(cam instanceof PerspectiveCamera)) throw new Error('Begehen braucht eine Perspektivkamera.');
  const camera: PerspectiveCamera = cam;
  const canvas = instance.domElement;

  let aktiv = false;
  let z: WalkZustand = { pos: [0, 0, 0], yaw: 0, pitch: 0, vel: [0, 0, 0] };
  let tempo = 2;
  let fovVorher = camera.fov;
  let raf: number | null = null;
  let zuletzt = 0;
  let schnellModus: BedienEinstellungen['schnell'] = 'halten';
  const halten = new Haltezustand();
  const drehTasten = new Set<HalteAktion>();
  let zielenGehalten = false;
  let zielenUm = false;
  let mausZielen = false;
  let quelle: ((dt: number) => EingabeQuelle | null) | null = null;
  let testLock = false;
  let lockVerlorenUm = -Infinity;
  /** Setzen-Taste gedrückt, während keine Rückfrage offen war: erst ihr Loslassen setzt. */
  let setzBereit = false;
  /** Laufende Setz-Vorgänge: solange einer läuft, steht die Kamera (das Ergebnis gehört zu diesem Blick). */
  const setzLaeuft = new SetzZaehler();
  let setzKette: Promise<void> = Promise.resolve();
  /** Aufgelaufener Mausweg unter Pointer-Lock [px] und sein Stand beim Drücken der linken Maustaste. */
  let mausWeg = 0;
  let linksAb: number | null = null;
  const changeCbs: ((aktiv: boolean) => void)[] = [];
  const bildCbs: (() => void)[] = [];

  const gesperrt = (): boolean => (aktiv && testLock) || document.pointerLockElement === canvas;
  const reduziert = (): boolean => bewegungReduziert(o.einstellungen(), o.systemReduziert());
  const inSchritten = (): boolean => drehenInSchritten(o.einstellungen(), o.systemReduziert());
  const zielt = (): boolean =>
    mausZielen || (o.einstellungen().zielen === 'umschalten' ? zielenUm : zielenGehalten);
  const rueckfrageOffen = (): boolean => o.rueckfrage?.offen() ?? false;
  /** Ein modaler Dialog (Tastenhilfe, Einstellungen) ist offen. */
  const dialogOffen = (): boolean => document.querySelector('dialog[open]') !== null;
  /** Während eines Setz-Vorgangs oder einer offenen Rückfrage bewegt sich die Kamera nicht: die zweite
   *  Suche nach der Rückfrage braucht denselben Strahl. Gehaltene Tasten wirken danach weiter. Solange ein
   *  modaler Dialog offen ist, steht das Begehen ebenfalls (auch für Stick und Steuerkreuz). */
  const angehalten = (): boolean => setzLaeuft.laeuft || rueckfrageOffen() || dialogOffen() || (o.formularOffen?.() ?? false);

  /** Fokusverlust: alles Gehaltene loslassen und sofort stehen bleiben (kein Nachgleiten). */
  const stoppe = (): void => {
    halten.leere();
    drehTasten.clear();
    zielenGehalten = false;
    mausZielen = false;
    setzBereit = false;
    linksAb = null;
    z = { ...z, vel: [0, 0, 0] };
  };

  const starteSetzen = (befund = false): Promise<void> => {
    if (!o.setzen) return Promise.resolve();
    // Offenes Formular: nichts dahinter setzen (weder Messpunkt noch zweiten Befund).
    if (o.formularOffen?.()) {
      o.melde(FORMULAR_OFFEN);
      return Promise.resolve();
    }
    const vorher = setzKette;
    const fertig = setzLaeuft.start();
    setzKette = o.setzen({ zielen: zielt(), vorher, befund }).then(fertig, (e: unknown) => {
      fertig();
      console.error('Setzen fehlgeschlagen:', e);
      o.melde('Punkt konnte nicht ausgewertet werden');
    });
    return setzKette;
  };

  const setzeKamera = (): void => {
    const d = blick(z.yaw, z.pitch);
    const [x, y, h] = z.pos;
    camera.position.set(x, y, h);
    camera.up.set(0, 0, 1);
    camera.lookAt(x + d[0], y + d[1], h + d[2]);
    controls.target.set(x + d[0] * ZIEL_ABSTAND_M, y + d[1] * ZIEL_ABSTAND_M, h + d[2] * ZIEL_ABSTAND_M);
    instance.notifyChange(camera);
  };

  const bild = (jetzt: number): void => {
    raf = null;
    if (!aktiv) return;
    const dt = (jetzt - zuletzt) / 1000;
    zuletzt = jetzt;
    const e = o.einstellungen();
    if (e.schnell !== schnellModus) {
      schnellModus = e.schnell;
      halten.leere();
    }
    // Sichtfeld im Einstellungsdialog geändert: sofort übernehmen.
    if (camera.fov !== e.sichtfeldGrad) {
      camera.fov = e.sichtfeldGrad;
      camera.updateProjectionMatrix();
    }
    // Das Gamepad auch im Stillstand abfragen: seine Knöpfe bedienen dann die Rückfrage.
    const q = quelle ? quelle(dt) : null;
    // Eine Gamepad-Flanke kann das Begehen eben beendet haben: dann nichts mehr in die Kamera schreiben.
    if (!aktiv) return;
    if (angehalten()) {
      z = { ...z, vel: [0, 0, 0] };
      setzeKamera();
      o.vignetteEl.style.opacity = '0';
      bildCbs.forEach((cb) => cb());
      raf = requestAnimationFrame(bild);
      return;
    }
    const tasten = halten.eingabe(e.schnell === 'umschalten');
    const eingabe: WalkEingabe = q
      ? {
          vor: klemme1(tasten.vor + q.eingabe.vor),
          seit: klemme1(tasten.seit + q.eingabe.seit),
          hoch: klemme1(tasten.hoch + q.eingabe.hoch),
          schnell: tasten.schnell || q.eingabe.schnell,
        }
      : tasten;
    const g = (a: HalteAktion): number => (drehTasten.has(a) ? 1 : 0);
    if (inSchritten()) {
      // Tasten drehen beim Druck (halte); hier nur die Schritte der Eingabequelle.
      if (q?.schritte.dreh) z = { ...z, yaw: drehSchritt(z.yaw, q.schritte.dreh, e.drehSchrittGrad) };
      if (q?.schritte.neig) z = { ...z, pitch: neigSchritt(z.pitch, q.schritte.neig, e.drehSchrittGrad) };
    } else {
      const dreh = klemme1(g('begehen.drehen-links') - g('begehen.drehen-rechts') + (q?.dreh ?? 0));
      const neig = klemme1(g('begehen.neigen-hoch') - g('begehen.neigen-runter') + (q?.neig ?? 0));
      if (dreh !== 0 || neig !== 0) z = { ...z, ...drehe(z.yaw, z.pitch, dreh, neig, DREHRATE_RAD_S, dt) };
    }
    const p: WalkParameter = {
      tempo, schnellFaktor: SCHNELL_FAKTOR, zeitkonstante: ZEITKONSTANTE_S, hoeheHalten: e.hoeheHalten, reduziert: reduziert(),
    };
    z = schritt(z, eingabe, p, dt);
    setzeKamera();
    o.vignetteEl.style.opacity = String(vignette(Math.hypot(z.vel[0], z.vel[1], z.vel[2]), tempo, e.vignette));
    bildCbs.forEach((cb) => cb());
    raf = requestAnimationFrame(bild);
  };

  const benachrichtige = (): void => changeCbs.forEach((cb) => cb(aktiv));

  const startText = (): string => {
    const k = o.kuerzel;
    const schauen = ['begehen.drehen-links', 'begehen.drehen-rechts', 'begehen.neigen-hoch', 'begehen.neigen-runter'] as const;
    const pfeile = schauen.map(k).join('') === '←→↑↓';
    const blickTasten = pfeile ? 'Pfeiltasten' : schauen.map(k).filter(Boolean).join(' ');
    const bewegen = (['begehen.vor', 'begehen.links', 'begehen.zurueck', 'begehen.rechts'] as const).map(k).filter(Boolean).join(' ');
    const teile = [blickTasten && `${blickTasten} schauen`, bewegen && `${bewegen} bewegen`, 'Esc beendet.'].filter(Boolean);
    return `Begehen. ${teile.join(', ')}`;
  };

  const starten = (): boolean => {
    if (aktiv) return true;
    if (pano.active) {
      o.melde('Begehen ist im Panorama nicht verfügbar');
      return false;
    }
    const sperre = o.gesperrt?.() ?? null;
    if (sperre) {
      o.melde(sperre);
      return false;
    }
    const e = o.einstellungen();
    fovVorher = camera.fov;
    camera.fov = e.sichtfeldGrad;
    camera.updateProjectionMatrix();
    const pos: V3 = [camera.position.x, camera.position.y, camera.position.z];
    const t = controls.target;
    const dir: V3 = [t.x - pos[0], t.y - pos[1], t.z - pos[2]];
    // Blick aus Kamera → Drehpunkt; fällt beides zusammen, aus der Kamerarichtung.
    const d = Math.hypot(dir[0], dir[1], dir[2]) > 0 ? dir : camera.getWorldDirection(new Vector3()).toArray();
    const yp = yawPitch([d[0], d[1], d[2]]);
    z = { pos, yaw: yp.yaw, pitch: Math.max(-PITCH_MAX, Math.min(PITCH_MAX, yp.pitch)), vel: [0, 0, 0] };
    tempo = e.tempo;
    schnellModus = e.schnell;
    stoppe();
    zielenUm = false;
    controls.enabled = false;
    aktiv = true;
    canvas.focus({ preventScroll: true });
    setzeKamera();
    zuletzt = performance.now();
    raf = requestAnimationFrame(bild);
    o.melde(startText());
    benachrichtige();
    return true;
  };

  const beenden = (treffer?: V3 | null): void => {
    if (!aktiv) return;
    aktiv = false;
    if (raf !== null) cancelAnimationFrame(raf);
    raf = null;
    stoppe();
    zielenUm = false;
    testLock = false;
    // Laufende Setz-Vorgänge gehören zum beendeten Begehen: beim nächsten Start steht nichts still.
    setzLaeuft.zuruecksetzen();
    if (document.pointerLockElement === canvas) document.exitPointerLock();
    o.vignetteEl.style.opacity = '0';
    // Ein Panorama hat Kamera, Sichtfeld und Steuerung bereits übernommen (Sprung von außerhalb dieses
    // Moduls): nichts zurückschreiben, sonst schrieben zwei Zustände gleichzeitig in die Kamera. Das
    // gesicherte Sichtfeld des Panoramas ist dann das des Begehens — deshalb rufen alle Aufrufer von
    // pano.enter vorher beenden().
    if (!pano.active) {
      const ziel = zielBeimAustritt(z.pos, blick(z.yaw, z.pitch), treffer ? v.localToWorld([treffer[0], treffer[1], treffer[2]]) : null);
      camera.fov = fovVorher;
      camera.updateProjectionMatrix();
      controls.target.set(ziel[0], ziel[1], ziel[2]);
      controls.enabled = true;
      controls.update();
      controls.dispatchEvent({ type: 'change' });
      instance.notifyChange(camera);
    }
    const zusatz = pano.active ? '' : (o.endeZusatz?.() ?? '');
    o.melde(zusatz ? `Begehen beendet. ${zusatz}` : 'Begehen beendet');
    benachrichtige();
  };

  const halte = (a: HalteAktion, gedrueckt: boolean): void => {
    if (!aktiv) return;
    if (a === 'begehen.setzen') {
      if (!gedrueckt) {
        // Gesetzt wird beim Loslassen — ein Loslassen ohne eigenes Drücken (z. B. die Leertaste, die eben
        // die Rückfrage beantwortet hat) setzt nichts.
        if (!setzBereit) return;
        setzBereit = false;
        void starteSetzen();
        return;
      }
      if (rueckfrageOffen()) {
        o.melde('Erst die Rückfrage beantworten');
        return;
      }
      setzBereit = true;
      return;
    }
    if (a === 'begehen.zielen') {
      zielenGehalten = gedrueckt;
      if (gedrueckt) zielenUm = !zielenUm;
      return;
    }
    if (DREH_TASTEN.has(a)) {
      if (!gedrueckt) {
        drehTasten.delete(a);
        return;
      }
      drehTasten.add(a);
      // Drehen in Schritten: ein Schritt je Druck (der Verteiler verwirft Tastenwiederholungen).
      if (inSchritten() && !angehalten()) {
        const grad = o.einstellungen().drehSchrittGrad;
        if (a === 'begehen.drehen-links') z = { ...z, yaw: drehSchritt(z.yaw, 1, grad) };
        else if (a === 'begehen.drehen-rechts') z = { ...z, yaw: drehSchritt(z.yaw, -1, grad) };
        else if (a === 'begehen.neigen-hoch') z = { ...z, pitch: neigSchritt(z.pitch, 1, grad) };
        else z = { ...z, pitch: neigSchritt(z.pitch, -1, grad) };
        setzeKamera();
      }
      return;
    }
    if (gedrueckt) halten.druecke(a);
    else halten.lasse(a);
  };

  // Maus: ein Klick auf die Leinwand holt sich den Pointer-Lock (der Browser verlangt dafür eine
  // Nutzergeste). Dieser Klick gehört dem Begehen und erreicht die übrigen Klick-Handler (Messen, POI,
  // Standpunkt) nicht: Capture an der Leinwand läuft vor deren Listenern.
  canvas.addEventListener(
    'click',
    (ev) => {
      if (!aktiv || gesperrt()) return;
      ev.stopImmediatePropagation();
      // Offenes Formular: kein Zeiger (die Kamera steht ohnehin), der Klick führt zurück ins Formular.
      if (o.formularOffen?.()) {
        o.formularFokussieren?.();
        return;
      }
      try {
        const r = canvas.requestPointerLock() as unknown;
        if (r instanceof Promise) r.catch(() => undefined);
      } catch {
        /* ohne Lock bleibt die Tastatur */
      }
    },
    true,
  );
  document.addEventListener('mousemove', (ev) => {
    if (aktiv && gesperrt()) mausWeg += Math.hypot(ev.movementX, ev.movementY);
    if (!aktiv || document.pointerLockElement !== canvas || angehalten()) return;
    const e = o.einstellungen();
    const r = mausBlick(z.yaw, z.pitch, ev.movementX, ev.movementY, e.empfindlichkeit * (zielt() ? ZIELEN_FAKTOR : 1), e.invertY);
    z = { ...z, ...r };
  });
  // Rad = Lauftempo, solange das Begehen läuft — auch ohne Zeigersperre: Das erste Esc gibt nur die Sperre frei
  // und das Begehen läuft weiter, die Orbit-Steuerung bleibt gesperrt. Ohne diesen Zweig war das Rad dann tot
  // (Handtest 0.3.0). Das neue Tempo wird nach kurzer Ruhe angesagt, sonst bliebe die Änderung im Stand unsichtbar.
  let tempoAnsage: number | null = null;
  canvas.addEventListener(
    'wheel',
    (ev) => {
      if (!aktiv) return;
      ev.preventDefault();
      if (rueckfrageOffen()) {
        if (ev.deltaY !== 0) o.rueckfrage?.wechsle();
        return;
      }
      const neu = tempoAusRad(tempo, ev.deltaY);
      if (neu === tempo) return;
      tempo = neu;
      if (tempoAnsage !== null) window.clearTimeout(tempoAnsage);
      tempoAnsage = window.setTimeout(() => {
        tempoAnsage = null;
        if (aktiv) o.melde(tempoText(tempo));
      }, TEMPO_ANSAGE_MS);
    },
    { passive: false },
  );
  // Linke Maustaste unter Pointer-Lock setzt am Fadenkreuz — beim Loslassen und nur, wenn die Maus seit
  // dem Drücken kaum bewegt wurde. Bei offener Rückfrage bestätigt sie die Wahl.
  canvas.addEventListener('mousedown', (ev) => {
    if (!aktiv || !gesperrt()) return;
    if (ev.button === 2) mausZielen = true;
    if (ev.button !== 0) return;
    if (rueckfrageOffen()) {
      linksAb = null;
      o.rueckfrage?.bestaetige();
      return;
    }
    linksAb = mausWeg;
  });
  document.addEventListener('mouseup', (ev) => {
    if (ev.button === 2) mausZielen = false;
    if (ev.button !== 0 || linksAb === null) return;
    const weg = mausWeg - linksAb;
    linksAb = null;
    if (!aktiv || !gesperrt()) return;
    if (weg > ABBRUCH_PX) o.melde('Setzen abgebrochen');
    else if (rueckfrageOffen()) o.melde('Erst die Rückfrage beantworten');
    else void starteSetzen();
  });
  canvas.addEventListener('contextmenu', (ev) => {
    if (aktiv && gesperrt()) ev.preventDefault();
  });

  /** Pointer-Lock freigeben und alles Gehaltene loslassen; das Begehen läuft weiter. */
  const zeigerFreigeben = (): void => {
    testLock = false;
    if (document.pointerLockElement === canvas) document.exitPointerLock();
    stoppe();
  };

  // Fokusverlust: sofort stehen bleiben. Unter Pointer-Lock (Tab verlässt die Leinwand trotzdem) auch den
  // Lock freigeben — sonst drehte die Maus weiter, während die Tasten schon anderswo ankommen. Ausnahme: der
  // Fokus wandert in die Rückfrage, die unter Lock per Mausrad und Klick beantwortet wird.
  canvas.addEventListener('blur', (ev) => {
    if (!aktiv) return;
    if (!gesperrt()) {
      stoppe();
      return;
    }
    if (o.rueckfrage?.enthaelt(ev.relatedTarget instanceof Node ? ev.relatedTarget : null)) return;
    zeigerFreigeben();
  });
  window.addEventListener('blur', () => {
    if (aktiv) stoppe();
  });
  document.addEventListener('visibilitychange', () => {
    if (aktiv && document.visibilityState === 'hidden') stoppe();
  });
  document.addEventListener('pointerlockchange', () => {
    if (document.pointerLockElement === canvas) return;
    lockVerlorenUm = performance.now();
    if (aktiv) stoppe();
  });

  // Ein Panorama, das nicht über goToStation/POI-Sprung betreten wird (die beenden vorher selbst), beendet
  // das Begehen hier — nur als Rückfall: Das Panorama hat dann das Begehen-Sichtfeld als „vorher“ gesichert
  // und stellt es beim Verlassen her. Jeder neue Aufrufer von pano.enter muss deshalb vorher
  // begehen.beenden() rufen.
  pano.onChange(() => {
    if (aktiv && pano.active) beenden(null);
  });

  return {
    get aktiv() {
      return aktiv;
    },
    starten,
    beenden,
    esc: (treffer) => {
      if (!aktiv) return;
      if (gesperrt()) {
        testLock = false;
        if (document.pointerLockElement === canvas) document.exitPointerLock();
        return;
      }
      if (performance.now() - lockVerlorenUm < ESC_NACH_LOCK_MS) return;
      beenden(treffer);
    },
    halte,
    zustand: () => ({ pos: v.worldToLocal([z.pos[0], z.pos[1], z.pos[2]]), yaw: z.yaw, pitch: z.pitch, tempo }),
    setzeEingabeQuelle: (q) => {
      quelle = q;
    },
    gesperrt,
    // Nur im Begehen: beenden() löst selbst den Lock und setzt damit lockVerlorenUm.
    zeigerGesperrt: () => aktiv && (gesperrt() || performance.now() - lockVerlorenUm < ESC_NACH_LOCK_MS),
    onChange: (cb) => {
      changeCbs.push(cb);
    },
    onBild: (cb) => {
      bildCbs.push(cb);
    },
    zielt,
    zeigerFreigeben,
    befund: () => {
      if (!aktiv) return Promise.resolve();
      if (rueckfrageOffen()) {
        o.melde('Erst die Rückfrage beantworten');
        return Promise.resolve();
      }
      return starteSetzen(true);
    },
    __testLock: (on) => {
      testLock = on;
    },
  };
}
