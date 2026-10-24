// Seite /uploads (nur Admins mit eingeschaltetem Import): Formular „Neues Projekt“ (eine Mesh- oder
// Splat-Datei hochladen, in Stücken, fortsetzbar) und die Listen der Importe und Uploads, die sich
// alle 5 s aktualisieren, solange etwas offen ist. Nur createElement/textContent (strenge CSP).
import { anfrage, PlattformFehler } from '../plattformApi';
import { viewerLink } from './karten';
import {
  ENDUNGEN, fortsetzenSchluessel, gemerkt, groesseText, HOEHENSYSTEME, importOffen, importOptionen, importText, merken,
  POLL_MS, pruefeEingabe, TITEL_MAX, TYP, uploadText, vergessen, zeitText,
  type Eingabe, type Fehler, type Import, type Quelle, type Upload,
} from './uploadModell';
import { abschliessen, ankuendigen, importAbbrechen, importStarten, senden, stand, uploadLesen, uploadLoeschen } from './uploadSenden';
import { abschnitt, el, fehlerText, knopf, tabelle, tabelleRegion, zeile } from './verwaltung';

type Melde = (text: string) => void;

function hinweis(id: string, text: string): HTMLParagraphElement {
  const p = el('p', 'sv-hinweis', text);
  p.id = id;
  return p;
}

function feldMit(text: string, id: string, eingabe: HTMLInputElement | HTMLSelectElement, ...hinweise: HTMLElement[]): HTMLDivElement {
  const box = el('div', 'sv-feld');
  const l = el('label', undefined, text);
  l.htmlFor = id;
  eingabe.id = id;
  const fehler = el('p', 'sv-fehler sv-feld-fehler');
  fehler.id = `${id}-fehler`;
  fehler.hidden = true;
  eingabe.setAttribute('aria-describedby', [...hinweise.map((h) => h.id), fehler.id].join(' '));
  box.append(l, eingabe, ...hinweise, fehler);
  return box;
}

function auswahl(werte: readonly { wert: string; text: string }[], leer: string): HTMLSelectElement {
  const s = el('select');
  const o = el('option', undefined, leer);
  o.value = '';
  s.append(o);
  for (const w of werte) {
    const x = el('option', undefined, w.text);
    x.value = w.wert;
    s.append(x);
  }
  return s;
}

interface Formular {
  form: HTMLFormElement;
  laeuft: () => boolean;
}

