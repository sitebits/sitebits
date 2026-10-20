import { expect, it, vi } from 'vitest';
import { BoxGeometry, DoubleSide, Group, Mesh, MeshBasicMaterial, PerspectiveCamera, PlaneGeometry } from 'three';
import { MeshPickSource } from '../src/meshPick';
import type { MeshLayerHandle } from '../src/meshLayer';
import type { ClipBoxState } from '../src/clipBoxMath';
const camera = new PerspectiveCamera(45, 1, .01, 100);
camera.position.set(500000, 5800000, 103); camera.lookAt(500000,5800000,100); camera.updateMatrixWorld(true);
const mesh = new Mesh(new BoxGeometry(1,1,1), new MeshBasicMaterial({side:DoubleSide}));
mesh.position.set(500000,5800000,100); mesh.updateMatrixWorld(true);
const fixture = (stage: 'full'|'preview' = 'full', visible = true, clip?: ClipBoxState) => {
 const requestFull = vi.fn();
 const layer = {id:'modell',stage,visible,failure:null,pickMeshes:stage === 'full' ? [mesh] : [],occluderMeshes:visible ? [mesh] : [],requestFull} as unknown as MeshLayerHandle;
 const picker = new MeshPickSource({camera:()=>camera,rect:()=>({left:0,top:0,width:1000,height:1000}),layers:()=>[layer],clip:()=>clip});
 return {picker,requestFull};
};
it('UTM-Würfel: Vollstufen-Flächentreffer ≤0,1 mm mit Quelle und Dreieck', async () => {
 const r=await fixture().picker.pickAtScreen(500,500);
 expect(r.point![2]).toBeCloseTo(100.5,6); expect(r.info.source).toBe('mesh'); expect(r.info.layerId).toBe('modell');
 expect(r.info.level).toBe('fine'); expect(r.info.triangle?.index).toBeGreaterThanOrEqual(0);
});
it('Vorschau liefert keinen Messwert und fordert Vollstufe an', async () => {
 const {picker,requestFull}=fixture('preview'); const r=await picker.pickAtScreen(500,500);
 expect(r.point).toBeNull(); expect(r.info.reason).toBe('imprecise'); expect(requestFull).toHaveBeenCalledOnce();
 // Tiefe der sichtbaren Vorschaufläche: der Verbund entscheidet damit, ob sie vor einem Wolkentreffer liegt.
 expect(r.info.frontDepthM).toBeCloseTo(2.5,5);
});
it('Vorschaustufe neben dem Strahl sperrt nicht', async () => {
 const {picker,requestFull}=fixture('preview'); const r=await picker.pickAtScreen(5,5);
 expect(r.point).toBeNull(); expect(r.info.reason).toBe('empty'); expect(requestFull).toHaveBeenCalledOnce();
});
it('Ebene mit Ladefehler sperrt die übrigen Ebenen nicht', async () => {
 const kaputt={id:'kaputt',stage:'none',visible:true,failure:'load-error',pickMeshes:[],occluderMeshes:[],requestFull:vi.fn()} as unknown as MeshLayerHandle;
 const gut={id:'modell',stage:'full',visible:true,failure:null,pickMeshes:[mesh],occluderMeshes:[mesh],requestFull:vi.fn()} as unknown as MeshLayerHandle;
 const picker=new MeshPickSource({camera:()=>camera,rect:()=>({left:0,top:0,width:1000,height:1000}),layers:()=>[kaputt,gut],clip:()=>undefined});
 const r=await picker.pickAtScreen(500,500);
 expect(r.info.level).toBe('fine'); expect(r.info.layerId).toBe('modell'); expect(kaputt.requestFull).not.toHaveBeenCalled();
});
// Würfeloberseite z=100,5 in 2,5 m: Ecke (+0,5|+0,5) liegt bei Pixel ≈ (741|259).
it('Vertex-Fang nur bei eingeschaltetem Fang (E), sonst Flächentreffer', async () => {
 const {picker}=fixture();
 const ohne=await picker.pickAtScreen(737,263); expect(ohne.info.snap?.result).toBe('none');
 expect(ohne.point![0]).toBeLessThan(500000.5); expect(ohne.point![2]).toBeCloseTo(100.5,6);
 const mit=await picker.pickAtScreen(737,263,{snapEdges:true}); expect(mit.info.snap?.result).toBe('vertex');
 expect(mit.point![0]).toBeCloseTo(500000.5,6); expect(mit.point![1]).toBeCloseTo(5800000.5,6);
});
it('ausgeblendete Ebene liefert nichts', async () => {
 expect((await fixture('full',false).picker.pickAtScreen(500,500)).info.reason).toBe('hidden');
});
it('Schnittbox verwirft beide Würfelflächen', async () => {
 const r=await fixture('full',true,{enabled:true,box:[[0,0,0],[1,1,1]]}).picker.pickAtScreen(500,500);
 expect(r.point).toBeNull();
});
it('Vorschau liefert nur die sichtbare (vorderste) Fläche, keine Messung', () => {
 // Rückseite des Würfels (3,5 m) ist verdeckt: als zweite „Schicht“ löste sie sonst eine Rückfrage aus.
 const r=fixture().picker.vorschauAtScreen(500,500);
 expect(r.tiefeM).toBeCloseTo(2.5,5); expect(r.mitteTiefen).toHaveLength(1); expect(r.fensterTiefen).toHaveLength(1);
 expect(r.mitteTiefen[0]).toBeCloseTo(2.5,5);
});

