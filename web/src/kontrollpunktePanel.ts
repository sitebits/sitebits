// Panel „Kontrollpunkte“ (J3b): Kontrollpunktliste laden (lokale Datei, kein Upload), Punkt wählen → Kamera fliegt
// zur Sollposition, Marke am Modell picken, Liste mit Ist-Werten für den Katalogprüfer exportieren. Lesen und
// Schreiben in kontrollpunkte.ts (Vitest); hier nur Bedienung und Pick.
//
// Bewusst KEIN Sollmarker und KEINE Abweichungsanzeige: Die Lage soll unabhängig gemessen werden. Wer die Sollposition
// sieht, pickt sie (oder korrigiert unbewusst zu ihr hin) statt der physischen Marke im Modell; ausgewertet wird
// ausschließlich im Prüfer (RMSE_H, Maßstab). Die Kamera fliegt nur in die Nähe, damit die Marke im Bild ist.
//
// Picken wie Messen und POIs: dieselbe Strahlsuche (Wolke/Mesh, CompositePickSource), dieselbe Sichtbarkeitsregel mit
// Rückfrage vorn/hinten bzw. „grobe Stufe – trotzdem setzen?“ (zielpunktSetzen), im Panorama die Panorama-Optionen.
// Ist = Weltkoordinate des Treffers, wie im Messexport. Splat-Ebenen sind nie messbar und daher nie Quelle.
// Der Pickmodus gilt für genau einen Klick; beim Scharfschalten geht die Werkzeugleiste auf „Navigieren“, damit kein
// Messwerkzeug denselben Klick nimmt, und ein Werkzeugwechsel schaltet ihn wieder ab (main.ts).
// Entwurf (kontrollpunktEntwurf.ts): Picks werden nach jeder Änderung lokal gesichert, je Projekt und SHA-256 der
// Soll-Liste. Lädt man dieselbe Liste wieder, wird der Entwurf angeboten (Übernehmen/Verwerfen), nie still übernommen;
// solange das Angebot offen ist, wird nichts gesichert (sonst überschriebe ein neuer Pick den Entwurf). Ein Export
// lässt den Entwurf stehen, verworfen wird er nur von Hand. Eine wieder geladene Exportdatei bringt ihre Ist-Werte
// dagegen nicht mit (der Kommentar „am Modell gepickt“ ist kein Beleg) — und hat einen anderen SHA als die Soll-Liste.
import { sourceFromPick, sourceLabel } from './pointSources';
import type { SourceLayer } from './pointSources';
import { dekodiere, exportName, KontrollpunktFehler, kontrollpunkteCsv, leseKontrollpunkte } from './kontrollpunkte';
import type { IstWert, KontrollpunktListe } from './kontrollpunkte';
import { zahlAnzeige } from './koordinaten';
import type { Frame, Vec3 } from './manifest';
import { panoPickOptions } from './rayPick';
import type { PickSource } from './rayPick';
import { keinPunktGrund, keinPunktText, zielpunktSetzen } from './zielpunkt';
import type { Rueckfrage, Wahl } from './zielpunkt';
import { entwurfSchluessel, listenSha } from './kontrollpunktEntwurf';
import type { KpEntwurfSpeicher } from './kontrollpunktEntwurf';

const CLICK_MOVE_TOLERANCE_PX = 4;
const ROLLE_TEXT = { check: 'Kontrollpunkt', control: 'Passpunkt' } as const;

export interface KontrollpunkteZustand {
  datei: string | null;
  crs: string | null;
  punkte: { id: string; rolle: 'check' | 'control'; soll: Vec3; ist: Vec3 | null; quelle: string | null }[];
  /** ID des Punkts, für den der nächste Klick pickt; null = kein Pickmodus. */
  picken: string | null;
  auswahl: string | null;
  /** Zahl der Picks im angebotenen Entwurf; null = kein Angebot offen. */
  angebot: number | null;
  /** Schlüssel des Entwurfs dieser Liste (nach dem Hashen); null ohne Liste oder ohne Speicher. */
  entwurf: string | null;
}

export interface KontrollpunktePanel {
  /** Pickmodus aktiv: der nächste Klick in die Ansicht gehört den Kontrollpunkten. */
  picktBereit(): boolean;
  /** Pickmodus ohne Pick beenden (Werkzeugwechsel). */
  abbrechen(): void;
  /** Liste aus Text laden (Dateiauswahl, E2E); false bei Ablehnung (Grund in der Statuszeile). */
  ladeText(text: string, name: string): boolean;
  waehle(id: string): void;
  picke(id: string): void;
  leere(id: string): void;
  /** Exporttext (wie der Download). null ohne Liste. */
  exportText(): string | null;
  exportiere(): void;
  zustand(): KontrollpunkteZustand;
}