function neuesProjekt(neu: () => Promise<void>, melde: Melde): Formular {
  const form = el('form', 'sv-neues-projekt');
  form.noValidate = true;
  form.dataset.sv = 'neues-projekt';
  const projekt = el('input');
  projekt.autocomplete = 'off';
  projekt.spellcheck = false;
  projekt.maxLength = 64;
  projekt.required = true;
  const titel = el('input');
  titel.maxLength = TITEL_MAX;
  const quelle = el('fieldset', 'sv-quelle');
  quelle.append(el('legend', undefined, 'Quelle'));
  const radios = new Map<Quelle, HTMLInputElement>();
  for (const [wert, text] of [['mesh', 'Mesh (GLB oder PLY, ohne Textur)'], ['splats', 'Gaussian Splats (PLY, SPZ oder SPLAT)']] as const) {
    const r = el('input');
    r.type = 'radio';
    r.name = 'sv-np-quelle';
    r.value = wert;
    r.id = `sv-np-quelle-${wert}`;
    r.checked = wert === 'mesh';
    const l = el('label', 'sv-schalter');
    l.htmlFor = r.id;
    l.append(r, el('span', undefined, text));
    quelle.append(l);
    radios.set(wert, r);
  }
  const datei = el('input');
  datei.type = 'file';
  datei.dataset.sv = 'datei';
  const crs = el('input');
  crs.placeholder = 'EPSG:25833';
  crs.spellcheck = false;
  crs.autocomplete = 'off';
  const quellHoehe = auswahl(HOEHENSYSTEME, '– keine Umrechnung –');
  const zielHoehe = auswahl(HOEHENSYSTEME, '– wie Datei –');

  const fortsetzen = el('p', 'sv-hinweis sv-fortsetzen');
  fortsetzen.dataset.sv = 'fortsetzen-hinweis';
  fortsetzen.hidden = true;
  const start = el('button', 'sv-primaer-knopf', 'Hochladen und Projekt anlegen');
  start.type = 'submit';
  start.dataset.sv = 'start';
  const neuBeginnen = el('button', undefined, 'Neu beginnen');
  neuBeginnen.type = 'button';
  neuBeginnen.hidden = true;
  neuBeginnen.dataset.sv = 'neu-beginnen';
  const abbrechen = el('button', undefined, 'Abbrechen');
  abbrechen.type = 'button';
  abbrechen.hidden = true;
  abbrechen.dataset.sv = 'abbrechen';
  const balken = el('progress');
  balken.max = 1;
  balken.value = 0;
  balken.hidden = true;
  balken.dataset.sv = 'fortschritt';
  balken.setAttribute('aria-label', 'Fortschritt des Uploads');
  const status = el('p', 'sv-status');
  status.dataset.sv = 'status';
  status.setAttribute('role', 'status');

  const felder: Record<keyof Fehler, HTMLInputElement | HTMLSelectElement> = { projekt, titel, datei, crs, hoehe: quellHoehe };
  const boxen = {
    projekt: feldMit('Projekt-ID', 'sv-np-projekt', projekt, hinweis('sv-np-projekt-hinweis', 'Kleinbuchstaben, Ziffern und Bindestrich, z. B. fassade-nord. Erscheint in der Adresse des Projekts.')),
    titel: feldMit('Titel (optional)', 'sv-np-titel', titel),
    datei: feldMit('Datei', 'sv-np-datei', datei,
      hinweis('sv-np-datei-hinweis', 'Genau eine Datei. Archive (zip) werden nicht angenommen – Ordner entpacken und als Ordner hochladen. Texturierte Meshes, OBJ und Punktwolken kommen später.')),
    crs: feldMit('Koordinatensystem (optional)', 'sv-np-crs', crs,
      hinweis('sv-np-crs-hinweis', 'Ohne Angabe: lokal, Modellmaß – Lage und Maßstab nicht belegt. Messwerte tragen dann diesen Hinweis.')),
    hoehe: feldMit('Höhensystem der Datei', 'sv-np-quellhoehe', quellHoehe, hinweis('sv-np-hoehe-hinweis', 'Nur mit Koordinatensystem.')),
  };
  const ziel = feldMit('Höhensystem im Projekt', 'sv-np-zielhoehe', zielHoehe);

  const aktionen = el('div', 'sv-aktionen');
  aktionen.append(start, neuBeginnen, abbrechen);
  form.append(boxen.projekt, boxen.titel, quelle, boxen.datei, boxen.crs, boxen.hoehe, ziel, fortsetzen, aktionen, balken, status);

  const art = (): Quelle => (radios.get('splats')!.checked ? 'splats' : 'mesh');
  const accept = (): void => { datei.accept = ENDUNGEN[art()].join(','); };
  const hoeheAn = (): void => {
    const an = crs.value.trim() !== '';
    quellHoehe.disabled = !an;
    zielHoehe.disabled = !an || quellHoehe.value === '';
  };
  accept();
  hoeheAn();
  for (const r of radios.values()) r.addEventListener('change', accept);
  crs.addEventListener('input', hoeheAn);
  quellHoehe.addEventListener('change', hoeheAn);

  const eingabe = (): Eingabe => ({
    projekt: projekt.value.trim(), titel: titel.value, quelle: art(), datei: datei.files?.[0] ?? null,
    crs: crs.value, quellHoehe: quellHoehe.disabled ? '' : quellHoehe.value, zielHoehe: zielHoehe.disabled ? '' : zielHoehe.value,
  });
  const zeigeFehler = (f: Fehler): boolean => {
    let erstes: HTMLElement | null = null;
    for (const k of Object.keys(felder) as (keyof Fehler)[]) {
      const e = felder[k];
      const p = form.querySelector<HTMLElement>(`#${e.id}-fehler`);
      const text = f[k];
      if (p) { p.textContent = text ?? ''; p.hidden = !text; }
      if (text) { e.setAttribute('aria-invalid', 'true'); erstes ??= e; } else e.removeAttribute('aria-invalid');
    }
    erstes?.focus();
    return erstes !== null;
  };

  let laeuft = false;
  let steuerung: AbortController | null = null;
  let aktuelleId: string | null = null;
  const bedienbar = (an: boolean): void => {
    for (const e of [projekt, titel, datei, crs, ...radios.values()]) e.disabled = !an;
    if (an) hoeheAn(); else { quellHoehe.disabled = true; zielHoehe.disabled = true; }
    start.disabled = !an;
    neuBeginnen.disabled = !an;
    abbrechen.hidden = an;
  };
  const zeigeStatus = (text: string): void => { status.textContent = text; };

  // Fortsetzen: Dieselbe Datei erneut gewählt → gemerkten Upload anbieten, Felder vorbelegen.
  const pruefeFortsetzen = async (): Promise<void> => {
    fortsetzen.hidden = true;
    neuBeginnen.hidden = true;
    start.textContent = 'Hochladen und Projekt anlegen';
    const f = datei.files?.[0];
    if (!f) return;
    const m = gemerkt(fortsetzenSchluessel(f));
    if (!m) return;
    let u: Upload;
    try {
      u = await uploadLesen(m.uploadId);
    } catch {
      vergessen(fortsetzenSchluessel(f));
      return;
    }
    projekt.value = m.projekt;
    titel.value = m.titel;
    radios.get(m.quelle)!.checked = true;
    accept();
    crs.value = m.crs;
    hoeheAn();
    quellHoehe.value = m.quellHoehe;
    hoeheAn();
    zielHoehe.value = m.zielHoehe;
    fortsetzen.textContent = u.status === 'abgeschlossen'
      ? 'Diese Datei ist schon vollständig hochgeladen. „Fortsetzen“ legt das Projekt an.'
      : `Für diese Datei gibt es einen unvollständigen Upload (${uploadText(u)}). „Fortsetzen“ sendet ab dem Stand weiter.`;
    fortsetzen.hidden = false;
    neuBeginnen.hidden = false;
    start.textContent = 'Fortsetzen';
  };
  datei.addEventListener('change', () => { void pruefeFortsetzen(); });
  neuBeginnen.addEventListener('click', () => {
    const f = datei.files?.[0];
    if (!f) return;
    const m = gemerkt(fortsetzenSchluessel(f));
    vergessen(fortsetzenSchluessel(f));
    if (m) void uploadLoeschen(m.uploadId).catch(() => undefined).then(neu);
    void pruefeFortsetzen();
  });

  abbrechen.addEventListener('click', () => {
    if (!window.confirm('Upload abbrechen und die schon gesendeten Teile löschen?')) return;
    steuerung?.abort();
  });

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (laeuft) return;
    melde('');
    const e = eingabe();
    if (zeigeFehler(pruefeEingabe(e))) return;
    const f = e.datei as File;
    const schluessel = fortsetzenSchluessel(f);
    laeuft = true;
    steuerung = new AbortController();
    const signal = steuerung.signal;
    bedienbar(false);
    balken.hidden = false;
    balken.value = 0;
    const fortschritt = (n: number, gesamt: number): void => {
      balken.value = gesamt > 0 ? n / gesamt : 0;
      zeigeStatus(`Hochgeladen: ${groesseText(n)} von ${groesseText(gesamt)}`);
    };
    void (async () => {
      try {
        const m = gemerkt(schluessel);
        let u: Upload | null = null;
        if (m) {
          try { u = await uploadLesen(m.uploadId); } catch { vergessen(schluessel); }
        }
        if (!u) {
          u = await ankuendigen(f);
          merken(schluessel, { uploadId: u.id, projekt: e.projekt, titel: e.titel, quelle: e.quelle, crs: e.crs.trim(), quellHoehe: e.quellHoehe, zielHoehe: e.zielHoehe });
          void neu();
        }
        aktuelleId = u.id;
        if (u.status === 'offen') {
          const ab = await stand(u.id, 0);
          fortschritt(ab, f.size);
          await senden(u.id, 0, f, u.stueckBytes, ab, fortschritt, signal);
          zeigeStatus('Prüfsumme wird auf dem Server nachgerechnet …');
          await abschliessen(u.id);
        }
        fortschritt(f.size, f.size);
        const imp = await importStarten(u.id, TYP[e.quelle], importOptionen(e));
        vergessen(schluessel);
        form.reset();
        accept();
        hoeheAn();
        fortsetzen.hidden = true;
        neuBeginnen.hidden = true;
        start.textContent = 'Hochladen und Projekt anlegen';
        zeigeStatus(`Projekt ${imp.projekt}: Import eingereiht – der Stand steht unten unter „Importe“.`);
      } catch (err) {
        if (signal.aborted) {
          if (aktuelleId) await uploadLoeschen(aktuelleId).catch(() => undefined);
          vergessen(schluessel);
          zeigeStatus('Upload abgebrochen, die gesendeten Teile sind gelöscht.');
        } else {
          const text = fehlerText(err);
          const fortsetzbar = err instanceof PlattformFehler && (err.status === 0 || err.status >= 500);
          melde(fortsetzbar ? `${text} Der Upload lässt sich fortsetzen: dieselbe Datei erneut wählen.` : text);
          zeigeStatus('');
        }
      } finally {
        laeuft = false;
        steuerung = null;
        aktuelleId = null;
        balken.hidden = true;
        bedienbar(true);
        await neu();
      }
    })();
  });
  return { form, laeuft: () => laeuft };
}

