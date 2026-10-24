// Panel „Sonne“ (Etappe S2): Schalter, Datum (Tag im Jahr), Uhrzeit (Ortszeit Berlin), Zeitraffer, Sonnenbahn,
// Anzeige von Azimut/Höhe und Auf-/Untergang. Nur Bedienung von sonne.ts — derselbe Weg wie API/MCP
// (sonne.setzen). Texte über textContent; Regler mit aria-valuetext, damit Screenreader Datum/Uhrzeit sagen
// statt der rohen Zahl. Ohne Georeferenz: Hinweis, Bedienelemente gesperrt.
import type { Sonne } from './sonne';
import { ausOrtszeit, ortszeit, tageImJahr, uhrzeitText } from './sonneOrt';

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  return e;
};
const datumText = new Intl.DateTimeFormat('de-DE', { day: 'numeric', month: 'long', timeZone: 'UTC' });
const grad = (x: number): string => `${x.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}°`;
const himmelsrichtung = (az: number): string => ['N', 'NO', 'O', 'SO', 'S', 'SW', 'W', 'NW'][Math.round(az / 45) % 8]!;

export function attachSonnePanel(root: HTMLElement, sonne: Sonne): void {
  const box = el('div'); box.className = 'sv-sonne';
  root.replaceChildren(box);
  if (!sonne.ort) {
    const p = el('p', 'Sonne nicht verfügbar: Das Paket hat kein georeferenziertes Koordinatensystem (lokale Koordinaten), '
      + 'der Ort für den Sonnenstand ist unbekannt.');
    p.className = 'sv-sonne-hinweis'; p.setAttribute('role', 'note');
    box.append(p);
    return;
  }
  const schalterLabel = el('label');
  const schalter = el('input'); schalter.type = 'checkbox'; schalter.setAttribute('aria-label', 'Sonne an');
  schalterLabel.append(schalter, ' Sonne und Schatten');

  const datum = el('input'); datum.type = 'range'; datum.min = '1'; datum.step = '1';
  datum.setAttribute('aria-label', 'Datum (Tag im Jahr)');
  const datumAus = el('output'); datumAus.setAttribute('aria-hidden', 'true');
  const datumLabel = el('label', 'Datum '); datumLabel.className = 'sv-sonne-regler'; datumLabel.append(datum, datumAus);

  const uhr = el('input'); uhr.type = 'range'; uhr.min = '0'; uhr.max = '1435'; uhr.step = '5';
  uhr.setAttribute('aria-label', 'Uhrzeit (Ortszeit)');
  const uhrAus = el('output'); uhrAus.setAttribute('aria-hidden', 'true');
  const uhrLabel = el('label', 'Uhrzeit '); uhrLabel.className = 'sv-sonne-regler'; uhrLabel.append(uhr, uhrAus);

  const spielen = el('button', 'Abspielen'); spielen.type = 'button'; spielen.setAttribute('aria-pressed', 'false');
  spielen.title = 'Tag im Zeitraffer (1 Stunde je Sekunde)';
  const bahnLabel = el('label');
  const bahn = el('input'); bahn.type = 'checkbox'; bahn.setAttribute('aria-label', 'Sonnenbahn zeigen');
  bahnLabel.append(bahn, ' Sonnenbahn');
  const knoepfe = el('div'); knoepfe.className = 'sv-sonne-knoepfe'; knoepfe.append(spielen, bahnLabel);

  const werte = el('dl'); werte.className = 'sv-sonne-werte';
  const wert = (titel: string): HTMLElement => { const dd = el('dd'); werte.append(el('dt', titel), dd); return dd; };
  const azimut = wert('Azimut'); const hoehe = wert('Höhe'); const auf = wert('Aufgang'); const unter = wert('Untergang');
  // Live-Region nur für Zustandswechsel (an/aus, unter dem Horizont), nicht für jeden Reglerschritt.
  const status = el('p'); status.className = 'sv-sonne-status'; status.setAttribute('role', 'status');
  const ortZeile = el('small', `${grad(sonne.ort.breiteGrad)} N, ${grad(sonne.ort.laengeGrad)} O · Zeit Europe/Berlin`);
  ortZeile.className = 'sv-sonne-ort';
  box.append(schalterLabel, datumLabel, uhrLabel, knoepfe, werte, status, ortZeile);

  const zeitAusReglern = (): Date => {
    const oz = ortszeit(sonne.zeit);
    return ausOrtszeit(oz.jahr, Number(datum.value), Number(uhr.value));
  };
  schalter.addEventListener('change', () => sonne.setzen({ an: schalter.checked }));
  datum.addEventListener('input', () => sonne.setzen({ zeit: zeitAusReglern() }));
  uhr.addEventListener('input', () => sonne.setzen({ zeit: zeitAusReglern() }));
  spielen.addEventListener('click', () => sonne.setzen({ spielt: !sonne.spielt }));
  bahn.addEventListener('change', () => sonne.setzen({ bahn: bahn.checked }));

  let letzterStatus = '';
  const nachfuehren = (): void => {
    const oz = ortszeit(sonne.zeit);
    schalter.checked = sonne.an; bahn.checked = sonne.bahn;
    datum.max = String(tageImJahr(oz.jahr));
    // Während des Ziehens nicht zurücksetzen (Werte sind dieselben, nur gerundet).
    if (document.activeElement !== datum) datum.value = String(oz.tagImJahr);
    if (document.activeElement !== uhr || sonne.spielt) uhr.value = String(Math.round(oz.minuten / 5) * 5 % 1440);
    const tagText = datumText.format(new Date(Date.UTC(oz.jahr, 0, oz.tagImJahr)));
    datumAus.textContent = tagText; datum.setAttribute('aria-valuetext', `${tagText} ${oz.jahr}`);
    const zeitText = `${uhrzeitText(oz.minuten)} ${oz.kuerzel}`;
    uhrAus.textContent = zeitText; uhr.setAttribute('aria-valuetext', zeitText);
    for (const e of [spielen, bahn]) e.disabled = !sonne.an;
    spielen.textContent = sonne.spielt ? 'Anhalten' : 'Abspielen'; spielen.setAttribute('aria-pressed', String(sonne.spielt));
    const s = sonne.stand!;
    azimut.textContent = `${grad(s.azimutGrad)} (${himmelsrichtung(s.azimutGrad)})`;
    hoehe.textContent = grad(s.hoeheScheinbarGrad);
    const zeit = (d: Date | null | undefined): string => (d ? `${uhrzeitText(ortszeit(d).minuten)} ${ortszeit(d).kuerzel}` : '–');
    auf.textContent = zeit(sonne.aufUnter?.auf); unter.textContent = zeit(sonne.aufUnter?.unter);
    const neu = !sonne.an ? '' : s.hoeheScheinbarGrad > 0 ? 'Sonne an' : 'Sonne unter dem Horizont – nur Himmelslicht';
    if (neu !== letzterStatus) { letzterStatus = neu; status.textContent = neu; }
  };
  sonne.onChange(nachfuehren);
  nachfuehren();
}
