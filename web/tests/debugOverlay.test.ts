import { afterEach, describe, expect, it, vi } from 'vitest';
import { zeigeDebugOverlay } from '../src/debugOverlay';

interface Ereignis { key?: string; preventDefault(): void; stopPropagation(): void }
// Kleinstes Element für die reine Darstellung (Vitest läuft ohne DOM).
class Element {
  className = ''; tabIndex = -1; textContent = ''; style = { cssText: '' };
  attrs = new Map<string, string>(); entfernt = false;
  private hoerer = new Map<string, ((ev: Ereignis) => void)[]>();
  setAttribute(k: string, v: string) { this.attrs.set(k, v); }
  addEventListener(art: string, f: (ev: Ereignis) => void) { this.hoerer.set(art, [...(this.hoerer.get(art) ?? []), f]); }
  remove() { this.entfernt = true; }
  click() { for (const f of this.hoerer.get('click') ?? []) f({ preventDefault() {}, stopPropagation() {} }); }
}
const werte = () => ({ fps: 59.6, punkte: 1234, anfragenLaufend: 1, anfragenFehler: 0, letzteMs: 12 });
let el: Element;
const parent = { appendChild: (e: Element) => { el = e; } } as unknown as HTMLElement;
vi.stubGlobal('document', { createElement: () => new Element() });
afterEach(() => vi.clearAllTimers());

describe('Debug-Anzeige', () => {
  it('klein: unten links, startet eingeklappt, ein Klick klappt auf und wieder ein', () => {
    vi.useFakeTimers();
    const abbau = zeigeDebugOverlay(parent, werte, true);
    expect(el.style.cssText).toContain('left:calc(4px');
    expect(el.style.cssText).toContain('bottom:calc(4px');
    expect(el.style.cssText).toContain('font:10px');
    expect(el.textContent).toBe('FPS 60 ▸');
    el.click();
    expect(el.textContent).toContain('Punkte 1.234');
    el.click();
    expect(el.textContent).toBe('FPS 60 ▸');
    abbau();
    expect(el.entfernt).toBe(true);
  });
  it('groß: startet aufgeklappt', () => {
    vi.useFakeTimers();
    const abbau = zeigeDebugOverlay(parent, werte, false);
    expect(el.textContent).toContain('Anfragen 1');
    expect(el.attrs.get('aria-label')).toBe('Leistungsanzeige einklappen');
    abbau();
  });
});
