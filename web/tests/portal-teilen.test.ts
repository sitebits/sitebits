import { afterEach, describe, expect, it, vi } from 'vitest';
import { zeigeProjektEinstellungen } from '../src/portal/projektEinstellungen';
import { gastZiel, linkAdresse, teilenText, tokenAusFragment, zustand } from '../src/portal/teilenModell';

const T = 'A'.repeat(42) + '_';

describe('teilenModell', () => {
  it('Token nur aus #t= mit 43 Zeichen', () => {
    expect(tokenAusFragment(`#t=${T}`)).toBe(T);
    for (const h of ['', '#t=', `#t=${T}x`, `#t=${T.slice(1)}`, `#x=${T}`, `#t=${'ä'.repeat(43)}`, `#t=${T}&a=1`]) expect(tokenAusFragment(h), h).toBeNull();
  });
  it('Adresse trägt das Token im Fragment', () => {
    expect(linkAdresse('https://x.test', T)).toBe(`https://x.test/teilen#t=${T}`);
  });
  it('gastZiel nur aus gültiger Projekt-ID', () => {
    expect(gastZiel('ost')).toBe('/ansicht?p=/data/ost/&gast=1');
    for (const p of ['', 'Ost', '//evil.test', '../x', 'a/b', 5, null]) expect(gastZiel(p), String(p)).toBe('/');
  });
  it('Zustand', () => {
    expect(zustand({ ablauf: 200, widerrufen: null }, 100)).toBe('aktiv');
    expect(zustand({ ablauf: 100, widerrufen: null }, 100)).toBe('abgelaufen');
    expect(zustand({ ablauf: 200, widerrufen: 50 }, 100)).toBe('zurueckgezogen');
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
