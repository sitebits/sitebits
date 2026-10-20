// Dialoge der Bedienung (DOM): Ansage-Region, Bedienung (Maus, Finger, Tasten), Einstellungen (Tasten umbelegen, Bewegung).
// Natives <dialog> mit showModal(): Esc schließt, der Fokus kehrt dorthin zurück, wo er vorher war.
// Texte nur per textContent/Attribut (strenge CSP).
import { AKTIONEN, akkordVon, belege, belegePad, beschriftung, normalisiere, padBelegung, vorgabeBelegung } from './tastenbelegung';
import { PAD_AKTIONEN, PAD_KNOEPFE, PAD_NICHT_BELEGT, PAD_TITEL, TOTZONE } from './gamepad';
import type { PadAktion } from './gamepad';
import type { Akkord, AktionDef, AktionId, Belegung } from './tastenbelegung';
import { PANORAMA_ZIEL_AKTIONEN } from './keymap';
import { GRENZEN, ladeEinstellungen, speichereEinstellungen, vorgabeEinstellungen } from './bedienEinstellungen';
import type { BedienEinstellungen } from './bedienEinstellungen';
import { ansageFaellig, ansageMelden, leereSchlange } from './ansage';
import { mausUndFinger } from './bedienhilfe';
import type { HilfeKontext } from './bedienhilfe';
import type { AnsageSchlange } from './ansage';

export interface Ansage {
  /** Text ansagen. Mit `vorrang` steht er eine Weile; Meldungen in dieser Zeit folgen danach (ansage.ts). */
  melde(text: string, o?: { vorrang?: boolean }): void;
}

/** Live-Region: gleicher Text zweimal hintereinander wird durch Leeren + nächstes Bild erneut angesagt. */
export function createAnsage(el: HTMLElement): Ansage {
  const zeige = (text: string): void => {
    if (el.textContent !== text) {
      el.textContent = text;
      return;
    }
    el.textContent = '';
    requestAnimationFrame(() => {
      el.textContent = text;
    });
  };
  let schlange: AnsageSchlange = leereSchlange;
  let takt: ReturnType<typeof setTimeout> | null = null;
  const plane = (): void => {
    if (takt !== null || schlange.warten.length === 0) return;
    takt = setTimeout(() => {
      takt = null;
      const r = ansageFaellig(schlange, performance.now());
      schlange = r.schlange;
      if (r.jetzt !== null) zeige(r.jetzt);
      plane();
    }, Math.max(0, schlange.bis - performance.now()));
  };
  return {
    melde(text, o) {
      const r = ansageMelden(schlange, { t: performance.now(), text, vorrang: o?.vorrang });
      schlange = r.schlange;
      if (r.jetzt !== null) zeige(r.jetzt);
      // Ein neuer Vorrang verschiebt das Ende des Haltens: neu planen.
      if (takt !== null && o?.vorrang) {
        clearTimeout(takt);
        takt = null;
      }
      plane();
    },
  };
}

function el<K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
}

const knopf = (text: string, label?: string): HTMLButtonElement => {
  const b = el('button', text);
  b.type = 'button';
  if (label) b.setAttribute('aria-label', label);
  return b;
};

interface Huelle { dialog: HTMLDialogElement; oeffne(vorher?: () => void): void }

/** Dialog mit Überschrift und Fokusrückgabe; der Inhalt wird vom Aufrufer eingehängt. */
function baueDialog(titel: string, inhalt: (d: HTMLDialogElement) => void): Huelle {
  const dialog = el('dialog', undefined, 'sv-dialog');
  const h = el('h2', titel);
  h.id = `sv-dialog-${titel.replace(/\W+/g, '-').toLowerCase()}`;
  dialog.setAttribute('aria-labelledby', h.id);
  dialog.append(h);
  inhalt(dialog);
  document.body.append(dialog);
  let zurueck: HTMLElement | null = null;
  dialog.addEventListener('close', () => {
    zurueck?.focus();
    zurueck = null;
  });
  return {
    dialog,
    oeffne(vorher) {
      if (dialog.open) return;
      zurueck = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      vorher?.();
      dialog.showModal();
    },
  };
}

