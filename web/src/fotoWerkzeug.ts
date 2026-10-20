import { Vector3 } from 'three';
import type { Viewer } from './viewer';
import type { PickSource } from './rayPick';
import type { ImageSet } from './images';
import type { Vec3 } from './manifest';
import type { ClipBoxState } from './clipBoxMath';
import { insideBox } from './clipBoxMath';
import type { Measurement } from './measureStore';
import { polygonArea } from './areaMath';
import { bildHuelle, fotoVorfilter, polygonRaster, sucheFotos } from './fotoZuordnung';
import type { Bildpunkt, FotoTreffer, SuchTiefenquelle } from './fotoZuordnung';
import { istTouch } from './geraet';

export function attachFotoWerkzeug(o: {
  v: Viewer; picker: PickSource; set: ImageSet; tiefe: SuchTiefenquelle;
  vorbereiten(): void; messungen(): readonly Measurement[]; box(): ClipBoxState;
  ergebnis(t: FotoTreffer[], punkte: Vec3[], rand: Vec3[]): void; melde(s: string): void;
}): { starten(): void; abbrechen(): void } {
  const root = document.createElement('div'); root.className = 'sv-fotos-werkzeug'; root.hidden = true; root.dataset.tastenEigen = '';
  const modus = document.createElement('select'); modus.setAttribute('aria-label', 'Fotoauswahl');
  for (const [value, text] of [['punkt', 'Punkt wählen'], ['polygon', 'Polygon mit Klicks'], ['flaeche', 'Vorhandene Fläche'], ['box', 'Schnittbox']]) {
    const option = document.createElement('option'); option.value = value!; option.textContent = text!; modus.append(option);
  }
  const flaechen = document.createElement('select'); flaechen.setAttribute('aria-label', 'Flächenmessung für Fotos'); flaechen.hidden = true;
  const fertig = document.createElement('button'); fertig.type = 'button'; fertig.textContent = 'Auswahl auswerten';
  const abbrechen = document.createElement('button'); abbrechen.type = 'button'; abbrechen.textContent = 'Abbrechen (Esc)';
  const status = document.createElement('p'); status.setAttribute('role', 'status');
  const hilfe = document.createElement('p'); hilfe.textContent = 'Punkt oder Polygon auf der Oberfläche wählen. Verdeckung wird nur gegen geladene Wolkenknoten und sichtbare Meshes geprüft (LOD); dünne Hindernisse können fehlen. Ohne Tiefentreffer: ungeprüft, zählt nicht als sichtbare Stelle.';
  root.append(modus, flaechen, fertig, abbrechen, status, hilfe); document.body.append(root);
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg'); svg.classList.add('sv-foto-polygon'); svg.style.display = 'none';
  const polygon = document.createElementNS(svg.namespaceURI, 'polygon'); svg.append(polygon); document.body.append(svg);
  let aktiv = false, arbeitet = false, vorherControls = true, controller: AbortController | null = null;
  let ecken: Bildpunkt[] = [];
  const pause = (): Promise<void> => new Promise((r) => requestAnimationFrame(() => r()));
  const zeichnen = (): void => { svg.style.display = !aktiv || ecken.length === 0 ? 'none' : '';  polygon.setAttribute('points', ecken.map((p) => p.join(',')).join(' ')); };
  const ende = (): void => {
    if (aktiv) o.v.controls.enabled = vorherControls;
    aktiv = false; arbeitet = false; root.hidden = true; ecken = []; zeichnen();
  };
  const abbreche = (): void => { controller?.abort(); controller = null; ende(); };
  const projiziere = (p: Vec3): Bildpunkt | null => {
    const v = new Vector3(...p).project(o.v.instance.view.camera);
    if (v.z < -1 || v.z > 1) return null;
    const r = o.v.instance.domElement.getBoundingClientRect();
    return [r.left + (v.x + 1) / 2 * r.width, r.top + (1 - v.y) / 2 * r.height];
  };
  const auswerten = async (stellen: readonly Bildpunkt[], normale?: Vec3, box?: [Vec3, Vec3]): Promise<void> => {
    if (arbeitet) return;
    arbeitet = true; controller?.abort(); const lauf = new AbortController(); controller = lauf;
    fertig.disabled = modus.disabled = flaechen.disabled = true;
    try {
      const punkte: Vec3[] = [], rand: Vec3[] = [];
      const oberflaeche = new Map<Bildpunkt, Vec3>();
      for (const [i, p] of stellen.entries()) {
        lauf.signal.throwIfAborted(); status.textContent = `Oberfläche prüfen: ${i + 1} von ${stellen.length}`;
        const r = await o.picker.pickAtScreen(p[0], p[1], { budgetMs: 60 });
        lauf.signal.throwIfAborted();
        if (r.point && (!box || insideBox([...r.point], ...box))) {
          const punkt: Vec3 = [...r.point];
          punkte.push(punkt); oberflaeche.set(p, punkt);
        }
        await pause();
      }
      // Umriss ebenfalls aus echten Oberflächentreffern, niemals aus einer angenommenen Tiefe.
      if (ecken.length >= 3) for (const p of ecken) {
        lauf.signal.throwIfAborted();
        const r = await o.picker.pickAtScreen(p[0], p[1], { budgetMs: 60 });
        lauf.signal.throwIfAborted(); if (r.point) rand.push([...r.point]); await pause();
      }
      // Rand-Picks können an Rasterlücken scheitern, obwohl die Auswahl eine
      // Oberfläche trifft. Dann den belegten Stichprobenbereich markieren:
      // keine geschätzte Tiefe und kein Schließen über einzelne fehlende Ecken.
      if (ecken.length >= 3 && rand.length !== ecken.length) {
        rand.length = 0;
        const huelle = bildHuelle([...oberflaeche.keys()]);
        if (huelle.length >= 3) for (const p of huelle) rand.push(oberflaeche.get(p)!);
      }
      if (punkte.length === 0) { o.melde('Keine Oberfläche in der Auswahl getroffen.'); return; }
      const kandidaten = fotoVorfilter(o.set.images, punkte, normale);
      const t = await sucheFotos({ kandidaten, tiefe: o.tiefe, signal: lauf.signal, portion: istTouch() ? 16 : 64, budgetMs: istTouch() ? 4 : 8, pause,
        fortschritt: (n, gesamt) => { status.textContent = `Fotos prüfen: ${n} von ${gesamt}${kandidaten.length > 300 ? ` (beste 300 von ${kandidaten.length})` : ''}`; } });
      lauf.signal.throwIfAborted();
      o.ergebnis(t, punkte, rand);
      o.melde(`${t.length} Fotos gefunden${kandidaten.length > 300 ? ` – auf die besten 300 von ${kandidaten.length} Kameras begrenzt` : ''}.`);
      ende();
    } catch (e) { if (!lauf.signal.aborted) o.melde(`Fotosuche fehlgeschlagen: ${e instanceof Error ? e.message : String(e)}`); }
    finally { if (controller === lauf) { arbeitet = false; fertig.disabled = modus.disabled = flaechen.disabled = false; } }
  };
  const auswahl = async (): Promise<void> => {
    if (modus.value === 'polygon') {
      if (ecken.length < 3) { status.textContent = 'Mindestens drei Polygonecken wählen.'; return; }
      await auswerten(polygonRaster(ecken));
    } else if (modus.value === 'flaeche') {
      const m = o.messungen().find((m) => m.id === flaechen.value && m.kind === 'area');
      if (!m) { status.textContent = 'Keine Flächenmessung vorhanden.'; return; }
      const projected = m.points.map(projiziere);
      if (projected.some((p) => p === null)) { status.textContent = 'Die Fläche muss vollständig im Blick liegen.'; return; }
      ecken = projected as Bildpunkt[]; zeichnen();
      const a = polygonArea(m.points);
      await auswerten(polygonRaster(ecken), a.ok ? [...a.plane.normal] : undefined);
    } else if (modus.value === 'box') {
      const b = o.box();
      if (!b.enabled || !b.box) { status.textContent = 'Zuerst eine Schnittbox einschalten.'; return; }
      const [min, max] = b.box;
      const ecken3d: Vec3[] = [];
      for (const x of [min[0], max[0]]) for (const y of [min[1], max[1]]) for (const z of [min[2], max[2]]) ecken3d.push([x, y, z]);
      const proj = ecken3d.map(projiziere).filter((p): p is Bildpunkt => p !== null);
      if (proj.length < 3) { status.textContent = 'Die Schnittbox muss im Blick liegen.'; return; }
      ecken = bildHuelle(proj); zeichnen();
      await auswerten(polygonRaster(ecken), undefined, b.box);
    } else status.textContent = 'Eine Stelle in der 3D-Ansicht anklicken.';
  };
  modus.addEventListener('change', () => { ecken = []; zeichnen(); flaechen.hidden = modus.value !== 'flaeche'; status.textContent = ''; });
  fertig.addEventListener('click', () => { void auswahl(); }); abbrechen.addEventListener('click', abbreche);
  const unterdruecken = (ev: Event): void => { if (aktiv) { ev.preventDefault(); ev.stopImmediatePropagation(); } };
  const view = o.v.instance.domElement;
  for (const name of ['pointerdown', 'pointerup', 'dblclick', 'contextmenu']) view.addEventListener(name, unterdruecken, true);
  view.addEventListener('click', (ev) => {
    if (!aktiv) return;
    ev.preventDefault(); ev.stopImmediatePropagation(); if (arbeitet) return;
    if (modus.value === 'punkt') void auswerten([[ev.clientX, ev.clientY]]);
    else if (modus.value === 'polygon') { ecken.push([ev.clientX, ev.clientY]); zeichnen(); status.textContent = `${ecken.length} Ecken – mit „Auswahl auswerten“ oder Enter abschließen`; }
  }, true);
  document.addEventListener('keydown', (ev) => {
    if (!aktiv) return;
    if (ev.key === 'Escape') { ev.preventDefault(); ev.stopImmediatePropagation(); abbreche(); }
    else if (ev.key === 'Enter' && ev.target !== fertig && ev.target !== abbrechen && !(ev.target instanceof HTMLSelectElement)) {
      ev.preventDefault(); ev.stopImmediatePropagation(); void auswahl();
    }
  }, true);
  return { abbrechen: abbreche, starten() {
    abbreche(); o.vorbereiten(); aktiv = true; vorherControls = o.v.controls.enabled; o.v.controls.enabled = false;
    root.hidden = false; flaechen.replaceChildren();
    for (const m of o.messungen().filter((m) => m.kind === 'area')) { const option = document.createElement('option'); option.value = m.id; option.textContent = `Fläche ${m.id}`; flaechen.append(option); }
    fertig.disabled = modus.disabled = flaechen.disabled = false;
    status.textContent = 'Eine Stelle in der 3D-Ansicht anklicken.'; modus.focus();
  } };
}
