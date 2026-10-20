// Bedienleiste (lil-gui): Farbmodus, Punktgröße, Punktbudget, Tiefenschattierung (EDL), Ansichten,
// Schnittbox, Panorama, POI-Liste. Werkzeuge (Messen, POI anlegen) und Messliste: toolbar.ts,
// measureList.ts.
import { GRENZEN } from './geraet';
import GUI from 'lil-gui';
import type { OptionController } from 'lil-gui';
import type { ClipBox, ClipBoxMode } from './clipBox';
import type { PointcloudLayer } from './manifest';
import type { Panorama } from './panorama';
import type { AktionId } from './tastenbelegung';
import type { ColorMode, Viewer } from './viewer';
import { hoeheText } from './viewMath';
import { LETZTE_ATTRIBUT, vorhandeneKlassen } from './klassen';
import type { Echo, Punktfilter } from './klassen';
import type { FensterEffekt } from './fensterEffekt';

const COLOR_LABELS: Record<ColorMode, string> = { rgb: 'RGB', intensity: 'Intensität', elevation: 'Höhe', classification: 'Klassen' };

export interface UiHandle {
  gui: GUI;
  /** Namen der Controller mit Kürzel neu setzen (nach Änderung der Belegung). */
  aktualisiereKuerzel(): void;
}

const mit = (label: string, k: string): string => (k ? `${label} (${k})` : label);

export function buildUi(v: Viewer, cloud: PointcloudLayer | null, clip: ClipBox, container: HTMLElement, kuerzel: (a: AktionId) => string): UiHandle {
  const gui = new GUI({ title: 'Ansicht', container });
  const s = { farbe: v.colorModes[0] ?? 'elevation', punktgroesse: 2, budget: GRENZEN.punkte, edl: true, schnittbox: false };
  // Nur Farbmodi anbieten, die die Punktwolke hat (z. B. ohne Intensität bei reinem RGB-Scan).
  const options = Object.fromEntries(v.colorModes.map((k) => [COLOR_LABELS[k], k]));
  const farbe = gui.add(s, 'farbe', options).name('Farbe');
  const legende = hoehenLegende(v);
  if (legende) farbe.domElement.after(legende);
  farbe.onChange((x: ColorMode) => {
    v.setColorMode(x);
    if (legende) legende.hidden = x !== 'elevation';
  });
  if (legende) legende.hidden = s.farbe !== 'elevation';
  gui.add(s, 'punktgroesse', 1, 8, 1).name('Punktgröße').onChange((x: number) => v.setPointSize(x));
  gui.add(s, 'budget', 200_000, 5_000_000, 100_000).name('Punktbudget').onChange((x: number) => v.setPointBudget(x));
  gui.add(s, 'edl').name('Tiefenschattierung').onChange((x: boolean) => v.setEdl(x));
  gui.add({ reset: () => v.reset() }, 'reset').name('Startansicht');
  const alles = gui.add({ f: () => v.frameAll() }, 'f').name(mit('Alles zeigen', kuerzel('alles-zeigen')));
  gui
    .add(s, 'schnittbox')
    .name('Schnittbox')
    .onChange((on: boolean) => {
      clip.setEnabled(on);
      modus.show(on);
    });
  // Griffe der Schnittbox: sichtbar umschaltbar (Tasten G/S gibt es auf dem Handy nicht); `listen`
  // zeigt auch den per Taste gewählten Modus an. Nur bei eingeschalteter Schnittbox sichtbar.
  const griffe = {
    get modus(): ClipBoxMode {
      return clip.getMode();
    },
    set modus(m: ClipBoxMode) {
      clip.setMode(m);
    },
  };
  const modusOptionen = (): Record<string, ClipBoxMode> => ({
    [mit('Verschieben', kuerzel('schnittbox.verschieben'))]: 'translate',
    [mit('Größe ändern', kuerzel('schnittbox.skalieren'))]: 'scale',
  });
  const modus = gui.add(griffe, 'modus', modusOptionen()).name('Schnittbox-Griffe').listen().hide();
  // CRS-Hinweis (I4): Langtext dauerhaft in der Meldungszeile (main.ts), hier nur kurz markiert.
  const crsNote = v.crsHint ? ' · lokal ⚠' : v.local ? ' · lokal' : '';
  gui.title(cloud ? `siteBits · ${(cloud.pointCount / 1e6).toFixed(0)} Mio. Punkte${crsNote}` : `siteBits${crsNote}`);
  if (!cloud) for (const key of ['farbe', 'punktgroesse', 'budget', 'edl']) gui.controllers.find((c) => c.property === key)?.hide();
  v.setPointSize(s.punktgroesse);
  v.setPointBudget(s.budget);
  return {
    gui,
    aktualisiereKuerzel: () => {
      alles.name(mit('Alles zeigen', kuerzel('alles-zeigen')));
      // Nur die Texte der <option>-Elemente setzen: `Controller.options()` würde den Controller neu anlegen
      // und ans Ende der GUI hängen. Werte und Reihenfolge (translate, scale) bleiben.
      const texte = Object.keys(modusOptionen());
      const oc = modus as unknown as OptionController;
      // lil-gui 0.17: `updateDisplay()` setzt die Anzeige des gewählten Eintrags aus dem privaten `_names`
      // (nur im Konstruktor befüllt) — ohne dessen Aktualisierung überschriebe es den neuen Text wieder.
      texte.forEach((t, i) => {
        const o = oc.$select.options[i];
        if (o) o.textContent = t;
        oc._names[i] = t;
      });
      modus.updateDisplay();
    },
  };
}