function importTabelle(liste: Import[], neu: () => Promise<void>, melde: Melde): HTMLElement {
  if (liste.length === 0) return abschnitt('Importe', el('p', undefined, 'Noch keine Importe.'));
  const { table, tbody } = tabelle(['Projekt', 'Quelle', 'Stand', 'Meldung', 'Eingereiht', ''], 'sv-tabelle sv-importe');
  for (const i of liste) {
    const aktion = el('div', 'sv-aktionen');
    if (importOffen(i)) {
      aktion.append(knopf('Abbrechen', async () => { await importAbbrechen(i.id); await neu(); }, melde,
        `Import für ${i.projekt} abbrechen? Ein laufender Lauf wird beendet.`));
    } else if (i.status === 'fertig') {
      const a = el('a', undefined, 'Im Viewer öffnen');
      a.href = viewerLink(i.projekt);
      aktion.append(a);
    }
    const stand = el('span', `sv-import-${i.status}`, importText(i));
    const tr = zeile(i.titel && i.titel !== i.projekt ? `${i.titel} (${i.projekt})` : i.projekt,
      i.typ === 'neu_splats' ? 'Splats' : 'Mesh', stand, i.fehler ?? '', zeitText(i.erstelltAm), aktion);
    tr.dataset.import = i.id;
    tr.dataset.status = i.status;
    tbody.append(tr);
  }
  return abschnitt('Importe', tabelleRegion('Importe', table),
    el('p', 'sv-hinweis', 'Das vollständige Protokoll eines Imports steht im Journal des Import-Workers auf dem Server.'));
}