const BEREICH = { global: 'überall', begehen: 'im Begehen, Fokus auf der 3D-Ansicht' } as const;
/** Tasten der Ebene „begehen“, die auch im Panorama-Fadenkreuz gelten (Panorama mit gewähltem Werkzeug). */
const BEREICH_MIT_PANORAMA = 'im Begehen und im Panorama mit Werkzeug, Fokus auf der 3D-Ansicht';
const FESTE_ZEILEN: readonly (readonly [string, string])[] = [
  ['Esc', 'beendet jeden Zustand'],
  ['Tab', 'verlässt die 3D-Ansicht'],
  ['Enter', 'Zeichnung abschließen'],
  ['Strg+Z', 'Rückgängig'],
];

/** Tabelle mit Kopfzeile; `beschriftung` wird zur caption (Name der Tabelle für Hilfstechnik). */
function tabelle(spalten: readonly string[], tbody: HTMLTableSectionElement, beschriftung?: string): HTMLTableElement {
  const table = el('table');
  if (beschriftung !== undefined) table.append(el('caption', beschriftung));
  const kopf = el('tr');
  for (const t of spalten) {
    const th = el('th', t);
    th.scope = 'col';
    kopf.append(th);
  }
  const thead = el('thead');
  thead.append(kopf);
  table.append(thead, tbody);
  return table;
}

/**
 * Dialog „Bedienung“: oben Maus und Finger, die gerade aktive Ansicht zuerst (bedienhilfe.ts), darunter die Tasten
 * mit der aktuellen Belegung. Der Inhalt entsteht bei jedem Öffnen neu (Ansicht, Einstellung „Linke Maustaste“).
 */
export function createBedienung(o: { belegung: () => Belegung; kuerzel: (a: AktionId) => string; kontext: () => HilfeKontext }): { oeffne(): void } {
  const tbody = el('tbody');
  const gesten = el('div', undefined, 'sv-gesten');
  const huelle = baueDialog('Bedienung', (d) => {
    const zu = knopf('Schließen');
    zu.addEventListener('click', () => d.close());
    const fuss = el('div', undefined, 'sv-aktionen');
    fuss.append(zu);
    d.append(el('h3', 'Maus und Finger'), gesten, el('h3', 'Tasten'), tabelle(['Aktion', 'Taste', 'Bereich'], tbody), fuss);
  });
  const fuelleGesten = (): void => {
    gesten.replaceChildren(...mausUndFinger(o.kontext()).map((a) => {
      const body = el('tbody');
      for (const zeile of a.zeilen) {
        const tr = el('tr');
        const th = el('th', zeile.geste);
        th.scope = 'row';
        tr.append(th, el('td', zeile.wirkung));
        body.append(tr);
      }
      const t = tabelle(['Geste', 'Wirkung'], body, a.aktiv ? `${a.titel} – aktuelle Ansicht` : a.titel);
      if (a.aktiv) t.classList.add('sv-aktiv');
      return t;
    }));
  };
  const fuelle = (): void => {
    fuelleGesten();
    const b = o.belegung();
    const zeilen: HTMLTableRowElement[] = [];
    const zeile = (aktion: string, taste: string, bereich: string): void => {
      const tr = el('tr');
      const th = el('th', aktion);
      th.scope = 'row';
      tr.append(th, el('td', taste), el('td', bereich));
      zeilen.push(tr);
    };
    for (const a of AKTIONEN) {
      const k = o.kuerzel(a.id);
      const bereich = PANORAMA_ZIEL_AKTIONEN.has(a.id) ? BEREICH_MIT_PANORAMA : BEREICH[a.ebene];
      zeile(a.titel, k || (akkordVon(b, a.id) ? 'abgeschaltet' : 'nicht belegt'), bereich);
    }
    for (const [taste, text] of FESTE_ZEILEN) zeile(text, taste, 'überall');
    tbody.replaceChildren(...zeilen);
  };
  return { oeffne: () => huelle.oeffne(fuelle) };
}

type ZahlFeld = 'drehSchrittGrad' | 'empfindlichkeit' | 'sichtfeldGrad' | 'tempo' | 'fadenkreuzPx';
type WahlFeld = 'drehenInSchritten' | 'bewegungReduzieren' | 'zielen' | 'schnell' | 'orbitLinks';
type JaNeinFeld = 'invertY' | 'vignette' | 'hoeheHalten';

