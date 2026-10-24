// Kontrollpunktliste (J3b, Viewer-Teil): CSV im P3-Schema des Katalogprüfers lesen, gegen das Bezugssystem des
// Pakets prüfen und mit den am Modell gepickten Ist-Werten wieder schreiben. Rein (Vitest), ohne Giro3D/three.
// Gegenstück: import/src/scanview_import/pruefen/kontrollpunkte.py (`lese`). Gleiche Strenge, nie raten:
//   - Kopf `id;rolle;e;n;h` Pflicht, optional `sigma_lage;sigma_hoehe;ist_e;ist_n;ist_h`; unbekannte oder doppelte
//     Spalten sind ein Fehler. Trenner `;` (Dezimalkomma oder -punkt) oder `,` (nur Punkt).
//   - Kommentarzeilen `#`, Pflicht `# crs = EPSG:<Lage>+<Höhe>`. Umgerechnet wird nie: passt das Bezugssystem nicht
//     zum Paket, wird die Liste abgelehnt.
// Strenger als der Prüfer (Viewer-eigene Gründe, je mit Meldung): IDs mit „;“, Steuer- oder Zeilenwechselzeichen,
// führendem „#“ (die Exportzeile wäre für den Prüfer ein Kommentar) oder Formelzeichen (= + - @, Tab) am Anfang —
// IDs sind Fremdeingabe und gehen unverändert in die Exportdatei, die auch in Tabellenprogrammen landet. Zahlen nur
// in einfacher Dezimalschreibweise (kein „1_000“, kein „inf“). Bezugssysteme nur aus einer kleinen Liste (ohne
// pyproj kann der Viewer nicht mehr sicher vergleichen); ein widersprüchlich doppelt deklariertes ist ein Fehler.
//
// Export: dieselbe Spaltenfolge für jede Liste, Trenner „;“, Dezimalpunkt, ohne Anführungszeichen (der Prüfer
// zerlegt mit split, nicht als CSV), ohne BOM, Zeilenende LF. Soll und σ als Ziffern der geladenen Liste (nur „,“ →
// „.“ und ohne führendes „+“), Ist mit 3 Nachkommastellen; ungepickte Punkte mit leeren Ist-Zellen (der Prüfer
// liest leer als „fehlt“). Die Ebene jedes Picks steht nur in Kommentarzeilen — eine weitere Spalte lehnte der
// Prüfer ab. Ist-Werte des Lieferanten werden nicht übernommen: die Datei sagt „am Modell gepickt“.
import type { Frame, Vec3 } from './manifest';

export const ROLLEN: Readonly<Record<string, 'check' | 'control'>> = {
  check: 'check',
  kontrollpunkt: 'check',
  control: 'control',
  passpunkt: 'control',
};
export const PFLICHT = ['id', 'rolle', 'e', 'n', 'h'] as const;
export const SPALTEN = [...PFLICHT, 'sigma_lage', 'sigma_hoehe', 'ist_e', 'ist_n', 'ist_h'] as const;
type Spalte = (typeof SPALTEN)[number];
export const MAX_PUNKTE = 10_000;

/** Höhensysteme, die als EPSG-Code in einer Liste stehen dürfen → Normwert wie hoehen.py. */
export const HOEHE_EPSG: Readonly<Record<number, string>> = { 7837: 'dhhn2016', 5783: 'dhhn92' };
/** Lage-Codes, die der Viewer vergleichen kann, mit gleichwertigen Schreibweisen (nur andere Achsfolge, wie
 *  `ignore_axis_order` im Prüfer). Andere Codes (z. B. mit Zonenvorsatz) sind nicht gleichwertig. */
const LAGE_GLEICH: Readonly<Record<number, readonly number[]>> = { 25832: [25832, 3044], 25833: [25833, 3045], 3068: [3068] };
/** Schreibweisen des Höhensystems im Paket → Normwert (import/src/scanview_import/hoehen.py, _SCHREIBWEISEN). */
const HOEHE_NORM: Readonly<Record<string, string>> = {
  dhhn2016: 'dhhn2016',
  nhn: 'dhhn2016',
  'dhhn2016 height': 'dhhn2016',
  ellipsoidisch: 'ellipsoidal-etrs89',
  ellipsoidal: 'ellipsoidal-etrs89',
  'ellipsoidal-etrs89': 'ellipsoidal-etrs89',
  etrs89: 'ellipsoidal-etrs89',
  dhhn92: 'dhhn92',
};
const HOEHE_NAME: Readonly<Record<string, string>> = { dhhn2016: 'DHHN2016 (NHN)', dhhn92: 'DHHN92', 'ellipsoidal-etrs89': 'ellipsoidisch (ETRS89)' };

