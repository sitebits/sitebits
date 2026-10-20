// Empfängeransicht (DOM): Wer einen Befundlink öffnet, sieht den Befund geführt statt der Werkzeuge — links die
// Befundliste (Textalternative zur 3D-Ansicht, im Dokument vor ihr), rechts die Befundkarte mit Titel, Kategorie,
// Notiz, „Frage an Sie“, Blättern zum nächsten/vorigen Befund und Knöpfen zum Drehen, Neigen und Zoomen (dieselben
// Schritte wie die Pfeiltasten auf der Leinwand). Messen, Markieren, Begehen und die Panels der Werkzeugansicht
// sind ausgeblendet (styles/empfang.css); „Im Werkzeug öffnen“ beendet den Empfang. Die Leinwand ist dort ein
// Bild mit beschreibendem Namen. Reines Modell: empfangModell.ts; Kameraschritte: viewMath.ts.
// Mobil (schmal oder Touch): die Karte ist eine Leiste unten, eingeklappt nur mit Titel; ausgeklappt schiebt sie die
// Ansicht nach oben, statt sie zu überdecken; die Befundliste klappt oben auf („Alle Befunde“, Sprunglink).
// Ausgeklappt trennt ein verschiebbarer Trenner Ansicht und Karte: hochkant liegt die Karte unten, quer rechts neben
// der Ansicht (sonst bliebe im Querformat nur ein Streifen). Ziehen, Doppeltipp und Tasten; Grenzen: aufteilung.ts.
// Messbeschriftungen sind hier kleiner und ruhiger (empfang.css), mit „Messwerte zeigen“ abschaltbar (gemerkt wie die
// Aufteilung) und weichen den Bedienelementen (messBeschriftung.ts).
// Wer POIs anlegen darf (Plattform), gibt hier eine Rückmeldung: als Text an der Stelle des Befunds
// (rueckmeldung.ts) oder mit „Hier markieren“ an einer selbst gewählten Stelle (feine Suche wie beim Markieren).
// Mit Befund-Fäden (Projekt-Schalter) steht unter den Feldern der Bereich „Verlauf“ (fadenAnsicht.ts); „Rückmeldung zu
// diesem Befund“ schreibt dann einen Kommentar in den Faden statt eines neuen POIs, die Liste hat Filter und Kennzeichen.
import './styles/empfang.css';
import './styles/faden.css';
import { ANTEIL, ANZEIGE_SCHLUESSEL, anteilAusZeiger, doppeltipp, grenzen, klemme, ladeAnzeige, schritt, speichereAnzeige, TRENNER_PX, umschalten } from './aufteilung';
import type { Lage, Tipp } from './aufteilung';
import type { BedienEinstellungen } from './bedienEinstellungen';
import { createBefundListe } from './befundListe';
import { befundEintraege, leinwandName, nachbar } from './empfangModell';
import type { BefundEintrag, FadenSicht } from './empfangModell';
import { pruefknoepfe } from './vorschlagBedienung';
import { vorschlagText } from './vorschlagApi';
import { createFadenAnsicht } from './fadenAnsicht';
import type { FadenAnsicht } from './fadenAnsicht';
import type { FadenStaende } from './fadenStaende';
import type { ViewState } from './hash';
import type { DrehRichtung } from './keymap';
import type { Leinwand } from './leinwand';
import { attachMessBeschriftung } from './messBeschriftung';
import type { Panorama } from './panorama';
import type { PoiPersistenz } from './poiPersistenz';
import type { PoiPanel } from './pois';
import { RUECKMELDUNG } from './poiStore';
import type { ViewerRechte } from './rechte';
import { rueckmeldungEntwurf, WartendeAnlagen, zuBefundZeile } from './rueckmeldung';
import type { Toolbar } from './toolbar';
import type { Viewer } from './viewer';
import { orbitSchritt, orbitZoom } from './viewMath';
import type { Vec3 } from './manifest';

/** Zoom-Knöpfe: Sichtfeld im Panorama ± so viel Grad, Abstand im Orbit × bzw. ÷ Faktor. */
export const ZOOM_FOV_GRAD = 10;
export const ZOOM_FAKTOR = 0.8;

export interface Empfang {
  readonly aktiv: boolean;
  /** section#befundliste (Ziel des Sprunglinks). */
  readonly liste: HTMLElement;
  /** Befundliste zeigen (mobil aufklappen) und zurückgeben — für den Sprunglink. */
  zeigeListe(): HTMLElement;
  /** Empfang beginnen; die Pose bleibt, wie sie ist (aus dem Link). `eintrag`: Anker auf einen Eintrag im Faden. */
  starte(befundId: string | null, eintrag?: string): void;
  beende(): void;
  /** Zu einem Befund springen (Standpunkt mit Blick, sonst Rahmen) und ihn zeigen. */
  zeige(id: string): void;
  /** Ein Schritt drehen bzw. neigen (Pfeiltasten, Drehknöpfe). */
  drehe(richtung: DrehRichtung): void;
  /** Messungen geändert: „Messwerte zeigen“ gibt es nur, wenn es Messungen gibt. */
  messwerteGeaendert(): void;
  onChange(cb: (aktiv: boolean) => void): void;
}

type Teil = string | { sr: string } | { deko: string };

