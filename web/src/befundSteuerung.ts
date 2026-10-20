// Befund im Werkzeug: die Auswahl im POI-Panel bestimmt den Seitentitel, nach dem Speichern wird der neue
// Befund ausgewählt und der Fokus geht zurück auf die 3D-Ansicht; Verwerfen wird angesagt. Die Angaben für
// einen Befundlink (b = Server-ID bzw. lokale ID, rev = Datensatzstand) liefert imLink() — nur für den
// erzeugten Teilen-Link, nicht für die gedrosselt geschriebene Adresszeile (sonst öffnete Neuladen mit
// Auswahl die Empfängeransicht). teile() erzeugt den Link aus der aktuellen Ansicht, kopiert ihn (wenn die
// Zwischenablage es erlaubt) und zeigt ihn immer zusätzlich in einem Feld zum Kopieren; oeffneAusLink()
// wählt den Befund aus einem mitgebrachten Link aus. Die reinen Teile prüft Vitest (befundImLink hier,
// Link, Hinweis, Titel und Zuordnung in befundLink.ts).
import { befundAusLink, befundLink, revHinweis, seitentitel } from './befundLink';
import { encodeHash } from './hash';
import type { ViewState } from './hash';
import type { Project } from './manifest';
import type { PoiPersistenz } from './poiPersistenz';
import type { Poi } from './poiStore';
import type { PoiPanel } from './pois';

/** Ansage beim Verwerfen des Formulars (Esc, Abbrechen, Werkzeugwechsel). */
export const BEFUND_VERWORFEN = 'Befund verworfen';

export interface BefundSteuerung {
  /** Nur für den Teilen-Link: b = Server-ID (Plattform) bzw. lokale ID (Einzelmodus), rev = Revision des
   *  Datensatzes, auf dem der Befund gesetzt wurde; ohne Auswahl oder ohne Server-Bestätigung leer. */
  imLink(): Pick<ViewState, 'b' | 'rev'>;
  /** „<Titel> – <Projekt> – siteBits“ bzw. „<Projekt> – siteBits“. */
  seitentitel(): string;
  /** Befundlink des ausgewählten Befunds mit der aktuellen Ansicht: Feld zeigen, kopieren, ansagen. */
  teile(): void;
  /** Befund aus einem mitgebrachten Link auswählen. Wurde die Pose aus dem Link angewandt, bleibt sie; sonst
   *  (kaputte oder fehlende Pose) springt die Ansicht zum Befund. 'ohne' = der Link nennt keinen Befund. */
  oeffneAusLink(s: Pick<ViewState, 'b' | 'rev'>, o: { poseAngewandt: boolean }): 'gefunden' | 'fehlt' | 'ohne';
}

export const ERST_AUSWAEHLEN = 'Erst einen POI auswählen';
export const KEIN_LINK = 'Kein Befundlink: der POI ist nicht auf dem Server gespeichert';
export const NICHT_GEFUNDEN = 'Befund aus dem Link nicht gefunden – gelöscht oder nicht freigegeben.';
export const LINK_KOPIERT = 'Link kopiert';
export const LINK_MARKIERT = 'Link markiert – zum Kopieren Strg+C';