export class KontrollpunktFehler extends Error {}

export interface Kontrollpunkt {
  id: string;
  rolle: 'check' | 'control';
  /** Soll in Weltkoordinaten (E, N, H). */
  soll: Vec3;
  /** Ziffern wie geladen (Punkt als Dezimaltrenner), für den Export. */
  sollText: [string, string, string];
  sigmaLage: string | null;
  sigmaHoehe: string | null;
}

export interface KontrollpunktListe {
  punkte: readonly Kontrollpunkt[];
  /** Text der Kommentarzeile, wie geladen (für den Export). */
  crs: string;
  lageEpsg: number;
  hoehe: string;
  /** Zahl der Punkte, deren Ist-Werte aus der Liste nicht übernommen wurden. */
  istVerworfen: number;
}

export interface IstWert {
  punkt: Vec3;
  /** Lesbare Ebene, z. B. „Punktwolke: Punktwolke“ oder „Mesh: Fassade“. */
  quelle: string;
  /** Ersatzwert aus der groben Stufe (kein Originalpunkt direkt am Cursor). */
  grob?: boolean;
}

/** Zeilentrenner wie Pythons str.splitlines (der Prüfer zerlegt so). */
const ZEILEN = /\r\n|[\n\r\v\f\x1c-\x1e\x85\u2028\u2029]/;
const STEUER = /[\x00-\x1f\x7f-\x9f\u2028\u2029]/;
const CRS_ZEILE = /^#\s*crs\s*=\s*(.+?)\s*$/i;
const ZAHL = /^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$/;
const LISTEN_CRS = /^EPSG:(\d+)\+(\d+)$/i;

/** Zahlzelle → Ziffern mit Dezimalpunkt (ohne führendes „+“) oder null für leer. */
function zahlText(roh: string, trenner: string, wo: string): string | null {
  let t = roh.trim();
  if (!t) return null;
  if (trenner === ';') t = t.replace(/,/g, '.');
  if (!ZAHL.test(t)) throw new KontrollpunktFehler(`${wo}: „${roh.trim()}“ ist keine Zahl`);
  if (!Number.isFinite(Number(t))) throw new KontrollpunktFehler(`${wo}: Zahl nicht endlich`);
  return t.startsWith('+') ? t.slice(1) : t;
}

/** Grund, warum eine ID nicht unverändert in die Exportdatei darf; null = in Ordnung. */
export function idProblem(id: string): string | null {
  if (STEUER.test(id)) return 'enthält Steuer- oder Zeilenwechselzeichen';
  if (id.includes(';')) return 'enthält „;“ (Trenner der Exportdatei)';
  if (id.startsWith('#')) return 'beginnt mit „#“ (wäre im Export eine Kommentarzeile)';
  if (/^[=+\-@]/.test(id)) return 'beginnt mit einem Formelzeichen (= + - @) und würde in Tabellenprogrammen ausgeführt';
  return null;
}

function zerlegeListenCrs(text: string): { lage: number; hoehe: string } {
  const m = LISTEN_CRS.exec(text.trim());
  if (!m) throw new KontrollpunktFehler(`Bezugssystem „${text}“ im Viewer nicht auswertbar (erwartet EPSG:<Lage>+<Höhe>, z. B. EPSG:25833+7837)`);
  const lage = Number(m[1]);
  const hoehe = HOEHE_EPSG[Number(m[2])];
  if (!LAGE_GLEICH[lage] && !Object.values(LAGE_GLEICH).some((g) => g.includes(lage))) {
    throw new KontrollpunktFehler(`Lagesystem EPSG:${lage} der Liste im Viewer nicht hinterlegt (bekannt: EPSG:25832, 25833, 3068)`);
  }
  if (!hoehe) throw new KontrollpunktFehler(`Höhensystem EPSG:${m[2]} der Liste im Viewer nicht hinterlegt (bekannt: EPSG:7837 DHHN2016, 5783 DHHN92)`);
  return { lage, hoehe };
}

