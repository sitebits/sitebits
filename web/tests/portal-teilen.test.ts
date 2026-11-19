import { afterEach, describe, expect, it, vi } from 'vitest';
import { zeigeProjektEinstellungen } from '../src/portal/projektEinstellungen';
import { zeigeTeilen } from '../src/portal/teilen';
import { ANSICHT_MAX, ansichtLesen, EHRLICH_TEXT, gastAnsicht, gastZiel, linkAdresse, linkTeile, PROFIL_HILFE, teilenText, zustand } from '../src/portal/teilenModell';

const T = 'A'.repeat(42) + '_';

describe('teilenModell', () => {
  it('Token nur aus #t= mit 43 Zeichen', () => {
    expect(linkTeile(`#t=${T}`)).toEqual({ token: T, ansicht: null });
    for (const h of ['', '#t=', `#t=${T}x`, `#t=${T.slice(1)}`, `#x=${T}`, `#t=${'ä'.repeat(43)}`]) expect(linkTeile(h), h).toBeNull();
    // Ein fremder Zusatz macht das Token nicht ungültig, er fällt nur weg (die Seite /teilen tauscht nur das Token).
    expect(linkTeile(`#t=${T}&a=1`)).toEqual({ token: T, ansicht: null });
  });
  it('Adresse trägt das Token im Fragment', () => {
    expect(linkAdresse('https://x.test', T)).toBe(`https://x.test/teilen#t=${T}`);
  });
  it('gastZiel nur aus gültiger Projekt-ID', () => {
    expect(gastZiel('ost')).toBe('/ansicht?p=/data/ost/&gast=1');
    for (const p of ['', 'Ost', '//evil.test', '../x', 'a/b', 5, null]) expect(gastZiel(p), String(p)).toBe('/');
  });
  it('gastZiel reicht den Show-Modus weiter, aber nie an einer ungültigen Projekt-ID vorbei', () => {
    expect(gastZiel('ost', true)).toBe('/ansicht?p=/data/ost/&gast=1&zeigen=1');
    expect(gastZiel('../x', true)).toBe('/');
  });
  it('Zustand', () => {
    expect(zustand({ ablauf: 200, widerrufen: null }, 100)).toBe('aktiv');
    expect(zustand({ ablauf: 100, widerrufen: null }, 100)).toBe('abgelaufen');
    expect(zustand({ ablauf: 200, widerrufen: 50 }, 100)).toBe('zurueckgezogen');
  });
  it('Hinweis nennt Sichtschicht und dass Texturen/Sichtschicht Personen zeigen können', () => {
    expect(EHRLICH_TEXT).toContain('Sichtschicht');
    expect(EHRLICH_TEXT).toContain('Personen');
  });
  it('Text nennt Passwort nur, wenn es eins gibt', () => {
    expect(teilenText('L', 'Gast Ost', '29.10.2026', true)).toContain('Passwort');
    expect(teilenText('L', 'Gast Ost', '29.10.2026', false)).not.toContain('Passwort');
  });
});

// DOM-Testdouble für Baum und Events, ohne Layoutsimulation. hidden und Scroll-Aufruf
// werden hier geprüft, der echte Viewport im Plattform-E2E.
class Element extends EventTarget {
  children: Element[] = [];
  parentElement: Element | null = null;
  text = '';
  hidden = false;
  disabled = false;
  value = '';
  id = '';
  checked = false;
  className = '';
  attributes = new Map<string, string>();
  scrollIntoView = vi.fn();
  focus = vi.fn();
  select = vi.fn();
  constructor(readonly tag: string) { super(); }
  get textContent(): string { return this.text + this.children.map((e) => e.textContent).join(''); }
  set textContent(text: string) { this.replaceChildren(); this.text = text; }
  append(...nodes: Element[]): void {
    for (const node of nodes) {
      if (node.parentElement) node.parentElement.children = node.parentElement.children.filter((e) => e !== node);
      node.parentElement = this;
      this.children.push(node);
    }
  }
  replaceChildren(...nodes: Element[]): void {
    for (const node of this.children) node.parentElement = null;
    this.children = [];
    this.text = '';
    this.append(...nodes);
  }
  setAttribute(name: string, value: string): void { this.attributes.set(name, value); }
  querySelector(tag: string): Element | null { return suche(this, (e) => e.tag === tag); }
}

