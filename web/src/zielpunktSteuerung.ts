import { sourceFromPick } from './pointSources';
// Zielpunkt im Begehen: Setzen am Fadenkreuz (auch „Befund setzen“ mit F bzw. Gamepad Y), Fadenkreuz mit
// gedrosselter Vorschau, Live-Wert und dessen Ansage sowie die Abbildung des Gamepads auf Begehen, Rückfrage
// und Verteiler. Dazu das Fadenkreuz im Panorama (Fokus auf der Leinwand, Werkzeug gewählt): Pfeiltasten
// drehen in Schritten, Leertaste und F setzen per feiner Suche mit Panorama-Budget, ohne Vorschau und
// Live-Wert. main.ts verdrahtet nur.
import { PerspectiveCamera, Vector3 } from 'three';
import { ansageSchritt, kurzText, liveText, startZustand, vorschauNoetig } from './ansage';
import type { AnsageZustand } from './ansage';
import type { Ansage } from './bedienDialoge';
import type { BedienEinstellungen } from './bedienEinstellungen';
import type { Begehen } from './begehen';
import { createFadenkreuz } from './fadenkreuz';
import type { PadAktion } from './gamepad';
import type { KeyAction } from './keymap';
import type { MeasureTool } from './measure';
import { erzeugePadQuelle } from './padQuelle';
import type { Panorama } from './panorama';
import type { PoiPanel } from './pois';
import { panoPickOptions } from './rayPick';
import type { PickSource, Vorschau } from './rayPick';
import type { RueckfrageDialog } from './rueckfrage';
import { padBelegung } from './tastenbelegung';
import type { AktionId, Belegung, HalteAktion } from './tastenbelegung';
import type { Toolbar } from './toolbar';
import type { Viewer } from './viewer';
import { SetzZaehler } from './walkControls';
import { FORMULAR_OFFEN, keinPunktGrund, keinPunktText, zielpunktSetzen } from './zielpunkt';
import type { Rueckfrage, Wahl } from './zielpunkt';

export interface ZielpunktSteuerung {
  /** Setzen am Fadenkreuz (für `attachBegehen({ setzen })`); `befund` = Befund unabhängig vom Werkzeug. */
  setzen(s: { zielen: boolean; vorher: Promise<void>; befund?: boolean }): Promise<void>;
  /** Befund am Fadenkreuz setzen, unabhängig vom Werkzeug (F, Gamepad Y); im Begehen und im Panorama-Fadenkreuz. */
  befundSetzen(): Promise<void>;
  /** Fadenkreuz im Panorama: Panorama aktiv, Fokus auf der Leinwand, ein Werkzeug außer Navigieren gewählt. */
  panoramaZiel(): boolean;
  /** Halte-Taste im Panorama-Fadenkreuz: Drehen/Neigen einen Schritt beim Drücken, Setzen beim Loslassen. */
  panoHalte(a: HalteAktion, gedrueckt: boolean): void;
  /** Lokaler Punkt der letzten Vorschau als Ziel beim Verlassen des Begehens; ohne Vorschau null. */
  austrittsTreffer(): [number, number, number] | null;
  /** Fadenkreuz-Größe aus den Einstellungen. */
  groesse(px: number): void;
  /** Abfragetaste: frische Vorschau, sofort ansagen (ohne die Ruhezeit abzuwarten). */
  ansageJetzt(): void;
  /** Gamepad an das Begehen hängen; `verteile` ist der Kürzel-Verteiler. */
  verbindePad(verteile: (a: KeyAction) => void): void;
}