const ZAHL_FELDER: readonly { key: ZahlFeld; label: string; step: number }[] = [
  { key: 'drehSchrittGrad', label: 'Drehschritt (Grad)', step: 1 },
  { key: 'empfindlichkeit', label: 'Empfindlichkeit beim Umschauen', step: 0.1 },
  { key: 'sichtfeldGrad', label: 'Sichtfeld (Grad)', step: 1 },
  { key: 'tempo', label: 'Tempo (m/s)', step: 0.1 },
  { key: 'fadenkreuzPx', label: 'Fadenkreuz-Größe (Pixel)', step: 1 },
];
const WAHL_FELDER: readonly { key: WahlFeld; label: string; optionen: readonly (readonly [string, string])[] }[] = [
  { key: 'drehenInSchritten', label: 'Drehen in Schritten', optionen: [['auto', 'automatisch (bei reduzierter Bewegung)'], ['an', 'immer'], ['aus', 'nie']] },
  { key: 'bewegungReduzieren', label: 'Bewegung reduzieren', optionen: [['system', 'wie das System'], ['an', 'ja'], ['aus', 'nein']] },
  { key: 'zielen', label: 'Zielen', optionen: [['halten', 'Taste halten'], ['umschalten', 'Taste umschalten']] },
  { key: 'schnell', label: 'Schnell bewegen', optionen: [['halten', 'Taste halten'], ['umschalten', 'Taste umschalten']] },
  {
    key: 'orbitLinks',
    label: 'Linke Maustaste und ein Finger im Orbit',
    optionen: [
      ['drehen', 'dreht um den Drehpunkt (rechts bzw. zwei Finger verschieben)'],
      ['verschieben', 'verschiebt (rechts bzw. zwei Finger drehen)'],
    ],
  },
];
const JA_NEIN_FELDER: readonly { key: JaNeinFeld; label: string }[] = [
  { key: 'invertY', label: 'Blick nach oben/unten umkehren' },
  { key: 'vignette', label: 'Rand abdunkeln bei Bewegung' },
  { key: 'hoeheHalten', label: 'Höhe beim Gehen halten' },
];

const MODIFIKATOREN = new Set(['Control', 'Alt', 'Meta', 'AltGraph']);

