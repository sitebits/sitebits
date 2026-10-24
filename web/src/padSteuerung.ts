// Controller (DOM-Adapter): je Bild einmal abgefragt, solange ein Controller mit Standardbelegung verbunden ist, und
// in allen Ansichten wirksam — Übersicht (Orbit), Begehen, Panorama und modale Dialoge. Die Eingabeschicht ist
// getrennt: gamepad.ts (Rohwerte → Totzone/Kurve → Aktionen mit Flanken), padGefuehl.ts (Drehrate mit Beschleunigung
// und Nachlauf, dt-basiert), padModus.ts (Bedeutung je Ansicht, Symbole, Leisten). Hier nur Abfrage, Verteilung,
// Kamera in Übersicht und Panorama, Menü, Belegungsübersicht, Kontextleiste, Meldung beim Verbinden und Vibration.
// Im Begehen liest dessen Bildschleife über `quelle` (eine Abfrage je Bild, nie zwei Leser).
import { Vector3 } from 'three';
import type { BedienEinstellungen } from './bedienEinstellungen';
import { bewegungReduziert, drehenInSchritten } from './bedienEinstellungen';
import type { Begehen, EingabeQuelle } from './begehen';
import { controllerTyp, kanten, lies, stickSchritt } from './gamepad';
import type { PadAktion, PadLage, PadRoh, PadTyp } from './gamepad';
import { drehRuhe, drehSchritt, zoomFaktor } from './padGefuehl';
import type { DrehRaten, DrehZustand } from './padGefuehl';
import { belegungsTabelle, kontextLeiste, MODUS_TITEL, symbol, TYP_NAME } from './padModus';
import type { PadKontext, PadModus } from './padModus';
import type { Panorama } from './panorama';
import type { RueckfrageDialog } from './rueckfrage';
import type { Viewer } from './viewer';

export const MELDUNG_KEIN_STANDARD = 'Controller ohne Standardbelegung wird nicht unterstützt';

/** Begehen: Umschauen waagerecht/senkrecht [rad/s] bei vollem Ausschlag (vor Beschleunigung). */
export const RATEN_BEGEHEN: DrehRaten = { waagerecht: 2.4, senkrecht: 2.4 * 0.65 };
/** Übersicht: Drehen um den Drehpunkt [rad/s]. */
export const RATEN_ORBIT: DrehRaten = { waagerecht: 2.0, senkrecht: 1.3 };
/** Panorama: Umsehen [°/s] bei 80° Sichtfeld; enger gezoomt dreht es langsamer. */
export const RATEN_PANORAMA: DrehRaten = { waagerecht: 120, senkrecht: 120 * 0.65 };
const PANO_BEZUG_FOV = 80;
/** Übersicht: Verschieben je Sekunde als Anteil des Abstands Kamera–Drehpunkt (passt sich dem Maßstab an). */
export const VERSCHIEBEN_JE_ABSTAND = 0.9;
const SCHNELL_ORBIT = 3;
/** Zielen im Begehen: Drehrate × 0,4. */
const ZIELEN_FAKTOR = 0.4;
/** Menü: gehaltene Richtung wiederholt nach 0,4 s alle 0,12 s. */
const WIEDERHOLEN_AB_S = 0.4;
const WIEDERHOLEN_S = 0.12;
const SUCHE_MS = 1000;

interface PadAblesbar extends PadRoh {
  connected: boolean;
  mapping: string;
  vibrationActuator?: { playEffect?: (typ: string, p: Record<string, number>) => Promise<unknown> } | null;
}

export interface PadSteuerung {
  /** Eingabequelle für die Bildschleife des Begehens. */
  quelle(dt: number): EingabeQuelle | null;
  /** Kurz vibrieren (Setzen, Bestätigen), wenn eingeschaltet und vom Browser angeboten. */
  vibriere(): void;
  /** Verbundener Controller-Typ (Standardbelegung) oder null. */
  typ(): PadTyp | null;
  /** Wurde der Controller zuletzt benutzt (statt Tastatur/Maus)? */
  benutzt(): boolean;
  /** Belegungsübersicht ein-/ausblenden (auch für Tests). */
  belegungZeigen(an: boolean): void;
}

export interface MenueEintrag { text: string; tue: () => void }