it('Dreieckdiagnose benennt bei mehreren Primitiven das getroffene Objekt', async () => {
 const back = mesh.clone(); back.position.z = 98; back.updateMatrixWorld(true);
 const layer = {id:'modell',stage:'full',visible:true,pickMeshes:[back,mesh],requestFull:vi.fn()} as unknown as MeshLayerHandle;
 const picker = new MeshPickSource({camera:()=>camera,rect:()=>({left:0,top:0,width:1000,height:1000}),layers:()=>[layer],clip:()=>undefined});
 expect((await picker.pickAtScreen(500,500)).info.triangle?.tile).toBe('full/1');
});

it('schräge Platte bei UTM: echter Flächentreffer ≤0,1 mm', async () => {
 const plate=new Mesh(new PlaneGeometry(2,2),new MeshBasicMaterial({side:DoubleSide}));
 const angle=Math.PI/5;plate.rotation.x=angle;plate.position.set(500000,5800000,100);plate.updateMatrixWorld(true);
 const cam=new PerspectiveCamera(45,1,.01,100);cam.position.set(500000.1,5800000.2,103);cam.lookAt(500000.1,5800000.2,100);cam.updateMatrixWorld(true);
 const layer={id:'platte',stage:'full',visible:true,pickMeshes:[plate],requestFull:vi.fn()} as unknown as MeshLayerHandle;
 const picker=new MeshPickSource({camera:()=>cam,rect:()=>({left:0,top:0,width:1000,height:1000}),layers:()=>[layer],clip:()=>undefined});
 const r=await picker.pickAtScreen(500,500);
 expect(Math.abs(r.point![2]-(100+.2*Math.tan(angle)))).toBeLessThan(.0001);
 expect(r.info.snap?.result).toBe('none');
});

it('Kamerakopie erhält die Weltpose einer Kamera mit Elterntransformation', async () => {
 const parent=new Group();parent.position.set(500000,5800000,100);
 const cam=new PerspectiveCamera(45,1,.01,100);cam.position.set(0,0,3);parent.add(cam);
 parent.updateMatrixWorld(true);cam.lookAt(500000,5800000,100);parent.updateMatrixWorld(true);
 const layer={id:'modell',stage:'full',visible:true,pickMeshes:[mesh],requestFull:vi.fn()} as unknown as MeshLayerHandle;
 const picker=new MeshPickSource({camera:()=>cam,rect:()=>({left:0,top:0,width:1000,height:1000}),layers:()=>[layer],clip:()=>undefined});
 const r=await picker.pickAtScreen(500,500);expect(r.point![2]).toBeCloseTo(100.5,6);
});