/** Text in die Zwischenablage; false, wenn es sie nicht gibt (kein sicherer Kontext) oder sie ablehnt. */
async function kopiere(text: string): Promise<boolean> {
  try {
    if (!navigator.clipboard?.writeText) return false;
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

/**
 * Plattform (Persistenz mit Server-Angaben): nur ein vom Server bekannter POI ergibt einen Befundlink —
 * seine Server-ID, und nur, wenn sie wieder auf genau diesen lokalen POI zeigt (ein POI aus pois.json gilt nie
 * als Server-Befund). Ein eigener POI, dessen Bestätigung nur über einen Konflikt mit gleichem Inhalt kam
 * (noch keine Server-Angaben), trägt seine lokale ID, die er dem Server als ID mitgab. Sonst — nur lokal,
 * unbestätigt, aus pois.json — gibt es keinen Befundlink. Einzelmodus: die lokale ID.
 */
export function befundImLink(o: {
  id: string | null;
  poi: Pick<Poi, 'source'> | undefined;
  persistenz: Pick<PoiPersistenz, 'meta' | 'lokalId' | 'bestaetigt'> | undefined;
  revision: number;
}): Pick<ViewState, 'b' | 'rev'> {
  const { id, poi, persistenz } = o;
  if (id === null || !poi) return {};
  if (!persistenz?.meta) return { b: id, rev: o.revision };
  const m = persistenz.meta(id);
  if (m) {
    if ((persistenz.lokalId?.(m.serverId) ?? null) !== id) return {};
    return { b: m.serverId, rev: m.paketRevision ?? o.revision };
  }
  if (poi.source === 'browser' && persistenz.bestaetigt?.(id) === true) return { b: id, rev: o.revision };
  return {};
}

export function attachBefundSteuerung(o: {
  pois: PoiPanel;
  persistenz: PoiPersistenz | undefined;
  leinwand: HTMLElement;
  manifest: Pick<Project, 'title' | 'revision' | 'originWorld'>;
  /** Bleibende Ansage (Live-Region und Meldezeile). */
  melde: (t: string) => void;
  /** Vorübergehende Meldung (Hinweise beim Öffnen eines Links). */
  hinweis: (t: string) => void;
  /** Nach jeder Änderung der Auswahl (z. B. Hash neu schreiben). */
  onAenderung: () => void;
  /** Aktuelle Ansicht (Pose, Messungen, Ebenen) ohne Befund — Grundlage des Teilen-Links. */
  ansicht: () => ViewState;
  /** Ort der Seite (Herkunft, Pfad, Suche) für den Link. */
  ort: Pick<Location, 'origin' | 'pathname' | 'search'>;
  /** Knopf „Teilen“ der Werkzeugleiste: Verfügbarkeit und Ziel der Fokusrückgabe. */
  teilenKnopf: { setTeilen(verfuegbar: boolean): void; fokussiere(): void };
  /** Begehen: unter Zeigersperre erst freigeben; nach dem Schließen geht der Fokus zurück auf die Leinwand. */
  begehen: () => { aktiv: boolean; zeigerFreigeben(): void } | null;
  /** Ein Link wurde erzeugt und angezeigt (Taste oder Knopf), z. B. für die Tastenhinweise. */
  geteilt?: () => void;
}): BefundSteuerung {
  const { pois, leinwand, manifest } = o;
  const titel = (): string => {
    const id = pois.ausgewaehlt();
    return seitentitel((id !== null ? pois.store.get(id)?.title : undefined) ?? null, manifest.title);
  };
  const imLink = (): Pick<ViewState, 'b' | 'rev'> => {
    const id = pois.ausgewaehlt();
    return befundImLink({ id, poi: id !== null ? pois.store.get(id) : undefined, persistenz: o.persistenz, revision: manifest.revision });
  };

  // Feld mit dem Link: immer gezeigt (auch wenn das Kopieren klappt), damit man ihn sieht und von Hand
  // kopieren kann. Eigene Tastenbehandlung (data-tasten-eigen): Esc schließt, Tab verlässt den Bereich normal.
  const bereich = document.createElement('div');
  bereich.className = 'sv-teilen';
  bereich.hidden = true;
  bereich.setAttribute('role', 'group');
  bereich.setAttribute('aria-labelledby', 'sv-teilen-titel');
  bereich.dataset.tastenEigen = '';
  const beschriftung = document.createElement('label');
  beschriftung.id = 'sv-teilen-titel';
  beschriftung.htmlFor = 'sv-teilen-link';
  beschriftung.textContent = 'Befundlink';
  const feld = document.createElement('input');
  feld.id = 'sv-teilen-link';
  feld.type = 'text';
  feld.readOnly = true;
  feld.spellcheck = false;
  const schliessenKnopf = document.createElement('button');
  schliessenKnopf.type = 'button';
  schliessenKnopf.textContent = 'Schließen';
  bereich.append(beschriftung, feld, schliessenKnopf);
  document.body.append(bereich);
  let imBegehenGeoeffnet = false;
  const schliesse = (): void => {
    if (bereich.hidden) return;
    const hatteFokus = bereich.contains(document.activeElement);
    bereich.hidden = true;
    if (!hatteFokus) return;
    if (imBegehenGeoeffnet && o.begehen()?.aktiv) leinwand.focus({ preventScroll: true });
    else o.teilenKnopf.fokussiere();
  };
  schliessenKnopf.addEventListener('click', schliesse);
  bereich.addEventListener('keydown', (ev) => {
    if (ev.key !== 'Escape') return;
    ev.preventDefault();
    ev.stopPropagation();
    schliesse();
  });

  const teile = (): void => {
    if (pois.ausgewaehlt() === null) {
      o.melde(ERST_AUSWAEHLEN);
      return;
    }
    const befund = imLink();
    if (befund.b === undefined) {
      o.melde(KEIN_LINK);
      return;
    }
    const walk = o.begehen();
    // Unter Zeigersperre zuerst freigeben, sonst erreicht der Fokus das Feld nicht.
    walk?.zeigerFreigeben();
    imBegehenGeoeffnet = walk?.aktiv ?? false;
    const link = befundLink({ ...o.ort, hash: encodeHash({ ...o.ansicht(), ...befund }, manifest.originWorld) });
    // Sofort im Auslöser anstoßen (Nutzeraktivierung), das Ergebnis kommt später.
    const kopiert = kopiere(link);
    feld.value = link;
    bereich.hidden = false;
    feld.focus();
    feld.select();
    o.geteilt?.();
    void kopiert.then((ok) => {
      if (feld.value === link) o.melde(ok ? LINK_KOPIERT : LINK_MARKIERT);
    });
  };

  document.title = titel();
  o.teilenKnopf.setTeilen(pois.ausgewaehlt() !== null);
  pois.onAuswahl((id) => {
    document.title = titel();
    o.teilenKnopf.setTeilen(id !== null);
    // Ein angezeigter Link gehört zur vorigen Auswahl.
    schliesse();
    o.onAenderung();
  });
  // Liste neu gezeichnet (Abgleich, eigene Änderung): ein umbenannter gewählter Befund ändert den Titel.
  pois.onNeuGezeichnet(() => {
    document.title = titel();
  });
  pois.onGespeichert((id) => {
    pois.waehle(id);
    o.melde('Befund gespeichert');
  });
  pois.onFormular((offen, grund) => {
    if (offen) return;
    // Nach dem Speichern zurück in die 3D-Ansicht (dort wirken die Begehen-Tasten wieder); Verwerfen führt den
    // Fokus selbst zurück (pois.ts) und wird hier nur angesagt.
    if (grund === 'gespeichert') leinwand.focus({ preventScroll: true });
    else o.melde(BEFUND_VERWORFEN);
  });
  return {
    imLink,
    seitentitel: titel,
    teile,
    oeffneAusLink: (s, { poseAngewandt }) => {
      if (s.b === undefined) return 'ohne';
      const lokal = befundAusLink(s.b, { poi: (id) => pois.store.get(id), persistenz: o.persistenz });
      if (lokal === null) {
        o.hinweis(NICHT_GEFUNDEN);
        return 'fehlt';
      }
      // Die Pose aus dem Link gilt; nur ohne sie springt die Ansicht zum Befund (Standpunkt bzw. Rahmen).
      if (poseAngewandt) pois.waehle(lokal);
      else pois.goTo(lokal);
      const h = revHinweis(s.rev, manifest.revision);
      if (h) o.hinweis(h);
      return 'gefunden';
    },
  };
}
