// Lokaler Befundentwurf im Viewer: Ein angefangener Befund (Formular offen) wird samt Zielpunkt, Standpunkt,
// Ansicht und einem verkleinerten Vorschaubild im Browser gehalten (entwurfSpeicher.ts), damit er einen
// Verbindungsabbruch oder ein Neuladen übersteht. Gelöscht wird er nach dem Verwerfen und erst dann nach dem
// Speichern, wenn der Befund wirklich sicher ist: im Einzelmodus und ohne Server-Schreibrecht sofort nach dem
// lokalen Speichern, sonst erst nach der Bestätigung durch den Server. Beim Laden wird ein vorhandener Entwurf
// angeboten (Fortsetzen / Verwerfen), ohne den Fokus zu nehmen; ist er zu alt oder unlesbar, wird er gelöscht.
// Ein Entwurf je Konto und Projekt; ohne Schlüssel (Plattform ohne eigene Konto-ID, Einzelmodus nur als
// Rückfall) gibt es keinen. Die ID des künftigen POIs steht ab dem Öffnen des Formulars im Entwurf.
// Vorschaubild: im nächsten gezeichneten Bild synchron von der Leinwand auf ein Hilfs-Canvas kopiert (ohne
// preserveDrawingBuffer ist der Inhalt nur dort gültig), angezeigt per createImageBitmap auf einem <canvas> —
// so bleibt die Bildquelle der Seite auf die eigene Herkunft beschränkt (keine blob:- oder data:-Adressen).
import { entwurfAbgelaufen, entwurfText, vorschauGroesse } from './befundEntwurf';
import type { Entwurf } from './befundEntwurf';
import { oeffneEntwurfSpeicher } from './entwurfSpeicher';
import type { EntwurfSpeicher } from './entwurfSpeicher';
import type { PoiPersistenz } from './poiPersistenz';
import type { PoiPanel } from './pois';
import type { Viewer } from './viewer';

export const ENTWURF_ANGEBOT = 'Unvollständiger Befund gefunden – Fortsetzen oder Verwerfen';
export const SCHON_GESPEICHERT = 'Befund war schon gespeichert';
export const ENTWURF_VERWORFEN = 'Entwurf verworfen';
/** Esc im Angebot: der Entwurf bleibt — aber nur, bis ein neues Formular ihn ersetzt (ein Entwurf je Konto und Projekt). */
export const ENTWURF_ZURUECKGESTELLT = 'Entwurf zurückgestellt – beim nächsten Laden wieder angeboten; ein neuer Befund ersetzt ihn';
export const ENTWURF_NICHT_MOEGLICH = 'Entwürfe in diesem Browser nicht möglich';

const EINGABE_RUHE_MS = 500;
const BILD_WARTEN_MS = 1000;
const BESTAETIGUNG_TAKT_MS = 2000;
const BESTAETIGUNG_MAX_MS = 10 * 60 * 1000;
/** Länger als ein Hinweis steht (4 s). Die Ansagen-Warteschlange hält nur Ansagen mit Vorrang zurück; der Hinweis
 *  „Befund aus dem Link nicht gefunden“ hat keinen (ohne Befund keine Befund-Ansage), deshalb wartet das Angebot selbst. */
export const ANSAGE_NACH_BEFUNDLINK_MS = 4500;

export interface EntwurfSteuerung {
  /** Vorhandenen Entwurf anbieten (nach dem Anwenden des Links, vor dem Öffnen eines verlinkten Befunds). */
  biete(): Promise<void>;
}

