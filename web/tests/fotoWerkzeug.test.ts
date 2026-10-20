import { expect, it, vi } from 'vitest';
import type { FotoTreffer } from '../src/fotoZuordnung';
import type { Vec3 } from '../src/manifest';
import { attachFotoWerkzeug } from '../src/fotoWerkzeug';
import { attachFotoTeilung, synchronisiereFotoLeinwand } from '../src/fotoTeilung';
import type { Viewer } from '../src/viewer';
import type { PickSource } from '../src/rayPick';
import type { PanelHost } from '../src/panels';
import { BufferGeometry, Float32BufferAttribute, Group, Points } from 'three';
import { fotoTiefenquelle } from '../src/fotoTiefe';
import { projiziereFoto } from '../src/fotoZuordnung';
import type { OrientedImage } from '../src/images';

// Echte Werkzeug-Listener, ohne GPU. CSS-Treffer gegen überlagerte Panels
// bleiben Aufgabe der E2E-Abnahme.
class Element extends EventTarget {
  hidden = false;
  id = '';
  className = '';
  value = '';
  textContent = '';
  disabled = false;
  dataset: Record<string, string> = {};
  style: Record<string, string> = {};
  classList = { add: vi.fn() };
  children: Element[] = [];
  attributes = new Map<string, string>();
  append(...kinder: Element[]): void { this.children.push(...kinder); }
  replaceChildren(): void { this.children = []; }
  setAttribute(name: string, value: string): void { this.attributes.set(name, value); }
  focus(): void { /* Kein Browser-Fokus für den Ereignistest erforderlich. */ }
}
function klick(el: Element, x: number, y: number): void {
  for (const name of ['pointerdown', 'pointerup', 'click']) {
    const ev = new Event(name, { cancelable: true });
    Object.defineProperties(ev, { clientX: { value: x }, clientY: { value: y } });
    el.dispatchEvent(ev);
  }
}