/** Höhenlegende unter dem Farbregler: Überschrift, Farbbalken (nur Darstellung, Farben aus dem Stil) und die
 *  Grenzen der Höhenfarben als Text. null, wenn die Punktwolke keine Höhenfarben hat. */
function hoehenLegende(v: Viewer): HTMLElement | null {
  const spanne = v.hoehenSpanne();
  if (!spanne) return null;
  const fig = document.createElement('figure');
  fig.className = 'sv-legende';
  const titel = document.createElement('figcaption');
  titel.textContent = 'Höhe in Metern';
  const skala = document.createElement('div');
  skala.className = 'sv-legende-skala';
  const min = document.createElement('span');
  min.className = 'sv-legende-min';
  min.textContent = hoeheText(spanne[0]);
  const balken = document.createElement('span');
  balken.className = 'sv-legende-balken';
  balken.setAttribute('aria-hidden', 'true');
  const bis = document.createElement('span');
  bis.className = 'sv-nur-sr';
  bis.textContent = ' bis ';
  const max = document.createElement('span');
  max.className = 'sv-legende-max';
  max.textContent = hoeheText(spanne[1]);
  skala.append(min, balken, bis, max);
  fig.append(titel, skala);
  return fig;
}

/** Klassen-Ordner (F30): je vorhandene Klasse ein Schalter mit Farbfeld (Farben im Modus „Klassen“), dazu der
 *  Echofilter, wenn die Wolke Mehrfachechos hat. Ausgeblendet wirkt in jedem Farbmodus und beim Messen (klassen.ts).
 *  Nichts angelegt ohne Klassen und ohne Echos. `geaendert` nach jeder Änderung (Ansichtslink). */
