// The stage's 3D model: the selected clone as the game draws it (the character's in-match body, glove and
// hand, with the preview's recoloured or painted pictures) playing the fielder's stand-still loop. The model
// comes once per character (`stage_model`, a .glb without pictures); a new colour only swaps its pictures.
// No WebGL, or a model that fails: the page keeps its silhouette (`.stage[data-stage]`).
import * as THREE from "./vendor/three.module.js";
import { GLTFLoader } from "./vendor/GLTFLoader.js";

const { invoke } = window.__TAURI__.core;
const WRAP = { 33071: THREE.ClampToEdgeWrapping, 10497: THREE.RepeatWrapping, 33648: THREE.MirroredRepeatWrapping };
const FILTER = { 9728: THREE.NearestFilter, 9729: THREE.LinearFilter };
const KEEP = 8; // models kept for switching back

/** Returns the stage's controller, or null without WebGL. */
export function create(stage) {
  let renderer;
  try {
    renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
  } catch {
    stage.dataset.stage = "fallback";
    return null;
  }
  const canvas = renderer.domElement;
  canvas.className = "model";
  canvas.setAttribute("aria-hidden", "true");
  stage.querySelector(".label").before(canvas);
  renderer.setClearColor(0, 0);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8a8f99, 2.2));
  const sun = new THREE.DirectionalLight(0xffffff, 1.6);
  sun.position.set(1, 2, 3);
  scene.add(sun);
  const camera = new THREE.PerspectiveCamera(24, 1, 0.1, 1000);
  const loader = new GLTFLoader();
  const models = new Map(); // source -> Promise<{ root, mixer, materials, box }>
  const still = matchMedia("(prefers-reduced-motion: reduce)");
  let current = null, pictures = [], token = 0, visible = true, frame = 0, last = 0, spinning = false;

  function load(source) {
    if (!models.has(source)) {
      const model = invoke("stage_model", { source }).then((glb) => loader.parseAsync(glb, "")).then((gltf) => {
        const root = gltf.scene, mixer = new THREE.AnimationMixer(root);
        if (gltf.animations[0]) mixer.clipAction(gltf.animations[0]).play();
        // untextured pieces (a flat quad at the feet, small boxes on some) are not drawn that way in
        // the game: probably the shadow and markers, which use materials the export doesn't have
        const plain = [];
        root.traverse((o) => o.isMesh && o.material.userData.texture === undefined && plain.push(o));
        for (const o of plain) o.removeFromParent();
        // frame the whole loop, not just its first pose (King Boo bobs below it)
        const box = new THREE.Box3(), length = gltf.animations[0]?.duration || 0;
        for (let i = 0; i <= 8; i++) {
          mixer.setTime((length * i) / 8);
          root.updateMatrixWorld(true);
          box.union(new THREE.Box3().setFromObject(root, true));
        }
        mixer.setTime(0);
        const materials = new Set();
        root.traverse((o) => o.material && materials.add(o.material));
        // turn about the model's own vertical axis, not the scene origin
        const centre = box.getCenter(new THREE.Vector3()), spin = new THREE.Group();
        spin.position.set(centre.x, 0, centre.z);
        root.position.set(-centre.x, 0, -centre.z);
        spin.add(root);
        return { root: spin, mixer, materials: [...materials], box };
      });
      models.set(source, model);
      model.catch(() => models.delete(source));
      if (models.size > KEEP) drop(models.keys().next().value);
    }
    return models.get(source);
  }
  function drop(source) {
    const model = models.get(source);
    models.delete(source);
    model.then((m) => m !== current && m.root.traverse((o) => o.geometry?.dispose()), () => {});
  }

  /** Base64 PNGs -> textures in archive order, with each material's sampler. */
  async function decode(pngs, materials) {
    const samplers = new Map(materials.map((m) => [m.userData.texture, m.userData.sampler]));
    return Promise.all(pngs.map(async (png, i) => {
      const image = new Image();
      image.src = "data:image/png;base64," + png;
      await image.decode();
      const texture = new THREE.Texture(image), s = samplers.get(i) || {};
      texture.flipY = false;
      texture.colorSpace = THREE.SRGBColorSpace;
      texture.wrapS = WRAP[s.wrapS] ?? THREE.RepeatWrapping;
      texture.wrapT = WRAP[s.wrapT] ?? THREE.RepeatWrapping;
      texture.magFilter = FILTER[s.magFilter] ?? THREE.LinearFilter;
      texture.minFilter = FILTER[s.minFilter] ?? THREE.LinearFilter;
      texture.needsUpdate = true;
      return texture;
    }));
  }

  /** Frames the model's idle pose: feet at the stage's floor, its height most of the stage's. */
  function frameCamera() {
    const { clientWidth: w, clientHeight: h } = stage;
    if (!w || !h) return;
    renderer.setPixelRatio(devicePixelRatio);
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    if (current) {
      const size = current.box.getSize(new THREE.Vector3()), centre = current.box.getCenter(new THREE.Vector3());
      const tall = Math.max(size.y / 0.74, size.x / camera.aspect / 0.8);
      const distance = tall / 2 / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2)) + size.z / 2;
      // the model's feet a little above the stage's bottom edge
      const lift = tall * 0.5 - size.y / 2 - tall * 0.13; // feet clear the turn bar
      camera.position.set(centre.x, centre.y + lift, centre.z + distance);
      camera.lookAt(centre.x, centre.y + lift, centre.z);
    }
    camera.updateProjectionMatrix();
    draw();
  }

  function draw() {
    if (current && visible) renderer.render(scene, camera);
  }
  function tick(now) {
    frame = 0;
    if (!current || !visible || still.matches) return;
    const dt = Math.min((now - last) / 1000, 0.1);
    current.mixer.update(dt);
    if (spinning) turn(yaw + dt * 0.6);
    last = now;
    draw();
    frame = requestAnimationFrame(tick);
  }
  function play() {
    if (!frame && current && visible && !still.matches) {
      last = performance.now();
      frame = requestAnimationFrame(tick);
    }
    draw();
  }

  function setPictures(textures) {
    for (const m of current.materials) {
      const t = m.userData.texture;
      if (t === undefined) continue;
      m.map = textures[t] ?? null;
      m.needsUpdate = true;
    }
    for (const t of pictures) t.dispose();
    pictures = textures;
  }

  // drag to turn the model all the way round; double-click faces it forward again
  let yaw = 0, dragX = null;
  const turn = (to) => {
    yaw = Math.atan2(Math.sin(to), Math.cos(to)); // keep within ±π so Front/Side/Back can be recognised
    if (current) current.root.rotation.y = yaw;
    api.onturn?.(yaw);
    draw();
  };
  canvas.title = "Drag to turn · double-click to reset";
  canvas.addEventListener("pointerdown", (e) => {
    dragX = e.clientX;
    try {
      canvas.setPointerCapture(e.pointerId);
    } catch {} // synthetic pointers (the self-test) have nothing to capture
  });
  canvas.addEventListener("pointermove", (e) => {
    if (dragX === null) return;
    turn(yaw + (e.clientX - dragX) * 0.012);
    dragX = e.clientX;
  });
  canvas.addEventListener("pointerup", () => (dragX = null));
  canvas.addEventListener("pointercancel", () => (dragX = null));
  canvas.addEventListener("dblclick", () => turn(0));

  new ResizeObserver(frameCamera).observe(stage);
  new IntersectionObserver(([e]) => {
    visible = e.isIntersecting && !document.hidden;
    play();
  }).observe(stage);
  document.addEventListener("visibilitychange", () => {
    visible = !document.hidden && stage.getBoundingClientRect().bottom > 0;
    play();
  });
  still.addEventListener("change", () => {
    if (still.matches && current) current.mixer.setTime(0);
    play();
  });

  const api = {
    /** Called with the new angle whenever the model turns. */
    onturn: null,
    get yaw() {
      return yaw;
    },
    turn,
    get spinning() {
      return spinning;
    },
    /** Slowly turns the model until stopped (not with reduced motion: the model then stays still). */
    spin(on) {
      spinning = on;
      play();
    },
    /** The latest `show`'s promise (the self-test times it). */
    drawn: Promise.resolve(null),
    show(source, pngs) {
      return (this.drawn = this.wear(source, pngs));
    },
    /** Shows `source` wearing `pngs` (the preview's pictures); resolves to the milliseconds
     * until it is drawn, or null when a newer call or a failure took over. */
    async wear(source, pngs) {
      const mine = ++token, t0 = performance.now();
      const switching = !current || current.source !== source;
      if (switching) stage.dataset.stage = "loading";
      try {
        const model = await load(source);
        const textures = await decode(pngs, model.materials);
        if (mine !== token) {
          for (const t of textures) t.dispose();
          return null;
        }
        if (model !== current) {
          if (current) scene.remove(current.root);
          current = Object.assign(model, { source });
          if (still.matches) current.mixer.setTime(0);
          model.root.rotation.y = yaw;
          scene.add(model.root);
        }
        setPictures(textures);
        stage.dataset.stage = "ready";
        frameCamera();
        play();
        return performance.now() - t0;
      } catch (e) {
        if (mine === token) this.clear("fallback");
        console.warn("3D stage:", e);
        return null;
      }
    },
    /** Takes the model off the stage (no clone selected, or `fallback` after a failure). */
    clear(why = "") {
      ++token;
      if (current) scene.remove(current.root);
      current = null;
      renderer.clear(); // WebKitGTK can keep showing a hidden canvas's last frame
      if (why) stage.dataset.stage = why;
      else delete stage.dataset.stage;
    },
  };
  return api;
}