function suche(root: Element, passt: (e: Element) => boolean): Element | null {
  for (const e of root.children) {
    if (passt(e)) return e;
    const gefunden = suche(e, passt);
    if (gefunden) return gefunden;
  }
  return null;
}
function muss(root: Element, passt: (e: Element) => boolean): Element {
  const e = suche(root, passt);
  expect(e).not.toBeNull();
  return e!;
}
const antwort = (daten: unknown, status = 200): Response => new Response(JSON.stringify(daten), { status });
const FRISCHE_FEHLER = 'Bitte zuerst neu anmelden, um einen Nur-Lese-Link anzulegen.';

async function seite(post: () => Response | Promise<Response>): Promise<Element> {
  const root = new Element('div');
  vi.stubGlobal('document', {
    createElement: (tag: string) => new Element(tag),
    getElementById: (id: string) => suche(root, (e) => e.id === id),
  });
  vi.stubGlobal('location', { origin: 'https://x.test' });
  vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit) => {
    if (init.method === 'POST') return Promise.resolve(post());
    if (init.method === 'PATCH') return Promise.resolve(antwort({}));
    const ende = url.split('/').at(-1);
    const daten: Record<string, unknown> = {
      p1: { titel: 'Testprojekt', rolle: 'projektleitung', kategorien: [], einstellungen: { nurLeseLinks: true } },
      beteiligte: { beteiligte: [] }, einladungen: { einladungen: [] }, gewerke: { gewerke: [] }, teilen: { links: [] },
      freigaben: { ebenen: [], panoramenNurFreigegeben: false },
    };
    if (!ende || !(ende in daten)) throw new Error(`Unerwartete Anfrage: ${url}`);
    return Promise.resolve(antwort(daten[ende]));
  }));
  // Einziger Cast an der DOM-Grenze; Produktfunktionen werden unverändert ausgeführt.
  await zeigeProjektEinstellungen(root as unknown as HTMLElement, 'p1');
  return root;
}
const teilenForm = (root: Element): Element => muss(root, (e) => e.tag === 'form' && suche(e, (b) => b.textContent === 'Link anlegen') !== null);
const submit = (form: Element): void => { form.dispatchEvent(new Event('submit', { cancelable: true })); };
const fehler = (form: Element): Element => muss(form, (e) => e.attributes.get('role') === 'alert');
const knopf = (form: Element): Element => muss(form, (e) => e.tag === 'button');
async function fertig(form: Element): Promise<void> {
  await vi.waitFor(() => { expect(knopf(form).disabled).toBe(false); });
}
afterEach(() => { vi.unstubAllGlobals(); });

describe('Nur-Lese-Link anlegen in den Projekt-Einstellungen', () => {
  it('zeigt beim ersten 403 die Anleitung am Knopf und erhält sie beim Neuaufbau', async () => {
    const root = await seite(() => antwort({ fehler: FRISCHE_FEHLER }, 403));
    const form = teilenForm(root);
    submit(form);
    expect(knopf(form).disabled).toBe(true);
    await fertig(form);
    const meldung = fehler(form);
    expect(meldung.hidden).toBe(false);
    expect(meldung.textContent).toContain('Bitte abmelden, wieder anmelden');
    expect(form.children.indexOf(meldung)).toBe(form.children.indexOf(knopf(form)) + 1);
    expect(meldung.scrollIntoView).toHaveBeenCalledWith({ block: 'nearest' });
    const global = muss(root, (e) => e.attributes.get('role') === 'alert' && e.parentElement !== form);
    expect(global.hidden).toBe(true);
    // Eine andere Aktion leert die globale Meldung und baut alle Abschnitte neu auf.
    muss(root, (e) => e.id === 'sv-pe-nurlese').dispatchEvent(new Event('change'));
    await vi.waitFor(() => { expect(teilenForm(root)).not.toBe(form); });
    expect(fehler(teilenForm(root))).toBe(meldung);
    expect(meldung.hidden).toBe(false);
    expect(meldung.textContent).toContain('Bitte abmelden, wieder anmelden');
  });

  it.each([[403, 'Anfrage abgelehnt.'], [422, FRISCHE_FEHLER]])('behält bei Status %i den sonstigen Servertext', async (status, text) => {
    const root = await seite(() => antwort({ fehler: text }, status));
    const form = teilenForm(root);
    submit(form);
    await fertig(form);
    expect(fehler(form).hidden).toBe(false);
    expect(fehler(form).textContent).toBe(text);
  });

  it('zeigt Verbindungsfehler am Formular und gibt den Knopf wieder frei', async () => {
    const root = await seite(() => Promise.reject(new TypeError('offline')));
    const form = teilenForm(root);
    submit(form);
    await fertig(form);
    expect(fehler(form).textContent).toBe('Keine Verbindung zum Server.');
    expect(fehler(form).hidden).toBe(false);
  });

  it('leert den Fehler beim erneuten Senden und hält ihn nach Erfolg ausgeblendet', async () => {
    let erfolg: ((r: Response) => void) | undefined;
    const post = vi.fn<() => Response | Promise<Response>>()
      .mockImplementationOnce(() => antwort({ fehler: FRISCHE_FEHLER }, 403))
      .mockImplementationOnce(() => new Promise<Response>((resolve) => { erfolg = resolve; }));
    const root = await seite(post);
    const form = teilenForm(root);
    submit(form);
    await fertig(form);
    expect(fehler(form).hidden).toBe(false);
    submit(form);
    expect(fehler(form).hidden).toBe(true);
    expect(fehler(form).textContent).toBe('');
    expect(knopf(form).disabled).toBe(true);
    erfolg!(antwort({ token: T, link: { name: 'Testgast', ablauf: 2_000_000_000, passwort: false } }));
    await fertig(form);
    const neu = teilenForm(root);
    expect(neu).not.toBe(form);
    expect(fehler(neu).hidden).toBe(true);
    expect(fehler(neu).textContent).toBe('');
    expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toBe(`https://x.test/teilen#t=${T}`);
    expect(muss(root, (e) => e.attributes.get('role') === 'status').textContent).toContain('angelegt');
  });
});

