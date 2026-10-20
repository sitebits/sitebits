import { expect, it, vi } from 'vitest';
import { CompositePickSource } from '../src/compositePick';
import { bewerteTreffer } from '../src/pickMath';
import type { PickSource, RayPickResult } from '../src/rayPick';
const source = (depth: number|null, mesh=false): PickSource => ({
 pickAtEvent: vi.fn(), warm: vi.fn(),
 pickAtScreen: vi.fn(async (): Promise<RayPickResult> => ({point: depth === null ? null : [0,0,depth], info:{level:depth === null?'none':'fine',ms:0,wideCandidates:0,nearCandidates:0,tiefeM:depth ?? undefined,...(mesh?{source:'mesh',layerId:'modell'}:{})}})),
 vorschauAtScreen: () => ({punktWelt:depth === null?null:[0,0,depth],tiefeM:depth,mitteTiefen:depth===null?[]:[depth],fensterTiefen:depth===null?[]:[depth],ms:0}),
});
it('nächster Treffer gewinnt unabhängig von Reihenfolge', async () => {
 const picker=new CompositePickSource({pointcloud:source(2),cloudLayerId:'punkte',meshes:[source(1,true)],preference:()=> 'auto',panorama:()=>false});
 expect((await picker.pickAtScreen(0,0)).info.source).toBe('mesh');
});
it.each(['auto','pointcloud','mesh'] as const)('5-mm-Gleichstand: %s', async (pref) => {
 const picker=new CompositePickSource({pointcloud:source(1.004),cloudLayerId:'punkte',meshes:[source(1,true)],preference:()=>pref,panorama:()=>false});
 expect((await picker.pickAtScreen(0,0)).info.source).toBe(pref==='mesh'?'mesh':'pointcloud');
});
it('außerhalb Gleichstand zählt die Tiefe, auch bei bevorzugter Wolke', async () => {
 const picker=new CompositePickSource({pointcloud:source(1.006),cloudLayerId:'punkte',meshes:[source(1,true)],preference:()=> 'pointcloud',panorama:()=>false});
 expect((await picker.pickAtScreen(0,0)).info.source).toBe('mesh');
});
it('Panorama fragt ausschließlich Wolke ab und kennzeichnet ihre Ebene', async () => {
 const mesh=source(.5,true); const picker=new CompositePickSource({pointcloud:source(1),cloudLayerId:'punkte',meshes:[mesh],preference:()=> 'mesh',panorama:()=>true});
 const r=await picker.pickAtScreen(0,0); expect(r.info.layerId).toBe('punkte'); expect(mesh.pickAtScreen).not.toHaveBeenCalled();
});
it('kein Treffer von ausgeblendeter Quelle verdrängt keinen gültigen Treffer', async () => {
 const picker=new CompositePickSource({pointcloud:source(null),cloudLayerId:'punkte',meshes:[source(1,true)],preference:()=> 'auto',panorama:()=>false});
 expect((await picker.pickAtScreen(0,0)).info.source).toBe('mesh');
});

const unfertig = (front: number|null): PickSource => ({
 pickAtEvent: vi.fn(), warm: vi.fn(),
 pickAtScreen: vi.fn(async (): Promise<RayPickResult> => ({point:null,info:{level:'none',ms:0,wideCandidates:0,nearCandidates:0,source:'mesh',
  reason:front===null?'empty':'imprecise',...(front===null?{}:{frontDepthM:front})}})),
 vorschauAtScreen: () => ({punktWelt:null,tiefeM:null,mitteTiefen:[],fensterTiefen:[],ms:0}),
});
it('unfertiges Mesh vor der Wolke: kein Ersatzwert von der Wolke dahinter', async () => {
 const picker=new CompositePickSource({pointcloud:source(3),cloudLayerId:'punkte',meshes:[unfertig(1)],preference:()=> 'auto',panorama:()=>false});
 const r=await picker.pickAtScreen(0,0); expect(r.point).toBeNull(); expect(r.info.reason).toBe('imprecise');
});
it('unfertiges Mesh hinter oder neben dem Wolkentreffer: Wolke gilt', async () => {
 for (const mesh of [unfertig(5), unfertig(null)]) {
  const picker=new CompositePickSource({pointcloud:source(3),cloudLayerId:'punkte',meshes:[mesh],preference:()=> 'auto',panorama:()=>false});
  expect((await picker.pickAtScreen(0,0)).info.source).toBe('pointcloud');
 }
});
it('Vorschau: Wolke hinter dem Mesh ist verdeckt und keine zweite Schicht', () => {
 const picker=new CompositePickSource({pointcloud:source(4),cloudLayerId:'punkte',meshes:[source(2,true)],preference:()=> 'auto',panorama:()=>false});
 const v=picker.vorschauAtScreen(0,0);
 expect(v.fensterTiefen).toEqual([2]);
 expect(bewerteTreffer({trefferTiefe:2,level:'fine',mitteTiefen:v.mitteTiefen,fensterTiefen:v.fensterTiefen}).art).toBe('eindeutig');
});
it('Vorschau: Wolke vor dem Mesh bleibt sichtbar', () => {
 const picker=new CompositePickSource({pointcloud:source(1),cloudLayerId:'punkte',meshes:[source(2,true)],preference:()=> 'auto',panorama:()=>false});
 expect(picker.vorschauAtScreen(0,0).fensterTiefen).toEqual([1,2]);
});
