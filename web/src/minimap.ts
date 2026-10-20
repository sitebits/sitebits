// Minikarte: Draufsicht topview.png (same-origin <img>, CSP img-src 'self') mit
// Standpunkt-Punkten; Klick → nächster Standpunkt (onPick). Ohne topview-Block (Paket ohne
// `overview`) nur Umriss aus boundsWorld + Punkte. Blickrichtung des aktiven Standpunkts als Strich
// (yaw 0 = +X = nach rechts, gegen den Uhrzeigersinn; Canvas-Y zeigt nach unten).
// Kartenrahmen = Draufsicht, bei Standpunkten außerhalb erweitert (frameFor): die
// Draufsicht sitzt dann georeferenziert in einem größeren dunklen Rahmen, alle Punkte bleiben klickbar.
import type { Project, Vec3 } from './manifest';
import { resolveUrl } from './manifest';
import { boundsTopview, canvasToWorld, fitTopview, frameFor, worldToCanvas } from './minimapMath';
import type { Station } from './stations';
import { nearestStation } from './stationMath';

const W = 280;
const H = 210;

export interface Minimap {
  readonly canvas: HTMLCanvasElement;
  setActive(id: string | null, yawDeg?: number | null): void;
  /** Seitenkoordinaten des Punkts eines Standpunkts (E2E). */
  canvasPointOf(id: string): { x: number; y: number } | null;
}

export function attachMinimap(
  host: HTMLElement,
  pkgBase: string,
  m: Pick<Project, 'topview'>,
  bounds: [Vec3, Vec3],
  stations: readonly Station[],
  onPick: (id: string) => void,
): Minimap {
  const t = m.topview ?? boundsTopview(bounds);
  const frame = frameFor(
    t,
    stations.map((s): [number, number] => [s.pose.translation[0], s.pose.translation[1]]),
  );
  const fit = fitTopview(frame, W, H);
  // Rechteck der Draufsicht (bzw. des Umrisses) im Canvas.
  const [ix, iy] = worldToCanvas(frame, fit, t.upperLeftWorld[0], t.upperLeftWorld[1]);
  const iw = t.widthPx * fit.scale;
  const ih = t.heightPx * fit.scale;
  const dpr = window.devicePixelRatio || 1;
  const canvas = document.createElement('canvas');
  canvas.width = Math.round(W * dpr);
  canvas.height = Math.round(H * dpr);
  canvas.style.width = `${W}px`;
  canvas.style.height = `${H}px`;
  canvas.title = 'Minikarte – Klick springt zum nächsten Standpunkt';
  host.appendChild(canvas);
  const ctx = canvas.getContext('2d');
  let img: HTMLImageElement | null = null;
  let active: string | null = null;
  let yaw: number | null = null;

  const draw = (): void => {
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = '#1b1b1b';
    ctx.fillRect(0, 0, W, H);
    if (img && img.complete && img.naturalWidth > 0) ctx.drawImage(img, ix, iy, iw, ih);
    else {
      ctx.strokeStyle = '#555';
      ctx.strokeRect(ix, iy, iw, ih);
    }
    for (const s of stations) {
      const [cx, cy] = worldToCanvas(frame, fit, s.pose.translation[0], s.pose.translation[1]);
      const isActive = s.id === active;
      ctx.beginPath();
      ctx.arc(cx, cy, isActive ? 5 : 3.5, 0, 2 * Math.PI);
      ctx.fillStyle = isActive ? '#ffcc00' : s.panorama ? '#33d1ff' : '#888888';
      ctx.fill();
      if (isActive && yaw !== null) {
        const a = (yaw * Math.PI) / 180;
        ctx.beginPath();
        ctx.moveTo(cx, cy);
        ctx.lineTo(cx + 14 * Math.cos(a), cy - 14 * Math.sin(a));
        ctx.strokeStyle = '#ffcc00';
        ctx.lineWidth = 2;
        ctx.stroke();
      }
    }
  };

  if (m.topview) {
    img = new Image();
    img.onload = draw;
    img.onerror = () => {
      img = null;
      draw();
    };
    img.src = resolveUrl(pkgBase, m.topview.url);
  }

  canvas.addEventListener('click', (ev) => {
    const r = canvas.getBoundingClientRect();
    const [x, y] = canvasToWorld(frame, fit, ev.clientX - r.left, ev.clientY - r.top);
    const s = nearestStation(stations, [x, y]);
    if (s) onPick(s.id);
  });
  draw();

  return {
    canvas,
    setActive: (id, yawDeg = null) => {
      active = id;
      yaw = yawDeg;
      draw();
    },
    canvasPointOf: (id) => {
      const s = stations.find((x) => x.id === id);
      if (!s) return null;
      const [cx, cy] = worldToCanvas(frame, fit, s.pose.translation[0], s.pose.translation[1]);
      const r = canvas.getBoundingClientRect();
      return { x: r.left + cx, y: r.top + cy };
    },
  };
}