function uploadTabelle(liste: Upload[], belegt: number, quote: number, neu: () => Promise<void>, melde: Melde, laeuft: () => boolean): HTMLElement {
  const kopf = el('p', 'sv-hinweis', `Belegt: ${groesseText(belegt)} von ${groesseText(quote)}. Unvollständige oder nicht genutzte Uploads werden 24 Stunden nach dem letzten Fortschritt gelöscht.`);
  if (liste.length === 0) return abschnitt('Uploads', kopf, el('p', undefined, 'Keine Uploads.'));
  const { table, tbody } = tabelle(['Datei', 'Größe', 'Stand', 'Gelöscht ab', ''], 'sv-tabelle sv-uploads');
  for (const u of liste) {
    const loeschen = knopf('Löschen', async () => {
      if (laeuft()) throw new Error('Während eines laufenden Uploads bitte „Abbrechen“ im Formular nutzen.');
      await uploadLoeschen(u.id);
      await neu();
    }, melde, 'Upload löschen?');
    const tr = zeile(u.dateien.map((d) => d.pfad).join(', '), groesseText(u.groesse), uploadText(u), zeitText(u.ablaufAm), loeschen);
    tr.dataset.upload = u.id;
    tr.dataset.status = u.status;
    tbody.append(tr);
  }
  return abschnitt('Uploads', kopf, tabelleRegion('Uploads', table));
}

export async function zeigeUploads(root: HTMLElement, nutzer: { importAn: boolean }): Promise<void> {
  root.replaceChildren();
  const seite = el('div', 'sv-seite sv-uploads-seite');
  const kopf = el('header', 'sv-kopf');
  const zurueck = el('a', undefined, '← Projekte');
  zurueck.href = '/';
  kopf.append(zurueck, el('h1', 'sv-kopf-titel', 'Uploads'));
  const meldung = el('p', 'sv-fehler');
  meldung.hidden = true;
  meldung.setAttribute('role', 'alert');
  const melde: Melde = (text) => {
    meldung.textContent = text;
    meldung.hidden = text === '';
  };
  const listen = el('div');
  seite.append(kopf, meldung);
  root.append(seite);
  if (!nutzer.importAn) {
    melde('Hochladen und neue Projekte sind auf dieser Instanz nicht eingeschaltet oder nur für Admins.');
    return;
  }
  let takt: number | undefined;
  const neu = async (): Promise<void> => {
    window.clearTimeout(takt);
    try {
      const [u, i] = await Promise.all([
        anfrage<{ uploads: Upload[]; quoteBytes: number; belegtBytes: number }>('GET', '/api/v1/uploads'),
        anfrage<{ importe: Import[]; worker: string }>('GET', '/api/v1/importe'),
      ]);
      const teile: HTMLElement[] = [importTabelle(i.importe, neu, melde), uploadTabelle(u.uploads, u.belegtBytes, u.quoteBytes, neu, melde, formular.laeuft)];
      if (i.worker !== 'aktiv') teile.unshift(el('p', 'sv-hinweis-betreiber', i.worker === 'aus'
        ? 'Der Import-Worker läuft nicht – Importe warten, bis er gestartet ist.'
        : 'Der Import-Worker meldet sich gerade nicht – Importe warten.'));
      listen.replaceChildren(...teile);
      if (i.importe.some(importOffen) || u.uploads.some((x) => x.status === 'offen')) {
        takt = window.setTimeout(() => { if (document.visibilityState === 'visible') void neu(); else takt = undefined; }, POLL_MS);
      } else takt = undefined;
    } catch (e) {
      melde(fehlerText(e));
    }
  };
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && takt === undefined) void neu();
  });
  const formular = neuesProjekt(neu, melde);
  seite.append(abschnitt('Neues Projekt', formular.form), listen);
  await neu();
}
