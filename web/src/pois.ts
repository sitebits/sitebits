import { sourceFromPick } from './pointSources';
import type { PointSource } from './pointSources';
// POIs im Viewer: Panel mit Suche, Liste, Export/Import; Anlegen per Klick auf einen
// echten Punkt (rayPick, wie Messen — kein Punkt, kein POI); Marker = schattierte Kugel am Weltpunkt
// (markerKugel.ts, Farbe je Art, ausgewählt gelb und größer) mit CSS2D-Beschriftung darüber (Form der Art im Schild);
// Sprung = Panorama am gespeicherten bzw. nächsten Standpunkt (≤ 30 m) mit Blick auf den Punkt, sonst
// Orbit-Rahmen. Auswahl: ein POI ist ausgewählt (Marker .sv-gewaehlt, Listeneintrag aria-current); das
// Formular steht als Inspektor rechts (styles/befund.css) und meldet Öffnen, Speichern und Verwerfen
// (befundSteuerung.ts führt Fokus und Ansagen). Sicherheit: Texte ausschließlich per textContent (Dateien sind fremde Daten); localStorage
// nur als Bequemlichkeit je Betrachter, jeder Zugriff in try/catch; Lade-/Speicherfehler werden angezeigt
// und ein unlesbarer Stand wird nicht überschrieben (PoiPersistence, I1).
import { Group } from 'three';
import { ausgelasseneVorschlaege, vorschlagText } from './vorschlagApi';
import { exportMitHinweis, pruefknoepfe } from './vorschlagBedienung';
import { CSS2DObject } from 'three/examples/jsm/renderers/CSS2DRenderer.js';
import type { FormularWerte } from './befundEntwurf';
import { KugelMarker, kugelMaterial } from './markerKugel';
import { KUGEL_POI, KUGEL_POI_GEWAEHLT } from './markerMath';
import type { Project, Vec3 } from './manifest';
import { resolveUrl } from './manifest';
import type { Panorama } from './panorama';
import { istRueckmeldung, markerKlassen, POI_CATEGORIES, POI_LIMITS, PoiError, PoiPersistence, parsePoiFile } from './poiStore';
import type { PoiPersistenz } from './poiPersistenz';
import type { Poi, PoiDraft, PoiStore } from './poiStore';
import { panoPickOptions } from './rayPick';
import type { PickSource } from './rayPick';
import type { Station } from './stations';
import { nearestStation3d } from './stationMath';
import type { Viewer } from './viewer';
import { FORMULAR_OFFEN, keinPunktGrund, keinPunktText, zielpunktSetzen } from './zielpunkt';
import type { Rueckfrage, Wahl } from './zielpunkt';

const POI_STATION_MAX_M = 30;
/** Abstand zwischen Kugel und Unterkante der Beschriftung [px]. */
const LABEL_ABSTAND_PX = 3;
// Kugelfarben (je Art und Auswahl ein geteiltes Material). Die Art steckt zusätzlich in der Form im Schild,
// die Auswahl in Größe der Kugel und Doppelrand des Schilds — nicht nur in der Farbe.
const KUGEL_MAT = {
  befund: kugelMaterial(0xff5a5f),
  rueckmeldung: kugelMaterial(0xf2f2f2),
  gewaehlt: kugelMaterial(0xffcc00),
};
const CLICK_MOVE_TOLERANCE_PX = 4;