it.each([[false, false], [true, false], [false, true], [true, true]])('Polygon-Auswertung liefert Umriss für die Markierung mit CPU-Tiefe; vorheriger Punktfall: %s, Eck-Picks fehlen: %s', async (vorherPunkt, eckenFehlen) => {
  const body = new Element(), view = new Element(), canvas = new Element();
  const doc = new EventTarget();
  Object.assign(doc, { body, createElement: () => new Element(), createElementNS: () => new Element() });
  vi.stubGlobal('document', doc);
  vi.stubGlobal('window', new EventTarget());
  vi.stubGlobal('innerWidth', 1920); vi.stubGlobal('innerHeight', 1080);
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { queueMicrotask(() => cb(0)); return 1; });
  try {
    let offen = false, canvasBreite = 1920;
    let aenderung: (id: string, an: boolean) => void = () => {};
    const panels = {
      isOpen: () => offen, auslagern: vi.fn(),
      onChange: (cb: typeof aenderung) => { aenderung = cb; },
      open: () => { offen = true; aenderung('foto', true); },
      close: () => { offen = false; aenderung('foto', false); },
    };
    const projection = { setSize: vi.fn(), camera: {} };
    const v = { controls: { enabled: true }, instance: {
      domElement: canvas,
      viewport: { get clientWidth() { return view.style.width ? 936 : 1920; }, clientHeight: 1080 },
      engine: { getWindowSize: () => ({ width: canvasBreite, height: 1080 }),
        onWindowResize: (w: number) => { canvasBreite = w; } },
      view: projection, notifyChange: vi.fn(),
    } };
    const teilung = attachFotoTeilung({ panels: panels as unknown as PanelHost, view: view as unknown as HTMLElement,
      werte: { tiefe: 1.5, hoch: 0.5, quer: 0.5 }, speichern: vi.fn(),
      // Kein ResizeObserver: schon vor dessen verzögerter Ausführung müssen
      // Canvas und Layout dieselbe Größe haben.
      groesseAendern: () => synchronisiereFotoLeinwand(v as unknown as Viewer),
    });
    const { controls } = v;
    const info = { level: 'fine' as const, ms: 0, wideCandidates: 1, nearCandidates: 1 };
    const root3d = new Group(), geometry = new BufferGeometry();
    const position: number[] = [];
    for (let x = -1; x <= 1.001; x += 0.02) for (let y = -1; y <= 1.001; y += 0.02) position.push(x, y, 0);
    geometry.setAttribute('position', new Float32BufferAttribute(position, 3));
    root3d.add(new Points(geometry));
    Object.assign(v, { cloud: { object3d: root3d } });
    const bild: OrientedImage = { id: 'F0001', label: 'Wand', group: 'wand',
      pose: { translation: [0, 0, 5], rotationQuat: [1, 0, 0, 0] },
      intrinsics: { width: 1000, height: 800, focalPx: 800 }, levels: [512], rev: null, tiles: null };
    const tiefe = fotoTiefenquelle(v as unknown as Viewer, [], () => true);
    const pick = vi.fn<PickSource['pickAtScreen']>((px, py) => Promise.resolve({
      // Rasterstellen treffen die Fläche, separate Picks auf der Grenze bleiben leer.
      point: eckenFehlen && (py === 530 || py === 550) ? null : [(px - canvasBreite / 2) / 100, (540 - py) / 100, 0], info,
    }));
    const ergebnis = vi.fn<(t: FotoTreffer[], p: Vec3[], r: Vec3[]) => void>(() => teilung.oeffnen());
    const werkzeug = attachFotoWerkzeug({
      v: v as unknown as Viewer,
      picker: { pickAtScreen: pick } as unknown as PickSource,
      set: { images: [bild], groups: [], anonymization: { required: true, status: 'unreviewed' } }, tiefe: tiefe.tiefeAsync, vorbereiten: vi.fn(),
      messungen: () => [], box: () => ({ enabled: false, box: null }), ergebnis, melde: vi.fn(),
    });
    const root = body.children.find((e) => e.className === 'sv-fotos-werkzeug')!;
    const [modus, , fertig] = root.children;
    modus!.value = 'punkt';
    if (vorherPunkt) {
      werkzeug.starten(); klick(canvas, 960, 540);
      await vi.waitFor(() => expect(root.hidden).toBe(true));
      expect(ergebnis).toHaveBeenCalledOnce(); expect(offen).toBe(true);
      expect(canvasBreite).toBe(936); expect(projection.setSize).toHaveBeenLastCalledWith(936, 1080);
      expect(controls.enabled).toBe(true);
      pick.mockClear(); ergebnis.mockClear();
    }
    werkzeug.starten(); expect(controls.enabled).toBe(false);
    modus!.value = 'polygon'; modus!.dispatchEvent(new Event('change'));
    const x = canvasBreite / 2;
    const ecken = [[x - 10, 530], [x + 10, 530], [x + 10, 550], [x - 10, 550]];
    for (const [px, py] of ecken) klick(canvas, px!, py!);
    const polygon = body.children.at(-1)!.children[0]!;
    expect(polygon.attributes.get('points')).toBe(ecken.map((p) => p.join(',')).join(' '));
    expect(pick).not.toHaveBeenCalled();
    klick(fertig!, 0, 0);
    await vi.waitFor(() => expect(ergebnis).toHaveBeenCalledOnce());
    const [treffer, punkte, rand] = ergebnis.mock.calls[0]!;
    expect(treffer.map((t) => t.bild.id)).toEqual(['F0001']);
    expect(treffer[0]!.sichtbar).toHaveLength(25);
    if (eckenFehlen) expect(rand.every((p) => punkte.includes(p))).toBe(true);
    const markierung = rand.map((p) => projiziereFoto(treffer[0]!.bild, p, null));
    expect(markierung.every((p) => p !== null && Number.isFinite(p.x) && Number.isFinite(p.y))).toBe(true);
    expect(new Set(markierung.map((p) => `${p!.x},${p!.y}`)).size).toBe(4);
    expect(punkte.length).toBeGreaterThan(0); expect(rand).toHaveLength(4);
    expect(pick.mock.calls.slice(-4).map(([px, py]) => [px, py])).toEqual(ecken);
    expect(root.hidden).toBe(true); expect(controls.enabled).toBe(true);
    expect(polygon.attributes.get('points')).toBe('');
    werkzeug.starten(); werkzeug.abbrechen();
    expect(root.hidden).toBe(true); expect(controls.enabled).toBe(true);
  } finally { vi.unstubAllGlobals(); }
});