export function attachKontrollpunktePanel(o: {
  root: HTMLElement;
  frame: Frame;
  projectId: string;
  sourceLayers: readonly SourceLayer[];
  version: string;
  leinwand: HTMLElement;
  picker: PickSource;
  inPanorama: () => boolean;
  snapEdges: () => boolean;
  /** Kamera zur Sollposition (Panorama/Begehen vorher verlassen). */
  fliegeZu: (p: Vec3) => void;
  /** Vor dem Scharfschalten: Werkzeugleiste auf Navigieren. */
  vorPicken: () => void;
  hiddenCloudMessage: string;
  notify: (t: string) => void;
  unterPointerLock?: () => boolean;
  frage?: (b: Rueckfrage, ort: { x: number; y: number }) => Promise<Wahl | null>;
  rueckfrageOffen?: () => boolean;
  pose?: () => readonly number[];
  /** Lokaler Entwurfsspeicher; fehlt er, gibt es keinen Entwurf. */
  entwurf?: KpEntwurfSpeicher;
}): KontrollpunktePanel {
  const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
    const e = document.createElement(tag);
    if (text !== undefined) e.textContent = text;
    return e;
  };
  let liste: KontrollpunktListe | null = null;
  let datei: string | null = null;
  const ist = new Map<string, IstWert>();
  let picken: string | null = null;
  let auswahl: string | null = null;
  let clickSeq = 0;
  let schluessel: string | null = null;
  let angebot: Map<string, IstWert> | null = null;
  let ladeNr = 0;
  let nichtGesichertGemeldet = false;

  const box = el('div'); box.className = 'sv-kp';
  const laden = el('button', 'Liste laden …'); laden.type = 'button';
  laden.title = 'Kontrollpunktliste (CSV, id;rolle;e;n;h …, mit „# crs = EPSG:…+…“) aus einer lokalen Datei – nichts wird hochgeladen';
  const datei1 = el('input'); datei1.type = 'file'; datei1.accept = '.csv,text/csv'; datei1.hidden = true;
  datei1.setAttribute('aria-label', 'Kontrollpunktliste wählen');
  const exportBtn = el('button', 'Exportieren (CSV)'); exportBtn.type = 'button';
  exportBtn.title = 'Liste mit den gepickten Ist-Werten für den Katalogprüfer speichern';
  const verwerfenBtn = el('button', 'Entwurf verwerfen'); verwerfenBtn.type = 'button';
  verwerfenBtn.title = 'Lokal gesicherte Picks dieser Liste löschen (die Werte dieser Sitzung bleiben bis zum Neuladen)';
  const leiste = el('div'); leiste.className = 'sv-kp-leiste'; leiste.append(laden, exportBtn, verwerfenBtn, datei1);
  const angebotBox = el('div'); angebotBox.className = 'sv-kp-angebot'; angebotBox.setAttribute('role', 'group');
  angebotBox.setAttribute('aria-label', 'Entwurf gefunden'); angebotBox.hidden = true;
  const angebotText = el('p');
  const uebernehmen = el('button', 'Übernehmen'); uebernehmen.type = 'button';
  const angebotVerwerfen = el('button', 'Verwerfen'); angebotVerwerfen.type = 'button';
  angebotVerwerfen.setAttribute('aria-label', 'Entwurf verwerfen');
  const angebotKnoepfe = el('div'); angebotKnoepfe.className = 'sv-kp-leiste'; angebotKnoepfe.append(uebernehmen, angebotVerwerfen);
  angebotBox.append(angebotText, angebotKnoepfe);
  const info = el('p'); info.className = 'sv-kp-info';
  const leer = el('p', 'Keine Liste geladen.'); leer.className = 'muted';
  const tabelle = el('table'); tabelle.className = 'sv-kp-tabelle';
  const kopf = el('thead'); const kopfZeile = el('tr');
  for (const t of ['Punkt', 'Ist (gepickt)', 'Aktion']) { const th = el('th', t); th.scope = 'col'; kopfZeile.append(th); }
  kopf.append(kopfZeile);
  const rumpf = el('tbody'); tabelle.append(kopf, rumpf);
  const hinweis = el('p', 'Kein Sollmarker, keine Abweichung: die Marke im Modell picken, nicht die Sollposition. '
    + 'Ausgewertet wird im Prüfer. Picks werden in diesem Browser als Entwurf gesichert und beim erneuten Laden '
    + 'derselben Soll-Liste angeboten; eine wieder geladene Exportdatei bringt ihre Ist-Werte nicht mit.');
  hinweis.className = 'sv-kp-hinweis muted';
  const status = el('p'); status.className = 'sv-kp-status'; status.setAttribute('role', 'status');
  // Schmales Panel: die Tabelle darf seitlich rollen statt Knöpfe abzuschneiden.
  const rahmen = el('div'); rahmen.className = 'sv-kp-rahmen'; rahmen.append(tabelle);
  box.append(leiste, angebotBox, info, leer, rahmen, status, hinweis);
  o.root.replaceChildren(box);

  const melde = (t: string): void => { status.textContent = t; };
  const ebenen = (): readonly SourceLayer[] => o.sourceLayers;

  /** Picks sichern (nicht, solange ein Angebot offen ist). Scheitert es, einmal je Liste melden. */
  const sichern = (): void => {
    if (!o.entwurf || !schluessel || angebot) return;
    if (!o.entwurf.schreibe(schluessel, ist) && !nichtGesichertGemeldet) {
      nichtGesichertGemeldet = true;
      o.notify('Entwurf nicht gesichert (Browser-Speicher gesperrt, voll oder Liste zu groß) – vor dem Neuladen exportieren');
    }
  };
  const schliesseAngebot = (): void => { angebot = null; angebotBox.hidden = true; };

  const zeichne = (): void => {
    exportBtn.disabled = liste === null;
    verwerfenBtn.disabled = !schluessel;
    leer.hidden = liste !== null;
    tabelle.hidden = liste === null;
    if (!liste) { info.textContent = ''; rumpf.replaceChildren(); return; }
    const n = liste.punkte.length; const k = liste.punkte.filter((p) => p.rolle === 'check').length;
    info.textContent = `${datei ?? 'Liste'} · ${liste.crs} · ${k} Kontrollpunkte, ${n - k} Passpunkte · ${ist.size} von ${n} gepickt`;
    rumpf.replaceChildren(...liste.punkte.map((p) => {
      const tr = el('tr');
      if (p.id === auswahl) tr.setAttribute('aria-current', 'true');
      const go = el('button', p.id); go.type = 'button'; go.className = 'sv-kp-go'; go.dataset.kp = p.id;
      go.setAttribute('aria-label', `Zu ${p.id} fliegen`);
      go.title = 'Kamera in die Nähe der Sollposition (kein Marker)';
      go.addEventListener('click', () => waehle(p.id));
      const w = ist.get(p.id);
      const istTd = el('td');
      if (w) {
        istTd.append(el('span', `E ${zahlAnzeige(w.punkt[0])}  N ${zahlAnzeige(w.punkt[1])}  H ${zahlAnzeige(w.punkt[2])}`));
        const q = el('div', `${w.quelle}${w.grob ? ' · grobe Stufe' : ''}`); q.className = 'muted'; istTd.append(q);
      } else istTd.textContent = '–';
      const pick = el('button', picken === p.id ? 'Abbrechen' : w ? 'Neu picken' : 'Picken'); pick.type = 'button';
      pick.setAttribute('aria-pressed', String(picken === p.id));
      pick.setAttribute('aria-label', `${p.id} am Modell picken`);
      pick.addEventListener('click', () => (picken === p.id ? abbrechen(true) : picke(p.id)));
      const loesch = el('button', 'Leeren'); loesch.type = 'button'; loesch.disabled = !w;
      loesch.setAttribute('aria-label', `Ist-Wert von ${p.id} leeren`);
      loesch.addEventListener('click', () => leere(p.id));
      const aktion = el('td'); aktion.className = 'sv-kp-aktion'; aktion.append(pick, loesch);
      const rolle = el('div', ROLLE_TEXT[p.rolle]); rolle.className = 'muted';
      const idTd = el('td'); idTd.append(go, rolle);
      tr.append(idTd, istTd, aktion);
      return tr;
    }));
  };

  const ladeText = (text: string, name: string): boolean => {
    try {
      const neu = leseKontrollpunkte(text, o.frame.crs);
      liste = neu; datei = name; ist.clear(); picken = null; auswahl = null; clickSeq++;
      schluessel = null; schliesseAngebot(); nichtGesichertGemeldet = false;
      const nr = ++ladeNr;
      zeichne();
      if (o.entwurf) void listenSha(text).then((sha) => {
        if (nr !== ladeNr || !sha || !o.entwurf) return;
        schluessel = entwurfSchluessel(o.projectId, sha);
        const e = o.entwurf.lies(schluessel, new Set(neu.punkte.map((p) => p.id)));
        if (e) {
          angebot = e;
          angebotText.textContent = `${e.size} gepickte Punkte aus dem Entwurf übernehmen?`;
          angebotBox.hidden = false;
          melde(`Entwurf zu ${name} gefunden: ${e.size} gepickte Punkte – übernehmen oder verwerfen`);
        } else sichern();
        zeichne();
      });
      melde(`${name}: ${neu.punkte.length} Punkte geladen${neu.istVerworfen
        ? ` – Ist-Werte aus der Liste (${neu.istVerworfen} Punkte) nicht übernommen, der Export enthält nur am Modell Gepicktes` : ''}`);
      return true;
    } catch (e) {
      melde(`${name} abgelehnt: ${e instanceof KontrollpunktFehler ? e.message : String(e)}`);
      return false;
    }
  };

  const punkt = (id: string) => liste?.punkte.find((p) => p.id === id) ?? null;
  const waehle = (id: string): void => {
    const p = punkt(id); if (!p) return;
    auswahl = id; zeichne();
    o.fliegeZu(p.soll);
    // Fokus zurück auf den neu gezeichneten Knopf (Tastaturbedienung).
    rumpf.querySelector<HTMLButtonElement>(`button.sv-kp-go[data-kp="${CSS.escape(id)}"]`)?.focus();
  };
  const picke = (id: string): void => {
    if (!punkt(id)) return;
    o.vorPicken();
    picken = id; auswahl = id; clickSeq++;
    o.picker.warm();
    zeichne();
    rumpf.querySelector<HTMLButtonElement>(`button[aria-pressed="true"]`)?.focus();
    melde(`${id}: auf die Marke im Modell klicken`);
  };
  const abbrechen = (sagen = false): void => {
    if (picken === null) return;
    const id = picken; picken = null; clickSeq++;
    zeichne();
    if (sagen) melde(`${id}: Picken abgebrochen`);
  };
  const leere = (id: string): void => {
    if (!ist.delete(id)) return;
    sichern();
    zeichne();
    melde(`${id}: Ist-Wert geleert`);
  };
  const exportText = (): string | null => (liste ? kontrollpunkteCsv(liste, ist, o.version) : null);
  const exportiere = (): void => {
    const text = exportText(); if (text === null) return;
    const url = URL.createObjectURL(new Blob([text], { type: 'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href = url; a.download = exportName(o.projectId); a.click();
    URL.revokeObjectURL(url);
    melde(`Exportiert: ${ist.size} von ${liste!.punkte.length} Punkten mit Ist-Wert`);
  };

  laden.addEventListener('click', () => datei1.click());
  datei1.addEventListener('change', () => {
    const f = datei1.files?.[0]; datei1.value = '';
    if (!f) return;
    void f.arrayBuffer().then((b) => {
      let text: string;
      try { text = dekodiere(b); } catch (e) { melde(`${f.name} abgelehnt: ${e instanceof Error ? e.message : String(e)}`); return; }
      ladeText(text, f.name);
    }, () => melde(`${f.name} nicht lesbar`));
  });
  exportBtn.addEventListener('click', exportiere);
  uebernehmen.addEventListener('click', () => {
    if (!angebot) return;
    // Was in dieser Sitzung schon gepickt ist, gilt vor dem Entwurf.
    let n = 0;
    for (const [id, w] of angebot) if (!ist.has(id)) { ist.set(id, w); n++; }
    schliesseAngebot(); sichern(); zeichne();
    melde(`${n} Punkte aus dem Entwurf übernommen`);
    exportBtn.focus();
  });
  const verwirf = (): void => {
    if (!o.entwurf || !schluessel) return;
    o.entwurf.loesche(schluessel);
    const offen = angebot !== null;
    schliesseAngebot();
    // Nach dem Verwerfen eines Angebots gilt der Stand dieser Sitzung als neuer Entwurf; „Entwurf verwerfen“ in der
    // Leiste löscht nur, bis der nächste Pick wieder sichert.
    if (offen) sichern();
    zeichne();
    melde(offen ? 'Entwurf verworfen' : 'Entwurf gelöscht – die Werte dieser Sitzung bleiben bis zum Neuladen, der nächste Pick sichert neu');
    laden.focus();
  };
  angebotVerwerfen.addEventListener('click', verwirf);
  verwerfenBtn.addEventListener('click', verwirf);

  // Pick per Klick (Kamera-Drag und Doppelklick zählen nicht), wie beim Anlegen eines POIs.
  let down: { x: number; y: number } | null = null;
  o.leinwand.addEventListener('pointerdown', (ev) => { down = { x: ev.clientX, y: ev.clientY }; });
  o.leinwand.addEventListener('click', (ev) => {
    const moved = down ? Math.hypot(ev.clientX - down.x, ev.clientY - down.y) : 0;
    down = null;
    if (picken === null || o.unterPointerLock?.()) return;
    if (moved > CLICK_MOVE_TOLERANCE_PX || ev.detail > 1) return;
    if (o.rueckfrageOffen?.()) { o.notify('Erst die Rückfrage beantworten'); return; }
    const id = picken; const seq = ++clickSeq;
    const panorama = o.inPanorama();
    const opts = { ...(panorama ? panoPickOptions() : {}), snapEdges: o.snapEdges() };
    const frage = o.frage; const pose0 = o.pose?.();
    const ergebnis = frage
      ? zielpunktSetzen({
          quelle: {
            pickAtScreen: (x, y, op) => (op?.tiefenFenster ? o.picker.pickAtScreen(x, y, op) : o.picker.pickAtEvent(ev, op)),
            vorschauAtScreen: (x, y) => o.picker.vorschauAtScreen(x, y),
          },
          x: ev.clientX, y: ev.clientY, opts, sichtbarkeitPruefen: !panorama,
          frage: (b) => frage(b, { x: ev.clientX, y: ev.clientY }),
          ansichtUnveraendert: () => {
            const jetzt = o.pose?.();
            return !pose0 || !jetzt || jetzt.every((w, i) => Math.abs(w - pose0[i]!) <= 1e-9);
          },
        })
      : o.picker.pickAtEvent(ev, opts).then((r) => (r.point && r.info.level !== 'none'
        ? { art: 'gesetzt' as const, punkt: r.point, info: r.info }
        : { art: 'kein-treffer' as const, info: r.info }));
    ergebnis.then((e) => {
      // Überholt (neuer Klick, abgebrochen, andere Liste) → verwerfen.
      if (seq !== clickSeq || picken !== id) return;
      if (e.art === 'abgebrochen') {
        melde('grund' in e && e.grund === 'ansicht' ? 'abgebrochen – Ansicht hat sich bewegt' : `${id}: abgebrochen – kein Ist-Wert gesetzt`);
        return;
      }
      if (e.art !== 'gesetzt') {
        const grund = keinPunktGrund(e.info, 'grund' in e ? e.grund : undefined);
        melde(`${id}: ${keinPunktText(grund, { folge: 'kein Ist-Wert gesetzt', ausgeblendet: o.hiddenCloudMessage, feinGrund: e.info.fineReason, mesh: e.info.source === 'mesh' })}`);
        return;
      }
      const grob = e.info.level === 'coarse';
      ist.set(id, { punkt: [e.punkt[0], e.punkt[1], e.punkt[2]], quelle: sourceLabel(sourceFromPick(e.info), ebenen()), ...(grob ? { grob } : {}) });
      picken = null; clickSeq++;
      sichern();
      zeichne();
      melde(`${id} gepickt${grob ? ' (grobe Stufe – kein Originalpunkt direkt am Cursor)' : ''}: E ${zahlAnzeige(e.punkt[0])} N ${zahlAnzeige(e.punkt[1])} H ${zahlAnzeige(e.punkt[2])}`);
    }).catch((err: unknown) => {
      console.error('Kontrollpunkt nicht auswertbar:', err);
      if (seq === clickSeq) melde(`${id}: Punkt konnte nicht ausgewertet werden`);
    });
  });

  zeichne();
  return {
    picktBereit: () => picken !== null,
    abbrechen: () => abbrechen(true),
    ladeText, waehle, picke, leere, exportText, exportiere,
    zustand: () => ({
      datei, crs: liste?.crs ?? null, picken, auswahl, angebot: angebot?.size ?? null, entwurf: schluessel,
      punkte: (liste?.punkte ?? []).map((p) => {
        const w = ist.get(p.id);
        return { id: p.id, rolle: p.rolle, soll: p.soll, ist: w ? w.punkt : null, quelle: w ? w.quelle : null };
      }),
    }),
  };
}