export interface PoiPanel {
  readonly store: PoiStore;
  setCreateMode(on: boolean): void;
  isCreateMode(): boolean;
  onModeChange(cb: (on: boolean) => void): void;
  goTo(id: string): void;
  exportFile(): void;
  importText(text: string): number;
  togglePanel(): void;
  /** Formular für einen neuen POI an `pos` (Weltkoordinaten, aus der feinen Suche) öffnen, Fokus ins
   *  Titelfeld. false (mit Hinweis), wenn schon ein Formular offen ist. Beim Fortsetzen eines Entwurfs:
   *  `werte` füllen das Formular vor (eine feste Kategorie gilt trotzdem), `id` wird die ID des neuen POIs
   *  (ohne `id` wird beim Öffnen eine freie reserviert). */
  openAt(pos: readonly [number, number, number], station: string | null, o?: { id?: string; werte?: FormularWerte; positionSource?: PointSource|null }): boolean;
  /** Aktueller Inhalt des offenen Formulars (geschlossen: leer). */
  formularWerte(): FormularWerte;
  /** Punkt, Standpunkt und künftige POI-ID des offenen Formulars, null wenn keins offen ist. */
  formularOrt(): { punkt: [number, number, number]; station: string | null; id: string; positionSource?: PointSource|null } | null;
  /** Bei jeder Eingabe im offenen Formular. */
  onEingabe(cb: () => void): void;
  /** Liste und Marker neu zeichnen (nach Änderungen von außen, z. B. Server-Abgleich). */
  refresh(): void;
  /** POI auswählen (Marker .sv-gewaehlt, Listeneintrag aria-current="true"); null oder unbekannt = keine Auswahl. */
  waehle(id: string | null): void;
  ausgewaehlt(): string | null;
  onAuswahl(cb: (id: string | null) => void): void;
  /** Nach erfolgreichem Absenden des Formulars, mit der lokalen ID des neuen POIs. */
  onGespeichert(cb: (id: string) => void): void;
  /** Formular geöffnet bzw. geschlossen (gespeichert oder verworfen — auch durch Werkzeugwechsel). */
  onFormular(cb: (offen: boolean, grund: 'geoeffnet' | 'gespeichert' | 'verworfen') => void): void;
  formularOffen(): boolean;
  /** Fokus ins Titelfeld des offenen Formulars (z. B. nach einem Klick in die 3D-Ansicht); sonst wirkungslos. */
  fokussiereFormular(): void;
  /** Nach jedem Neuzeichnen von Liste und Marker (eigene Änderung oder Abgleich): z. B. ein geänderter Titel. */
  onNeuGezeichnet(cb: () => void): void;
  /** Neuen POI ohne Formular anlegen und über die Persistenz speichern (z. B. eine Rückmeldung als Text). Fehler
   *  werden gemeldet; dann null und nichts bleibt zurück. Auswahl und Fokus bleiben, wie sie sind. */
  anlegen(d: PoiDraft): Poi | null;
  /** Vorbelegung für das nächste per Klick oder Fadenkreuz geöffnete Formular (null = keine). Eine feste
   *  Kategorie gilt trotzdem; ein fortgesetzter Entwurf bringt seine eigenen Werte mit. */
  vorbelegen(w: FormularWerte | null): void;
}


const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};

