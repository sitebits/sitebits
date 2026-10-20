// Foto-Fenster (Panel „Foto“): zeigt das angeklickte orientierte Foto, Zoom per Mausrad/
// Tasten, Verschieben per Ziehen, vorige/nächste Kamera der Gruppe, „Durch die Kamera“ mit
// Überblendregler. Stufe nach Anzeigegröße (imageMath.stufeFuer), über der größten Einzelstufe
// Kacheln (kachelEbene/sichtbareKacheln). Bilder nur als <img src> vom eigenen Ursprung (CSP img-src
// 'self'), Texte nur über textContent. `data-tasten-eigen`: Tasten im Fenster gehören dem Fenster,
// nicht der globalen Belegung (keymap.ts).
import type { ImageSet, OrientedImage } from './images';
import { levelUrl, tileUrl } from './images';
import type { FotoAnsicht } from './imageMath';
import { einpassen, kachelEbene, nachGroesse, sichtbareKacheln, stufeFuer, zoomUm } from './imageMath';
import { attachFotoVorschau } from './fotoVorschau';
import type { FotoTreffer } from './fotoZuordnung';
import type { Vec3 } from './manifest';
import { projiziereFoto } from './fotoZuordnung';
import type { ThroughCamera } from './throughCamera';

const ZOOM_SCHRITT = 1.25;
const MAX_ZOOM_GEGEN_VOLL = 4;

export interface PhotoPanel {
  show(id: string | null): void;
  current(): string | null;
  auswahl(treffer: readonly FotoTreffer[] | null, punkte: readonly Vec3[], umriss?: readonly Vec3[]): void;
  onSelect(cb: (id: string | null) => void): void;
}

const el = <K extends keyof HTMLElementTagNameMap>(tag: K, text?: string, cls?: string): HTMLElementTagNameMap[K] => {
  const e = document.createElement(tag);
  if (text !== undefined) e.textContent = text;
  if (cls) e.className = cls;
  return e;
};

