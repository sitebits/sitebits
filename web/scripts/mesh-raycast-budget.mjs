// Nur lesender Browser-Benchmark: festes 20-Strahlen-Raster auf der Reiter-Vollstufe.
import { build } from 'esbuild';
import { chromium } from '@playwright/test';
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
const web = resolve(dirname(fileURLToPath(import.meta.url)), '..');
if (!process.argv[2]) {
  console.error('Aufruf: node web/scripts/mesh-raycast-budget.mjs <Paketordner mit Mesh-Ebene>');
  process.exit(2);
}
const pkg = resolve(process.argv[2]);
const code = `
import { Box3, Group, Mesh, PerspectiveCamera, Raycaster, Vector2, Vector3 } from 'three';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js';
import { prepareMeshGlb } from './src/meshLayer.ts';
import { featureEdges } from './src/meshPickMath.ts';
window.run = async () => {
 const m = await (await fetch('/manifest')).json(); const layer = m.layers.find(l => l.kind === 'mesh');
 const buffer = await (await fetch('/full')).arrayBuffer();
 const loader = new GLTFLoader().setMeshoptDecoder(MeshoptDecoder);
 const gltf = await loader.parseAsync(prepareMeshGlb(buffer, true), location.href);
 const root = new Group(); root.position.set(...m.frame.originWorld); root.rotation.x = Math.PI/2; root.add(gltf.scene); root.updateMatrixWorld(true);
 const center = new Box3().setFromObject(root).getCenter(new Vector3());
 const cam = new PerspectiveCamera(45, 1920/1080, .01, 1000); cam.up.set(0,0,1);
 cam.position.copy(center).add(new Vector3(0,-16,7)); cam.lookAt(center); cam.updateMatrixWorld(true);
 const caster = new Raycaster(); const samples = []; let triangles=0; let edges=0;
 root.traverse(o => { if(o instanceof Mesh) triangles += (o.geometry.index?.count ?? o.geometry.attributes.position.count)/3; });
 for(let j=0;j<4;j++) for(let i=0;i<5;i++) {
  const x=(i-2)*.1, y=(j-1.5)*.1; caster.setFromCamera(new Vector2(x,y),cam);
  const t=performance.now(); const hits=caster.intersectObject(root,true); const ms=performance.now()-t;
  samples.push({x,y,ms,hits:hits.length,depth:hits[0]?.distance ?? null});
 }
 const prep=performance.now(); root.traverse(o=>{if(o instanceof Mesh) edges+=featureEdges(o.geometry).length;});
 const edgePreparationMs=performance.now()-prep;
 const sorted=samples.map(x=>x.ms).sort((a,b)=>a-b);
 return {declaredTriangles:layer.triangleCount,triangles,samples,medianMs:(sorted[9]+sorted[10])/2,p95Ms:sorted[18],edgePreparationMs,edges,userAgent:navigator.userAgent};
};`;
const bundle = await build({ stdin: { contents: code, resolveDir: web }, bundle: true, write: false, format: 'esm', platform: 'browser' });
const manifest = JSON.parse(await readFile(resolve(pkg, 'project.json'), 'utf8'));
const mesh = manifest.layers.find(l => l.kind === 'mesh');
const server = createServer(async (req, res) => {
 try {
  if(req.url === '/bundle.js') { res.setHeader('Content-Type', 'text/javascript'); res.end(bundle.outputFiles[0].contents); }
  else if(req.url === '/manifest') { res.setHeader('Content-Type', 'application/json'); res.end(JSON.stringify(manifest)); }
  else if(req.url === '/full') { res.setHeader('Content-Type', 'model/gltf-binary'); res.end(await readFile(resolve(pkg, mesh.url))); }
  else if(req.url === '/') { res.setHeader('Content-Type', 'text/html'); res.end('<script type="module" src="/bundle.js"></script>'); }
  else { res.statusCode=404; res.end(); }
 } catch(e) { res.statusCode=500; res.end(String(e)); }
});
await new Promise((ok, fail) => { server.once('error', fail); server.listen(0, '127.0.0.1', ok); });
let browser;
try {
 browser=await chromium.launch({headless:true}); const page=await browser.newPage();
 await page.goto(`http://127.0.0.1:${server.address().port}/`); await page.waitForFunction(()=>typeof window.run === 'function');
 const result=await page.evaluate(()=>window.run()); console.log(JSON.stringify(result,null,2));
 if(result.triangles !== result.declaredTriangles) process.exitCode=2;
} finally { await browser?.close(); server.close(); }
