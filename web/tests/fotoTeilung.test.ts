import { expect, it, vi } from 'vitest';
import { attachFotoTeilung } from '../src/fotoTeilung';
import type { PanelHost } from '../src/panels';

// DOM-Hülle für den Lebenszyklus. Zeigertreffer prüft der bestehende Browser-Test.
class Element {
  hidden = false;
  id = '';
  className = '';
  textContent = '';
  type = '';
  tabIndex = 0;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  children: Element[] = [];
  append(...kinder: Element[]): void { this.children.push(...kinder); }
  setAttribute(): void { /* ARIA ist für den Lebenszyklus ohne Bedeutung. */ }
  addEventListener(): void { /* Keine simulierten Zeiger-/Tastaturereignisse. */ }
}

it.each([false, true])('Panel-API nimmt Foto-Inhalt mit; gespeicherter Offen-Zustand %s', (anfangsOffen) => {
  const body = new Element(), view = new Element(), inhalt = new Element();
  let offen = anfangsOffen;
  let aenderung: (id: string, an: boolean) => void = () => {};
  let ziel: Element | null = null;
  const panels = {
    isOpen: () => offen,
    onChange: (cb: typeof aenderung) => { aenderung = cb; },
    auslagern: (_id: string, host: Element | null) => {
      if (ziel) ziel.children = [];
      ziel = host;
      if (offen && host) host.append(inhalt);
    },
    open: () => { offen = true; aenderung('foto', true); },
    close: () => { offen = false; aenderung('foto', false); },
  };
  vi.stubGlobal('document', { body, createElement: () => new Element() });
  vi.stubGlobal('window', { addEventListener: vi.fn() });
  vi.stubGlobal('innerWidth', 1200); vi.stubGlobal('innerHeight', 800);
  try {
    const teilung = attachFotoTeilung({ panels: panels as unknown as PanelHost, view: view as unknown as HTMLElement,
      werte: { tiefe: 1.5, quer: 0.5, hoch: 0.5 }, speichern: vi.fn(), groesseAendern: vi.fn() });
    const [seite, trenner] = body.children;
    const host = seite!.children[1]!;
    expect(seite!.hidden).toBe(!anfangsOffen);
    if (anfangsOffen) expect(host.children).toContain(inhalt);
    // Öffnung über Panel-API/hidden-Observer, ohne teilung.oeffnen().
    panels.open();
    expect(seite!.hidden).toBe(false); expect(host.children).toContain(inhalt);
    expect(view.style.width).not.toBe('');
    expect(teilung.umschalten()).toBe(false);
    expect(seite!.hidden).toBe(true); expect(trenner!.hidden).toBe(true);
    expect(ziel).toBeNull(); expect(view.style.width).toBe('');
    expect(teilung.umschalten()).toBe(true);
    expect(host.children).toContain(inhalt);
    panels.close();
    expect(seite!.hidden).toBe(true); expect(host.children).toEqual([]);
    teilung.oeffnen(); expect(host.children).toContain(inhalt);
  } finally { vi.unstubAllGlobals(); }
});