export function attachPadSteuerung(o: {
  v: Viewer;
  pano: Panorama;
  begehen: Begehen;
  rueckfrage: RueckfrageDialog;
  einstellungen: () => BedienEinstellungen;
  systemReduziert: () => boolean;
  belegung: () => Record<PadAktion, number>;
  say: (t: string) => void;
  /** Vorübergehende, sichtbare und angesagte Meldung (Controller verbunden). */
  hinweis: (t: string) => void;
  werkzeug: () => string;
  zeichnung: () => boolean;
  ziehen: () => boolean;
  formularOffen: () => boolean;
  empfang: () => boolean;
  durchKamera: () => boolean;
  /** Aktion je Knopf in Übersicht/Begehen/Panorama (main.ts kennt Verteiler und Zustände). */
  aktion: {
    werkzeug(richtung: -1 | 1): void;
    standpunkt(richtung: -1 | 1): void;
    fang(): void;
    allesZeigen(): void;
    begehenStarten(): void;
    befund(): void;
    /** Übersicht: Punkt bzw. Befund in Bildmitte. */
    mitteSetzen(): void;
    /** Panorama-Fadenkreuz: Setzen (Drücken/Loslassen wie die Leertaste). */
    panoSetzen(gedrueckt: boolean): void;
    abbrechenZiehen(): void;
    abbrechenZeichnung(): void;
    begehenBeenden(): void;
    panoramaVerlassen(): void;
  };
  menue: () => MenueEintrag[];
  /** Controller benutzt bzw. nicht mehr (Tastenhinweise treten zurück, Fadenkreuz in der Übersicht). */
  benutztAenderung?: (an: boolean) => void;
  /** Nur für Tests: Pads lesen (Vorgabe navigator.getGamepads). */
  pads?: () => ArrayLike<PadAblesbar | null> | null;
}): PadSteuerung {
  const { v, pano, begehen: walk, rueckfrage } = o;
  const leinwand = v.instance.domElement;
  const lese = o.pads ?? ((): ArrayLike<PadAblesbar | null> | null => {
    try {
      return (navigator.getGamepads?.() ?? null) as unknown as ArrayLike<PadAblesbar | null> | null;
    } catch {
      return null;
    }
  });

  // --- Zustand -----------------------------------------------------------------------------------------------
  let pad: PadAblesbar | null = null;
  let padTyp: PadTyp | null = null;
  let padId = '';
  let vorher: ReadonlySet<PadAktion> | null = null;
  /** Knöpfe, die beim Wechsel der Ansicht gehalten waren: ihre Flanken gehören der vorigen Ansicht. */
  let geschluckt = new Set<PadAktion>();
  let modusVorher: PadModus | null = null;
  let benutztAn = false;
  let gemeldetFremd = false;
  let dreh: DrehZustand = drehRuhe();
  let drehBereit = true;
  let neigBereit = true;
  let sprint = false;
  let menueRichtung = 0;
  let menueGehalten = 0;
  let menueBereit = true;
  let raf: number | null = null;
  let zuletzt = 0;

  // --- DOM: Kontextleiste, Belegungsübersicht, Meldung, Menü -----------------------------------------------------
  const leiste = document.createElement('div');
  leiste.className = 'sv-pad-leiste';
  leiste.id = 'pad-leiste';
  leiste.hidden = true;
  // Dieselben Angaben stehen in der Belegungsübersicht und im Dialog „Bedienung“; die Leiste ist nur Blickhilfe.
  leiste.setAttribute('aria-hidden', 'true');
  const uebersicht = document.createElement('section');
  uebersicht.className = 'sv-pad-belegung';
  uebersicht.id = 'pad-belegung';
  uebersicht.setAttribute('aria-label', 'Controller-Belegung');
  uebersicht.hidden = true;
  document.body.append(leiste, uebersicht);

  const menueDialog = document.createElement('dialog');
  menueDialog.className = 'sv-dialog sv-pad-menue';
  const menueTitel = document.createElement('h2');
  menueTitel.id = 'sv-dialog-menue';
  menueTitel.textContent = 'Menü';
  menueDialog.setAttribute('aria-labelledby', menueTitel.id);
  const menueListe = document.createElement('div');
  menueListe.className = 'sv-pad-menue-liste';
  menueDialog.append(menueTitel, menueListe);
  document.body.append(menueDialog);
  let menueZurueck: HTMLElement | null = null;
  menueDialog.addEventListener('close', () => {
    menueZurueck?.focus();
    menueZurueck = null;
  });
  const oeffneMenue = (): void => {
    if (menueDialog.open) return;
    walk.zeigerFreigeben();
    menueZurueck = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const knoepfe = o.menue().map((e) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.textContent = e.text;
      b.addEventListener('click', () => {
        menueDialog.close();
        e.tue();
      });
      return b;
    });
    const weiter = document.createElement('button');
    weiter.type = 'button';
    weiter.textContent = 'Weiter';
    weiter.addEventListener('click', () => menueDialog.close());
    menueListe.replaceChildren(weiter, ...knoepfe);
    menueDialog.showModal();
    weiter.focus();
  };

  // Die Leiste weicht aus: liegt sie auf Werkzeugleiste, Panels, Meldungen oder Formularen, tritt sie zurück (dieselben
  // Angaben stehen in der Belegungsübersicht).
  const ANDERE = '#toolbar, #panel-menu, .sv-frame, .sv-move-menu, .sv-zurueck, #msg, #scan-hint, .sv-koordinaten, .sv-hinweise, '
    + '.sv-pano-aus, .sv-gast, .sv-fotos-werkzeug, .sv-befundkarte, .sv-befundliste, .sv-trenner, .sv-teilen, .sv-entwurf, '
    + '#poi-form, .sv-blatt-griff, .sv-rueckfrage';
  const rechtecke = (sel: string): DOMRect[] =>
    Array.from(document.querySelectorAll(sel), (a) => a.getBoundingClientRect()).filter((b) => b.width > 0 && b.height > 0);
  const ausweichen = (): void => {
    leiste.classList.remove('sv-verdraengt');
    if (leiste.hidden) return;
    const r = leiste.getBoundingClientRect();
    const trifft = rechtecke(ANDERE).some((b) => r.left < b.right && b.left < r.right && r.top < b.bottom && b.top < r.bottom);
    leiste.classList.toggle('sv-verdraengt', trifft);
  };
  /** Belegungsübersicht in die freie Fläche der 3D-Ansicht legen: unter die Bänder oben (Werkzeugleiste, Meldung),
   *  über die Bänder unten, zwischen die Panels links und rechts. */
  const platziereUebersicht = (): void => {
    if (uebersicht.hidden) return;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    let l = 8;
    let r = vw - 8;
    let t = 8;
    let u = vh - 8;
    for (const b of rechtecke(`${ANDERE}, .sv-livewert`)) {
      const band = b.height < 140 && (b.top < vh * 0.25 || b.bottom > vh * 0.75);
      if (band && b.top < vh / 2) t = Math.max(t, b.bottom + 8);
      else if (band) u = Math.min(u, b.top - 8);
      else if (b.left + b.width / 2 < vw / 2) l = Math.max(l, b.right + 8);
      else r = Math.min(r, b.left - 8);
    }
    const breite = Math.max(0, Math.min(r - l, 480));
    // Nie in Notch, Island oder Home-Indikator (Safe Area): die Werte aus CSS gelten als Mindestabstand.
    const links = `max(${Math.round(l + (r - l - breite) / 2)}px, calc(var(--sv-sa-links) + 8px))`;
    const oben = `max(${Math.round(t)}px, calc(var(--sv-sa-oben) + 8px))`;
    uebersicht.style.left = links;
    uebersicht.style.top = oben;
    uebersicht.style.width = `min(${Math.round(breite)}px, calc(100vw - var(--sv-sa-rechts) - 8px - ${links}))`;
    uebersicht.style.maxHeight = `max(0px, calc(min(${Math.round(u)}px, 100vh - var(--sv-sa-unten) - 8px) - ${oben}))`;
  };
  window.addEventListener('resize', () => {
    ausweichen();
    platziereUebersicht();
  });

  // --- Ansicht ------------------------------------------------------------------------------------------------
  const offenerDialog = (): HTMLDialogElement | null => document.querySelector<HTMLDialogElement>('dialog[open]');
  const modus = (): PadModus => {
    if (offenerDialog()) return 'menue';
    if (walk.aktiv) return 'begehen';
    if (pano.active) return 'panorama';
    return 'uebersicht';
  };
  const kontext = (): PadKontext => ({
    modus: modus(), werkzeug: o.werkzeug() !== 'navigate', zeichnung: o.zeichnung(), rueckfrage: rueckfrage.offen(),
  });

  const zeichneLeiste = (): void => {
    // Die Belegungsübersicht nennt alles, was die Leiste nennt: solange sie offen ist, ruht die Leiste.
    const sichtbar = benutztAn && padTyp !== null && !o.formularOffen() && uebersicht.hidden;
    leiste.hidden = !sichtbar;
    if (!sichtbar || padTyp === null) return;
    const teile = kontextLeiste(kontext(), padTyp, o.belegung());
    leiste.replaceChildren(...teile.map((t) => {
      const s = document.createElement('span');
      const k = document.createElement('b');
      k.textContent = t.knopf;
      s.append(k, ` ${t.text}`);
      return s;
    }));
    ausweichen();
  };
  const zeichneUebersicht = (): void => {
    if (uebersicht.hidden) return;
    const typ = padTyp ?? 'neutral';
    const m = modus();
    const h = document.createElement('h2');
    h.textContent = `Controller-Belegung: ${MODUS_TITEL[m]}`;
    const info = document.createElement('p');
    info.textContent = `${TYP_NAME[typ]}${padId ? ` – ${padId}` : ''}`;
    info.className = 'sv-pad-id';
    const t = document.createElement('table');
    const body = document.createElement('tbody');
    for (const z of belegungsTabelle(m, typ, o.belegung())) {
      const tr = document.createElement('tr');
      const th = document.createElement('th');
      th.scope = 'row';
      th.textContent = z.eingabe;
      const td = document.createElement('td');
      td.textContent = z.wirkung;
      tr.append(th, td);
      body.append(tr);
    }
    t.append(body);
    const fuss = document.createElement('p');
    fuss.textContent = `${symbol(typ, o.belegung().belegung) || 'Auswahl'} blendet aus`;
    uebersicht.replaceChildren(h, info, t, fuss);
  };
  const belegungZeigen = (an: boolean): void => {
    uebersicht.hidden = !an;
    zeichneUebersicht();
    zeichneLeiste();
    platziereUebersicht();
  };

  const setzeBenutzt = (an: boolean): void => {
    if (benutztAn === an) return;
    benutztAn = an;
    o.benutztAenderung?.(an);
    zeichneLeiste();
  };
  // Tastatur oder Maus benutzt: die Controller-Leiste tritt zurück.
  window.addEventListener('keydown', () => setzeBenutzt(false), true);
  window.addEventListener('pointerdown', () => setzeBenutzt(false), true);

  const vibriere = (): void => {
    if (!benutztAn || !o.einstellungen().vibration) return;
    const a = pad?.vibrationActuator;
    if (!a || typeof a.playEffect !== 'function') return;
    try {
      a.playEffect('dual-rumble', { duration: 40, strongMagnitude: 0.25, weakMagnitude: 0.5 }).catch(() => undefined);
    } catch {
      /* ohne Vibration */
    }
  };

  // --- Abfrage -------------------------------------------------------------------------------------------------
  const finde = (): PadAblesbar | null => {
    const liste = lese();
    let fremd = false;
    for (let i = 0; liste && i < liste.length; i++) {
      const p = liste[i];
      if (!p || !p.connected) continue;
      if (p.mapping === 'standard') return p;
      fremd = true;
    }
    if (fremd && !gemeldetFremd) {
      gemeldetFremd = true;
      o.say(MELDUNG_KEIN_STANDARD);
    }
    return null;
  };

  /** Alles Gehaltene loslassen (Abstecken, Ansicht beendet). */
  const loslassen = (): void => {
    if (vorher?.has('bestaetigen') && walk.aktiv) walk.halte('begehen.setzen', false);
    if (vorher?.has('zielen') && walk.aktiv) walk.halte('begehen.zielen', false);
    vorher = null;
    dreh = drehRuhe();
    sprint = false;
  };

  const verbunden = (p: PadAblesbar): void => {
    pad = p;
    padId = p.id;
    padTyp = controllerTyp(p.id);
    const b = o.belegung();
    const typ = padTyp;
    const k = (a: PadAktion): string => symbol(typ, b[a]);
    const text = `Controller verbunden (${TYP_NAME[typ]}). ${k('bestaetigen')} setzen, ${k('zurueck')} zurück, ${k('belegung')} zeigt die Belegung.`;
    o.hinweis(text);
    setzeBenutzt(true);
    zeichneUebersicht();
  };
  const getrennt = (): void => {
    loslassen();
    pad = null;
    padTyp = null;
    padId = '';
    setzeBenutzt(false);
    uebersicht.hidden = true;
    leiste.hidden = true;
  };

  /** Ein Bild: Controller lesen, Flanken verteilen. null = kein Controller. */
  const abtasten = (): PadLage | null => {
    const p = finde();
    if (!p) {
      if (pad) getrennt();
      return null;
    }
    if (!pad || p.id !== padId) verbunden(p);
    pad = p;
    const lage = lies(p, o.belegung(), { invertY: o.einstellungen().invertY });
    const m = modus();
    if (modusVorher !== null && m !== modusVorher) {
      // Ansicht gewechselt (oft durch einen Knopf dieses Controllers): was jetzt gehalten ist, löst nichts mehr aus.
      geschluckt = new Set(lage.gedrueckt);
      dreh = drehRuhe();
      sprint = false;
    }
    modusVorher = m;
    const alt = vorher ?? lage.gedrueckt; // erstes Bild: Gehaltenes löst nichts aus
    const k = kanten(alt, lage.gedrueckt);
    vorher = lage.gedrueckt;
    for (const a of geschluckt) if (!lage.gedrueckt.has(a)) geschluckt.delete(a);
    const bewegt = lage.links.x !== 0 || lage.links.y !== 0 || lage.rechts.x !== 0 || lage.rechts.y !== 0 || lage.hochAchse !== 0;
    if (k.runter.length > 0 || bewegt) setzeBenutzt(true);
    for (const a of k.hoch) if (!geschluckt.has(a)) kante(a, false);
    for (const a of k.runter) if (!geschluckt.has(a)) kante(a, true);
    if (k.runter.length > 0 || k.hoch.length > 0) zeichneLeiste();
    return lage;
  };

  // --- Knöpfe je Ansicht ----------------------------------------------------------------------------------------
  const zurueck = (): void => {
    if (!uebersicht.hidden) return belegungZeigen(false);
    if (o.ziehen()) return o.aktion.abbrechenZiehen();
    if (o.zeichnung()) return o.aktion.abbrechenZeichnung();
    if (walk.aktiv) return o.aktion.begehenBeenden();
    if (pano.active || o.durchKamera()) return o.aktion.panoramaVerlassen();
  };

  const kante = (a: PadAktion, runter: boolean): void => {
    // Befund-Formular offen: es braucht die Tastatur, der Controller ruht (außer der Belegungsübersicht).
    if (o.formularOffen() && a !== 'belegung') return;
    if (a === 'belegung') {
      if (runter) belegungZeigen(uebersicht.hidden);
      return;
    }
    const m = modus();
    if (m === 'menue') return menueKante(a, runter);
    if (a === 'menue') {
      if (runter) oeffneMenue();
      return;
    }
    if (rueckfrage.offen() && (a === 'bestaetigen' || a === 'zurueck' || a === 'tempo-hoch' || a === 'tempo-runter')) {
      if (!runter) return;
      if (a === 'bestaetigen') {
        rueckfrage.bestaetige();
        vibriere();
      } else if (a === 'zurueck') rueckfrage.abbreche();
      else rueckfrage.wechsle();
      return;
    }
    if (o.empfang()) {
      // Empfängeransicht: nur ansehen (Drehen, Zoomen, Standpunkt, Ausrichten), nichts setzen.
      if (!runter) return;
      if (a === 'standpunkt-voriger') o.aktion.standpunkt(-1);
      else if (a === 'standpunkt-naechster') o.aktion.standpunkt(1);
      else if (a === 'ausrichten') o.aktion.allesZeigen();
      else if (a === 'zurueck' && pano.active) o.aktion.panoramaVerlassen();
      return;
    }
    if (o.durchKamera() && !walk.aktiv) {
      if (runter && a === 'zurueck') o.aktion.panoramaVerlassen();
      return;
    }
    // Ansichtsübergreifend gleich:
    if (runter) {
      switch (a) {
        case 'werkzeug-voriges': return o.aktion.werkzeug(-1);
        case 'werkzeug-naechstes': return o.aktion.werkzeug(1);
        case 'standpunkt-voriger': return o.aktion.standpunkt(-1);
        case 'standpunkt-naechster': return o.aktion.standpunkt(1);
        case 'zurueck': return zurueck();
        default: break;
      }
    }
    if (m === 'begehen') {
      if (a === 'bestaetigen') return walk.halte('begehen.setzen', runter);
      if (a === 'zielen') return walk.halte('begehen.zielen', runter);
      if (!runter) return;
      if (a === 'kontext') return o.aktion.befund();
      if (a === 'tempo-hoch') return walk.tempoAendern(1);
      if (a === 'tempo-runter') return walk.tempoAendern(-1);
      if (a === 'schnell') sprint = true;
      if (a === 'ausrichten') {
        dreh = drehRuhe(); // kein Nachlauf über das Ausrichten hinaus
        walk.blickWaagerecht();
      }
      return;
    }
    if (m === 'panorama') {
      // Das Panorama-Fadenkreuz hängt am Fokus der Leinwand.
      if (document.activeElement !== leinwand) leinwand.focus({ preventScroll: true });
      if (a === 'bestaetigen') return o.aktion.panoSetzen(runter);
      if (!runter) return;
      if (a === 'kontext') return o.aktion.befund();
      if (a === 'zielen') return o.aktion.fang();
      if (a === 'ausrichten') {
        dreh = drehRuhe();
        pano.setView({ pitch: 0 });
      }
      return;
    }
    // Übersicht
    if (!runter) return;
    if (a === 'bestaetigen') {
      if (o.werkzeug() === 'navigate') return o.say(`Kein Werkzeug gewählt – ${symbol(padTyp ?? 'neutral', o.belegung()['werkzeug-naechstes'])} wählt ein Werkzeug`);
      return o.aktion.mitteSetzen();
    }
    if (a === 'kontext') return o.aktion.begehenStarten();
    if (a === 'zielen') return o.aktion.fang();
    if (a === 'ausrichten') return o.aktion.allesZeigen();
  };

  // --- Menü und Dialoge -----------------------------------------------------------------------------------------
  const FOKUSSIERBAR = 'button, select, input, textarea, a[href], [tabindex]:not([tabindex="-1"])';
  const fokussierbare = (d: HTMLDialogElement): HTMLElement[] =>
    Array.from(d.querySelectorAll<HTMLElement>(FOKUSSIERBAR)).filter((e) =>
      !(e as HTMLButtonElement).disabled && e.getClientRects().length > 0 && !e.closest('[hidden]'));
  const bewegeFokus = (r: 1 | -1): void => {
    const d = offenerDialog();
    if (!d) return;
    const liste = fokussierbare(d);
    if (liste.length === 0) return;
    const i = liste.indexOf(document.activeElement as HTMLElement);
    const j = i < 0 ? (r > 0 ? 0 : liste.length - 1) : (i + r + liste.length) % liste.length;
    liste[j]!.focus();
    liste[j]!.scrollIntoView?.({ block: 'nearest' });
  };
  const aendereWert = (r: 1 | -1): boolean => {
    const e = document.activeElement;
    if (e instanceof HTMLSelectElement) {
      const n = e.options.length;
      if (n === 0) return true;
      e.selectedIndex = Math.min(n - 1, Math.max(0, e.selectedIndex + r));
      e.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    if (e instanceof HTMLInputElement && (e.type === 'number' || e.type === 'range')) {
      if (r > 0) e.stepUp();
      else e.stepDown();
      e.dispatchEvent(new Event('change', { bubbles: true }));
      return true;
    }
    return false;
  };
  const menueKante = (a: PadAktion, runter: boolean): void => {
    if (!runter) return;
    const d = offenerDialog();
    if (!d) return;
    switch (a) {
      case 'tempo-hoch': return bewegeFokus(-1);
      case 'tempo-runter': return bewegeFokus(1);
      case 'standpunkt-voriger':
        if (!aendereWert(-1)) bewegeFokus(-1);
        return;
      case 'standpunkt-naechster':
        if (!aendereWert(1)) bewegeFokus(1);
        return;
      case 'bestaetigen': {
        const e = document.activeElement;
        if (e instanceof HTMLSelectElement) {
          // Auswahllisten öffnen sich per Skript nicht: ✕ geht zur nächsten Wahl, am Ende zurück zur ersten.
          e.selectedIndex = (e.selectedIndex + 1) % Math.max(1, e.options.length);
          e.dispatchEvent(new Event('change', { bubbles: true }));
        } else if (e instanceof HTMLElement && d.contains(e)) {
          e.click();
        }
        vibriere();
        return;
      }
      case 'zurueck':
      case 'menue':
        d.close();
        return;
      default:
        return;
    }
  };
  const menueStick = (lage: PadLage, dt: number): void => {
    const y = lage.links.y;
    const r = y > 0.5 ? -1 : y < -0.5 ? 1 : 0;
    if (r === 0) {
      menueRichtung = 0;
      menueGehalten = 0;
      menueBereit = true;
      return;
    }
    if (r !== menueRichtung || menueBereit) {
      menueRichtung = r;
      menueGehalten = 0;
      menueBereit = false;
      bewegeFokus(r as 1 | -1);
      return;
    }
    menueGehalten += dt;
    if (menueGehalten >= WIEDERHOLEN_AB_S) {
      menueGehalten -= WIEDERHOLEN_S;
      bewegeFokus(r as 1 | -1);
    }
  };

  // --- Kamera in Übersicht und Panorama -------------------------------------------------------------------------
  const versatz = new Vector3();
  const orbit = (lage: PadLage, dt: number): void => {
    const c = v.controls;
    if (!c.enabled) return;
    const e = o.einstellungen();
    const reduziert = bewegungReduziert(e, o.systemReduziert());
    const r = drehSchritt(dreh, lage.rechts.x, lage.rechts.y, dt, { raten: RATEN_ORBIT, faktor: e.padEmpfindlichkeit, reduziert });
    dreh = r.z;
    const zoom = zoomFaktor(lage.hochAchse, dt);
    const schnell = vorher?.has('schnell') ? SCHNELL_ORBIT : 1;
    const lx = lage.links.x;
    const ly = lage.links.y;
    if (r.dx === 0 && r.dy === 0 && zoom === 1 && lx === 0 && ly === 0) return;
    const cam = v.instance.view.camera;
    const t = c.target;
    versatz.copy(cam.position).sub(t);
    let abstand = versatz.length();
    if (abstand === 0) return;
    let theta = Math.atan2(versatz.y, versatz.x);
    let phi = Math.acos(Math.min(1, Math.max(-1, versatz.z / abstand)));
    // Stick rechts: Blick dreht nach rechts (Kamera wandert gegen den Uhrzeigersinn um den Drehpunkt zurück).
    theta -= r.dx;
    // Stick oben: Blick hebt sich (Kamera senkt sich zum Horizont).
    phi = Math.min(Math.max(phi + r.dy, Math.max(0.01, c.minPolarAngle)), Math.min(Math.PI - 0.01, c.maxPolarAngle));
    abstand = Math.min(Math.max(abstand * zoom, Math.max(0.05, c.minDistance)), c.maxDistance);
    // Verschieben in der Waagerechten, vorwärts = Blickrichtung, Tempo im Verhältnis zum Abstand.
    const weg = abstand * VERSCHIEBEN_JE_ABSTAND * schnell * Math.min(0.1, Math.max(0, dt));
    const fx = -Math.cos(theta);
    const fy = -Math.sin(theta);
    // Rechts von der Blickrichtung (fx, fy) liegt (fy, −fx) (Z oben).
    t.x += (fy * lx + fx * ly) * weg;
    t.y += (-fx * lx + fy * ly) * weg;
    cam.position.set(
      t.x + abstand * Math.sin(phi) * Math.cos(theta),
      t.y + abstand * Math.sin(phi) * Math.sin(theta),
      t.z + abstand * Math.cos(phi),
    );
    cam.up.set(0, 0, 1);
    c.update();
    c.dispatchEvent({ type: 'change' });
    v.instance.notifyChange(cam);
  };

  const panorama = (lage: PadLage, dt: number): void => {
    const s = pano.state;
    if (!s) return;
    const e = o.einstellungen();
    const fovFaktor = s.fov / PANO_BEZUG_FOV;
    const zoom = zoomFaktor(lage.hochAchse, dt);
    if (drehenInSchritten(e, o.systemReduziert())) {
      const d = stickSchritt(lage.rechts.x, drehBereit);
      const n = stickSchritt(lage.rechts.y, neigBereit);
      drehBereit = d.bereit;
      neigBereit = n.bereit;
      if (d.schritt || n.schritt) pano.setView({ yaw: s.yaw - d.schritt * e.drehSchrittGrad, pitch: s.pitch + n.schritt * e.drehSchrittGrad });
    } else {
      const reduziert = bewegungReduziert(e, o.systemReduziert());
      const r = drehSchritt(dreh, lage.rechts.x, lage.rechts.y, dt, { raten: RATEN_PANORAMA, faktor: e.padEmpfindlichkeit * fovFaktor, reduziert });
      dreh = r.z;
      if (r.dx !== 0 || r.dy !== 0) pano.setView({ yaw: s.yaw - r.dx, pitch: s.pitch + r.dy });
    }
    if (zoom !== 1) pano.setView({ fov: (pano.state?.fov ?? s.fov) * zoom });
  };

  // --- Bildschleife außerhalb des Begehens ------------------------------------------------------------------------
  const bild = (jetzt: number): void => {
    raf = null;
    const dt = Math.min(0.1, Math.max(0, (jetzt - zuletzt) / 1000));
    zuletzt = jetzt;
    // Im Begehen liest dessen Bildschleife (quelle); hier nur warten.
    if (!walk.aktiv) {
      const lage = abtasten();
      if (!lage) return; // abgesteckt: Schleife endet, die Suche nimmt sie wieder auf
      if (!walk.aktiv && !o.formularOffen()) {
        const m = modus();
        if (m === 'menue') menueStick(lage, dt);
        else if (!rueckfrage.offen() && !o.durchKamera()) {
          if (m === 'panorama') panorama(lage, dt);
          else orbit(lage, dt);
        }
      }
    }
    zeichneUebersichtBeiWechsel();
    // Panels und Meldungen ändern sich auch ohne Controller: etwa alle halbe Sekunde nachmessen.
    if (++bilderSeitAusweichen >= 30) {
      bilderSeitAusweichen = 0;
      ausweichen();
    }
    raf = requestAnimationFrame(bild);
  };
  let bilderSeitAusweichen = 0;
  let uebersichtModus: PadModus | null = null;
  let leistenSchluessel = '';
  /** Ansicht oder Zustand (Werkzeug, Zeichnung, Rückfrage, Formular) hat sich geändert — auch ohne Controller-Knopf,
   *  z. B. wenn eine Rückfrage aufgeht: Leiste und Übersicht nachziehen. Billig genug für jedes Bild. */
  const zeichneUebersichtBeiWechsel = (): void => {
    const k = kontext();
    const schluessel = `${k.modus}|${k.werkzeug}|${k.zeichnung}|${k.rueckfrage}|${o.formularOffen()}|${benutztAn}`;
    if (schluessel !== leistenSchluessel) {
      leistenSchluessel = schluessel;
      zeichneLeiste();
    }
    if (k.modus === uebersichtModus) return;
    uebersichtModus = k.modus;
    zeichneUebersicht();
    platziereUebersicht();
  };
  const starte = (): void => {
    if (raf !== null) return;
    zuletzt = performance.now();
    raf = requestAnimationFrame(bild);
  };
  // Manche Browser melden Controller erst nach einem Knopfdruck (gamepadconnected); dazu eine sparsame Suche.
  window.addEventListener('gamepadconnected', starte);
  window.addEventListener('gamepaddisconnected', () => {
    if (!finde()) getrennt();
  });
  if (typeof navigator !== 'undefined' && 'getGamepads' in navigator) {
    window.setInterval(() => {
      if (raf === null && finde()) starte();
    }, SUCHE_MS);
    if (finde()) starte();
  }
  // Zustandswechsel ohne Controller-Knopf (Tastatur, Maus): Leiste und Übersicht nachziehen.
  walk.onChange(() => {
    loslassen();
    zeichneUebersichtBeiWechsel();
  });
  pano.onChange(zeichneUebersichtBeiWechsel);

  // --- Begehen ----------------------------------------------------------------------------------------------------
  const quelle = (dt: number): EingabeQuelle | null => {
    const lage = abtasten();
    // Kein Controller, oder eine Flanke hat das Begehen eben beendet: nichts bewegen.
    if (!lage || !walk.aktiv) return null;
    zeichneUebersichtBeiWechsel();
    // Dialog über dem Begehen (Menü, Bedienung, Einstellungen): das Begehen steht, der Stick bewegt die Auswahl.
    if (modus() === 'menue') {
      menueStick(lage, dt);
      return null;
    }
    const e = o.einstellungen();
    if (lage.links.x === 0 && lage.links.y === 0) sprint = false;
    const eingabe = { vor: lage.links.y, seit: lage.links.x, hoch: lage.hochAchse, schnell: sprint };
    if (drehenInSchritten(e, o.systemReduziert())) {
      const d = stickSchritt(-lage.rechts.x, drehBereit);
      const n = stickSchritt(lage.rechts.y, neigBereit);
      drehBereit = d.bereit;
      neigBereit = n.bereit;
      return { eingabe, dreh: 0, neig: 0, schritte: { dreh: d.schritt, neig: n.schritt } };
    }
    const reduziert = bewegungReduziert(e, o.systemReduziert());
    const faktor = e.padEmpfindlichkeit * (walk.zielt() ? ZIELEN_FAKTOR : 1);
    const r = drehSchritt(dreh, lage.rechts.x, lage.rechts.y, dt, { raten: RATEN_BEGEHEN, faktor, reduziert });
    dreh = r.z;
    // Stick rechts = nach rechts drehen = Blickwinkel (yaw) kleiner.
    return { eingabe, dreh: 0, neig: 0, schritte: { dreh: 0, neig: 0 }, blick: { yaw: -r.dx + 0, pitch: r.dy } };
  };

  return {
    quelle,
    vibriere,
    typ: () => padTyp,
    benutzt: () => benutztAn,
    belegungZeigen,
  };
}
