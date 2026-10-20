import { afterEach, expect, it, vi } from 'vitest';
import { createFadenAnsicht } from '../src/fadenAnsicht';
import type { Faden } from '../src/fadenModell';
// Minimaler DOM-Adapter für Node: geprüft wird der vom Produktionscode aufgebaute Baum.
class Element {
  children: (Element | string)[] = [];
  attrs = new Map<string, string>();
  listeners = new Map<string, () => void>();
  hidden = false;
  className = '';
  id = '';
  value = '';
  dataset: Record<string, string> = {};
  constructor(readonly tag: string) { }
  classList = { add: (...s: string[]) => { this.className += ' ' + s.join(' '); } };
  setAttribute(k: string, v: string): void { this.attrs.set(k, v); }
  append(...xs: (Element | string)[]): void { this.children.push(...xs); }
  replaceChildren(...xs: (Element | string)[]): void { this.children = xs; }
  set textContent(s: string) { this.children = [s]; }
  get textContent(): string { return this.children.map(c => typeof c === 'string' ? c : c.textContent).join(''); }
  addEventListener(k: string, f: () => void): void { this.listeners.set(k, f); }
  find(tag: string): Element[] { return [...(this.tag === tag ? [this] : []), ...this.children.flatMap(c => typeof c === 'string' ? [] : c.find(tag))]; }
}
const faden = (offen = true): Faden => ({ stand: { status: 'offen', zustaendig: null, frist: null, version: 9 }, rechte: { kommentieren: false, status: [], zustaendig: false, frist: false, intern: false }, eintraege: [{ schluessel: '0123456789abcdef', art: 'vorschlag', text: null, wert: { art: 'frist', wert: '2026-11-01' }, offen, herkunft: 'ki', intern: false, erstellt_am: 1, erstellt_von: { id: 1, name: 'Konto' }, kanal: 'api', zurueckgezogen_am: null }] });
afterEach(() => vi.unstubAllGlobals());
function aufbau(pruefen: boolean, offen = true) {
  vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
  let daten = faden(offen);
  const fetchFn = vi.fn<typeof fetch>().mockImplementation(async (url, init) => {
    if (String(url).endsWith('/gewerke'))
      return Response.json({ gewerke: [] });
    if (String(url).endsWith('/beteiligte'))
      return Response.json({ beteiligte: [] });
    if (init?.method === 'POST') {
      daten = { ...faden(false), stand: { ...daten.stand, frist: '2026-11-01', version: 10 } };
      return Response.json({ stand: daten.stand, eintrag: daten.eintraege[0] });
    }
    return Response.json(daten);
  });
  vi.stubGlobal('fetch', fetchFn);
  const stand = vi.fn();
  const ansicht = createFadenAnsicht({ pid: 'p', id: 'test', nutzerId: 2, ebene: 2, pruefen, kommentar: { label: 'Kommentar', senden: 'Senden' }, onStand: stand });
  return { ansicht, baum: ansicht.element as unknown as Element, fetchFn, stand };
}
it('zeichnet Vorschlag mit KI, Text, Begründung und Prüfknöpfen', async () => {
  const t = aufbau(true);
  await t.ansicht.zeige('P1');
  expect(t.baum.textContent).toContain('KI schlägt vor: Frist → 01.11.2026');
  const v = t.baum.find('li')[0]!;
  expect(v.className).toContain('sv-faden-vorschlag');
  expect(v.find('button').map(b => b.textContent)).toEqual(['Annehmen', 'Verwerfen']);
  expect(v.find('label')[0]?.textContent).toBe('Begründung (optional)');
  v.find('button')[0]!.listeners.get('click')!();
  await vi.waitFor(() => expect(t.stand).toHaveBeenCalledWith('P1', expect.objectContaining({ version: 10, frist: '2026-11-01' })));
  expect(t.fetchFn).toHaveBeenCalledWith('/api/v1/projekte/p/pois/P1/faden/0123456789abcdef/annehmen', expect.objectContaining({ body: '{"version":9}' }));
});
it('bietet Nicht-Prüfern keine Prüfknöpfe und entschiedene Vorschläge bleiben lesbar', async () => {
  const t = aufbau(false, false);
  await t.ansicht.zeige('P1');
  const v = t.baum.find('li')[0]!;
  expect(v.find('button')).toEqual([]);
  expect(v.textContent).toContain('Entschieden');
  expect(v.className).toContain('sv-faden-entschieden');
});
it('409 mit Stand lädt neu; 409 ohne Stand zeigt ausschließlich den Servertext', async () => {
  const t = aufbau(true);
  await t.ansicht.zeige('P1');
  t.fetchFn.mockImplementation(async (url, init) => {
    if (init?.method === 'POST')
      return Response.json({ fehler: 'Inzwischen geändert.', aktuell: { version: 10 } }, { status: 409 });
    if (String(url).endsWith('/gewerke'))
      return Response.json({ gewerke: [] });
    if (String(url).endsWith('/beteiligte'))
      return Response.json({ beteiligte: [] });
    return Response.json(faden());
  });
  t.baum.find('li')[0]!.find('button')[0]!.listeners.get('click')!();
  await vi.waitFor(() => expect(t.baum.textContent).toContain('Der aktuelle Stand ist jetzt geladen'));
  t.fetchFn.mockImplementation(async (_url, init) => init?.method === 'POST' ? Response.json({ fehler: 'Vorschlag bereits erledigt.' }, { status: 409 }) : Response.json(faden()));
  t.baum.find('li')[0]!.find('button')[0]!.listeners.get('click')!();
  await vi.waitFor(() => expect(t.baum.textContent).toContain('Vorschlag bereits erledigt.'));
  expect(t.baum.textContent).not.toContain('Der aktuelle Stand ist jetzt geladen');
});