describe('Startansicht im Gast-Link (cam/tgt/ly, nur Fragment)', () => {
  it('linkTeile trennt Token und Ansicht und behält nur cam/tgt/ly', () => {
    expect(linkTeile(`#t=${T}&ly=a,b&cam=1,2,3&b=x`)).toEqual({ token: T, ansicht: 'cam=1,2,3&ly=a,b' });
    expect(linkTeile(`#t=${T}`)).toEqual({ token: T, ansicht: null });
    expect(linkTeile(`#t=${T}&b=x&m=1,2,3;4,5,6`)).toEqual({ token: T, ansicht: null });
    for (const h of [`#t=${T.slice(1)}`, `#t=${T.slice(1)}&ly=a`, `#t=${T}x&ly=a`, '', '#t=']) expect(linkTeile(h), h).toBeNull();
  });
  it('#t=<43> allein verhält sich wie bisher', () => {
    expect(linkTeile(`#t=${T}`)?.token).toBe(T);
  });
  it('gastZiel hängt die Ansicht nur hinter einer geprüften Projekt-ID an', () => {
    expect(gastZiel('ost', true, 'ly=a')).toBe('/ansicht?p=/data/ost/&gast=1&zeigen=1#ly=a');
    expect(gastZiel('ost', false, 'cam=1,2,3&tgt=4,5,6&ly=punkte')).toBe('/ansicht?p=/data/ost/&gast=1#cam=1,2,3&tgt=4,5,6&ly=punkte');
    expect(gastZiel('ost', false, null)).toBe('/ansicht?p=/data/ost/&gast=1');
    for (const p of ['', '../x', '//evil.test', 'a/b', null]) expect(gastZiel(p, true, 'ly=a'), String(p)).toBe('/');
  });
  it('nimmt Viewer-Adresse oder Hash, verwirft fremde Zeichen einzeln', () => {
    expect(gastAnsicht('https://x.test/ansicht?p=/data/ost/#cam=1.5,-2,3&tgt=0,0,0&ly=punkte,mesh&ko=1')).toBe('cam=1.5,-2,3&tgt=0,0,0&ly=punkte,mesh');
    expect(gastAnsicht('#ly=punkte')).toBe('ly=punkte');
    expect(gastAnsicht('ly=punkte')).toBe('ly=punkte');
    expect(gastAnsicht('#ly=a%2Cb')).toBe('ly=a,b');
    for (const v of ['<script>', '"x"', 'a/b', '%3Cx', 'a b']) {
      expect(gastAnsicht(`#ly=${v}&cam=1,2,3`), v).toBe('cam=1,2,3');
      expect(gastAnsicht(`#ly=${v}`), v).toBeNull();
    }
    // Leer nur bei ly erlaubt; cam/tgt brauchen Werte.
    for (const k of ['cam', 'tgt']) expect(ansichtLesen(`#${k}=&ly=a`), k).toEqual({ ansicht: 'ly=a', verworfen: [k], zuLang: false });
    expect(gastAnsicht('https://x.test/ansicht?p=/data/ost/')).toBeNull();
    expect(gastAnsicht('Hallo')).toBeNull();
  });
  it('ly= (leer, so schreibt der Viewer „alle Ebenen aus“) bleibt bis ins Ziel erhalten, ohne Hinweis', () => {
    expect(ansichtLesen('https://x.test/ansicht?p=/data/ost/#cam=1,2,3&tgt=4,5,6&ly=')).toEqual(
      { ansicht: 'cam=1,2,3&tgt=4,5,6&ly=', verworfen: [], zuLang: false });
    expect(gastAnsicht('#ly=')).toBe('ly=');
    const teile = linkTeile(`#t=${T}&cam=1,2,3&ly=`);
    expect(teile).toEqual({ token: T, ansicht: 'cam=1,2,3&ly=' });
    expect(gastZiel('ost', false, teile!.ansicht)).toBe('/ansicht?p=/data/ost/&gast=1#cam=1,2,3&ly=');
    expect(linkAdresse('https://x.test', T, 'ly=')).toBe(`https://x.test/teilen#t=${T}&ly=`);
  });
  it(`mehr als ${ANSICHT_MAX} Zeichen ergibt null und meldet „zu lang“`, () => {
    const ly = `ly=${'a'.repeat(ANSICHT_MAX - 3)}`;
    expect(gastAnsicht(ly)).toBe(ly);
    expect(gastAnsicht(`${ly}a`)).toBeNull();
    expect(ansichtLesen(`${ly}a`)).toEqual({ ansicht: null, verworfen: [], zuLang: true });
    expect(ansichtLesen('Hallo').zuLang).toBe(false);
    expect(linkTeile(`#t=${T}&${ly}a`)).toEqual({ token: T, ansicht: null });
  });
  it('Befundlink, Messungen, Standpunkt, Filter und Anzeige reisen nie mit', () => {
    const alles = '#b=P1&e=0123456789abcdef&rev=3&m=1,2,3;4,5,6&g=p:1,2,3;4,5,6&o=d&st=S001&yaw=10&pitch=5&fov=60' +
      '&ko=1&kx=2,6&ec=1&kv=0&cam=1,2,3&tgt=4,5,6&ly=punkte';
    const ziel = gastZiel('ost', false, gastAnsicht(alles));
    const adresse = linkAdresse('https://x.test', T, gastAnsicht(alles));
    for (const k of ['b', 'e', 'rev', 'm', 'g', 'o', 'st', 'yaw', 'pitch', 'fov', 'ko', 'kx', 'ec', 'kv']) {
      for (const text of [ziel, adresse]) expect(new URLSearchParams(text.split('#')[1]!.replace(/^t=[^&]*&?/, '')).has(k), `${k} in ${text}`).toBe(false);
    }
    expect(ziel).toBe('/ansicht?p=/data/ost/&gast=1#cam=1,2,3&tgt=4,5,6&ly=punkte');
    expect(adresse).toBe(`https://x.test/teilen#t=${T}&cam=1,2,3&tgt=4,5,6&ly=punkte`);
    // linkAdresse prüft auch selbst nach, statt dem Aufrufer zu trauen.
    expect(linkAdresse('https://x.test', T, 'b=P1&ly=<x>')).toBe(`https://x.test/teilen#t=${T}`);
    expect(gastZiel('ost', false, 'b=P1')).toBe('/ansicht?p=/data/ost/&gast=1');
  });
});

