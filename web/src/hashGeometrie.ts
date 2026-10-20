// Messungen für den Deep-Link (m=, g=, o=) aus der Messliste — ausgelagert aus main.ts (Final-Fix C/D), damit
// der Plattformfilter testbar ist. Einzelmodus: alle Messungen wie bisher. Plattformmodus: nur Messungen, die
// NICHT an eine vom Server bestätigte Messung gebunden sind (imLink aus MessungSync) — der Serverstand gehört
// nicht in den Link: sonst sprengten große Projekte die Eckpunktgrenze (decodeHash verwirft den ganzen Link),
// alte Links/F5 holten von Kollegen gelöschte Messungen zurück, und die mm-Rundung des Links käme per PATCH
// zurück an den Server. m, g und o entstehen aus DERSELBEN gefilterten Liste (sonst passt o= nicht zu m=/g=).
import type { Vec3 } from './manifest';
import type { HashGeometry, OrderKind, ViewState } from './hash';
import type { Measurement } from './measureStore';

export function hashGeometrie(
  alle: readonly Measurement[],
  imLink?: (m: Measurement) => boolean,
): Pick<ViewState, 'm' | 'g' | 'order'> {
  const list = imLink ? alle.filter(imLink) : alle;
  return {
    m: list.flatMap((x): [Vec3, Vec3][] => (x.kind === 'distance' ? [[x.a, x.b]] : [])),
    g: list.flatMap((x): HashGeometry[] => (x.kind === 'distance' ? [] : [{ kind: x.kind, points: [...x.points] }])),
    order: list.map((x): OrderKind => x.kind),
  };
}
