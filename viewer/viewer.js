/**
 * PRIMORDIA · visor web de la maqueta .glb generada por blender/primordia_island.py
 *
 * Coordenadas: el exportador glTF convierte Blender (X, Y, Z-arriba) a glTF
 * (X, Y-arriba, −Z): un punto Blender (x, y, z) llega como (x, z, −y).
 */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { DRACOLoader } from 'three/addons/loaders/DRACOLoader.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { OutputPass } from 'three/addons/postprocessing/OutputPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

const MODEL_URL = new URL('./PRIMORDIA_TheIsland.glb', import.meta.url);
const DRACO_DECODER_URL = 'https://cdn.jsdelivr.net/npm/three@0.169.0/examples/jsm/libs/draco/gltf/';
const VIEWS = {
  aerial: { position: [0, 98, 124], target: [0, -4, 9] },   // = PRIM_Cam_Aerial
  map: { position: [0, 165, 0.01], target: [0, 0, 0] },    // = PRIM_Cam_Map
};
const BLOOM_LAYER = 1;            // bloom selectivo: solo las mallas emisivas (obeliscos)

const stage = document.querySelector('#stage');
const status = document.querySelector('#status');
const progress = document.querySelector('#progress');
const message = document.querySelector('#status-message');
const autoRotateButton = document.querySelector('[data-action="rotate"]');

const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: 'high-performance' });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.toneMapping = THREE.NeutralToneMapping;      // conserva el color horneado del mapa
stage.append(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1014);
const pmrem = new THREE.PMREMGenerator(renderer);
scene.environment = pmrem.fromScene(new RoomEnvironment(), 0.04).texture;
pmrem.dispose();

// Sol de la escena Blender: elevación 34°, azimut 300° (ONO).
const sun = new THREE.DirectionalLight(0xfff0dd, 2.2);
sun.position.set(-70, 60, -40);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(32, 1, 0.5, 2000);
const controls = new OrbitControls(camera, renderer.domElement);
Object.assign(controls, {
  enableDamping: true,
  dampingFactor: 0.08,
  minDistance: 20,
  maxDistance: 480,
  maxPolarAngle: Math.PI * 0.48,                       // nunca por debajo de la mesa
  autoRotateSpeed: 0.6,
});

// Bloom selectivo (patrón oficial de three.js): una pasada con todo lo no emisivo
// en negro genera el resplandor, que luego se suma a la imagen normal.
const bloomLayer = new THREE.Layers();
bloomLayer.set(BLOOM_LAYER);
const darkMaterial = new THREE.MeshBasicMaterial({ color: 0x000000 });
const stashedMaterials = new Map();

const bloomComposer = new EffectComposer(renderer);
bloomComposer.renderToScreen = false;
bloomComposer.addPass(new RenderPass(scene, camera));
bloomComposer.addPass(new UnrealBloomPass(new THREE.Vector2(1, 1), 1.1, 0.35, 0.0));

const mixPass = new ShaderPass(new THREE.ShaderMaterial({
  uniforms: { baseTexture: { value: null }, bloomTexture: { value: bloomComposer.renderTarget2.texture } },
  vertexShader: 'varying vec2 vUv; void main() { vUv = uv; gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0); }',
  fragmentShader: 'uniform sampler2D baseTexture; uniform sampler2D bloomTexture; varying vec2 vUv; '
    + 'void main() { gl_FragColor = texture2D(baseTexture, vUv) + texture2D(bloomTexture, vUv); }',
}), 'baseTexture');
mixPass.needsSwap = true;

const finalComposer = new EffectComposer(renderer);
finalComposer.addPass(new RenderPass(scene, camera));
finalComposer.addPass(mixPass);
finalComposer.addPass(new OutputPass());

function darkenNonBloomed(node) {
  if (node.isMesh && !bloomLayer.test(node.layers)) {
    stashedMaterials.set(node.uuid, node.material);
    node.material = darkMaterial;
  }
}

function restoreMaterial(node) {
  if (stashedMaterials.has(node.uuid)) {
    node.material = stashedMaterials.get(node.uuid);
    stashedMaterials.delete(node.uuid);
  }
}

function render() {
  const background = scene.background;
  scene.background = null;
  scene.traverse(darkenNonBloomed);
  bloomComposer.render();
  scene.traverse(restoreMaterial);
  scene.background = background;
  finalComposer.render();
}

function applyView(name) {
  const view = VIEWS[name];
  // Las vistas están encuadradas para 16:9; en pantallas verticales se aleja la cámara.
  const fit = Math.max(1, 1.25 / camera.aspect) ** 0.72;
  controls.target.fromArray(view.target);
  camera.position.fromArray(view.position).sub(controls.target).multiplyScalar(fit).add(controls.target);
  controls.update();
}

function resize() {
  const { clientWidth: width, clientHeight: height } = stage;
  camera.aspect = width / Math.max(height, 1);
  camera.updateProjectionMatrix();
  renderer.setSize(width, height, false);
  bloomComposer.setSize(width / 2, height / 2);          // resplandor a media resolución
  finalComposer.setSize(width, height);
}

function showError(text) {
  progress.hidden = true;
  message.textContent = text;
  message.classList.add('error');
  status.hidden = false;
}

function loadModel() {
  const draco = new DRACOLoader().setDecoderPath(DRACO_DECODER_URL);
  const loader = new GLTFLoader().setDRACOLoader(draco);
  loader.load(
    MODEL_URL.href,
    (gltf) => {
      gltf.scene.traverse((node) => {
        if (node.isMesh && node.material.emissiveIntensity > 1) node.layers.enable(BLOOM_LAYER);
      });
      scene.add(gltf.scene);
      draco.dispose();
      status.classList.add('is-done');
      status.addEventListener('transitionend', () => { status.hidden = true; }, { once: true });
    },
    (event) => {
      if (event.lengthComputable) progress.value = event.loaded / event.total;
    },
    () => showError('No se pudo cargar PRIMORDIA_TheIsland.glb. Genera el archivo con el script de Blender y '
      + 'colócalo junto a esta página (sírvela con un servidor local, no con file://).'),
  );
}

document.querySelector('.toolbar').addEventListener('click', (event) => {
  const button = event.target.closest('button[data-action]');
  if (!button) return;
  const { action } = button.dataset;
  if (action === 'rotate') {
    controls.autoRotate = !controls.autoRotate;
    autoRotateButton.setAttribute('aria-pressed', String(controls.autoRotate));
  } else {
    applyView(action);
  }
});

new ResizeObserver(resize).observe(stage);
resize();
applyView('aerial');
renderer.setAnimationLoop(() => {
  controls.update();
  render();
});
loadModel();