describe('Teilen-Seite: POST an /auth/teilen trägt keine Ansicht', () => {
  async function oeffne(hash: string, antworten: Response[]): Promise<{ root: Element; bodies: unknown[]; ziel: ReturnType<typeof vi.fn> }> {
    const root = new Element('div');
    const bodies: unknown[] = [];
    const ziel = vi.fn();
    vi.stubGlobal('document', { createElement: (tag: string) => new Element(tag) });
    vi.stubGlobal('location', { search: '', pathname: '/teilen', replace: ziel });
    vi.stubGlobal('history', { replaceState: vi.fn() });
    vi.stubGlobal('fetch', vi.fn((url: string, init: RequestInit) => {
      expect(url).toBe('/auth/teilen');
      bodies.push(JSON.parse(String(init.body)));
      return Promise.resolve(antworten.shift()!);
    }));
    zeigeTeilen(root as unknown as HTMLElement, hash);
    await vi.waitFor(() => { expect(bodies.length).toBe(1); });
    return { root, bodies, ziel };
  }
  it('ohne Passwort: Body genau {token}, Ziel mit Ansicht', async () => {
    const { bodies, ziel } = await oeffne(`#t=${T}&cam=1,2,3&ly=punkte&b=P1`, [antwort({ projekt: 'ost' })]);
    expect(bodies).toEqual([{ token: T }]);
    await vi.waitFor(() => { expect(ziel).toHaveBeenCalledWith('/ansicht?p=/data/ost/&gast=1#cam=1,2,3&ly=punkte'); });
  });
  it('mit Passwort: Body genau {token, passwort}', async () => {
    const { root, bodies, ziel } = await oeffne(`#t=${T}&ly=punkte`, [antwort({ fehler: 'Passwort nötig.' }, 401), antwort({ projekt: 'ost' })]);
    const form = await vi.waitFor(() => muss(root, (e) => e.tag === 'form'));
    muss(form, (e) => e.tag === 'input').value = 'hof1';
    submit(form);
    await vi.waitFor(() => { expect(bodies.length).toBe(2); });
    expect(bodies).toEqual([{ token: T }, { token: T, passwort: 'hof1' }]);
    await vi.waitFor(() => { expect(ziel).toHaveBeenCalledWith('/ansicht?p=/data/ost/&gast=1#ly=punkte'); });
  });
  it('ohne Ansicht wie bisher', async () => {
    const { bodies, ziel } = await oeffne(`#t=${T}`, [antwort({ projekt: 'ost' })]);
    expect(bodies).toEqual([{ token: T }]);
    await vi.waitFor(() => { expect(ziel).toHaveBeenCalledWith('/ansicht?p=/data/ost/&gast=1'); });
  });
});

