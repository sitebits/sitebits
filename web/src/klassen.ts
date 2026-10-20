// Punktklassen und Echos (F30): Legende, Filterzustand und Dekodierfilter — rein, ohne Giro3D-Import (Vitest).
// Der Filter wirkt beim Dekodieren (COPCSource.filters) und damit für Darstellung UND Strahlsuche gleich: eine
// ausgeblendete Klasse wird weder gezeigt noch beim Messen getroffen, in jedem Farbmodus. Ein Zustand für
// beides; der Punkte-Merker der Strahlsuche trägt dessen Kennung im Schlüssel (rayPick.ts).
// Echos: „nur erste“ = ReturnNumber 1 (Einzelechos 1/1 zählen dazu, 0 = ungültig fällt heraus). „Nur letzte“
// braucht ReturnNumber = NumberOfReturns, Giro3D vergleicht aber nur mit Konstanten: der Import markiert letzte
// Echos deshalb in UserData = 1 (Attribut `returnsLast`, import/klassen.py) — nur dann wird es angeboten.

/** ASPRS-Klassen (LAS 1.4) mit deutschen Namen; übrige Werte heißen „Klasse n“. */
export const ASPRS_NAMEN: Readonly<Record<number, string>> = {
  0: 'Nie klassifiziert',
  1: 'Unklassifiziert',
  2: 'Boden',
  3: 'Niedrige Vegetation',
  4: 'Mittlere Vegetation',
  5: 'Hohe Vegetation',
  6: 'Gebäude',
  7: 'Rauschen (niedrig)',
  8: 'Schlüsselpunkt',
  9: 'Wasser',
  10: 'Schiene',
  11: 'Straßenoberfläche',
  12: 'Überlappung',
  13: 'Leitungsschutz',
  14: 'Leitung',
  15: 'Mast',
  16: 'Leitungsverbinder',
  17: 'Brücke',
  18: 'Rauschen (hoch)',
};

export const klassenName = (k: number): string => ASPRS_NAMEN[k] ?? `Klasse ${k}`;

export interface KlasseEintrag {
  klasse: number;
  name: string;
  punkte: number;
}

/** Klassen der Legende: nur vorhandene (Punktzahl > 0), aufsteigend; ungültige Schlüssel fallen heraus. */
export function vorhandeneKlassen(classes: Readonly<Record<string, number>> | undefined): KlasseEintrag[] {
  if (!classes) return [];
  return Object.entries(classes)
    .map(([k, n]) => ({ klasse: Number(k), punkte: n }))
    .filter((e) => /^\d{1,3}$/.test(String(e.klasse)) && e.klasse <= 255 && Number.isInteger(e.punkte) && e.punkte > 0)
    .sort((a, b) => a.klasse - b.klasse)
    .map((e) => ({ ...e, name: klassenName(e.klasse) }));
}

export type Echo = 'alle' | 'erste' | 'letzte';

/** Attribut im Manifest: UserData = 1 markiert letzte Echos (import/klassen.py LETZTE_ATTRIBUT). */
export const LETZTE_ATTRIBUT = 'returnsLast';

export interface Punktfilter {
  /** Ausgeblendete Klassenwerte. */
  aus: ReadonlySet<number>;
  echo: Echo;
}

export const OHNE_FILTER: Punktfilter = { aus: new Set(), echo: 'alle' };

/** Gleiche Form wie Giro3Ds DimensionFilter (sources/las/filter.d.ts), ohne dessen Import. */
export type Dekodierfilter =
  | { dimension: 'Classification'; operator: 'not_in'; values: Set<number> }
  | { dimension: 'ReturnNumber' | 'UserData'; operator: 'equal'; value: number };

export function dekodierfilter(f: Punktfilter): Dekodierfilter[] {
  const out: Dekodierfilter[] = [];
  if (f.aus.size) out.push({ dimension: 'Classification', operator: 'not_in', values: new Set(f.aus) });
  if (f.echo === 'erste') out.push({ dimension: 'ReturnNumber', operator: 'equal', value: 1 });
  if (f.echo === 'letzte') out.push({ dimension: 'UserData', operator: 'equal', value: 1 });
  return out;
}

/** Kennung des Filterzustands für Merker-Schlüssel: gleiche Filter → gleiche Kennung, leer = ''. */
export function filterKennung(f: Punktfilter): string {
  const aus = [...f.aus].sort((a, b) => a - b).join(',');
  return aus || f.echo !== 'alle' ? `k${aus};e${f.echo}` : '';
}

/** Rohwerte aus dem Ansichtslink (hash.ts): `kx` = ausgeblendete Klassen (kommagetrennt, aufsteigend), `ec=1` = nur
 *  erste, `ec=2` = nur letzte Echos. */
export function filterAusLink(kx: string | null, ec: string | null): Punktfilter {
  const aus = new Set<number>();
  for (const t of (kx ?? '').split(',')) {
    if (/^\d{1,3}$/.test(t) && Number(t) <= 255) aus.add(Number(t));
  }
  return { aus, echo: ec === '1' ? 'erste' : ec === '2' ? 'letzte' : 'alle' };
}

/** Felder für den Ansichtslink (hash.ts kx/ec); ohne Filter leer. */
export function filterZuLink(f: Punktfilter): { kx?: number[]; ec?: Exclude<Echo, 'alle'> } {
  const aus = [...f.aus].sort((a, b) => a - b);
  return { ...(aus.length ? { kx: aus } : {}), ...(f.echo !== 'alle' ? { ec: f.echo } : {}) };
}

/** Filter aus dem Link, beschränkt auf das, wofür der Klassen-Ordner (ui.ts) einen Schalter hat: nur vorhandene
 *  Klassen, Echofilter nur bei Mehrfachechos, „nur letzte“ nur mit Markierung. Sonst könnte ein Link die Wolke
 *  leeren, ohne dass sich das in der Oberfläche zurücknehmen lässt. */
export function filterVonLink(
  s: { kx?: number[]; ec?: Exclude<Echo, 'alle'> },
  cloud: { attributes: readonly string[]; classes?: Readonly<Record<string, number>> },
): Punktfilter {
  const da = new Set(cloud.attributes.includes('classification') ? vorhandeneKlassen(cloud.classes).map((k) => k.klasse) : []);
  const echo = s.ec === 'letzte' ? cloud.attributes.includes(LETZTE_ATTRIBUT) : cloud.attributes.includes('returns');
  return { aus: new Set((s.kx ?? []).filter((k) => da.has(k))), echo: s.ec && echo ? s.ec : 'alle' };
}