export function attachZielpunktSteuerung(o: {
  v: Viewer;
  pano: Panorama;
  picker: PickSource;
  tool: MeasureTool;
  toolbar: Toolbar;
  begehen: Begehen;
  rueckfrage: RueckfrageDialog;
  /** Rückfrage mit gesperrter Orbitsteuerung. */
  frage: (b: Rueckfrage, ort: { x: number; y: number }) => Promise<Wahl | null>;
  pose: () => number[];
  pois: () => PoiPanel | null;
  einstellungen: () => BedienEinstellungen;
  belegung: () => Belegung;
  kuerzel: (a: AktionId) => string;
  say: (t: string) => void;
  ansage: Ansage;
  cloudVisible: () => boolean;
  hiddenCloudMessage: string;
  /** Rückmeldung für die Tastenhinweise: ein Punkt oder Befund am Fadenkreuz wurde gesetzt (Taste oder Controller; nicht bei Abbruch oder ohne Treffer),
   *  ein Controller-Knopf wirkt (nicht bei offenem Dialog oder Formular). */
  hinweis?: { gesetzt(befund: boolean): void; pad(a: PadAktion, runter: boolean): void };
}): ZielpunktSteuerung {
  const { v, pano, picker, tool, toolbar, rueckfrage, kuerzel, say, ansage, cloudVisible, hiddenCloudMessage } = o;
  const walk = o.begehen;
  const kameraPose = o.pose;
  const frageStill = o.frage;
  const gleichePose = (a: readonly number[], b: readonly number[]): boolean => a.every((x, i) => Math.abs(x - b[i]!) <= 1e-9);
  // Letzte Vorschau; ihr Punkt (Szenenkoordinaten wie die Kamera) wird beim Verlassen des Begehens das Ziel.
  let letzteVorschau: Vorschau | null = null;
  const austrittsTreffer = (): [number, number, number] | null => {
    const p = letzteVorschau?.punktWelt;
    return p ? v.worldToLocal([p[0], p[1], p[2]]) : null;
  };

  // Setzen am Fadenkreuz: feine Suche in Bildmitte (zielpunkt.ts), Ergebnis je nach Werkzeug, beim Befund
  // immer das Formular. Im Begehen mit Sichtbarkeitsregel, im Panorama ohne sie und mit Panorama-Budget. Die
  // Suche startet sofort; Rückfrage und Anwenden laufen erst nach dem vorigen Setzen.
  const setzenImBegehen = async ({ zielen, vorher, befund = false }: { zielen: boolean; vorher: Promise<void>; befund?: boolean }): Promise<void> => {
    const imPanorama = !walk.aktiv && pano.active;
    const werkzeug = toolbar.tool();
    if (!befund && werkzeug === 'navigate') {
      await vorher;
      const k = [kuerzel('begehen.werkzeug-voriges'), kuerzel('begehen.werkzeug-naechstes')].filter(Boolean).join('/');
      say(`Kein Werkzeug gewählt – ${k ? `${k} wählt` : 'die Werkzeugleiste wählt'} ein Werkzeug`);
      return;
    }
    const r = v.instance.domElement.getBoundingClientRect();
    const mitte = { x: r.x + r.width / 2, y: r.y + r.height / 2 };
    const snap = zielen || toolbar.snapEdges();
    if (!befund && werkzeug !== 'poi') {
      // Messen: derselbe Weg wie ein Klick in Bildmitte (Eckpunkt-Fang, Schließen am ersten Eckpunkt); den
      // Hinweis ohne Punkt sagt das Messwerkzeug selbst, mit demselben Satz wie beim Klick.
      const status = await tool.setzeAmBildschirm(mitte.x, mitte.y, { snapEdges: snap, vorher, keinPunkt: say });
      await vorher;
      if (status === 'gesetzt') {
        o.hinweis?.gesetzt(false);
        say('Punkt gesetzt');
      } else if (status === 'geschlossen') {
        o.hinweis?.gesetzt(false);
        say('Fläche geschlossen');
      } else if (status === 'abgebrochen') say('abgebrochen');
      else if (status === 'ansicht') say('abgebrochen – Ansicht hat sich bewegt');
      else if (toolbar.tool() !== werkzeug) say('Werkzeug gewechselt – Punkt nicht gesetzt');
      return;
    }
    // Befund (F, Gamepad Y, Werkzeug „Markieren“): Formular am Treffer.
    const pose0 = kameraPose();
    // Standpunkt wie die Pose vor der Suche erfassen (wie der Klickpfad in pois.ts): ein Wechsel währenddessen
    // bricht ab (wie ein Posewechsel), der Befund wird nie dem neuen Standpunkt zugeordnet.
    const station0 = imPanorama ? (pano.state?.station ?? null) : null;
    const laufend = zielpunktSetzen({
      quelle: picker,
      x: mitte.x,
      y: mitte.y,
      opts: { ...(imPanorama ? panoPickOptions() : {}), snapEdges: snap },
      sichtbarkeitPruefen: !imPanorama,
      frage: (b) => vorher.then(() => frageStill(b, mitte)),
      ansichtUnveraendert: () => gleichePose(kameraPose(), pose0),
    });
    await vorher;
    const e = await laufend;
    // Inzwischen beendet oder (beim Werkzeug) gewechselt: ansagen statt still verwerfen.
    if (imPanorama ? !pano.active : !walk.aktiv) return say(imPanorama ? 'Panorama verlassen – kein Befund gesetzt' : 'Begehen beendet – kein Befund gesetzt');
    if (!befund && toolbar.tool() !== werkzeug) return say('Werkzeug gewechselt – kein Befund gesetzt');
    // Im Panorama prüft die Suche die Ansicht nicht selbst (keine Sichtbarkeitsregel): hier, wie beim Messen.
    if (imPanorama && !(gleichePose(kameraPose(), pose0) && pano.state?.station === station0)) return say('abgebrochen – Ansicht hat sich bewegt');
    if (e.art === 'abgebrochen') return say(e.grund === 'ansicht' ? 'abgebrochen – Ansicht hat sich bewegt' : 'abgebrochen');
    if (e.art === 'kein-treffer') {
      const grund = cloudVisible() ? keinPunktGrund(e.info, e.grund) : 'hidden';
      return say(keinPunktText(grund, { folge: 'kein Befund gesetzt', ausgeblendet: hiddenCloudMessage, feinGrund: e.info.fineReason, mesh:e.info.source === 'mesh' }));
    }
    // Im Panorama trägt der Befund den Standpunkt.
    if (o.pois()?.openAt(e.punkt, station0,{positionSource:sourceFromPick(e.info)})) {
      o.hinweis?.gesetzt(befund);
      // Das Formular braucht die Tastatur: Pointer-Lock frei, gehaltene Tasten los.
      if (!imPanorama) walk.zeigerFreigeben();
      say('Befund am Zielpunkt, Formular geöffnet');
    }
  };

  // Panorama-Fadenkreuz: eigene Setz-Warteschlange (Reihenfolge wie im Begehen). Solange ein Setzen läuft, eine
  // Rückfrage oder das Formular offen ist, drehen die Pfeiltasten nicht — das Ergebnis gehört zu diesem Blick.
  const leinwandEl = v.instance.domElement;
  const panoramaZiel = (): boolean => pano.active && document.activeElement === leinwandEl && toolbar.tool() !== 'navigate';
  const panoSetzLaeuft = new SetzZaehler();
  let panoKette: Promise<void> = Promise.resolve();
  /** Leertaste im Panorama-Fadenkreuz gedrückt (ohne offene Rückfrage): erst ihr Loslassen setzt. */
  let panoSetzBereit = false;
  const panoAngehalten = (): boolean => panoSetzLaeuft.laeuft || rueckfrage.offen() || (o.pois()?.formularOffen() ?? false);
  const panoSetzen = (befund: boolean): Promise<void> => {
    if (o.pois()?.formularOffen()) {
      say(FORMULAR_OFFEN);
      return Promise.resolve();
    }
    if (rueckfrage.offen()) {
      say('Erst die Rückfrage beantworten');
      return Promise.resolve();
    }
    const vorher = panoKette;
    const fertig = panoSetzLaeuft.start();
    panoKette = setzenImBegehen({ zielen: false, vorher, befund })
      .then(fertig, (e: unknown) => {
        fertig();
        console.error('Setzen fehlgeschlagen:', e);
        say('Punkt konnte nicht ausgewertet werden');
      })
      .finally(zeigeFadenkreuz);
    return panoKette;
  };
  const panoHalte = (a: HalteAktion, gedrueckt: boolean): void => {
    if (!pano.active || walk.aktiv) return;
    if (a === 'begehen.setzen') {
      if (!gedrueckt) {
        // Ein Loslassen ohne eigenes Drücken (z. B. die Leertaste, die eben die Rückfrage beantwortet hat) setzt nichts.
        if (!panoSetzBereit) return;
        panoSetzBereit = false;
        void panoSetzen(false);
        return;
      }
      if (rueckfrage.offen()) {
        say('Erst die Rückfrage beantworten');
        return;
      }
      panoSetzBereit = true;
      return;
    }
    if (!gedrueckt) return;
    if (panoAngehalten()) {
      // Nicht still verwerfen: wer gleich nach dem Setzen dreht, erfährt, warum die Ansicht stehen bleibt.
      say(panoSetzLaeuft.laeuft ? 'Setzen läuft – Drehen danach' : rueckfrage.offen() ? 'Erst die Rückfrage beantworten' : FORMULAR_OFFEN);
      return;
    }
    const s = pano.state;
    if (!s) return;
    const grad = o.einstellungen().drehSchrittGrad;
    if (a === 'begehen.drehen-links') pano.setView({ yaw: s.yaw + grad });
    else if (a === 'begehen.drehen-rechts') pano.setView({ yaw: s.yaw - grad });
    else if (a === 'begehen.neigen-hoch') pano.setView({ pitch: s.pitch + grad });
    else if (a === 'begehen.neigen-runter') pano.setView({ pitch: s.pitch - grad });
  };

  // Fadenkreuz mit Vorschau und Live-Wert: nur im Begehen. Die Vorschau pickt nur bei geänderter Kamera und
  // höchstens jedes dritte Bild (ein GPU-Pick kostet einige Millisekunden); gesetzt wird allein über die
  // feine Suche, die Vorschau zeigt nur an. Im Panorama-Fadenkreuz nur das Kreuz (Zustand `neutral`); es
  // bleibt stehen, solange dort ein Setzen läuft (die Rückfrage nimmt der Leinwand den Fokus).
  const fadenkreuz = createFadenkreuz(document.body);
  fadenkreuz.groesse(o.einstellungen().fadenkreuzPx);
  function zeigeFadenkreuz(): void {
    const imZiel = panoramaZiel();
    if (!imZiel) panoSetzBereit = false;
    if (walk.aktiv) return fadenkreuz.zeige(true);
    const panoKreuz = pano.active && (imZiel || panoSetzLaeuft.laeuft);
    fadenkreuz.zeige(panoKreuz);
    if (!panoKreuz) return;
    fadenkreuz.zielen(false);
    fadenkreuz.aktualisiere({ zustand: 'neutral', punktBildschirm: null, text: null });
  }
  leinwandEl.addEventListener('focus', zeigeFadenkreuz);
  leinwandEl.addEventListener('blur', zeigeFadenkreuz);
  toolbar.onChange(zeigeFadenkreuz);
  let panoVorher = pano.active;
  pano.onChange(() => {
    // Panorama verlassen: keine Rückfrage bleibt stehen (sie gehörte zum Fadenkreuz bzw. zum Klick dort).
    if (panoVorher && !pano.active) rueckfrage.abbreche();
    panoVorher = pano.active;
    zeigeFadenkreuz();
  });
  const cam3 = v.instance.view.camera;
  const vorschauKamera = cam3 instanceof PerspectiveCamera ? cam3 : null;
  let pose: number[] | null = null;
  let bilderSeitPick = 0;
  let vorherGleich = false;
  let az: AnsageZustand = startZustand;
  // Nach dem Start bleibt die Startansage hörbar: Zustandsansagen kommen erst danach.
  let stilleBis = 0;
  let letzteArt = '';
  const projiziert = new Vector3();
  const vorschauSchritt = (erzwingen: boolean): void => {
    if (!vorschauKamera) return;
    const c = vorschauKamera;
    const jetztPose = [c.position.x, c.position.y, c.position.z, c.quaternion.x, c.quaternion.y, c.quaternion.z, c.quaternion.w, c.fov, c.aspect];
    // Mit Toleranz: die Geschwindigkeit klingt exponentiell ab, ohne je genau 0 zu werden.
    const poseGleich = pose !== null && pose.every((x, i) => Math.abs(x - jetztPose[i]!) <= (i < 3 ? 1e-6 : 1e-7));
    bilderSeitPick++;
    // Bewegungsbeginn nach Ruhe: sofort picken (die Drosselung gilt nur während der Bewegung).
    if (!poseGleich && vorherGleich) bilderSeitPick = 99;
    vorherGleich = poseGleich;
    const r = v.instance.domElement.getBoundingClientRect();
    const frisch = vorschauNoetig({ hat: letzteVorschau !== null, poseGleich, bilderSeitPick, erzwingen });
    if (frisch) {
      letzteVorschau = picker.vorschauAtScreen(r.x + r.width / 2, r.y + r.height / 2);
      pose = jetztPose;
      bilderSeitPick = 0;
    }
    const vs = letzteVorschau!;
    const pkt = vs.punktWelt;
    // Wert: Strecke zum letzten gesetzten Punkt bzw. Höhenunterschied bei offener Zeichnung, sonst Tiefe.
    let wertM: number | null = null;
    let art: 'ziel' | 'strecke' | 'hoehe' = 'ziel';
    if (pkt) {
      const offen = tool.offeneZeichnung();
      const letzter = offen.punkte[offen.punkte.length - 1];
      if (letzter) {
        if (offen.kind === 'height') {
          art = 'hoehe';
          wertM = Math.abs(pkt[2] - letzter[2]);
        } else {
          art = 'strecke';
          wertM = Math.hypot(pkt[0] - letzter[0], pkt[1] - letzter[1], pkt[2] - letzter[2]);
        }
      } else {
        wertM = vs.tiefeM;
      }
    }
    const sprechText = pkt ? (wertM === null ? 'Treffer' : liveText({ wertM, art })) : 'kein Treffer';
    let punktBildschirm: { x: number; y: number } | null = null;
    if (pkt) {
      c.updateMatrixWorld();
      projiziert.set(pkt[0], pkt[1], pkt[2]).project(c);
      if (projiziert.z < 1) punktBildschirm = { x: r.x + ((projiziert.x + 1) / 2) * r.width, y: r.y + ((1 - projiziert.y) / 2) * r.height };
    }
    fadenkreuz.aktualisiere({
      zustand: pkt ? 'treffer' : 'kein',
      punktBildschirm,
      text: sprechText,
      kurz: pkt ? (wertM === null ? 'Treffer' : kurzText(wertM)) : 'kein Treffer',
      frisch,
    });
    const artKey = `${art}:${tool.offeneZeichnung().punkte.length}`;
    if (artKey !== letzteArt) {
      // Neue Messart oder Punktzahl: der Wert ist ein anderer, der Anker gilt nicht mehr.
      az = { ...startZustand, treffer: az.treffer };
      letzteArt = artKey;
    }
    // In der Startstille bleibt der Ansagezustand unberührt: danach wird der dann aktuelle Zustand einmal angesagt.
    const jetzt = performance.now();
    if (jetzt < stilleBis) return;
    const s = ansageSchritt(az, { t: jetzt, treffer: pkt !== null, wertM, text: (m) => liveText({ wertM: m, art }) });
    az = s.zustand;
    if (s.ansage) ansage.melde(s.ansage);
  };
  // Solange das Befund-Formular oder eine Rückfrage offen ist, ruht der Live-Wert (keine Vorschau, keine Ansage) —
  // sonst überschriebe „Treffer …“ deren Ansage (z. B. „Rückfrage offen – Esc bricht ab“). Danach beginnt die
  // Ansage wie beim Start mit einem Stillefenster.
  let ruhteVorher = false;
  walk.onBild(() => {
    fadenkreuz.zielen(walk.zielt());
    const ruht = (o.pois()?.formularOffen() ?? false) || rueckfrage.offen();
    if (ruhteVorher && !ruht) {
      az = startZustand;
      letzteArt = '';
      stilleBis = performance.now() + 1500;
    }
    ruhteVorher = ruht;
    if (!ruht) vorschauSchritt(false);
  });
  walk.onChange((aktiv) => {
    // Endet das Begehen, bleibt keine Rückfrage stehen (sie gehörte zum Fadenkreuz).
    if (!aktiv) rueckfrage.abbreche();
    zeigeFadenkreuz();
    letzteVorschau = null;
    pose = null;
    az = startZustand;
    letzteArt = '';
    stilleBis = aktiv ? performance.now() + 1500 : 0;
  });

  // Gamepad: nur im Begehen, je Bild über die Eingabequelle des Begehens. Bei offener Rückfrage
  // bestätigt A, B bricht sie ab (statt das Begehen zu beenden), das Steuerkreuz oben/unten wechselt.
  const verbindePad = (run: (a: KeyAction) => void): void => {
    const padKante = (a: PadAktion, runter: boolean): void => {
      // Modaler Dialog (Tastenhilfe, Einstellungen) oder Befund-Formular offen: der Controller wirkt nicht — sonst
      // wechselte LB/RB das Werkzeug (und verwürfe das Formular), RT setzte dahinter einen Messpunkt.
      if (document.querySelector('dialog[open]') || o.pois()?.formularOffen()) return;
      o.hinweis?.pad(a, runter);
      if (rueckfrage.offen() && (a === 'bestaetigen' || a === 'zurueck' || a === 'hoch' || a === 'runter')) {
        if (!runter) return;
        if (a === 'bestaetigen') rueckfrage.bestaetige();
        else if (a === 'zurueck') rueckfrage.abbreche();
        else rueckfrage.wechsle();
        return;
      }
      if (a === 'zielen') walk.halte('begehen.zielen', runter);
      else if (a === 'setzen') walk.halte('begehen.setzen', runter);
      if (!runter) return;
      switch (a) {
        case 'werkzeug-voriges':
          return run({ type: 'werkzeug-wechsel', richtung: -1 });
        case 'werkzeug-naechstes':
          return run({ type: 'werkzeug-wechsel', richtung: 1 });
        case 'standpunkt-voriger':
          return run({ type: 'standpunkt', richtung: -1 });
        case 'standpunkt-naechster':
          return run({ type: 'standpunkt', richtung: 1 });
        case 'karte':
          return run({ type: 'toggle-panel', panel: 'karte' });
        case 'befund':
          return run({ type: 'befund' });
        case 'zurueck':
          return walk.beenden(austrittsTreffer());
        default:
          return; // zielen/setzen oben; hoch/runter wirken gehalten über die Eingabequelle
      }
    };
    const padQuelle = erzeugePadQuelle({
      belegung: () => padBelegung(o.belegung()),
      invertY: () => o.einstellungen().invertY,
      melde: say,
      kante: padKante,
    });
    walk.setzeEingabeQuelle(padQuelle.quelle);
    walk.onChange(() => padQuelle.zuruecksetzen());
  };

  return {
    setzen: setzenImBegehen,
    befundSetzen: () => (walk.aktiv ? walk.befund() : panoramaZiel() ? panoSetzen(true) : Promise.resolve()),
    panoramaZiel,
    panoHalte,
    austrittsTreffer,
    groesse: (px) => fadenkreuz.groesse(px),
    ansageJetzt: () => {
      if (!walk.aktiv) return;
      vorschauSchritt(true);
      ansage.melde(fadenkreuz.aktuellerText() ?? 'kein Treffer');
    },
    verbindePad,
  };
}