/** EPSG-Code des Pakets aus `frame.crs.wkt` („EPSG:25833“ oder WKT mit abschließender EPSG-Kennung); sonst null. */
export function paketLageEpsg(wkt: string | null): number | null {
  if (wkt === null) return null;
  const t = wkt.trim();
  const kurz = /^EPSG:(\d+)$/i.exec(t);
  if (kurz) return Number(kurz[1]);
  if (/^LOCAL_CS\[/.test(t)) return null;
  const wkt1 = /AUTHORITY\[\s*"EPSG"\s*,\s*"(\d+)"\s*\]\s*\]\s*$/.exec(t);
  const wkt2 = /ID\[\s*"EPSG"\s*,\s*(\d+)\s*\]\s*\]\s*$/.exec(t);
  return wkt1 ? Number(wkt1[1]) : wkt2 ? Number(wkt2[1]) : null;
}

export const hoeheNorm = (vd: string | null | undefined): string | null => (vd ? (HOEHE_NORM[vd.trim().toLowerCase()] ?? null) : null);

/** Passt die Liste zum Paket? Lage wie frame.crs, Höhe wie frame.crs.verticalDatum. Wirft mit Grund. */
export function pruefeBezug(liste: { lage: number; hoehe: string; text: string }, crs: Frame['crs']): void {
  const paketLage = paketLageEpsg(crs.wkt);
  if (crs.wkt === null || /^\s*LOCAL_CS\[/.test(crs.wkt)) {
    throw new KontrollpunktFehler('Paket in lokalen Koordinaten (ohne Bezugssystem) – Kontrollpunkte lassen sich nicht zuordnen');
  }
  if (paketLage === null) throw new KontrollpunktFehler('Bezugssystem des Pakets ohne EPSG-Kennung – Vergleich mit der Liste nicht möglich');
  const gleich = LAGE_GLEICH[paketLage] ?? [paketLage];
  if (!gleich.includes(liste.lage)) {
    throw new KontrollpunktFehler(`Lagesystem passt nicht: Liste EPSG:${liste.lage}, Paket EPSG:${paketLage} – umgerechnet wird nicht`);
  }
  const ph = hoeheNorm(crs.verticalDatum);
  if (ph === null) {
    throw new KontrollpunktFehler(`Höhensystem des Pakets ${crs.verticalDatum ? `„${crs.verticalDatum}“ unbekannt` : 'nicht angegeben'} – Vergleich nicht möglich`);
  }
  if (ph !== liste.hoehe) {
    throw new KontrollpunktFehler(`Höhensystem passt nicht: Liste ${HOEHE_NAME[liste.hoehe] ?? liste.hoehe}, Paket ${HOEHE_NAME[ph] ?? ph} – umgerechnet wird nicht`);
  }
}

/** Liste streng lesen (wie `lese` im Prüfer) und gegen das Bezugssystem des Pakets prüfen. */
export function leseKontrollpunkte(text: string, crs: Frame['crs']): KontrollpunktListe {
  // Ein BOM am Anfang wie der Prüfer beim Lesen der Datei (utf-8-sig).
  const roh = text.startsWith('﻿') ? text.slice(1) : text;
  let crsText: string | null = null;
  const zeilen: [number, string][] = [];
  roh.split(ZEILEN).forEach((z, i) => {
    if (!z.trim()) return;
    const m = CRS_ZEILE.exec(z.trim());
    if (m) {
      if (crsText !== null && crsText !== m[1]) throw new KontrollpunktFehler(`Bezugssystem widersprüchlich: „${crsText}“ und „${m[1]}“`);
      crsText = m[1]!;
    } else if (!z.trimStart().startsWith('#')) zeilen.push([i + 1, z]);
  });
  if (!zeilen.length) throw new KontrollpunktFehler('Liste ohne Kopf und Punkte');
  const kopf = zeilen[0]![1];
  const trenner = kopf.includes(';') ? ';' : kopf.includes(',') ? ',' : '';
  if (!trenner) throw new KontrollpunktFehler('Trenner weder „;“ noch „,“');
  const spalten = kopf.split(trenner).map((s) => s.trim().toLowerCase());
  const fremd = spalten.filter((s) => !(SPALTEN as readonly string[]).includes(s));
  if (fremd.length || new Set(spalten).size !== spalten.length) {
    throw new KontrollpunktFehler(`Kopf ungültig (unbekannt oder doppelt: ${(fremd.length ? fremd : spalten).join(', ')})`);
  }
  const fehlt = PFLICHT.filter((s) => !spalten.includes(s));
  if (fehlt.length) throw new KontrollpunktFehler(`Pflichtspalten fehlen: ${fehlt.join(', ')}`);
  if (zeilen.length - 1 > MAX_PUNKTE) throw new KontrollpunktFehler(`mehr als ${MAX_PUNKTE} Punkte`);

  let istVerworfen = 0;
  const punkte = zeilen.slice(1).map(([nr, z]): Kontrollpunkt => {
    const wo = `Zeile ${nr}`;
    const felder = z.split(trenner);
    if (felder.length !== spalten.length) {
      throw new KontrollpunktFehler(`${wo}: ${felder.length} statt ${spalten.length} Felder (Dezimalkomma bei „,“ als Trenner?)`);
    }
    const w = Object.fromEntries(spalten.map((s, i) => [s, felder[i]!])) as Partial<Record<Spalte, string>>;
    const rolle = ROLLEN[w.rolle!.trim().toLowerCase()];
    if (!rolle) throw new KontrollpunktFehler(`${wo}: Rolle „${w.rolle!.trim()}“ unbekannt (check/kontrollpunkt, control/passpunkt)`);
    const zahl = (s: Spalte): string | null => zahlText(w[s] ?? '', trenner, wo);
    const [e, n, h] = [zahl('e'), zahl('n'), zahl('h')];
    const sigmaLage = zahl('sigma_lage'); const sigmaHoehe = zahl('sigma_hoehe');
    if ([zahl('ist_e'), zahl('ist_n'), zahl('ist_h')].some((x) => x !== null)) istVerworfen++;
    if (e === null || n === null || h === null) throw new KontrollpunktFehler(`${wo}: Sollkoordinate fehlt`);
    const id = w.id!.trim();
    const problem = id ? idProblem(id) : null;
    if (problem) throw new KontrollpunktFehler(`${wo}: Punkt-ID „${id.replace(STEUER, '?')}“ ${problem}`);
    return { id, rolle, soll: [Number(e), Number(n), Number(h)], sollText: [e, n, h], sigmaLage, sigmaHoehe };
  });
  const ids = punkte.map((p) => p.id);
  if (ids.includes('') || new Set(ids).size !== ids.length) throw new KontrollpunktFehler('Punkt-IDs leer oder doppelt');
  if (crsText === null) throw new KontrollpunktFehler('Bezugssystem nicht deklariert (Kommentarzeile „# crs = EPSG:…+…“)');
  const bezug = zerlegeListenCrs(crsText);
  pruefeBezug({ ...bezug, text: crsText }, crs);
  return { punkte, crs: crsText, lageEpsg: bezug.lage, hoehe: bezug.hoehe, istVerworfen };
}

/** 3 Nachkommastellen, Dezimalpunkt, kein „-0.000“. */
export const ist3 = (x: number): string => {
  const t = x.toFixed(3);
  return t === '-0.000' ? '0.000' : t;
};

/** Text für eine Kommentarzeile: ohne Zeilenwechsel und Steuerzeichen (sonst entstünde eine Daten- oder crs-Zeile). */
export const kommentarText = (t: string): string => t.replace(new RegExp(`${ZEILEN.source}|${STEUER.source}`, 'g'), ' ').trim();

/** Liste mit Ist-Werten im P3-Schema schreiben (siehe Kopf dieser Datei). */
export function kontrollpunkteCsv(liste: KontrollpunktListe, ist: ReadonlyMap<string, IstWert>, version: string): string {
  const zeilen = [
    `# crs = ${liste.crs}`,
    `# quelle = siteBits-Viewer ${kommentarText(version)}, am Modell gepickt`,
  ];
  for (const p of liste.punkte) {
    const w = ist.get(p.id);
    if (w) zeilen.push(`# ebene ${p.id} = ${kommentarText(w.quelle)}${w.grob ? ' (grobe Stufe)' : ''}`);
  }
  zeilen.push(SPALTEN.join(';'));
  for (const p of liste.punkte) {
    const w = ist.get(p.id);
    zeilen.push([p.id, p.rolle, ...p.sollText, p.sigmaLage ?? '', p.sigmaHoehe ?? '', ...(w ? w.punkt.map(ist3) : ['', '', ''])].join(';'));
  }
  return zeilen.join('\n') + '\n';
}

/** Dateiname passend zum Suchmuster des Prüfers (`*kontrollpunkt*.csv`). */
export const exportName = (projectId: string): string => `kontrollpunkte-ist-${projectId.replace(/[^A-Za-z0-9_-]/g, '_')}.csv`;

/** Bytes streng als UTF-8 lesen (wie der Prüfer: falsche Kodierung ist ein Fehler, kein Ersatzzeichen). */
export function dekodiere(bytes: ArrayBuffer): string {
  try {
    return new TextDecoder('utf-8', { fatal: true, ignoreBOM: true }).decode(bytes);
  } catch {
    throw new KontrollpunktFehler('Datei ist kein gültiges UTF-8');
  }
}