/** Knopf aus sichtbarem Text, nur vorgelesenem Text (sr) und nur sichtbaren Pfeilen (deko). */
function knopf(teile: readonly Teil[], wirkung: () => void): HTMLButtonElement {
  const b = document.createElement('button');
  b.type = 'button';
  for (const t of teile) {
    if (typeof t === 'string') b.append(t);
    else {
      const s = document.createElement('span');
      if ('sr' in t) {
        s.className = 'sv-nur-sr';
        s.textContent = t.sr;
      } else {
        s.setAttribute('aria-hidden', 'true');
        s.textContent = t.deko;
      }
      b.append(s);
    }
  }
  b.addEventListener('click', () => {
    if (b.getAttribute('aria-disabled') !== 'true') wirkung();
  });
  return b;
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};

/** Schmal oder Touch: Karte als Leiste unten, Liste zum Aufklappen, Knöpfe ≥ 44 px (styles/empfang.css). */
export const MOBIL_ABFRAGE = '(max-width: 700px), (pointer: coarse)';

export function attachEmpfang(o: {
  pois: PoiPanel;
  persistenz: PoiPersistenz | undefined;
  vorschlaegeSichtbar?: () => boolean;
  pano: Panorama;
  v: Pick<Viewer, 'instance' | 'controls'>;
  leinwand: Leinwand;
  toolbar: Pick<Toolbar, 'setTool' | 'tabStopp' | 'tool'>;
  begehen: { beenden(treffer?: null): void };
  einstellungen: () => BedienEinstellungen;
  /** Aktuelle Pose (für „Zurück zur Befundansicht“) und ihr Wiederherstellen. */
  ansicht: () => ViewState;
  wendePoseAn: (s: ViewState) => boolean;
  /** Ansage mit Vorrang (Live-Region): „Befund n von m: …“ geht anderen Meldungen voraus. */
  melde: (t: string) => void;
  /** Nach Beginn, Ende und jedem gezeigten Befund (z. B. Adresszeile neu schreiben). */
  onAenderung: () => void;
  /** Rechte am Server; null = Einzelmodus (dort gibt es keine Rückmeldung). */
  rechte: ViewerRechte | null;
  /** Sichtbarer Hinweis (Meldungszeile), z. B. beim Markieren. */
  hinweis: (t: string) => void;
  /** Browser-Speicher für die Aufteilung (Zugriffe abgesichert: ohne Speicher gilt die Vorgabe für diese Sitzung). */
  speicher: { lies(k: string): string | null; schreib(k: string, wert: string): void };
  /** Mindestabstand zum Drehpunkt vor einer Mesh-Oberfläche (zoomStopp.ts), 0 = keiner. */
  zoomGrenze?: () => number;
  /** Anzahl der Messungen (für den Schalter „Messwerte zeigen“). */
  messungen: () => number;
  /** Befund-Fäden (Schalter an); fehlt oder null = alles wie ohne Fäden. */
  faeden?: {
    projectId: string;
    staende: FadenStaende;
    kategorien: readonly string[];
    nutzerId: number | null;
    pruefen: boolean;
    standGeaendert(serverId: string, version: number): void;
  } | null;
}): Empfang {
  const { pois, pano, v } = o;
  const canvas = v.instance.domElement;
  const view = document.getElementById('view')!;
  let aktiv = false;
  let aktuell: string | null = null;
  /** Pose beim Öffnen des aktuellen Befunds (aus dem Link bzw. nach dem Sprung). */
  let befundPose: ViewState | null = null;
  const hoerer: ((aktiv: boolean) => void)[] = [];

  const fd = o.faeden ?? null;
  const liste = createBefundListe({
    ...(fd ? {
      filter: {
        kategorien: fd.kategorien,
        onFilter: (f) => void fd.staende.setzeFilter(f).then((fehler) => {
          if (fehler !== null) o.hinweis(fehler);
        }),
      },
    } : {}),
    onWaehle: (id) => {
      zeige(id);
      // Mobil: die Liste schließt, der Blick geht zum gewählten Befund; der Fokus auf seine Karte.
      if (mobil() && document.body.classList.contains('sv-liste-offen')) {
        setzeListe(false);
        titel.focus();
      }
    },
  });
  liste.element.hidden = true;
  view.before(liste.element);

  // Befundkarte
  const karte = el('section');
  karte.id = 'sv-befundkarte';
  karte.className = 'sv-befundkarte';
  karte.hidden = true;
  const titel = el('h2');
  titel.id = 'sv-befundkarte-titel';
  titel.tabIndex = -1;
  karte.setAttribute('aria-labelledby', titel.id);
  const inhalt = el('div');
  inhalt.id = 'sv-befundkarte-inhalt';
  inhalt.className = 'sv-karte-inhalt';
  // Nur mobil sichtbar: Karte aus- und einklappen, Befundliste oben aufklappen.
  const klappKnopf = knopf(['Befund ausklappen'], () => klappe(!karte.classList.contains('sv-offen')));
  klappKnopf.setAttribute('aria-controls', inhalt.id);
  klappKnopf.setAttribute('aria-expanded', 'false');
  const alleKnopf = knopf(['Alle Befunde'], () => setzeListe(!document.body.classList.contains('sv-liste-offen')));
  alleKnopf.setAttribute('aria-controls', 'befundliste');
  alleKnopf.setAttribute('aria-expanded', 'false');
  const kopf = el('div');
  kopf.className = 'sv-karte-kopf';
  const kopfKnoepfe = el('div');
  kopfKnoepfe.className = 'sv-knopfreihe sv-nur-mobil';
  kopfKnoepfe.append(klappKnopf, alleKnopf);
  kopf.append(titel, kopfKnoepfe);
  const position = el('p');
  position.className = 'sv-befund-position';
  const kategorie = el('p');
  const notiz = el('p');
  notiz.className = 'sv-befund-notiz';
  const frageBlock = el('div');
  frageBlock.className = 'sv-frage';
  const frageText = el('p');
  frageBlock.append(el('h3', 'Frage an Sie'), frageText);
  const voriger = knopf([{ deko: '◂ ' }, 'Voriger Befund'], () => blaettere(-1));
  const naechster = knopf(['Nächster Befund', { deko: ' ▸' }], () => blaettere(1));
  const blaettern = el('div');
  blaettern.className = 'sv-knopfreihe';
  blaettern.append(voriger, naechster);
  const dreh = el('div');
  dreh.className = 'sv-knopfreihe sv-drehgruppe';
  dreh.setAttribute('role', 'group');
  dreh.setAttribute('aria-label', 'Ansicht drehen');
  dreh.append(
    knopf([{ deko: '◂ ' }, { sr: 'nach links ' }, 'drehen'], () => drehe('links')),
    knopf([{ sr: 'nach rechts ' }, 'drehen', { deko: ' ▸' }], () => drehe('rechts')),
    knopf([{ deko: '▴ ' }, { sr: 'nach oben ' }, 'neigen'], () => drehe('hoch')),
    knopf([{ deko: '▾ ' }, { sr: 'nach unten ' }, 'neigen'], () => drehe('runter')),
    knopf(['Näher'], () => zoome(true)),
    knopf(['Weiter'], () => zoome(false)),
    knopf(['Zurück zur Befundansicht'], () => zurueck()),
  );
  const frei = knopf(['Frei umsehen'], () => pano.leave());
  const werkzeug = knopf(['Im Werkzeug öffnen'], () => {
    beende();
    canvas.focus({ preventScroll: true });
  });
  // Messbeschriftungen ab- und zuschalten (Zustand im Speicher der Aufteilung; Wirkung über body.sv-ohne-messwerte).
  const messwerte = knopf(['Messwerte zeigen'], () => {
    anzeige = { ...anzeige, messwerte: !anzeige.messwerte };
    zeigeMesswerte();
    merke();
  });
  const unten = el('div');
  unten.className = 'sv-knopfreihe';
  unten.append(frei, werkzeug, messwerte);
  // --- Rückmeldung: als Text an der Stelle des Befunds oder „Hier markieren“ an einer gewählten Stelle. ---
  /** Im Empfang angelegte POIs, deren Antwort vom Server aussteht (mit Text, wenn als Text gesendet). */
  const wartend = new WartendeAnlagen((id) => o.persistenz?.bestaetigt?.(id) ?? false);
  /** Im Empfang angelegte POIs: ihre Auswahl wechselt die Karte nicht, sie zeigt weiter den Befund. */
  const neuImEmpfang = new Set<string>();
  /** Befund, zu dem das offene POI-Formular gehört (Hier markieren, fortgesetzter Entwurf). */
  let formularZu: string | null = null;
  const markiert = (): boolean => o.toolbar.tool() === 'poi';
  const beendeMarkieren = (): void => {
    if (markiert()) o.toolbar.setTool('navigate');
  };
  const baueRueckmeldung = (): { element: HTMLElement; markieren: HTMLButtonElement } => {
    const bereich = el('div');
    bereich.className = 'sv-rueckmelden';
    const markieren = knopf(['Hier markieren'], () => (markiert() ? beendeMarkieren() : starteMarkieren()));
    markieren.setAttribute('aria-pressed', 'false');
    const form = el('form');
    form.id = 'sv-rueckmeldung';
    form.className = 'sv-rueckmeldung';
    form.noValidate = true;
    form.hidden = true;
    // Esc schließt das Formular (auch vom Knopf „senden“ aus), nicht das Panorama.
    form.dataset.tastenEigen = '';
    const oeffner = knopf(['Rückmeldung zu diesem Befund'], () => zeigeFormular(form.hidden));
    oeffner.setAttribute('aria-controls', form.id);
    oeffner.setAttribute('aria-expanded', 'false');
    const reihe = el('div');
    reihe.className = 'sv-knopfreihe';
    reihe.append(markieren, oeffner);
    const feld = el('textarea');
    feld.id = 'sv-rueckmeldung-text';
    feld.rows = 3;
    const label = el('label', 'Ihre Rückmeldung');
    label.htmlFor = feld.id;
    const fehler = el('p');
    fehler.id = 'sv-rueckmeldung-fehler';
    fehler.className = 'sv-feldfehler';
    fehler.hidden = true;
    const senden = el('button', 'Rückmeldung senden');
    senden.type = 'submit';
    const status = el('p');
    status.className = 'sv-rueckmeldung-status';
    form.append(label, feld, fehler, senden, status);
    bereich.append(reihe, form);

    const zeigeFehler = (t: string | null): void => {
      fehler.textContent = t ?? '';
      fehler.hidden = t === null;
      if (t === null) {
        feld.removeAttribute('aria-invalid');
        feld.removeAttribute('aria-describedby');
      } else {
        feld.setAttribute('aria-invalid', 'true');
        feld.setAttribute('aria-describedby', fehler.id);
      }
    };
    function zeigeFormular(auf: boolean, fokus = true): void {
      form.hidden = !auf;
      oeffner.setAttribute('aria-expanded', String(auf));
      if (auf && fokus) feld.focus();
    }
    form.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      zeigeFormular(false);
      oeffner.focus();
    });
    form.addEventListener('submit', (ev) => {
      ev.preventDefault();
      const b = aktuell === null ? undefined : pois.store.get(aktuell);
      if (!b) return;
      const d = rueckmeldungEntwurf({ titel: b.title, position: b.position, station: b.station }, feld.value);
      if ('fehler' in d) {
        zeigeFehler(d.fehler);
        feld.focus();
        return;
      }
      zeigeFehler(null);
      const neu = pois.anlegen(d);
      if (!neu) {
        zeigeFehler('Rückmeldung nicht gespeichert.');
        feld.focus();
        return;
      }
      neuImEmpfang.add(neu.id);
      // Am Server gilt sie erst mit seiner Antwort als gespeichert (offline später, mit Hinweis des Abgleichs).
      if (o.persistenz?.onErgebnis) {
        wartend.merke(neu.id, feld.value);
        status.textContent = 'Rückmeldung wird gesendet …';
      } else {
        status.textContent = 'Rückmeldung gespeichert';
        o.melde('Rückmeldung gespeichert');
      }
      feld.value = '';
    });
    feld.addEventListener('input', () => {
      if (!fehler.hidden) zeigeFehler(null);
    });
    o.persistenz?.onErgebnis?.((id, fehler) => {
      const f = wartend.ergebnis(id, fehler);
      if (f === null) return;
      if (f.art === 'gespeichert') {
        if (f.text !== null) {
          status.textContent = 'Rückmeldung gespeichert';
          o.melde('Rückmeldung gespeichert');
        }
        return;
      }
      // Abgelehnt (z. B. Obergrenze erreicht): keine lokale Kopie zurücklassen, die am Server fehlt. Ein
      // markierter Befund bleibt als Entwurf erhalten; ein Text kommt zurück ins Feld, der Grund steht daran.
      if (f.entfernen && pois.store.get(id)?.source === 'browser') {
        pois.store.remove(id);
        pois.refresh();
      }
      if (f.text === null) return;
      status.textContent = '';
      if (f.art !== 'abgelehnt') return;
      if (feld.value.trim() === '') feld.value = f.text;
      // Mobil eingeklappt: aufklappen, sonst stünde der Grund in einem unsichtbaren Formular.
      if (mobil() && !karte.classList.contains('sv-offen')) klappe(true);
      zeigeFormular(true, false);
      // Angesagt hat den Grund schon der Abgleich („Nicht gespeichert: …“); hier steht er am Feld.
      zeigeFehler(`Nicht gespeichert: ${f.grund}`);
    });
    return { element: bereich, markieren };
  };
  const starteMarkieren = (): void => {
    const b = aktuell === null ? undefined : pois.store.get(aktuell);
    if (!b) return;
    pois.vorbelegen({ title: '', category: RUECKMELDUNG, note: `${zuBefundZeile(b.title)}\n` });
    o.toolbar.setTool('poi');
    // Mobil: die Ansicht frei machen, damit die Stelle zu sehen ist. Der Fokus lag im eingeklappten Inhalt: er geht
    // auf den Klappknopf, nicht verloren.
    if (mobil()) {
      const fokusInnen = inhalt.contains(document.activeElement);
      klappe(false);
      if (fokusInnen) klappKnopf.focus();
    }
    o.hinweis('Auf die Stelle tippen oder klicken – Esc bricht ab');
  };
  // --- Befund-Faden: Verlauf unter den Feldern; „Rückmeldung zu diesem Befund“ öffnet dessen Kommentarformular. ---
  let rueckOeffner: HTMLButtonElement | null = null;
  const faden: FadenAnsicht | null = fd ? createFadenAnsicht({
    pruefen: fd.pruefen, pid: fd.projectId, id: 'sv-befund-faden', nutzerId: fd.nutzerId, ebene: 3, eingeklappt: true,
    kommentar: { label: 'Ihre Rückmeldung', senden: 'Rückmeldung senden', formId: 'sv-rueckmeldung', textId: 'sv-rueckmeldung-text' },
    onStand: (sid, st) => {
      fd.standGeaendert(sid, st.version);
      void fd.staende.laden();
    },
    onGeladen: (f) => {
      if (rueckOeffner) rueckOeffner.hidden = !f?.rechte.kommentieren;
    },
  }) : null;
  // Eingaben im Verlauf bedienen ihre Tasten selbst (Ziffern im Datumsfeld, Pfeiltasten in Auswahllisten).
  if (faden) faden.element.dataset.tastenEigen = '';
  const baueFadenRueckmeldung = (f: FadenAnsicht): { element: HTMLElement; markieren: HTMLButtonElement } => {
    const markieren = knopf(['Hier markieren'], () => (markiert() ? beendeMarkieren() : starteMarkieren()));
    markieren.setAttribute('aria-pressed', 'false');
    const zeige = (auf: boolean, fokus = true): void => {
      oeffner.setAttribute('aria-expanded', String(auf));
      f.kommentarOffen(auf, fokus);
    };
    const oeffner = knopf(['Rückmeldung zu diesem Befund'], () => zeige(oeffner.getAttribute('aria-expanded') !== 'true'));
    oeffner.setAttribute('aria-controls', f.kommentarForm.id);
    oeffner.setAttribute('aria-expanded', 'false');
    oeffner.hidden = true;
    rueckOeffner = oeffner;
    f.kommentarForm.addEventListener('keydown', (ev) => {
      if (ev.key !== 'Escape') return;
      ev.preventDefault();
      ev.stopPropagation();
      zeige(false, false);
      oeffner.focus();
    });
    const reihe = el('div');
    reihe.className = 'sv-knopfreihe sv-rueckmelden';
    reihe.append(markieren, oeffner);
    return { element: reihe, markieren };
  };
  const rueck = o.rechte?.poisAnlegen ? (faden ? baueFadenRueckmeldung(faden) : baueRueckmeldung()) : null;
  inhalt.append(position, kategorie, notiz, frageBlock);
  if (faden) inhalt.append(faden.element);
  if (rueck) inhalt.append(rueck.element);
  inhalt.append(blaettern, dreh, unten);
  karte.append(kopf, inhalt);
  // Trenner zwischen Ansicht und Karte (auch in der Tab-Reihenfolge dazwischen); sichtbar nur mobil und ausgeklappt.
  const trenner = el('div');
  trenner.id = 'sv-trenner';
  trenner.className = 'sv-trenner';
  trenner.setAttribute('role', 'separator');
  trenner.setAttribute('aria-label', 'Größe des Befundbereichs');
  trenner.setAttribute('aria-controls', karte.id);
  trenner.tabIndex = 0;
  // Pfeiltasten, Pos1, Ende, Eingabe und Esc gehören dem Trenner, nicht dem Drehen der Ansicht (keymap.ts).
  trenner.dataset.tastenEigen = '';
  view.after(trenner, karte);

  const fadenSicht: FadenSicht | undefined = fd
    ? { passt: (sid) => fd.staende.passt(sid), kennzeichen: (sid) => fd.staende.kennzeichen(sid) }
    : undefined;
  const meta = (id: string) => o.persistenz?.meta?.(id) ?? null;
  const eintraege = (): BefundEintrag[] => befundEintraege(pois.store.list().filter(p => !p.vorschlag || o.vorschlaegeSichtbar?.() !== false), meta, fadenSicht);
  /** Befund, dessen Verlauf gerade geladen ist (Server-ID), und ein Eintrags-Anker aus dem Link (einmalig). */
  let fadenFuer: string | null = null;
  let anker: { befund: string; eintrag: string } | null = null;
  const fadenNachfuehren = (lokal: string | null): void => {
    if (!faden) return;
    const sid = lokal === null ? null : (meta(lokal)?.serverId ?? null);
    if (sid === fadenFuer) return;
    fadenFuer = sid;
    const e = anker !== null && anker.befund === lokal ? anker.eintrag : undefined;
    anker = null;
    void faden.zeige(sid, e);
  };

  // --- Mobil: Karte unten (aus-/einklappbar), Liste oben (aufklappbar); ihre Höhen schieben die Ansicht. ---
  const mobilAbfrage = window.matchMedia(MOBIL_ABFRAGE);
  const mobil = (): boolean => mobilAbfrage.matches;
  const setzeMobil = (): void => {
    document.body.classList.toggle('sv-mobil', mobil());
    pruefeTasten();
    wende();
  };
  /** Mobil und ausgeklappt behandelt die Karte ihre Tasten selbst: Esc klappt sie ein. */
  const pruefeTasten = (): void => {
    if (mobil() && karte.classList.contains('sv-offen')) karte.dataset.tastenEigen = '';
    else delete karte.dataset.tastenEigen;
  };
  const klappe = (auf: boolean): void => {
    karte.classList.toggle('sv-offen', auf);
    klappKnopf.setAttribute('aria-expanded', String(auf));
    klappKnopf.firstChild!.textContent = auf ? 'Befund einklappen' : 'Befund ausklappen';
    // Der Trenner verschwindet mit der ausgeklappten Karte: hatte er den Fokus, geht der zum Klappknopf.
    if (!auf && document.activeElement === trenner) klappKnopf.focus();
    pruefeTasten();
    wende();
  };
  const setzeListe = (auf: boolean): void => {
    document.body.classList.toggle('sv-liste-offen', auf);
    alleKnopf.setAttribute('aria-expanded', String(auf));
    wende();
  };

  // --- Trenner: Anteil des Befundbereichs je Lage (aufteilung.ts), gemerkt je Browser. Die Karte bekommt ihre Größe
  // in px (--sv-befund) aus innerHeight/innerWidth statt vh (auf dem iPhone ist 100vh nicht die sichtbare Höhe). ---
  const querAbfrage = window.matchMedia('(orientation: landscape)');
  let anzeige = ladeAnzeige(o.speicher.lies(ANZEIGE_SCHLUESSEL));
  const lage = (): Lage => (querAbfrage.matches ? 'quer' : 'hoch');
  const geteilt = (): boolean => aktiv && mobil() && karte.classList.contains('sv-offen');
  /** Fläche, die Ansicht, Trenner und Karte sich teilen: hochkant unter der offenen Befundliste, quer die Breite. */
  const flaeche = (l: Lage): { anfang: number; laenge: number } => {
    if (l === 'quer') return { anfang: 0, laenge: window.innerWidth };
    const oben = liste.element.getBoundingClientRect().height;
    return { anfang: oben, laenge: window.innerHeight - oben };
  };
  const merke = (): void => o.speicher.schreib(ANZEIGE_SCHLUESSEL, speichereAnzeige(anzeige));
  function zeigeMesswerte(): void {
    messwerte.setAttribute('aria-pressed', String(anzeige.messwerte));
    messwerte.hidden = o.messungen() === 0;
    document.body.classList.toggle('sv-ohne-messwerte', !anzeige.messwerte);
  }
  zeigeMesswerte();
  // Auch bei abgeschalteten Messwerten weiter prüfen (visibility: hidden behält die Box): sonst stünden nach dem
  // Wiedereinschalten die Klassen des letzten Bilds davor, bis zufällig neu gezeichnet wird.
  attachMessBeschriftung({ instance: v.instance, flaeche: view, aktiv: () => aktiv });
  /** Höhen und Breiten als Variablen: die Ansicht beginnt unter der Liste und endet am Trenner bzw. an der Karte. */
  const setzeMasse = (): void => {
    const b = document.body.style;
    const r = karte.getBoundingClientRect();
    const quer = document.body.classList.contains('sv-quer');
    const zwischen = geteilt() ? TRENNER_PX : 0;
    b.setProperty('--sv-unten', `${aktiv && !quer ? r.height + zwischen : 0}px`);
    b.setProperty('--sv-seite', `${aktiv && quer ? r.width + zwischen : 0}px`);
    b.setProperty('--sv-oben', `${aktiv && mobil() ? liste.element.getBoundingClientRect().height : 0}px`);
  };
  /** Aufteilung anwenden: Lage, Größe der Karte, Werte des Trenners. Die 3D-Ansicht folgt über ihre Größe (Giro3D
   *  beobachtet #view und passt Zeichenfläche, Beschriftungen und Seitenverhältnis an — ohne Verzerrung). */
  function wende(): void {
    const an = geteilt();
    const l = lage();
    document.body.classList.toggle('sv-geteilt', an);
    document.body.classList.toggle('sv-quer', an && l === 'quer');
    if (an) {
      const f = flaeche(l);
      const g = grenzen(l, f.laenge);
      const a = klemme(anzeige[l], g);
      document.body.style.setProperty('--sv-befund', `${Math.round(a * f.laenge)}px`);
      trenner.setAttribute('aria-orientation', l === 'hoch' ? 'horizontal' : 'vertical');
      trenner.setAttribute('aria-valuemin', String(Math.round(g.min * 100)));
      trenner.setAttribute('aria-valuemax', String(Math.round(g.max * 100)));
      trenner.setAttribute('aria-valuenow', String(Math.round(a * 100)));
    } else document.body.style.removeProperty('--sv-befund');
    setzeMasse();
  }
  const setzeAnteil = (l: Lage, a: number, speichern: boolean): void => {
    anzeige = { ...anzeige, [l]: a };
    wende();
    if (speichern) merke();
  };
  const jetzt = (l: Lage): { a: number; g: ReturnType<typeof grenzen> } => {
    const g = grenzen(l, flaeche(l).laenge);
    return { a: klemme(anzeige[l], g), g };
  };
  trenner.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') {
      // Wie in der Karte: Esc klappt sie ein (der Fokus geht an den Klappknopf).
      ev.preventDefault();
      klappe(false);
      return;
    }
    const l = lage();
    const { a, g } = jetzt(l);
    const neu = ev.key === 'Enter' ? umschalten(a, g, ANTEIL[l].vorgabe) : schritt(l, ev.key, a, g);
    if (neu === null) return;
    ev.preventDefault();
    setzeAnteil(l, neu, true);
  });
  // Ziehen mit Maus, Stift oder Finger (Zeiger gefangen: die Geste endet auch außerhalb des Trenners sauber).
  // Ein Tipp ohne Bewegung zählt für den Doppeltipp — selbst erkannt statt dblclick, das iOS bei Berührung nicht
  // zuverlässig liefert und das bei der Maus sonst zusätzlich umschalten würde.
  let tippVorher: Tipp | null = null;
  // Abbau der laufenden Geste: auch wenn der Trenner mitten im Ziehen verschwindet (Karte per Taste eingeklappt) und
  // kein pointerup mehr kommt — sonst hingen zwei ende-Handler am nächsten pointerup (ein Tipp zählte doppelt).
  let gesteAbbauen: (() => void) | null = null;
  trenner.addEventListener('pointerdown', (ev) => {
    if (ev.button !== 0) return;
    gesteAbbauen?.();
    ev.preventDefault();
    trenner.focus({ preventScroll: true });
    trenner.setPointerCapture(ev.pointerId);
    const l = lage();
    let bewegt = false;
    const zieh = (mv: PointerEvent): void => {
      if (mv.pointerId !== ev.pointerId) return;
      if (!bewegt && Math.hypot(mv.clientX - ev.clientX, mv.clientY - ev.clientY) < 6) return;
      bewegt = true;
      const f = flaeche(l);
      setzeAnteil(l, klemme(anteilAusZeiger(l, l === 'hoch' ? mv.clientY : mv.clientX, f), grenzen(l, f.laenge)), false);
    };
    const abbauen = (): void => {
      trenner.removeEventListener('pointermove', zieh);
      trenner.removeEventListener('pointerup', ende);
      trenner.removeEventListener('pointercancel', ende);
      trenner.removeEventListener('lostpointercapture', verloren);
      gesteAbbauen = null;
    };
    // Zeigerfang verloren ohne pointerup (Element ausgeblendet): Geste ohne Tipp beenden, Gezogenes merken.
    const verloren = (lp: PointerEvent): void => {
      if (lp.pointerId !== ev.pointerId) return;
      abbauen();
      if (bewegt) merke();
    };
    const ende = (up: PointerEvent): void => {
      if (up.pointerId !== ev.pointerId) return;
      abbauen();
      if (bewegt) {
        merke();
        tippVorher = null;
        return;
      }
      if (up.type === 'pointercancel') return;
      const tipp = { t: up.timeStamp, x: up.clientX, y: up.clientY };
      if (doppeltipp(tippVorher, tipp)) {
        tippVorher = null;
        const { a, g } = jetzt(l);
        setzeAnteil(l, umschalten(a, g, ANTEIL[l].vorgabe), true);
      } else tippVorher = tipp;
    };
    trenner.addEventListener('pointermove', zieh);
    trenner.addEventListener('pointerup', ende);
    trenner.addEventListener('pointercancel', ende);
    trenner.addEventListener('lostpointercapture', verloren);
    gesteAbbauen = abbauen;
  });
  mobilAbfrage.addEventListener('change', setzeMobil);
  querAbfrage.addEventListener('change', wende);
  window.addEventListener('resize', wende);
  setzeMobil();
  // Größenänderungen von Karte (Inhalt, Klappen) und Liste (Aufklappen) führen die Aufteilung nach.
  const hoehen = new ResizeObserver(() => wende());
  hoehen.observe(karte);
  hoehen.observe(liste.element);
  karte.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || ev.defaultPrevented || !mobil()) return;
    if (markiert()) beendeMarkieren();
    else if (karte.classList.contains('sv-offen')) {
      klappe(false);
      klappKnopf.focus();
    } else return;
    ev.preventDefault();
    ev.stopPropagation();
  });

  const aktualisiereName = (befund: string | null): void => {
    o.leinwand.setzeModus('empfang', leinwandName({ standpunkt: pano.state?.station ?? null, befund }));
  };

  /** Karte, Blätterknöpfe und Leinwandname zum aktuellen Befund. */
  const pruefung = el('div'); inhalt.prepend(pruefung);
  const fuelle = (): void => {
    const l = eintraege();
    const i = aktuell === null ? -1 : l.findIndex((e) => e.id === aktuell);
    // Mit Filter kann der gezeigte Befund außerhalb der Liste liegen: die Karte zeigt ihn trotzdem.
    const e = i >= 0 ? l[i]! : aktuell !== null && fadenSicht && pois.store.get(aktuell)
      ? (befundEintraege(pois.store.list().filter((p) => p.id === aktuell), meta)[0] ?? null) : null;
    pruefung.replaceChildren();
    const p = e ? pois.store.get(e.id) : undefined;
    if (p?.vorschlag) {
      pruefung.append(el('strong',vorschlagText(p.vorschlag)));
      if (o.persistenz?.pruefen) pruefknoepfe(pruefung,a => o.persistenz!.pruefen!(p.id,a),o.hinweis);
    }
    titel.textContent = e ? e.titel : 'Kein Befund gewählt';
    position.textContent = e ? (i >= 0 ? `Befund ${i + 1} von ${l.length}` : 'Befund außerhalb des Filters')
      : `${l.length} Befunde – mit „Nächster Befund“ zum ersten`;
    kategorie.textContent = e ? `Kategorie: ${e.kategorie}` : '';
    kategorie.hidden = !e;
    notiz.textContent = e?.notiz ?? '';
    notiz.hidden = !e?.notiz;
    frageText.textContent = e?.frage ?? '';
    frageBlock.hidden = !e?.frage;
    // An den Enden gesperrt; hatte der gesperrte Knopf den Fokus, geht er zum anderen (sonst auf den Titel).
    const fokus = document.activeElement;
    voriger.disabled = nachbar(l, aktuell, -1) === null || i < 0;
    naechster.disabled = nachbar(l, aktuell, 1) === null;
    if (fokus instanceof HTMLButtonElement && fokus.disabled && (fokus === voriger || fokus === naechster)) {
      const anderer = fokus === voriger ? naechster : voriger;
      if (anderer.disabled) titel.focus();
      else anderer.focus();
    }
    frei.setAttribute('aria-disabled', String(!pano.active));
    aktualisiereName(e?.titel ?? null);
    if (rueck) rueck.element.hidden = !e;
    fadenNachfuehren(e?.id ?? null);
  };
  /** Nur Ansicht gewechselt (Drehen, Zoomen, Panorama betreten/verlassen am selben Befund): Name und „Frei umsehen“. */
  const fuehreNach = (): void => {
    frei.setAttribute('aria-disabled', String(!pano.active));
    aktualisiereName(aktuell === null ? null : (pois.store.get(aktuell)?.title ?? null));
  };

  const sage = (): void => {
    const l = eintraege();
    const i = l.findIndex((e) => e.id === aktuell);
    if (i >= 0) o.melde(`Befund ${i + 1} von ${l.length}: ${l[i]!.titel}`);
  };

  const zeige = (id: string): void => {
    if (!aktiv || !pois.store.get(id)) return;
    aktuell = id;
    pois.goTo(id);
    befundPose = o.ansicht();
    liste.setzeAktuell(id);
    fuelle();
    sage();
    o.onAenderung();
  };

  const blaettere = (richtung: -1 | 1): void => {
    const id = nachbar(eintraege(), aktuell, richtung);
    if (id !== null) zeige(id);
  };

  const drehe = (r: DrehRichtung): void => {
    const grad = o.einstellungen().drehSchrittGrad;
    const s = pano.active ? pano.state : null;
    if (s) {
      if (r === 'links') pano.setView({ yaw: s.yaw + grad });
      else if (r === 'rechts') pano.setView({ yaw: s.yaw - grad });
      else if (r === 'hoch') pano.setView({ pitch: s.pitch + grad });
      else pano.setView({ pitch: s.pitch - grad });
      return;
    }
    // Orbit: links = gegen den Uhrzeigersinn um das Ziel; „neigen hoch“ hebt den Blick, also senkt die Kamera.
    const dYaw = r === 'links' ? grad : r === 'rechts' ? -grad : 0;
    const dPitch = r === 'hoch' ? -grad : r === 'runter' ? grad : 0;
    setzeKamera(orbitSchritt(kamera(), ziel(), dYaw, dPitch));
  };

  const kamera = (): Vec3 => {
    const p = v.instance.view.camera.position;
    return [p.x, p.y, p.z];
  };
  const ziel = (): Vec3 => [v.controls.target.x, v.controls.target.y, v.controls.target.z];
  const setzeKamera = (p: Vec3): void => {
    v.instance.view.camera.position.set(p[0], p[1], p[2]);
    v.controls.update();
    v.instance.notifyChange(v.instance.view.camera);
  };

  const zoome = (naeher: boolean): void => {
    const s = pano.active ? pano.state : null;
    if (s) pano.setView({ fov: s.fov + (naeher ? -ZOOM_FOV_GRAD : ZOOM_FOV_GRAD) });
    else setzeKamera(orbitZoom(kamera(), ziel(), naeher ? ZOOM_FAKTOR : 1 / ZOOM_FAKTOR, o.zoomGrenze?.() ?? 0));
  };

  const zurueck = (): void => {
    const p = befundPose;
    if (!p) return;
    // Gleicher Standpunkt: nur den Blick zurück (ohne die Bilder neu zu laden).
    if (p.st && pano.active && pano.state?.station === p.st) pano.setView({ yaw: p.yaw, pitch: p.pitch, fov: p.fov });
    else o.wendePoseAn(p);
  };

  const melde = (an: boolean): void => {
    for (const cb of hoerer) cb(an);
    o.onAenderung();
  };

  const starte = (befundId: string | null, eintrag?: string): void => {
    if (!aktiv) {
      aktiv = true;
      o.begehen.beenden(null);
      o.toolbar.setTool('navigate');
      document.body.classList.add('sv-empfang');
      liste.element.hidden = false;
      karte.hidden = false;
      klappe(false);
      setzeListe(false);
      o.toolbar.tabStopp();
    }
    aktuell = befundId !== null && pois.store.get(befundId) ? befundId : null;
    anker = aktuell !== null && eintrag !== undefined ? { befund: aktuell, eintrag } : null;
    fadenFuer = null;
    if (aktuell !== null) pois.waehle(aktuell);
    befundPose = o.ansicht();
    liste.zeichne(eintraege(), aktuell);
    fuelle();
    sage();
    melde(true);
  };

  const beende = (): void => {
    if (!aktiv) return;
    beendeMarkieren();
    aktiv = false;
    setzeListe(false);
    document.body.classList.remove('sv-empfang');
    liste.element.hidden = true;
    karte.hidden = true;
    o.leinwand.setzeModus('werkzeug');
    o.toolbar.tabStopp();
    melde(false);
  };

  // Neu füllen nur bei einem anderen Standpunkt (Leinwandname); jedes Drehen und Ziehen ändert sonst nur die Ansicht.
  let standpunkt: string | null = null;
  pano.onChange(() => {
    if (!aktiv) return;
    const st = pano.state?.station ?? null;
    if (st === standpunkt) return fuehreNach();
    standpunkt = st;
    fuelle();
  });
  pois.onNeuGezeichnet(() => {
    if (!aktiv) return;
    liste.zeichne(eintraege(), aktuell);
    fuelle();
  });
  // Erst mit der Antwort des Servers kennt die Liste Urheber und Datum eines eben angelegten POIs.
  o.persistenz?.onErgebnis?.((_id, fehler) => {
    if (aktiv && fehler === null) liste.zeichne(eintraege(), aktuell);
  });
  pois.onModeChange((an) => {
    rueck?.markieren.setAttribute('aria-pressed', String(an));
    if (!an) pois.vorbelegen(null);
  });
  pois.onFormular((offen, grund) => {
    if (!aktiv) return;
    if (offen) formularZu = aktuell;
    else if (grund === 'verworfen') {
      formularZu = null;
      beendeMarkieren();
    }
  });
  // Gespeichert (Hier markieren, fortgesetzter Entwurf): danach wählt die Befundsteuerung den neuen POI aus —
  // die Karte und der Befundlink bleiben beim Befund, zu dem die Rückmeldung gehört.
  pois.onGespeichert((id) => {
    if (!aktiv) return;
    neuImEmpfang.add(id);
    if (o.persistenz?.onErgebnis) wartend.merke(id);
    const zu = formularZu;
    formularZu = null;
    if (zu !== null) queueMicrotask(() => pois.waehle(zu));
  });
  // Auswahl von anderswo (z. B. ein Marker in der Ansicht): die Karte zeigt diesen Befund, ohne Sprung.
  pois.onAuswahl((id) => {
    if (!aktiv || id === null || id === aktuell || neuImEmpfang.has(id)) return;
    aktuell = id;
    befundPose = o.ansicht();
    liste.setzeAktuell(id);
    fuelle();
  });

  return {
    get aktiv() {
      return aktiv;
    },
    liste: liste.element,
    zeigeListe: () => {
      if (mobil()) setzeListe(true);
      return liste.element;
    },
    starte,
    beende,
    zeige,
    drehe,
    messwerteGeaendert: () => zeigeMesswerte(),
    onChange: (cb) => {
      hoerer.push(cb);
    },
  };
}