export function attachEntwurfSteuerung(o: {
  v: Pick<Viewer, 'instance'>;
  pois: PoiPanel;
  /** Konto und Projekt; null = kein Entwurf (Plattform ohne eigene Konto-ID). */
  schluessel: string | null;
  /** Plattform mit Server-Schreibrecht: true, sobald der Server den POI bestätigt hat. null = lokal gespeichert
   *  ist sicher (Einzelmodus, nur Lesen am Server). */
  serverBestaetigt: ((id: string) => boolean) | null;
  persistenz: Pick<PoiPersistenz, 'lokalId'> | undefined;
  /** Aktuelle Pose in Adresszeilen-Form (ohne „#“; ohne Messungen, Ebenen und Befundangaben). */
  ansicht: () => string;
  /** Gespeicherte Ansicht anwenden (nur die Pose). */
  wendeAnsichtAn: (ansicht: string) => void;
  leinwand: HTMLElement;
  /** Das Angebot steht im Dokument direkt hinter diesem Element (Tab-Folge: gleich nach der Ansicht). */
  nach: HTMLElement | null;
  /** Seite mit Befundlink geöffnet: dessen Hinweis („nicht gefunden“, „älterer Stand“ …) steht einige Sekunden
   *  in derselben Ansage — die Ansage des Angebots wartet so lange; das Angebot selbst erscheint sofort. */
  nachBefundlink?: boolean;
  melde: (t: string) => void;
  hinweis: (t: string) => void;
  speicher?: Promise<EntwurfSpeicher | null>;
}): EntwurfSteuerung {
  const { pois, schluessel } = o;
  const instance = o.v.instance;
  // Erst öffnen, wenn es einen Schlüssel gibt; ohne ihn wird nichts im Browser angelegt.
  const speicherP: Promise<EntwurfSpeicher | null> = schluessel === null ? Promise.resolve(null) : (o.speicher ?? oeffneEntwurfSpeicher());
  // Schreiben und Löschen nacheinander, in Auftragsreihenfolge (ein spätes Bild überholt kein Löschen).
  let kette: Promise<void> = Promise.resolve();
  const inKette = (arbeit: (s: EntwurfSpeicher) => Promise<void>): void => {
    kette = kette.then(async () => {
      const s = await speicherP;
      if (s) await arbeit(s).catch(() => undefined);
    });
  };
  let nichtMoeglichGemeldet = false;
  const meldeNichtMoeglich = (): void => {
    if (nichtMoeglichGemeldet) return;
    nichtMoeglichGemeldet = true;
    o.hinweis(ENTWURF_NICHT_MOEGLICH);
  };

  /** Generation: jede spätere Fortsetzung (Ruhezeit, Bild, Bestätigung) prüft sie, bevor sie schreibt —
   *  nach Verwerfen, Löschen oder einem neuen Formular schreibt nichts Altes mehr. */
  let gen = 0;
  /** Entwurf des offenen Formulars. */
  let aktuell: Entwurf | null = null;
  /** Gespeichert, aber die Speicher-Rückmeldung steht noch aus (zwischen Schließen und onGespeichert). */
  let abgeschlossen: Entwurf | null = null;
  /** Beim nächsten Öffnen: dieser Entwurf wird fortgesetzt. */
  let fortsetzung: Entwurf | null = null;
  let ruhe: ReturnType<typeof setTimeout> | null = null;
  let wache: { takt: ReturnType<typeof setInterval>; pruefe: () => void } | null = null;

  const schreibe = (e: Entwurf, bild?: Blob | null): void => {
    const kopie: Entwurf = { ...e, punkt: [...e.punkt], formular: { ...e.formular } };
    inKette((s) => s.schreibe(kopie, bild));
  };
  const loesche = (): void => {
    gen++;
    aktuell = null;
    abgeschlossen = null;
    if (schluessel !== null) inKette((s) => s.loesche(schluessel));
  };
  const ruheAus = (): void => {
    if (ruhe !== null) clearTimeout(ruhe);
    ruhe = null;
  };
  const wacheAus = (): void => {
    if (wache) clearInterval(wache.takt);
    wache = null;
  };

  /** Vorschaubild aus dem nächsten gezeichneten Bild (synchron im after-render), verkleinert. */
  const nimmBildAuf = (g: number): void => {
    let erledigt = false;
    const ende = (): void => {
      erledigt = true;
      clearTimeout(frist);
      instance.removeEventListener('after-render', nachBild);
    };
    const nachBild = (): void => {
      if (erledigt) return;
      ende();
      if (g !== gen || !aktuell) return;
      const quelle = instance.domElement;
      const { breite, hoehe } = vorschauGroesse(quelle.width, quelle.height);
      if (breite === 0 || hoehe === 0) return;
      const hilf = document.createElement('canvas');
      hilf.width = breite;
      hilf.height = hoehe;
      const ctx = hilf.getContext('2d');
      if (!ctx) return;
      try {
        ctx.drawImage(quelle, 0, 0, breite, hoehe);
      } catch {
        return;
      }
      hilf.toBlob(
        (blob) => {
          if (blob && g === gen && aktuell) schreibe(aktuell, blob);
        },
        'image/webp',
        0.8,
      );
    };
    // Kein Bild in absehbarer Zeit (Leinwand verborgen): der Entwurf bleibt ohne Vorschau.
    const frist = setTimeout(ende, BILD_WARTEN_MS);
    instance.addEventListener('after-render', nachBild);
    instance.notifyChange(instance.view.camera);
  };

  /** Plattform: Entwurf löschen, sobald der Server den POI bestätigt (alle 2 s und bei jedem Neuzeichnen). */
  const warteAufBestaetigung = (id: string, bestaetigt: (id: string) => boolean): void => {
    wacheAus();
    const g = gen;
    const beginn = Date.now();
    const pruefe = (): void => {
      if (g !== gen) return wacheAus();
      if (bestaetigt(id)) {
        wacheAus();
        loesche();
      } else if (Date.now() - beginn > BESTAETIGUNG_MAX_MS) wacheAus();
    };
    wache = { takt: setInterval(pruefe, BESTAETIGUNG_TAKT_MS), pruefe };
    pruefe();
  };
  pois.onNeuGezeichnet(() => wache?.pruefe());

  pois.onFormular((offen, grund) => {
    if (schluessel === null) return;
    ruheAus();
    if (offen) {
      schliesseAngebot();
      const ort = pois.formularOrt();
      const fort = fortsetzung;
      fortsetzung = null;
      gen++;
      wacheAus();
      abgeschlossen = null;
      if (!ort) return;
      void speicherP.then((s) => {
        if (!s) meldeNichtMoeglich();
      });
      aktuell = {
        schema: 1,
        schluessel,
        zeit: Date.now(),
        punkt: ort.punkt,
        ...(ort.positionSource !== undefined ? {positionSource:ort.positionSource} : {}),
        station: ort.station,
        ansicht: fort?.ansicht ?? o.ansicht(),
        // Schon beim Öffnen fest (fortgesetzt oder neu reserviert): jeder Versand trägt eine ID, die der Entwurf
        // kennt — auch wenn die Seite gleich nach dem Speichern endet.
        poiId: ort.id,
        formular: pois.formularWerte(),
      };
      // Neuer Entwurf: altes Bild weg, gleich ein neues aufnehmen. Fortsetzung: das Bild vom Beginn bleibt (die
      // eben angewandte Ansicht ist noch nicht fertig geladen).
      schreibe(aktuell, fort ? undefined : null);
      if (!fort) nimmBildAuf(gen);
      return;
    }
    const e = aktuell;
    aktuell = null;
    if (grund === 'verworfen') {
      loesche();
      return;
    }
    // Gespeichert: letzten Inhalt sichern; gelöscht wird erst, wenn der Befund sicher ist (onGespeichert).
    if (e) {
      abgeschlossen = e;
      schreibe(e);
    }
  });
  pois.onEingabe(() => {
    if (!aktuell) return;
    aktuell.formular = pois.formularWerte();
    // Beim Absenden kann die reservierte ID schon vergeben sein; das Formular meldet dann die neue ID über diesen
    // Weg. Ohne sie legte ein später fortgesetzter Entwurf den Befund unter der alten ID ein zweites Mal an.
    const ort = pois.formularOrt();
    if (ort) aktuell.poiId = ort.id;
    aktuell.zeit = Date.now();
    ruheAus();
    const g = gen;
    ruhe = setTimeout(() => {
      ruhe = null;
      if (g === gen && aktuell) schreibe(aktuell);
    }, EINGABE_RUHE_MS);
  });
  pois.onGespeichert((id) => {
    const e = abgeschlossen;
    abgeschlossen = null;
    if (!e) return;
    if (o.serverBestaetigt === null) {
      loesche();
      return;
    }
    // Die ID steht seit dem Öffnen im Entwurf; jetzt nur noch auf die Bestätigung warten.
    warteAufBestaetigung(id, o.serverBestaetigt);
  });

  // Angebot beim Laden
  const bereich = document.createElement('section');
  bereich.className = 'sv-entwurf';
  bereich.hidden = true;
  bereich.setAttribute('role', 'region');
  bereich.setAttribute('aria-labelledby', 'sv-entwurf-titel');
  bereich.dataset.tastenEigen = '';
  if (o.nach) o.nach.after(bereich);
  else document.body.append(bereich);
  const schliesseAngebot = (): void => {
    if (bereich.hidden) return;
    const hatteFokus = bereich.contains(document.activeElement);
    bereich.hidden = true;
    bereich.replaceChildren();
    if (hatteFokus) o.leinwand.focus({ preventScroll: true });
  };
  // Esc schließt das Angebot, ohne den Entwurf zu löschen (beim nächsten Laden kommt es wieder). Die Ansage sagt
  // dazu, dass ein neues Formular ihn ersetzt — sonst ginge er dabei unbemerkt verloren.
  bereich.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    ev.preventDefault();
    ev.stopPropagation();
    schliesseAngebot();
    o.hinweis(ENTWURF_ZURUECKGESTELLT);
  });
  const istGespeichert = (id: string): boolean => pois.store.get(id)?.source === 'browser' || (o.persistenz?.lokalId?.(id) ?? null) !== null;
  const fortsetzen = (e: Entwurf): void => {
    schliesseAngebot();
    if (e.poiId !== null && istGespeichert(e.poiId)) {
      loesche();
      o.melde(SCHON_GESPEICHERT);
      return;
    }
    o.wendeAnsichtAn(e.ansicht);
    // Eine inzwischen anders vergebene ID (z. B. ein Paket-POI) wird nicht übernommen.
    const id = e.poiId !== null && !pois.store.get(e.poiId) ? e.poiId : null;
    fortsetzung = { ...e, poiId: id };
    if (!pois.openAt(e.punkt, e.station, { id: id ?? undefined, werte: e.formular, positionSource:e.positionSource })) fortsetzung = null;
  };
  const zeigeAngebot = async (e: Entwurf, bild: Blob | null): Promise<void> => {
    const titel = document.createElement('h2');
    titel.id = 'sv-entwurf-titel';
    titel.textContent = 'Unvollständiger Befund';
    const teile: HTMLElement[] = [titel];
    if (bild) {
      try {
        const bmp = await createImageBitmap(bild);
        const c = document.createElement('canvas');
        c.width = bmp.width;
        c.height = bmp.height;
        c.setAttribute('role', 'img');
        c.setAttribute('aria-label', entwurfText(e, { titelLeer: 'ohne Titel' }));
        c.getContext('2d')?.drawImage(bmp, 0, 0);
        bmp.close();
        teile.push(c);
      } catch {
        // Bild nicht lesbar: Angebot ohne Vorschau.
      }
    }
    const zeile = document.createElement('p');
    const zeit = new Date(e.zeit).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' });
    zeile.textContent = `${zeit} – ${e.formular.title.trim() || 'ohne Titel'}`;
    const weiter = document.createElement('button');
    weiter.type = 'button';
    weiter.textContent = 'Fortsetzen';
    weiter.addEventListener('click', () => fortsetzen(e));
    const weg = document.createElement('button');
    weg.type = 'button';
    weg.textContent = 'Verwerfen';
    weg.addEventListener('click', () => {
      schliesseAngebot();
      loesche();
      o.melde(ENTWURF_VERWORFEN);
    });
    const knoepfe = document.createElement('div');
    knoepfe.append(weiter, weg);
    // Inzwischen ein Formular geöffnet: das neue ersetzt den Entwurf, kein Angebot mehr.
    if (pois.formularOffen()) return;
    bereich.replaceChildren(...teile, zeile, knoepfe);
    bereich.hidden = false;
    if (!o.nachBefundlink) o.hinweis(ENTWURF_ANGEBOT);
    else {
      const angebot = knoepfe;
      // Inzwischen geschlossen (Fortsetzen, Verwerfen, Esc, neues Formular): nichts mehr ansagen.
      setTimeout(() => {
        if (!bereich.hidden && bereich.contains(angebot)) o.hinweis(ENTWURF_ANGEBOT);
      }, ANSAGE_NACH_BEFUNDLINK_MS);
    }
  };

  return {
    biete: async () => {
      // Vor dem ersten Warten merken: ein währenddessen geöffnetes und verworfenes Formular zählt mit.
      const g = gen;
      if (schluessel === null) return;
      const s = await speicherP;
      if (!s) return;
      const r = await s.lies(schluessel).catch(() => null);
      // Inzwischen ein Formular geöffnet oder etwas gelöscht: nichts Altes mehr anbieten (und nichts löschen —
      // der Datensatz gehört dann schon dem neuen Formular).
      if (!r || g !== gen || pois.formularOffen()) return;
      // Unbrauchbar oder zu alt: löschen statt anbieten.
      if (r === 'ungueltig' || entwurfAbgelaufen(r.entwurf, Date.now())) {
        loesche();
        return;
      }
      await zeigeAngebot(r.entwurf, r.bild);
    },
  };
}