export async function attachPois(o: {
  v: Viewer;
  pkgBase: string;
  m: Project;
  store: PoiStore;
  picker: PickSource;
  pano: Panorama;
  stations: readonly Station[];
  notify: (t: string) => void;
  /** Meldung, wenn die Punktwolke ausgeblendet ist (Ebenen-Register). */
  hiddenCloudMessage: string;
  /** Plattformmodus: Server-Persistenz; fehlt sie, gilt wie bisher PoiPersistence (localStorage). */
  persistenz?: PoiPersistenz;
  vorschlaegeSichtbar?: () => boolean;
  /** Kategorie neuer POIs ist fest (schreibgeschützt), z. B. „Rückmeldung“ für die Rolle Rückmelder. */
  kategorieFest?: string;
  /** Vorschläge im Feld Kategorie (Plattform: aus dem Projekt); fehlt sie, die feste Liste. */
  kategorien?: readonly string[];
  /** Pointer-Lock aktiv (Begehen): der Klick trägt eine eingefrorene Mausposition und legt nichts an. */
  unterPointerLock?: () => boolean;
  /** Vor jedem Sprung zu einem POI (Panorama oder Kamera): z. B. das Begehen beenden, damit nur ein
   *  Zustand in die Kamera schreibt und das Panorama das richtige Sichtfeld sichert. */
  vorSprung?: () => void;
  /** Rückfrage „vorn/hinten“, wenn am Klickort zwei Flächen liegen; ohne sie gilt der Treffer wie bisher. */
  frage?: (b: Rueckfrage, ort: { x: number; y: number }) => Promise<Wahl | null>;
  /** Eine Rückfrage ist offen: weitere Klicks setzen nichts. */
  rueckfrageOffen?: () => boolean;
  /** Kamerapose: bewegt sie sich zwischen Klick und zweiter Suche, wird abgebrochen. */
  pose?: () => readonly number[];
  /** Begehen läuft: Esc im Formular gibt den Fokus an die Leinwand zurück (dort wirken die Begehen-Tasten). */
  begehenAktiv?: () => boolean;
  /** Befund-Fäden: Kennzeichen (Status, „überfällig“) als Text im Schild über der Kugel; null = keins. */
  kennzeichen?: (lokalId: string) => string | null;
}): Promise<PoiPanel> {
  const { v, m, store, picker, pano, stations, notify, hiddenCloudMessage } = o;
  const { instance } = v;
  const panel = document.getElementById('poi-panel') as HTMLDivElement;
  const form = document.getElementById('poi-form') as HTMLFormElement;
  let storage: Storage | null = null;
  try {
    storage = window.localStorage;
  } catch {
    // blockierter Speicher: POIs gelten nur für diese Sitzung (save() meldet das)
  }
  const persist: PoiPersistenz = o.persistenz ?? new PoiPersistence(storage, `scanview:pois:${m.projectId}`);
  const modeListeners: ((on: boolean) => void)[] = [];
  let createMode = false;
  let auswahl: string | null = null;
  const auswahlCbs: ((id: string | null) => void)[] = [];
  const gespeichertCbs: ((id: string) => void)[] = [];
  const formularCbs: ((offen: boolean, grund: 'geoeffnet' | 'gespeichert' | 'verworfen') => void)[] = [];
  const neuGezeichnetCbs: (() => void)[] = [];
  const eingabeCbs: (() => void)[] = [];

  /** Speichert; bei Fehler Hinweis statt Erfolgsmeldung (I1). true = gespeichert. */
  const save = (): boolean => {
    const err = persist.save(store, m.revision);
    if (err) notify(err);
    return err === null;
  };

  // Marker
  const VORSCHLAG_MAT = kugelMaterial(0x7777cc, 0.5);
  const group = new Group();
  group.name = 'scanview-pois';
  instance.threeObjects.add(group);
  const drawMarkers = (): void => {
    for (const child of [...group.children]) {
      group.remove(child);
      if (child instanceof CSS2DObject) child.element.remove();
    }
    for (const p of store.list().filter(p => !p.vorschlag || o.vorschlaegeSichtbar?.() !== false)) {
      const gewaehlt = p.id === auswahl;
      const kugel = new KugelMarker({
        material: p.vorschlag ? VORSCHLAG_MAT : gewaehlt ? KUGEL_MAT.gewaehlt : istRueckmeldung(p.category) ? KUGEL_MAT.rueckmeldung : KUGEL_MAT.befund,
        groesse: gewaehlt ? KUGEL_POI_GEWAEHLT : KUGEL_POI,
      });
      kugel.position.set(...p.position);
      const label = new CSS2DObject(el('div', p.title));
      label.element.className = markerKlassen(p.category, gewaehlt);
      if (p.vorschlag) { label.element.classList.add('sv-vorschlag'); if (p.vorschlag.loeschen) label.element.classList.add('sv-loeschvorschlag'); label.element.append(el('span', vorschlagText(p.vorschlag))); }
      label.element.dataset.poi = p.id;
      const k = o.kennzeichen?.(p.id) ?? null;
      if (k !== null) {
        const stand = el('span', k);
        stand.className = 'sv-marker-stand';
        label.element.append(stand);
      }
      label.position.set(...p.position);
      // Schild über der Kugel: Unterkante am Punkt (center), nach oben um den sichtbaren Kugelradius versetzt.
      // Die Kugel bestimmt ihren Radius beim Zeichnen; CSS2D wird im selben Bild danach gesetzt.
      label.center.set(0.5, 1);
      let versatz = -1;
      const setzeVersatz = (px: number): void => {
        const neu = Math.ceil(px) + LABEL_ABSTAND_PX;
        if (neu === versatz) return;
        versatz = neu;
        label.element.style.marginTop = `-${neu}px`;
      };
      setzeVersatz(kugel.pxZuletzt);
      kugel.beimZeichnen = setzeVersatz;
      group.add(kugel, label);
    }
    group.updateMatrixWorld(true);
    instance.notifyChange(group);
  };

  // Panel
  const search = el('input');
  search.type = 'search';
  search.placeholder = 'POI suchen …';
  const list = el('ul');
  const exportBtn = el('button', 'Exportieren');
  const importBtn = el('button', 'Importieren');
  const fileInput = el('input');
  fileInput.type = 'file';
  fileInput.accept = '.json,application/json';
  fileInput.hidden = true;
  const bar = el('div');
  // Mit fester Kategorie (Rückmelder) kein Import: Einträge anderer Kategorie lehnte der Server ab, die lokale
  // Kopie bliebe bis zum Neuladen stehen. Die Übernahme der Browser-POIs (importText) gibt es dort ebenfalls nicht.
  if (o.kategorieFest === undefined) bar.append(exportBtn, importBtn, fileInput);
  else bar.append(exportBtn);
  panel.append(search, list, bar);

  const renderList = (): void => {
    list.replaceChildren();
    for (const p of store.search(search.value).filter(p => !p.vorschlag || o.vorschlaegeSichtbar?.() !== false)) {
      const li = el('li');
      const go = el('button');
      go.className = 'go';
      go.dataset.poi = p.id;
      go.append(el('span', p.title), el('br'), el('span', [p.category, p.source === 'package' ? 'Paket' : ''].filter(Boolean).join(' · ')));
      (go.lastChild as HTMLElement).className = 'cat';
      go.title = p.note;
      if (p.id === auswahl) go.setAttribute('aria-current', 'true');
      go.addEventListener('click', () => goTo(p.id));
      li.append(go);
      if (p.vorschlag) { li.append(el('span',vorschlagText(p.vorschlag))); if (persist.pruefen) pruefknoepfe(li,a => persist.pruefen!(p.id,a),notify); }
      if (p.source === 'browser' && !p.vorschlag) {
        const del = el('button', '×');
        del.title = 'POI löschen';
        del.addEventListener('click', () => {
          store.remove(p.id);
          refresh();
          save();
        });
        li.append(del);
      }
      list.append(li);
    }
  };
  const refresh = (): void => {
    // Ausgewählter POI verschwunden (gelöscht, Abgleich): Auswahl aufheben.
    if (auswahl !== null && !store.get(auswahl)) {
      auswahl = null;
      auswahlCbs.forEach((cb) => cb(null));
    }
    renderList();
    drawMarkers();
    neuGezeichnetCbs.forEach((cb) => cb());
  };
  // Auswahl nur an den vorhandenen Listeneinträgen markieren, ohne die Liste neu aufzubauen: ein per Tastatur
  // gewählter Eintrag behält so den Fokus.
  const markiereListe = (): void => {
    for (const b of list.querySelectorAll<HTMLButtonElement>('button.go')) {
      if (b.dataset.poi === auswahl) b.setAttribute('aria-current', 'true');
      else b.removeAttribute('aria-current');
    }
  };
  const waehle = (id: string | null): void => {
    const neu = id !== null && store.get(id) ? id : null;
    if (neu === auswahl) return;
    auswahl = neu;
    markiereListe();
    drawMarkers();
    auswahlCbs.forEach((cb) => cb(neu));
  };
  search.addEventListener('input', renderList);

  // Formular
  const title = el('input');
  title.required = true;
  title.maxLength = POI_LIMITS.title;
  title.placeholder = 'Titel';
  const category = el('input');
  category.maxLength = POI_LIMITS.category;
  category.placeholder = 'Kategorie';
  const kategorieFest = o.kategorieFest;
  if (kategorieFest === undefined) category.setAttribute('list', 'poi-categories');
  else {
    category.readOnly = true;
    category.setAttribute('aria-readonly', 'true');
    category.title = 'Kategorie ist für Ihre Rolle fest';
    category.value = kategorieFest;
  }
  const datalist = el('datalist');
  datalist.id = 'poi-categories';
  for (const c of o.kategorien ?? POI_CATEGORIES) {
    const opt = el('option');
    opt.value = c;
    datalist.append(opt);
  }
  const note = el('textarea');
  note.maxLength = POI_LIMITS.note;
  // Vereinbarung: Eine Rückfrage an den Empfänger steht in einer eigenen Zeile, die mit „Frage:“ beginnt.
  note.placeholder = 'Notiz – Rückfrage in eigener Zeile mit „Frage:“ beginnen';
  const ok = el('button', 'Speichern');
  ok.type = 'submit';
  const cancel = el('button', 'Abbrechen');
  cancel.type = 'button';
  // Inspektor rechts (styles/befund.css); die Überschrift benennt das Formular.
  form.classList.add('sv-inspektor');
  const kopf = el('h2', 'Neuer POI');
  kopf.id = 'poi-form-titel';
  form.setAttribute('aria-labelledby', kopf.id);
  form.append(kopf, title, category, datalist, note);
  // Plattformmodus: Notizen landen unveränderlich im Bearbeitungsprotokoll (siehe ADR 0011).
  if (o.persistenz) {
    const hinweis = el('small', 'Für alle Projektbeteiligten sichtbar und dauerhaft im Verlauf – bitte keine Personendaten (Namen, Telefonnummern).');
    hinweis.className = 'sv-hinweis';
    form.append(hinweis);
  }
  form.append(ok, cancel);
  // Punkt und Standpunkt werden beim Klick erfasst (I3), nicht erst beim Absenden: Minikarte, Liste oder
  // Esc können das Panorama inzwischen gewechselt oder beendet haben.
  let pending: { pos: Vec3; station: string | null; id: string; fortgesetzt: boolean; positionSource?: PointSource|null } | null = null;
  // Nur das Ergebnis des jüngsten Klicks gilt (I2); Schließen/Moduswechsel verwirft laufende Picks.
  let clickSeq = 0;
  let vorbelegung: FormularWerte | null = null;
  /** Fokus vor dem Öffnen des Formulars (Ziel der Fokusrückgabe nach Esc). */
  let fokusVorher: HTMLElement | null = null;
  /** Einzige Stelle, die das Formular schließt: meldet das Schließen genau einmal (nur, wenn es offen war). */
  const closeForm = (grund: 'gespeichert' | 'verworfen' = 'verworfen'): void => {
    clickSeq++;
    const warOffen = !form.hidden;
    form.hidden = true;
    pending = null;
    form.reset();
    if (kategorieFest !== undefined) category.value = kategorieFest;
    if (warOffen) formularCbs.forEach((cb) => cb(false, grund));
  };
  // Esc und „Abbrechen“ verwerfen und geben den Fokus zurück: im Begehen an die Leinwand, sonst dorthin, wo er
  // vor dem Öffnen lag (Rückfall Leinwand).
  const verwerfen = (): void => {
    const vorher = fokusVorher;
    closeForm('verworfen');
    const ziel = !o.begehenAktiv?.() && vorher?.isConnected ? vorher : instance.domElement;
    ziel.focus({ preventScroll: true });
  };
  cancel.addEventListener('click', verwerfen);
  const openAt = (pos: readonly [number, number, number], station: string | null, opt?: { id?: string; werte?: FormularWerte; positionSource?: PointSource|null }): boolean => {
    if (!form.hidden) {
      notify(FORMULAR_OFFEN);
      return false;
    }
    // Ein noch laufender Klick-Pick darf das Formular nicht mehr verschieben.
    clickSeq++;
    // Die ID steht ab jetzt fest (fortgesetzt oder neu reserviert): der lokale Entwurf kennt sie, bevor der POI
    // gespeichert und gesendet wird — ein Fortsetzen nach verlorenem Versand ergibt so nie einen zweiten POI.
    pending = { pos: [pos[0], pos[1], pos[2]], station, id: opt?.id ?? store.reserviereId(), fortgesetzt: opt?.id !== undefined, ...(opt?.positionSource !== undefined ? {positionSource:opt.positionSource} : {}) };
    const werte = opt?.werte ?? vorbelegung;
    if (werte) {
      title.value = werte.title;
      category.value = kategorieFest ?? werte.category;
      note.value = werte.note;
    }
    fokusVorher = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
    form.hidden = false;
    title.focus();
    formularCbs.forEach((cb) => cb(true, 'geoeffnet'));
    return true;
  };
  // Esc schließt das Formular wie „Abbrechen“. Das Formular behandelt seine Tasten selbst
  // (data-tasten-eigen, keymap.ts): der Kürzel-Verteiler beendet mit diesem Esc nicht zusätzlich etwas.
  form.dataset.tastenEigen = '';
  form.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape' || form.hidden) return;
    ev.preventDefault();
    ev.stopPropagation();
    verwerfen();
  });
  form.addEventListener('input', () => {
    if (!form.hidden) eingabeCbs.forEach((cb) => cb());
  });
  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (!pending) return;
    const id = store.idZumAnlegen(pending.id, pending.fortgesetzt);
    if (id !== pending.id) {
      // Der Entwurf muss die neue ID kennen, bevor der POI gespeichert und gesendet wird.
      pending.id = id;
      eingabeCbs.forEach((cb) => cb());
    }
    let neu: Poi;
    try {
      neu = store.add(
        { title: title.value, category: kategorieFest ?? category.value, note: note.value, position: pending.pos, station: pending.station, ...(pending.positionSource !== undefined ? {positionSource:pending.positionSource} : {}) },
        pending.id,
      );
    } catch (e) {
      notify(e instanceof PoiError ? e.message : String(e));
      return;
    }
    closeForm('gespeichert');
    setCreateMode(false);
    refresh();
    if (save()) gespeichertCbs.forEach((cb) => cb(neu.id));
  });

  // Anlegen per Klick (Kamera-Drag und Doppelklick zählen nicht). Messen und Anlegen schließen sich über die
  // Werkzeugleiste aus (toolbar.ts ist einziger Besitzer des Werkzeugs).
  let down: { x: number; y: number } | null = null;
  instance.domElement.addEventListener('pointerdown', (ev) => {
    down = { x: ev.clientX, y: ev.clientY };
  });
  instance.domElement.addEventListener('click', (ev) => {
    const moved = down ? Math.hypot(ev.clientX - down.x, ev.clientY - down.y) : 0;
    down = null;
    if (o.unterPointerLock?.()) return;
    if (!createMode || moved > CLICK_MOVE_TOLERANCE_PX || ev.detail > 1) return;
    // Offenes Formular: kein stilles Verschieben des Punkts (I2) — erst speichern oder abbrechen.
    if (!form.hidden) {
      notify(FORMULAR_OFFEN);
      return;
    }
    if (o.rueckfrageOffen?.()) {
      notify('Erst die Rückfrage beantworten');
      return;
    }
    const seq = ++clickSeq;
    const station = pano.state?.station ?? null;
    // Panorama (wie Messen): längeres Zeitbudget, grober Ersatz nur bis PANO_COARSE_MAX_M, keine
    // Sichtbarkeitsregel (die Punktwolke ist dort nicht das sichtbare Bild).
    const imPanorama = pano.active;
    const opts = imPanorama ? panoPickOptions() : {};
    const frage = o.frage;
    const pose0 = o.pose?.();
    const ergebnis = frage
      ? zielpunktSetzen({
          // Erste Suche über das Ereignis (derselbe Weg wie bisher), die zweite im gewählten Tiefenfenster.
          quelle: {
            pickAtScreen: (x, y, op) => (op?.tiefenFenster ? picker.pickAtScreen(x, y, op) : picker.pickAtEvent(ev, op)),
            vorschauAtScreen: (x, y) => picker.vorschauAtScreen(x, y),
          },
          x: ev.clientX,
          y: ev.clientY,
          opts,
          sichtbarkeitPruefen: !imPanorama,
          frage: (b) => frage(b, { x: ev.clientX, y: ev.clientY }),
          ansichtUnveraendert: () => {
            const jetzt = o.pose?.();
            return !pose0 || !jetzt || jetzt.every((w, i) => Math.abs(w - pose0[i]!) <= 1e-9);
          },
        })
      : picker.pickAtEvent(ev, imPanorama ? opts : undefined).then((r) => (r.point
        ? { art: 'gesetzt' as const, punkt: r.point, info: r.info }
        : { art: 'kein-treffer' as const, info: r.info }));
    ergebnis
      .then((e) => {
        // Überholt (neuerer Klick, Formular geschlossen, Modus aus — auch durch Wechsel auf ein anderes
        // Werkzeug) → verwerfen.
        if (seq !== clickSeq || !createMode) return;
        if (e.art === 'abgebrochen') {
          notify('grund' in e && e.grund === 'ansicht' ? 'abgebrochen – Ansicht hat sich bewegt' : 'abgebrochen – kein Befund gesetzt');
          return;
        }
        if (e.art !== 'gesetzt') {
          const grund = keinPunktGrund(e.info, 'grund' in e ? e.grund : undefined);
          notify(keinPunktText(grund, { folge: 'kein Befund gesetzt', ausgeblendet: hiddenCloudMessage, feinGrund: e.info.fineReason, mesh:e.info.source === 'mesh' }));
          return;
        }
        openAt(e.punkt, station, {positionSource:sourceFromPick(e.info)});
      })
      .catch((e: unknown) => {
        console.error('POI-Punkt nicht auswertbar:', e);
        if (seq === clickSeq && createMode) notify('Punkt konnte nicht ausgewertet werden');
      });
  });

  const setCreateMode = (on: boolean): void => {
    if (on === createMode) return;
    createMode = on;
    clickSeq++;
    if (on) {
      picker.warm();
      notify('Markieren: auf einen Punkt klicken, um einen Befund zu setzen');
    } else closeForm();
    modeListeners.forEach((cb) => cb(on));
  };

  const goTo = (id: string): void => {
    const p = store.get(id);
    if (!p) return;
    // Ein Sprung zu einem POI (Liste, später Befundliste) wählt ihn aus.
    waehle(id);
    o.vorSprung?.();
    const own = p.station ? stations.find((s) => s.id === p.station && s.panorama) : undefined;
    const s = own ?? nearestStation3d(stations, p.position, { requirePanorama: true, maxDistM: POI_STATION_MAX_M });
    if (s && pano.enter(s.id)) {
      pano.lookAt(p.position);
      return;
    }
    if (pano.active) pano.leave();
    v.frameAt(p.position, 5);
  };

  const anlegen = (d: PoiDraft): Poi | null => {
    let neu: Poi;
    try {
      neu = store.add(d);
    } catch (e) {
      notify(e instanceof PoiError ? e.message : String(e));
      return null;
    }
    // Nicht gespeichert (Meldung kam von save): nichts zurücklassen, der Text bleibt beim Aufrufer.
    if (!save()) {
      store.remove(neu.id);
      return null;
    }
    refresh();
    return neu;
  };

  const importText = (text: string): number => {
    let r: { added: number; renamed: number };
    try {
      r = store.importBrowserPois(parsePoiFile(JSON.parse(text), m.projectId, 'browser'));
    } catch (e) {
      notify(`pois.json ungültig: ${e instanceof Error ? e.message : String(e)}`);
      return 0;
    }
    refresh();
    if (save())
      notify(`${r.added} POIs importiert${r.renamed ? ` (${r.renamed} mit Paket-ID unter neuer ID)` : ''}`);
    return r.added;
  };

  const exportFile = (): void => {
    const ausgelassen = ausgelasseneVorschlaege(store.list());
    exportMitHinweis(ausgelassen, () => {
      const blob = new Blob([JSON.stringify(store.toFile(m.revision), null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `pois-${m.projectId}.json`;
      a.click();
      URL.revokeObjectURL(url);
    });
  };
  exportBtn.addEventListener('click', exportFile);
  importBtn.addEventListener('click', () => fileInput.click());
  fileInput.addEventListener('change', () => {
    const f = fileInput.files?.[0];
    if (f) void f.text().then(importText);
    fileInput.value = '';
  });

  // Mitgeliefertes pois.json (fehlt → 404 → keine Paket-POIs, kein Fehler) und eigene POIs aus dem Browser
  try {
    const res = await fetch(resolveUrl(o.pkgBase, 'pois.json'));
    if (res.ok) store.setPackagePois(parsePoiFile(await res.json(), m.projectId, 'package'));
    else void res.body?.cancel().catch(() => undefined);
  } catch (e) {
    notify(`pois.json im Paket ungültig: ${e instanceof Error ? e.message : String(e)}`);
  }
  const loaded = await persist.load(store);
  if (loaded.warning) notify(loaded.warning);
  else if (loaded.renamed && save()) notify(`${loaded.renamed} eigene POIs mit Paket-ID unter neuer ID behalten`);
  refresh();

  return {
    store,
    setCreateMode,
    isCreateMode: () => createMode,
    onModeChange: (cb) => {
      modeListeners.push(cb);
    },
    goTo,
    exportFile,
    importText,
    refresh,
    openAt,
    waehle,
    ausgewaehlt: () => auswahl,
    onAuswahl: (cb) => {
      auswahlCbs.push(cb);
    },
    onGespeichert: (cb) => {
      gespeichertCbs.push(cb);
    },
    onFormular: (cb) => {
      formularCbs.push(cb);
    },
    formularOffen: () => !form.hidden,
    formularWerte: () => ({ title: title.value, category: kategorieFest ?? category.value, note: note.value }),
    formularOrt: () => (pending && !form.hidden ? { punkt: [pending.pos[0], pending.pos[1], pending.pos[2]], station: pending.station, id: pending.id, ...(pending.positionSource !== undefined ? {positionSource:pending.positionSource} : {}) } : null),
    onEingabe: (cb) => {
      eingabeCbs.push(cb);
    },
    fokussiereFormular: () => {
      if (!form.hidden) title.focus();
    },
    onNeuGezeichnet: (cb) => {
      neuGezeichnetCbs.push(cb);
    },
    anlegen,
    vorbelegen: (w) => {
      vorbelegung = w;
    },
    togglePanel: () => {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) search.focus();
    },
  };
}