describe('Feld „Startansicht“ beim Anlegen', () => {
  const ANGELEGT = { token: T, link: { name: 'Testgast', ablauf: 2_000_000_000, passwort: false } };
  const eingabe = (root: Element): Element => muss(root, (e) => e.tag === 'input' && e.id === 'sv-pe-teilen-ansicht');

  it('hat ein sichtbares, programmatisch verknüpftes Label', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const feldEingabe = eingabe(root);
    const label = feldEingabe.parentElement!;
    expect(label.tag).toBe('label');
    expect((label as unknown as { htmlFor: string }).htmlFor).toBe('sv-pe-teilen-ansicht');
    expect(label.textContent).toContain('Startansicht (optional)');
  });
  it('Ansicht steht nur im Link, nicht im Körper an den Server', async () => {
    const bodies: unknown[] = [];
    const root = await seite(() => antwort(ANGELEGT, 201));
    const f = vi.mocked(fetch);
    const echt = f.getMockImplementation()!;
    f.mockImplementation((url, init) => {
      if (init?.method === 'POST') bodies.push(JSON.parse(String(init.body)));
      return echt(url, init);
    });
    const form = teilenForm(root);
    muss(form, (e) => e.id === 'sv-pe-teilen-name').value = 'Testgast';
    muss(form, (e) => e.id === 'sv-pe-teilen-tage').value = '7';
    eingabe(root).value = 'https://x.test/ansicht?p=/data/p1/#cam=1,2,3&tgt=4,5,6&ly=punkte&b=P1';
    submit(form);
    await vi.waitFor(() => { expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toContain('&ly=punkte'); });
    expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toBe(`https://x.test/teilen#t=${T}&cam=1,2,3&tgt=4,5,6&ly=punkte`);
    // Das Ansichtsprofil (P08-S6) geht an den Server, die Startansicht (cam/tgt/ly) nicht.
    expect(bodies).toEqual([{ name: 'Testgast', tage: 7, profil: 'ansehen' }]);
    expect(suche(root, (e) => e.text.startsWith('Die Startansicht wurde nicht übernommen'))).toBeNull();
  });
  it('teilweise unbrauchbare Eingabe: Link mit dem Rest und ein Hinweis, welche Teile fehlen', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const form = teilenForm(root);
    eingabe(root).value = '#cam=1,2,3&tgt=4 5 6&ly=punkte';
    submit(form);
    await vi.waitFor(() => { expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toBe(`https://x.test/teilen#t=${T}&cam=1,2,3&ly=punkte`); });
    const meldung = muss(root, (e) => e.text.startsWith('Die Startansicht wurde nur teilweise übernommen'));
    expect(meldung.text).toContain('tgt');
    expect(meldung.attributes.get('role')).toBe('alert');
  });
  it('unbrauchbare Eingabe: Link ohne Ansicht und eine Meldung', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const form = teilenForm(root);
    eingabe(root).value = 'irgendein Text';
    submit(form);
    await vi.waitFor(() => { expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toBe(`https://x.test/teilen#t=${T}`); });
    const meldung = muss(root, (e) => e.text.startsWith('Die Startansicht wurde nicht übernommen'));
    expect(meldung.attributes.get('role')).toBe('alert');
    expect(meldung.text).toContain('Ansichtslink aus dem Viewer');
  });
  it('zu lange Ansicht: eigene Meldung statt „bitte Ansichtslink einfügen“', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const form = teilenForm(root);
    eingabe(root).value = `#ly=${'a,'.repeat(ANSICHT_MAX)}`;
    submit(form);
    await vi.waitFor(() => { expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toBe(`https://x.test/teilen#t=${T}`); });
    const meldung = muss(root, (e) => e.text.startsWith('Die Startansicht wurde nicht übernommen'));
    expect(meldung.text).toContain(`länger als ${ANSICHT_MAX} Zeichen`);
    expect(meldung.text).not.toContain('Ansichtslink aus dem Viewer einfügen');
  });
  it('ly= ohne Hinweis: der Link startet mit allen Ebenen aus', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const form = teilenForm(root);
    eingabe(root).value = 'https://x.test/ansicht?p=/data/p1/#cam=1,2,3&tgt=4,5,6&ly=';
    submit(form);
    await vi.waitFor(() => { expect(muss(root, (e) => e.attributes.get('aria-label') === 'Nur-Lese-Link').value).toBe(`https://x.test/teilen#t=${T}&cam=1,2,3&tgt=4,5,6&ly=`); });
    expect(suche(root, (e) => e.text.startsWith('Die Startansicht wurde'))).toBeNull();
  });
});