export function attachPhotoPanel(root: HTMLElement, o: { pkgBase: string; set: ImageSet; durch: ThroughCamera; schweben?(): boolean; strahl?(bild: OrientedImage, x: number, y: number): void; strahlAus?(): void; auswahlAus?(): void }): PhotoPanel {
  const { pkgBase, set, durch } = o;
  const byId = new Map(set.images.map((i) => [i.id, i] as const));
  const listeners: ((id: string | null) => void)[] = [];
  root.dataset.tastenEigen = '';
  root.classList.add('sv-foto');

  const kopf = el('div', undefined, 'sv-foto-kopf');
  const titel = el('span', 'Kein Foto gewählt', 'sv-foto-titel');
  titel.setAttribute('aria-live', 'polite');
  const vorBtn = el('button', '‹');
  vorBtn.type = 'button';
  vorBtn.setAttribute('aria-label', 'Vorige Kamera der Gruppe (Pfeil links)');
  const nachBtn = el('button', '›');
  nachBtn.type = 'button';
  nachBtn.setAttribute('aria-label', 'Nächste Kamera der Gruppe (Pfeil rechts)');
  const durchBtn = el('button', 'Durch die Kamera');
  durchBtn.type = 'button';
  durchBtn.setAttribute('aria-pressed', 'false');
  const blend = el('input');
  blend.type = 'range';
  blend.min = '0';
  blend.max = '1';
  blend.step = '0.05';
  blend.setAttribute('aria-label', 'Überblenden Foto ↔ 3D');
  blend.hidden = true;
  const teilen = el('button', 'Ansicht teilen aus'); teilen.type = 'button';
  teilen.addEventListener('click', () => { teilen.textContent = o.schweben?.() ? 'Ansicht teilen aus' : 'Ansicht teilen'; });
  const aufheben = el('button', 'Auswahl aufheben'); aufheben.type = 'button'; aufheben.hidden = true;
  aufheben.addEventListener('click', () => { api.auswahl(null, []); o.auswahlAus?.(); });
  kopf.append(vorBtn, titel, nachBtn, durchBtn, blend, teilen, aufheben);

  const fenster = el('div', undefined, 'sv-foto-fenster');
  fenster.tabIndex = 0;
  fenster.setAttribute('role', 'img');
  fenster.setAttribute('aria-label', 'Foto – Mausrad oder Plus/Minus zoomt, Ziehen verschiebt, 0 passt ein');
  const leer = el('p', 'Kamera in der 3D-Ansicht anklicken.', 'sv-foto-leer');
  const bild = el('img');
  bild.alt = '';
  bild.draggable = false;
  bild.decoding = 'async';
  const kacheln = el('div', undefined, 'sv-foto-kacheln');
  const marken = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  marken.classList.add('sv-foto-marken');
  marken.setAttribute('aria-hidden', 'true');
  fenster.append(leer, bild, kacheln, marken);
  const leiste = el('div', undefined, 'sv-foto-leiste'); leiste.id = 'foto-bildleiste';
  const status = el('p', '', 'sv-foto-status'); status.setAttribute('aria-live', 'polite');
  root.replaceChildren(kopf, fenster, status, leiste);
  let auswahl: readonly FotoTreffer[] | null = null, punkte: readonly Vec3[] = [], umriss: readonly Vec3[] = [];
  const vorschau = attachFotoVorschau(leiste, pkgBase, (id) => { api.show(id); });

  let aktuell: OrientedImage | null = null;
  let ansicht: FotoAnsicht = { scale: 1, x: 0, y: 0 };
  let eingepasst = true;
  const kachelCache = new Map<string, HTMLImageElement>();

  const groesse = (): { b: number; h: number } => ({ b: fenster.clientWidth || 1, h: fenster.clientHeight || 1 });

  const zeichnen = (): void => {
    const img = aktuell;
    leer.hidden = img !== null;
    bild.hidden = img === null;
    kacheln.hidden = true;
    marken.replaceChildren();
    if (!img) return;
    const bildpunkt = (p: Vec3): { x: number; y: number } | null => {
      const q = projiziereFoto(img, p, null);
      return q ? { x: ansicht.x + q.x * ansicht.scale, y: ansicht.y + q.y * ansicht.scale } : null;
    };
    for (const p of punkte) {
      const q = bildpunkt(p); if (!q) continue;
      const kreis = document.createElementNS(marken.namespaceURI, 'circle');
      kreis.setAttribute('cx', String(q.x)); kreis.setAttribute('cy', String(q.y)); kreis.setAttribute('r', '5');
      marken.append(kreis);
    }
    const rand = umriss.map(bildpunkt);
    if (rand.length >= 3 && rand.every((p) => p !== null)) {
      const linie = document.createElementNS(marken.namespaceURI, 'polygon');
      linie.setAttribute('points', rand.map((p) => `${p!.x},${p!.y}`).join(' ')); marken.append(linie);
    }
    const { width: W, height: H } = img.intrinsics;
    const dpr = globalThis.devicePixelRatio || 1;
    const benoetigt = Math.max(W, H) * ansicht.scale * dpr;
    const z = kachelEbene({ width: W, height: H, levels: img.levels, tiles: img.tiles }, benoetigt);
    if (z === null) {
      const stufe = stufeFuer(img.levels, benoetigt);
      const url = levelUrl(pkgBase, img, stufe);
      if (bild.getAttribute('src') !== url) bild.src = url;
      bild.style.width = `${W * ansicht.scale}px`;
      bild.style.height = `${H * ansicht.scale}px`;
      bild.style.transform = `translate(${ansicht.x}px, ${ansicht.y}px)`;
      return;
    }
    // Kacheln: die größte Einzelstufe bleibt als Hintergrund stehen, Kacheln darüber.
    const hintergrund = levelUrl(pkgBase, img, img.levels[img.levels.length - 1]!);
    if (bild.getAttribute('src') !== hintergrund) bild.src = hintergrund;
    kacheln.hidden = false;
    const f = 2 ** z;
    const eb = Math.ceil(W / f);
    const eh = Math.ceil(H / f);
    const groesseK = img.tiles!.size;
    const { b, h } = groesse();
    const gebraucht = new Set<string>();
    for (const [x, y] of sichtbareKacheln({ scale: ansicht.scale * f, x: ansicht.x, y: ansicht.y }, b, h, eb, eh, groesseK)) {
      const key = `${img.id}/${z}/${x}/${y}`;
      gebraucht.add(key);
      let k = kachelCache.get(key);
      if (!k) {
        k = el('img');
        k.alt = '';
        k.draggable = false;
        k.src = tileUrl(pkgBase, img, z, x, y);
        kachelCache.set(key, k);
        kacheln.append(k);
      }
      const s = ansicht.scale * f;
      k.style.transform = `translate(${ansicht.x + x * groesseK * s}px, ${ansicht.y + y * groesseK * s}px)`;
      k.style.width = `${Math.min(groesseK, eb - x * groesseK) * s}px`;
      k.style.height = `${Math.min(groesseK, eh - y * groesseK) * s}px`;
    }
    for (const [key, k] of kachelCache) {
      if (!gebraucht.has(key)) {
        k.remove();
        kachelCache.delete(key);
      }
    }
    bild.style.width = `${W * ansicht.scale}px`;
    bild.style.height = `${H * ansicht.scale}px`;
    bild.style.transform = `translate(${ansicht.x}px, ${ansicht.y}px)`;
  };

  const einpassenJetzt = (): void => {
    if (!aktuell) return;
    const { b, h } = groesse();
    ansicht = einpassen(aktuell.intrinsics.width, aktuell.intrinsics.height, b, h);
    eingepasst = true;
    zeichnen();
  };

  const zoom = (faktor: number, px?: number, py?: number): void => {
    if (!aktuell) return;
    const { b, h } = groesse();
    const neu = zoomUm(ansicht, faktor, px ?? b / 2, py ?? h / 2);
    const fit = einpassen(aktuell.intrinsics.width, aktuell.intrinsics.height, b, h).scale;
    if (neu.scale < fit * 0.999) return einpassenJetzt();
    if (neu.scale > MAX_ZOOM_GEGEN_VOLL) return;
    ansicht = neu;
    eingepasst = false;
    zeichnen();
  };

  const gruppe = (): OrientedImage[] => auswahl ? auswahl.map((t) => t.bild) : (aktuell ? set.images.filter((i) => i.group === aktuell!.group) : []);
  const bildleiste = (): void => {
    vorschau.setze(auswahl ? auswahl.map((t) => ({ bild: t.bild, zusatz: `${t.sichtbar.length} von ${t.pixel.length} Stellen · ${t.mmProPx.toLocaleString('de-DE', { maximumFractionDigits: 1 })} mm/px${t.ungeprueft.length ? ' · weitere Stellen ungeprüft' : ''}` })) : gruppe().map((bild) => ({ bild })), aktuell?.id);
    aufheben.hidden = auswahl === null;
    status.textContent = auswahl ? `${auswahl.length} Fotos gefunden` : '';
  };
  const blaettern = (richtung: 1 | -1): void => {
    const g = gruppe();
    if (!aktuell || g.length < 2) return;
    const i = g.findIndex((x) => x.id === aktuell!.id);
    api.show(g[(i + richtung + g.length) % g.length]!.id);
  };

  const kopfAktualisieren = (): void => {
    titel.textContent = aktuell ? `${aktuell.label}${aktuell.group ? ` · ${aktuell.group}` : ''}` : 'Kein Foto gewählt';
    const g = gruppe();
    vorBtn.disabled = nachBtn.disabled = g.length < 2;
    durchBtn.disabled = !aktuell;
    durchBtn.setAttribute('aria-pressed', String(durch.active));
    blend.hidden = !durch.active;
    blend.value = String(durch.blend());
  };

  vorBtn.addEventListener('click', () => blaettern(-1));
  nachBtn.addEventListener('click', () => blaettern(1));
  durchBtn.addEventListener('click', () => {
    if (durch.active) durch.leave();
    else if (aktuell) durch.enter(aktuell);
  });
  blend.addEventListener('input', () => durch.setBlend(Number(blend.value)));
  durch.onChange(kopfAktualisieren);

  fenster.addEventListener('wheel', (ev) => {
    ev.preventDefault();
    const r = fenster.getBoundingClientRect();
    zoom(ev.deltaY < 0 ? ZOOM_SCHRITT : 1 / ZOOM_SCHRITT, ev.clientX - r.left, ev.clientY - r.top);
  }, { passive: false });
  let zug: { x: number; y: number; vx: number; vy: number } | null = null;
  fenster.addEventListener('pointerdown', (ev) => {
    if (!aktuell) return;
    o.strahlAus?.();
    fenster.setPointerCapture(ev.pointerId);
    zug = { x: ev.clientX, y: ev.clientY, vx: ansicht.x, vy: ansicht.y };
  });
  fenster.addEventListener('pointerleave', () => o.strahlAus?.());
  fenster.addEventListener('pointermove', (ev) => {
    if (aktuell && !zug) {
      const r = fenster.getBoundingClientRect();
      o.strahl?.(aktuell, (ev.clientX - r.left - ansicht.x) / ansicht.scale, (ev.clientY - r.top - ansicht.y) / ansicht.scale);
    }
    if (!zug) return;
    ansicht = { ...ansicht, x: zug.vx + ev.clientX - zug.x, y: zug.vy + ev.clientY - zug.y };
    if (ev.clientX !== zug.x || ev.clientY !== zug.y) eingepasst = false;
    zeichnen();
  });
  const zugEnde = (): void => {
    zug = null;
  };
  fenster.addEventListener('pointerup', zugEnde);
  fenster.addEventListener('pointercancel', zugEnde);
  root.addEventListener('keydown', (ev) => {
    if (ev.target instanceof HTMLInputElement) return;
    const t = ev.key;
    if (t === '+' || t === '=') zoom(ZOOM_SCHRITT);
    else if (t === '-') zoom(1 / ZOOM_SCHRITT);
    else if (t === '0') einpassenJetzt();
    else if (t === 'ArrowLeft') blaettern(-1);
    else if (t === 'ArrowRight') blaettern(1);
    else if (t === 'Escape' && durch.active) durch.leave();
    else return;
    ev.preventDefault();
    ev.stopPropagation();
  });
  // Größenänderung behält Zoom und Bildmitte (vorher: jedes Mal neu eingepasst).
  let letzteGroesse = groesse();
  new ResizeObserver(() => {
    const neu = groesse();
    if (aktuell) {
      ansicht = nachGroesse(ansicht, letzteGroesse, neu, aktuell.intrinsics.width, aktuell.intrinsics.height, eingepasst);
      zeichnen();
    }
    letzteGroesse = neu;
  }).observe(fenster);

  const api: PhotoPanel = {
    auswahl(t, p, rand = []) { auswahl = t; punkte = p; umriss = rand; kopfAktualisieren(); bildleiste(); zeichnen(); },
    show(id) {
      const img = id ? byId.get(id) ?? null : null;
      if (img === aktuell) return;
      o.strahlAus?.();
      aktuell = img;
      for (const k of kachelCache.values()) k.remove();
      kachelCache.clear();
      bild.removeAttribute('src');
      einpassenJetzt();
      zeichnen();
      kopfAktualisieren();
      bildleiste();
      for (const cb of listeners) cb(aktuell?.id ?? null);
      if (durch.active && img) durch.enter(img);
      else if (durch.active) durch.leave();
    },
    current: () => aktuell?.id ?? null,
    onSelect(cb) {
      listeners.push(cb);
    },
  };
  kopfAktualisieren();
  zeichnen();
  return api;
}