export function createEinstellungen(o: {
  belegung: () => Belegung;
  setzeBelegung(b: Belegung): void;
  einstellungen: () => BedienEinstellungen;
  setzeEinstellungen(e: BedienEinstellungen): void;
  /** Zähler der Tastenhinweise dieses Kontos leeren: alle Hinweise erscheinen wieder. */
  hinweiseZuruecksetzen(): void;
  /** Leistungsanzeige (Debug-Leiste: FPS, Punkte, Anfragen): Stand und Schalter, gespeichert wie `?debug` (debug.ts). */
  leistung?: { an(): boolean; setze(an: boolean): void };
}): { oeffne(): void } {
  // Dokumentierte Ausnahme zur einen Live-Region (#ansage): showModal() macht die übrige Seite inert, also auch
  // #ansage. Deshalb melden die Region „Gespeichert“ (.sv-status) und die aria-live-Hinweise je Zeile (Fehler,
  // „Taste drücken …“) innerhalb des Dialogs. Aus dem Dialog heraus wird nichts zusätzlich in #ansage angesagt.
  const status = el('div', '', 'sv-status');
  status.setAttribute('role', 'status');
  const gespeichert = (): void => {
    status.textContent = '';
    requestAnimationFrame(() => {
      status.textContent = 'Gespeichert';
    });
  };

  // --- Tasten ---
  const einzel = el('input');
  einzel.type = 'checkbox';
  const einzelLabel = el('label');
  einzelLabel.append(einzel, 'Einzeltasten-Kürzel abschalten');
  einzel.addEventListener('change', () => {
    o.setzeBelegung({ ...o.belegung(), einzeltastenAus: einzel.checked });
    gespeichert();
  });

  let erfasse: { id: AktionId; hinweis: HTMLElement; knopf: HTMLButtonElement } | null = null;
  let escAbbruch = false; // Esc hat gerade nur die Aufnahme beendet: das native Schließen unterdrücken
  let schluckeKeyup: string | null = null; // keyup der Taste, die die Aufnahme beendet hat (z. B. Leertaste), darf nichts auslösen
  const beendeAufnahme = (): void => {
    if (erfasse) erfasse.hinweis.textContent = '';
    erfasse = null;
  };
  const zeilen = new Map<AktionId, { taste: HTMLElement; hinweis: HTMLElement; aendern: HTMLButtonElement }>();
  const titelVon = (id: AktionId): string => AKTIONEN.find((a) => a.id === id)?.titel ?? id;
  const zeigeTasten = (): void => {
    const b = o.belegung();
    for (const [id, z] of zeilen) z.taste.textContent = beschriftung(akkordVon(b, id)) || 'nicht belegt';
    einzel.checked = b.einzeltastenAus;
    zeigePad();
  };
  const setzeTaste = (id: AktionId, akkord: Akkord | null): void => {
    const z = zeilen.get(id)!;
    const r = belege(o.belegung(), id, akkord);
    if (!r.ok) {
      z.hinweis.textContent = r.grund === 'konflikt' ? `Schon belegt mit: ${titelVon(r.mit!)}` : r.grund === 'fest' ? 'Diese Taste ist fest vergeben' : 'Diese Taste ist nicht möglich';
      return;
    }
    z.hinweis.textContent = '';
    o.setzeBelegung(r.belegung);
    zeigeTasten();
    gespeichert();
  };
  const zeileFuer = (a: AktionDef): HTMLTableRowElement => {
    const tr = el('tr');
    const th = el('th', a.titel);
    th.scope = 'row';
    const taste = el('span');
    const hinweis = el('span', undefined, 'sv-hinweis');
    hinweis.setAttribute('aria-live', 'polite');
    const tdTaste = el('td');
    tdTaste.append(taste, hinweis);
    const aendern = knopf('Ändern', `Ändern: ${a.titel}`);
    const weg = knopf('Entfernen', `Entfernen: ${a.titel}`);
    aendern.addEventListener('click', () => {
      beendeAufnahme();
      erfasse = { id: a.id, hinweis, knopf: aendern };
      hinweis.textContent = 'Taste drücken … (Esc bricht ab)';
    });
    weg.addEventListener('click', () => setzeTaste(a.id, null));
    const tdKnoepfe = el('td');
    tdKnoepfe.append(aendern, ' ', weg);
    tr.append(th, tdTaste, tdKnoepfe);
    zeilen.set(a.id, { taste, hinweis, aendern });
    return tr;
  };

  // --- Controller ---
  const padAuswahl = new Map<PadAktion, { s: HTMLSelectElement; hinweis: HTMLElement }>();
  const zeigePad = (): void => {
    const k = padBelegung(o.belegung());
    for (const [a, z] of padAuswahl) z.s.value = String(k[a]);
  };
  const padZeile = (a: PadAktion): HTMLTableRowElement => {
    const tr = el('tr');
    const th = el('th', PAD_TITEL[a]);
    th.scope = 'row';
    const s = el('select');
    s.setAttribute('aria-label', `Controller-Knopf: ${PAD_TITEL[a]}`);
    const keiner = el('option', 'nicht belegt');
    keiner.value = String(PAD_NICHT_BELEGT);
    s.append(keiner);
    PAD_KNOEPFE.forEach((name, i) => {
      const opt = el('option', `${i}: ${name}`);
      opt.value = String(i);
      s.append(opt);
    });
    const hinweis = el('span', undefined, 'sv-hinweis');
    hinweis.setAttribute('aria-live', 'polite');
    s.addEventListener('change', () => {
      const r = belegePad(o.belegung(), a, Number(s.value));
      if (!r.ok) {
        hinweis.textContent = r.grund === 'konflikt' ? `Schon belegt mit: ${PAD_TITEL[r.mit!]}` : 'Dieser Knopf ist nicht möglich';
        zeigePad();
        return;
      }
      hinweis.textContent = '';
      o.setzeBelegung(r.belegung);
      zeigePad();
      gespeichert();
    });
    const td = el('td');
    td.append(s, hinweis);
    tr.append(th, td);
    padAuswahl.set(a, { s, hinweis });
    return tr;
  };

  // --- Bewegung ---
  const felder = el('div', undefined, 'sv-felder');
  const aktualisiereFelder: (() => void)[] = [];
  const aendere = (teil: Partial<BedienEinstellungen>): void => {
    // Über Speichern/Laden schicken: dieselbe Klemmung wie beim Start, auch bei Tipp- oder Fremdwerten.
    o.setzeEinstellungen(ladeEinstellungen(speichereEinstellungen({ ...o.einstellungen(), ...teil })));
    for (const f of aktualisiereFelder) f();
    gespeichert();
  };
  let nr = 0;
  const feld = (label: string, steuerung: HTMLElement): void => {
    const id = `sv-bed-${nr++}`;
    steuerung.id = id;
    const l = el('label', label);
    l.htmlFor = id;
    felder.append(l, steuerung);
  };
  for (const f of WAHL_FELDER) {
    const s = el('select');
    for (const [wert, text] of f.optionen) {
      const opt = el('option', text);
      opt.value = wert;
      s.append(opt);
    }
    s.addEventListener('change', () => aendere({ [f.key]: s.value }));
    aktualisiereFelder.push(() => {
      s.value = o.einstellungen()[f.key];
    });
    feld(f.label, s);
  }
  for (const f of ZAHL_FELDER) {
    const i = el('input');
    i.type = 'number';
    i.min = String(GRENZEN[f.key].min);
    i.max = String(GRENZEN[f.key].max);
    i.step = String(f.step);
    i.addEventListener('change', () => {
      const n = i.valueAsNumber;
      if (Number.isFinite(n)) aendere({ [f.key]: n });
      else for (const u of aktualisiereFelder) u();
    });
    aktualisiereFelder.push(() => {
      i.value = String(o.einstellungen()[f.key]);
    });
    feld(f.label, i);
  }
  for (const f of JA_NEIN_FELDER) {
    const c = el('input');
    c.type = 'checkbox';
    c.addEventListener('change', () => aendere({ [f.key]: c.checked }));
    aktualisiereFelder.push(() => {
      c.checked = o.einstellungen()[f.key];
    });
    const l = el('label', undefined, 'sv-ganz');
    l.append(c, f.label);
    felder.append(l);
  }

  const huelle = baueDialog('Tastatur, Controller und Bewegung', (d) => {
    const tasten = el('section');
    tasten.append(el('h3', 'Tasten'), einzelLabel);
    const table = el('table');
    const kopf = el('tr');
    for (const t of ['Aktion', 'Taste', 'Ändern']) {
      const th = el('th', t);
      th.scope = 'col';
      kopf.append(th);
    }
    const thead = el('thead');
    thead.append(kopf);
    const tbody = el('tbody');
    tbody.append(...AKTIONEN.map(zeileFuer));
    table.append(thead, tbody);
    const alle = knopf('Alle Tasten auf Vorgabe');
    alle.addEventListener('click', () => {
      o.setzeBelegung({ ...vorgabeBelegung(), einzeltastenAus: o.belegung().einzeltastenAus });
      for (const z of zeilen.values()) z.hinweis.textContent = '';
      for (const z of padAuswahl.values()) z.hinweis.textContent = '';
      erfasse = null;
      zeigeTasten();
      gespeichert();
    });
    const standard = knopf('Bewegung auf Vorgabe');
    // Die Hinweis-Einstellung gehört nicht zur Bewegung und bleibt.
    standard.addEventListener('click', () => aendere({ ...vorgabeEinstellungen(), hinweiseImmer: o.einstellungen().hinweiseImmer }));
    const padTabelle = el('table');
    const padKopf = el('tr');
    for (const t of ['Aktion', 'Knopf']) {
      const th = el('th', t);
      th.scope = 'col';
      padKopf.append(th);
    }
    const padHead = el('thead');
    padHead.append(padKopf);
    const padBody = el('tbody');
    padBody.append(...PAD_AKTIONEN.map(padZeile));
    padTabelle.append(padHead, padBody);
    const padVorgabe = knopf('Controller auf Vorgabe');
    padVorgabe.addEventListener('click', () => {
      const ohnePad = { ...o.belegung() };
      delete ohnePad.pad;
      o.setzeBelegung(ohnePad);
      for (const z of padAuswahl.values()) z.hinweis.textContent = '';
      zeigePad();
      gespeichert();
    });
    const controller = el('section');
    controller.append(
      el('h3', 'Controller'),
      el('p', `Wirkt nur im Begehen und nur bei Controllern mit Standardbelegung. Linker Stick bewegt, rechter Stick schaut um (Totzone ${Math.round(TOTZONE * 100)} %); „Blick nach oben/unten umkehren“ gilt auch hier.`),
      padTabelle,
      padVorgabe,
    );
    const bewegung = el('section');
    bewegung.append(el('h3', 'Bewegung'), felder, standard);
    const immer = el('input');
    immer.type = 'checkbox';
    immer.addEventListener('change', () => aendere({ hinweiseImmer: immer.checked }));
    aktualisiereFelder.push(() => {
      immer.checked = o.einstellungen().hinweiseImmer;
    });
    const immerLabel = el('label');
    immerLabel.append(immer, 'Hinweise immer zeigen');
    const zuruecksetzen = knopf('Hinweise zurücksetzen');
    zuruecksetzen.addEventListener('click', () => {
      o.hinweiseZuruecksetzen();
      status.textContent = '';
      requestAnimationFrame(() => {
        status.textContent = 'Hinweise zurückgesetzt';
      });
    });
    const hinweise = el('section');
    hinweise.append(
      el('h3', 'Hinweise'),
      el('p', 'Kurze Tastenhinweise unten in der 3D-Ansicht. Ein Hinweis verschwindet, nachdem die Aktion dreimal ausgeführt wurde.'),
      immerLabel,
      el('div', undefined, 'sv-aktionen'),
    );
    hinweise.lastElementChild!.append(zuruecksetzen);
    const anzeige = el('section');
    if (o.leistung) {
      const l = o.leistung;
      const c = el('input');
      c.type = 'checkbox';
      c.addEventListener('change', () => {
        l.setze(c.checked);
        gespeichert();
      });
      aktualisiereFelder.push(() => {
        c.checked = l.an();
      });
      const lab = el('label');
      lab.append(c, 'Leistungsanzeige (FPS, Punkte)');
      anzeige.append(el('h3', 'Anzeige'), el('p', 'Kleine Leiste unten rechts mit Bildrate, angezeigten Punkten und Datenanfragen. Gilt für diesen Browser; ?debug=1 bzw. ?debug=0 in der Adresse schaltet sie für einen Aufruf.'), lab);
    }
    const zu = knopf('Schließen');
    zu.addEventListener('click', () => d.close());
    const fuss = el('div', undefined, 'sv-aktionen');
    fuss.append(zu, status);
    tasten.append(table, el('div', undefined, 'sv-aktionen'));
    tasten.lastElementChild!.append(alle);
    d.append(tasten, controller, bewegung, hinweise);
    if (o.leistung) d.append(anzeige);
    d.append(fuss);

    // Während der Aufnahme gehört dem Dialog der nächste Tastendruck: nichts anderes darf ihn sehen.
    d.addEventListener('keydown', (ev) => {
      schluckeKeyup = null;
      if (!erfasse) return;
      // Tab wird nie umgedeutet: die Aufnahme endet, der Fokus wandert normal weiter.
      if (ev.key === 'Tab') {
        beendeAufnahme();
        return;
      }
      ev.preventDefault();
      ev.stopPropagation();
      if (ev.repeat || MODIFIKATOREN.has(ev.key)) return; // Strg/Alt allein: auf den Rest des Akkords warten
      const { id } = erfasse;
      if (ev.key === 'Escape') {
        escAbbruch = true;
        beendeAufnahme();
        return;
      }
      beendeAufnahme();
      schluckeKeyup = ev.key;
      setzeTaste(id, normalisiere({ key: ev.key, ctrlKey: ev.ctrlKey, metaKey: ev.metaKey, altKey: ev.altKey, altGraph: ev.getModifierState('AltGraph') }));
    }, true);
    d.addEventListener('keyup', (ev) => {
      if (ev.key === 'Escape') escAbbruch = false;
      if (!erfasse && schluckeKeyup !== ev.key) return;
      // Sonst aktivierte das Loslassen der Leertaste den Knopf erneut und startete die Aufnahme von vorn.
      ev.preventDefault();
      ev.stopPropagation();
      if (schluckeKeyup === ev.key) schluckeKeyup = null;
    }, true);
    // Esc während der Aufnahme bricht nur diese ab; der Dialog bleibt (natives cancel unterdrücken).
    d.addEventListener('cancel', (ev) => {
      if (escAbbruch) ev.preventDefault();
      escAbbruch = false;
    });
    // Fokuswechsel (Tab, Programm) oder Mausklick im Dialog beenden die Aufnahme, ohne etwas zu belegen.
    d.addEventListener('focusout', (ev) => {
      if (erfasse && ev.target === erfasse.knopf) beendeAufnahme();
    });
    d.addEventListener('pointerdown', () => beendeAufnahme());
  });
  return {
    oeffne: () => huelle.oeffne(() => {
      erfasse = null;
      escAbbruch = false;
      schluckeKeyup = null;
      for (const z of zeilen.values()) z.hinweis.textContent = '';
      for (const z of padAuswahl.values()) z.hinweis.textContent = '';
      status.textContent = '';
      zeigeTasten();
      for (const f of aktualisiereFelder) f();
    }),
  };
}