describe('Ansichtsprofil beim Anlegen (P08-S6)', () => {
  const ANGELEGT = { token: T, link: { name: 'Testgast', ablauf: 2_000_000_000, passwort: false, profil: 'arbeiten' } };
  const auswahl = (root: Element, id: string): Element => muss(root, (e) => e.tag === 'select' && e.id === id);
  const koerper = (methode: string): unknown[] => vi.mocked(fetch).mock.calls
    .filter(([, init]) => init?.method === methode).map(([, init]) => JSON.parse(String(init!.body)) as unknown);

  it('beide Auswahlen haben ein sichtbares, verknüpftes Label; Vorbelegung ist der Projektstandard', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    for (const [id, text] of [['sv-pe-teilen-profil', 'Ansicht für Gäste'], ['sv-pe-teilen-standard', 'Standardansicht für neue Links']] as const) {
      const a = auswahl(root, id);
      const label = a.parentElement!;
      expect(label.tag).toBe('label');
      expect((label as unknown as { htmlFor: string }).htmlFor).toBe(id);
      expect(label.textContent).toContain(text);
      expect(a.value, id).toBe('ansehen');
      expect(a.children.map((o) => o.value)).toEqual(['ansehen', 'messen', 'arbeiten']);
    }
  });
  it('das gewählte Profil geht beim Anlegen an den Server', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const form = teilenForm(root);
    muss(form, (e) => e.id === 'sv-pe-teilen-name').value = 'Testgast';
    muss(form, (e) => e.id === 'sv-pe-teilen-tage').value = '7';
    auswahl(root, 'sv-pe-teilen-profil').value = 'arbeiten';
    submit(form);
    await vi.waitFor(() => { expect(koerper('POST')).toEqual([{ name: 'Testgast', tage: 7, profil: 'arbeiten' }]); });
  });
  it('Standardansicht ändern schickt nur ansichtsprofil per PATCH', async () => {
    const root = await seite(() => antwort(ANGELEGT, 201));
    const a = auswahl(root, 'sv-pe-teilen-standard');
    a.value = 'messen';
    a.dispatchEvent(new Event('change'));
    await vi.waitFor(() => { expect(koerper('PATCH')).toEqual([{ ansichtsprofil: 'messen' }]); });
  });
  it('Hinweis erklärt die drei Ansichten und dass Gäste nicht wechseln', () => {
    expect(PROFIL_HILFE).toContain('Ansehen');
    expect(PROFIL_HILFE).toContain('Messen');
    expect(PROFIL_HILFE).toContain('Arbeiten');
    expect(PROFIL_HILFE).toContain('nicht wechseln');
  });
});
