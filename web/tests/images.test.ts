import { describe, expect, it, vi } from 'vitest';
import { ImagesError, levelUrl, loadImages, parseImages, tileUrl } from '../src/images';

export const bild = (id: string, over: Record<string, unknown> = {}): Record<string, unknown> => ({
  id, label: `L${id}`, group: 'Nadir',
  pose: { translation: [400000.5, 5800000.25, 30], rotationQuat: [1, 0, 0, 0] },
  intrinsics: { focalPx: 2800, width: 3700, height: 2080 },
  levels: [512, 2048, 3700], tiles: null, ...over,
});
export const imagesJson = (images = [bild('F0001'), bild('F0002', { group: 'Fassade' })]): Record<string, unknown> => ({
  schemaVersion: 1,
  groups: [{ id: 'Fassade', title: 'Fassade', count: 1 }, { id: 'Nadir', title: 'Nadir', count: 1 }],
  anonymization: { required: true, status: 'unreviewed' },
  provenance: {},
  images,
});

describe('images.json', () => {
  it('liest Fotos, Gruppen und Anonymisierungsstatus', () => {
    const s = parseImages(imagesJson());
    expect(s.images.map((i) => i.id)).toEqual(['F0001', 'F0002']);
    expect(s.groups.map((g) => g.id)).toEqual(['Fassade', 'Nadir']);
    expect(s.anonymization.status).toBe('unreviewed');
    expect(s.images[0]!.pose.translation).toEqual([400000.5, 5800000.25, 30]);
    expect(s.images[0]!.intrinsics.focalPx).toBe(2800);
  });
  it.each([
    ['Version', (j: Record<string, unknown>) => { j.schemaVersion = 2; }],
    ['ID', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.id = '../x'; }],
    ['doppelt', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[1]!.id = 'F0001'; }],
    ['Pose', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.pose = { translation: [1, 2] }; }],
    ['Intrinsik', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.intrinsics = { focalPx: 0, width: 10, height: 10 }; }],
    ['Stufen', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.levels = [2048, 512]; }],
    ['Stufen', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.levels = [5000]; }],
    ['Kacheln', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.tiles = { size: 0, levels: 1 }; }],
    ['Gruppe', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.group = 'Unbekannt'; }],
    ['Revision', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.rev = '../../x'; }],
    ['Revision', (j: Record<string, unknown>) => { (j.images as Record<string, unknown>[])[0]!.rev = 'ABCDEF0123456789'; }],
  ])('weist kaputte Daten ab (%s)', (wort, kaputt) => {
    const j = imagesJson();
    kaputt(j);
    expect(() => parseImages(j)).toThrow(ImagesError);
    expect(() => parseImages(j)).toThrow(new RegExp(wort, 'i'));
  });
  it('baut URLs unter fotos/<id>/', () => {
    const s = parseImages(imagesJson());
    expect(levelUrl('https://x/data/p/', s.images[0]!, 2048)).toBe('https://x/data/p/fotos/F0001/2048.jpg');
    expect(tileUrl('https://x/data/p/', s.images[0]!, 1, 2, 3)).toBe('https://x/data/p/fotos/F0001/tiles/1/2_3.jpg');
  });
  it('mit Revision (M7): URLs unter fotos/<id>/<rev>/ — neuer Inhalt, neue URL', () => {
    const s = parseImages(imagesJson([bild('F0001', { rev: '0123456789abcdef' }), bild('F0002', { group: 'Fassade' })]));
    expect(levelUrl('https://x/data/p/', s.images[0]!, 2048)).toBe('https://x/data/p/fotos/F0001/0123456789abcdef/2048.jpg');
    expect(tileUrl('https://x/data/p/', s.images[0]!, 1, 2, 3)).toBe('https://x/data/p/fotos/F0001/0123456789abcdef/tiles/1/2_3.jpg');
    expect(levelUrl('https://x/data/p/', s.images[1]!, 512)).toBe('https://x/data/p/fotos/F0002/512.jpg');
  });
  it('404 (keine Freigabe für diese Rolle) ist kein Fehler: null', async () => {
    const f = vi.fn(async () => new Response('nicht gefunden', { status: 404 }));
    expect(await loadImages('https://x/data/p/', { url: 'images.json' }, f as unknown as typeof fetch)).toBeNull();
  });
  it('andere Fehler werfen ImagesError', async () => {
    const f = vi.fn(async () => new Response('x', { status: 500 }));
    await expect(loadImages('https://x/data/p/', { url: 'images.json' }, f as unknown as typeof fetch))
      .rejects.toThrow(ImagesError);
  });
});