export function addKlassenControls(gui: GUI, v: Viewer, cloud: PointcloudLayer, geaendert: () => void): void {
  const klassen = cloud.attributes.includes('classification') ? vorhandeneKlassen(cloud.classes) : [];
  const echos = cloud.attributes.includes('returns');
  if (!klassen.length && !echos) return;
  const f = gui.addFolder('Klassen und Echos');
  const setze = (neu: Punktfilter): void => {
    v.setPunktfilter(neu);
    geaendert();
  };
  const zahl = new Intl.NumberFormat('de-DE');
  for (const k of klassen) {
    const s = { an: !v.punktfilter().aus.has(k.klasse) };
    const c = f.add(s, 'an').name(`${k.name} (${zahl.format(k.punkte)})`);
    c.domElement.dataset.klasse = String(k.klasse);
    const feld = document.createElement('span');
    feld.className = 'sv-klassenfarbe';
    feld.style.background = v.klassenFarbe(k.klasse);
    feld.setAttribute('aria-hidden', 'true');
    c.$name.prepend(feld);
    c.onChange((an: boolean) => {
      const aus = new Set(v.punktfilter().aus);
      if (an) aus.delete(k.klasse);
      else aus.add(k.klasse);
      setze({ ...v.punktfilter(), aus });
    });
  }
  if (echos) {
    const s = { echo: v.punktfilter().echo };
    // „Nur letzte“ nur, wenn der Import letzte Echos markiert hat (UserData, klassen.ts LETZTE_ATTRIBUT).
    const wahl: Record<string, Echo> = { 'Alle Echos': 'alle', 'Nur erste Echos': 'erste' };
    if (cloud.attributes.includes(LETZTE_ATTRIBUT)) wahl['Nur letzte Echos'] = 'letzte';
    f.add(s, 'echo', wahl)
      .name('Echos')
      .onChange((echo: Echo) => setze({ ...v.punktfilter(), echo }));
  }
}

/** Fenster-Effekt (F32): Schalter (startet die Webcam erst hier), „Mitte setzen“, Bildschirmgröße, Stärke.
 *  Kamerabild bleibt im Browser; Hinweis mit Aus-Knopf zeigt fensterEffekt.ts selbst. */
export function addFensterControls(gui: GUI, fe: FensterEffekt): (an: boolean) => void {
  const f = gui.addFolder('Fenster-Effekt (Webcam)');
  f.close();
  const e = fe.einstellungen();
  const s = { an: fe.an(), zoll: e.zoll, staerke: e.staerke };
  const schalter = f.add(s, 'an').name('Perspektive folgt dem Kopf').onChange((x: boolean) => void fe.setzeAn(x));
  f.add({ m: () => fe.mitteSetzen() }, 'm').name('Mitte setzen (gerade sitzen)');
  f.add(s, 'zoll', { '13″': 13, '14″': 14, '15,6″': 15.6, '16″': 16, '24″': 24, '27″': 27 }).name('Bildschirm')
    .onChange((x: number) => fe.setze({ zoll: x }));
  f.add(s, 'staerke', 0, 2, 0.1).name('Stärke').onChange((x: number) => fe.setze({ staerke: x }));
  return (an) => {
    s.an = an;
    schalter.updateDisplay();
  };
}

/** Panorama-Ordner: Überblendregler Punktwolke ↔ Panorama, Verlassen. */
export function addPanoramaControls(gui: GUI, pano: Panorama): void {
  const f = gui.addFolder('Panorama');
  const s = { blend: pano.blend };
  const slider = f
    .add(s, 'blend', 0, 1, 0.01)
    .name('Überblendung Wolke ↔ Bild')
    .onChange((x: number) => pano.setBlend(x));
  // Programmatische Änderungen (pano.setBlend) im Regler nachführen; updateDisplay löst kein onChange aus.
  pano.onChange(() => {
    if (s.blend !== pano.blend) {
      s.blend = pano.blend;
      slider.updateDisplay();
    }
  });
  f.add({ leave: () => pano.leave() }, 'leave').name('Panorama verlassen (Esc)');
}

/** POI-Ordner: Liste mit Suche/Export/Import. Anlegen über die Werkzeugleiste.
 *  Der Listenknopf geht wie Kürzel/Werkzeugleiste über panels.toggle('pois') (main.ts), nicht mehr
 *  direkt über pois.togglePanel() — sonst würde er einen nur verdeckten (nicht wirklich geschlossenen) POI-Reiter
 *  schließen statt ihn nach vorn zu holen. */
export function addPoiControls(gui: GUI, onToggle: () => void): void {
  const f = gui.addFolder('POIs');
  f.add({ list: onToggle }, 'list').name('POI-Liste ein/aus');
}
