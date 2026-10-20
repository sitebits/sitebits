import { expect, it, vi } from 'vitest';
import { createRayPicker } from '../src/rayPick';
import { CompositePickSource } from '../src/compositePick';
import { OHNE_FILTER } from '../src/klassen';
import type { Viewer } from '../src/viewer';
import type { PickSource } from '../src/rayPick';

it('ohne Wolke sucht Messen/Fangen niemals in der sichtbaren Splat-Szene', async () => {
  const scenePick = vi.fn(() => { throw new Error('Sichtschicht darf nicht gepickt werden'); });
  const viewer = { cloud: null, instance: { pickObjectsAt: scenePick }, punktfilter: () => OHNE_FILTER } as unknown as Pick<Viewer, 'cloud' | 'instance' | 'punktfilter'>;
  const cloud = createRayPicker(viewer);
  const picker = new CompositePickSource({ pointcloud: cloud, cloudLayerId: '', meshes: [],
    preference: () => 'auto', panorama: () => false });
  const result = await picker.pickAtScreen(10, 10, { snapEdges: true });
  expect(result.point).toBeNull();
  expect(result.info.level).toBe('none');
  expect(picker.vorschauAtScreen(10, 10).punktWelt).toBeNull();
  expect(scenePick).not.toHaveBeenCalled();
});

it('ohne Wolke liefert weiterhin die messbare Mesh-Ebene den Treffer', async () => {
  const cloud = createRayPicker({ cloud: null, instance: {}, punktfilter: () => OHNE_FILTER } as unknown as Pick<Viewer, 'cloud' | 'instance' | 'punktfilter'>);
  const mesh: PickSource = { warm: () => undefined, pickAtEvent: vi.fn(),
    pickAtScreen: async () => ({ point: [1, 2, 3], info: { source: 'mesh', layerId: 'modell',
      level: 'fine', tiefeM: 1, ms: 0, wideCandidates: 1, nearCandidates: 1 } }),
    vorschauAtScreen: () => ({ punktWelt: [1, 2, 3], tiefeM: 1, mitteTiefen: [1], fensterTiefen: [1], ms: 0 }),
  };
  const picker = new CompositePickSource({ pointcloud: cloud, cloudLayerId: '', meshes: [mesh],
    preference: () => 'auto', panorama: () => false });
  expect(await picker.pickAtScreen(0, 0, { snapEdges: true })).toMatchObject({ point: [1, 2, 3], info: { source: 'mesh' } });
});
