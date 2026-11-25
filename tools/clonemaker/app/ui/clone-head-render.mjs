// A portrait uses the same skinned body, fielder pose, UVs and archive texture indices as the Studio stage.
// Rasterize offscreen on the CPU so a tournament with many clones never needs a WebGL context per picture.
import { AnimationMixer, Vector3, Vector4 } from "./vendor/three.module.js";
import { GLTFLoader } from "./vendor/GLTFLoader.js";

const SIZE = 96;
const RES = 2 * SIZE;
const models = new Map();
const loader = new GLTFLoader();

function canvas(width, height = width) {
  const element = document.createElement("canvas");
  element.width = width;
  element.height = height;
  return element;
}

async function pixels(png) {
  const image = new Image();
  image.src = "data:image/png;base64," + png;
  await image.decode();
  const ctx = canvas(image.naturalWidth, image.naturalHeight).getContext("2d");
  ctx.drawImage(image, 0, 0);
  return ctx.getImageData(0, 0, image.naturalWidth, image.naturalHeight);
}

function isAccessory(mesh) {
  for (let node = mesh; node; node = node.parent) {
    if (node.userData.subitem_slot !== undefined) return true;
  }
  return false;
}

// Actor joint 9 is the head. Its descendants carry the face, ears, hair, hats and crowns.
// Boo and King Boo's spherical torso is also the silhouette of their faces.
function headJoint(bone) {
  for (let node = bone; node; node = node.parent) {
    if (node.userData.bone_id === 9) return true;
  }
  return false;
}

// Keep the portion above the neck seam. A triangle crossing that seam needs its UVs and
// position interpolated there, otherwise individual triangular gaps appear along the jaw.
function retainHead(vertices, triangles, bodyIsHead) {
  if (bodyIsHead) return triangles;
  const indices = [];
  function intersect(a, b) {
    const start = vertices[a], end = vertices[b];
    const t = (0.5 - start.head) / (end.head - start.head);
    const point = {};
    for (const key of ["x", "y", "z", "u", "v", "shade"]) point[key] = start[key] + (end[key] - start[key]) * t;
    return vertices.push(point) - 1;
  }
  for (let i = 0; i < triangles.length; i += 3) {
    const a = triangles[i], b = triangles[i+1], c = triangles[i+2];
    const inside = [a,b,c].filter(index => vertices[index].head >= 0.5).length;
    if (inside === 3) indices.push(a, b, c);
    if (inside === 0 || inside === 3) continue;
    const polygon = [];
    let previous = c;
    for (const index of [a,b,c]) {
      const keep = vertices[index].head >= 0.5;
      if ((vertices[previous].head >= 0.5) !== keep) polygon.push(intersect(previous, index));
      if (keep) polygon.push(index);
      previous = index;
    }
    for (let j = 2; j < polygon.length; j++) indices.push(polygon[0], polygon[j-1], polygon[j]);
  }
  return indices;
}

function project(scene, source) {
  const meshes = [], pieces = [];
  const points = [];
  const position = new Vector3();
  const normal = new Vector4();
  const yaw = 0.59, cos = Math.cos(yaw), sin = Math.sin(yaw);

  scene.traverse(mesh => {
    if (!mesh.isMesh || isAccessory(mesh)) return;
    const { texture, sampler } = mesh.material.userData;
    const geometry = mesh.geometry;
    const { uv, normal: normals } = geometry.attributes;
    if (texture === undefined || !uv) return;
    const vertices = [];
    const headBones = mesh.skeleton.bones.map(headJoint);
    const { skinIndex, skinWeight } = geometry.attributes;
    for (let i = 0; i < uv.count; i++) {
      mesh.getVertexPosition(i, position).applyMatrix4(mesh.matrixWorld);
      let shade = 1;
      if (normals) {
        normal.set(normals.getX(i), normals.getY(i), normals.getZ(i), 0);
        if (mesh.isSkinnedMesh) mesh.applyBoneTransform(i, normal);
        normal.applyMatrix4(mesh.matrixWorld).normalize();
        shade = 0.80 + 0.27 * Math.max(0, -0.3 * normal.x + 0.55 * normal.y + 0.77 * normal.z);
      }
      let head = 0;
      for (let component = 0; component < 4; component++) {
        if (headBones[skinIndex.getComponent(i, component)]) head += skinWeight.getComponent(i, component);
      }
      const point = {head, x: cos * position.x - sin * position.z, y: position.y,
        z: sin * position.x + cos * position.z, u: uv.getX(i), v: uv.getY(i), shade};
      vertices.push(point);
    }
    meshes.push({vertices, triangles: geometry.index.array, texture, sampler});
  });

  // Piantas have no anatomical neck. The retail HUD frames the whole face down to the
  // green skirt. Find its top from the skirt material in all three colour variants.
  if (source >= 21 && source <= 23) {
    let skirt = -Infinity, crown = -Infinity;
    for (const {vertices, texture} of meshes) {
      for (const p of vertices) {
        crown = Math.max(crown, p.y);
        if (texture === 2) skirt = Math.max(skirt, p.y);
      }
    }
    const hem = skirt - 0.18 * (crown - skirt);
    for (const {vertices} of meshes) {
      for (const p of vertices) p.head = 0.5 + p.y - hem;
    }
  }
  for (const {vertices, triangles, texture, sampler} of meshes) {
    const indices = retainHead(vertices, triangles, source === 14 || source === 37);
    for (const index of indices) points.push(vertices[index]);
    if (indices.length) pieces.push({vertices, indices, texture, sampler});
  }
  if (!points.length) throw new Error("Character model contains no textured head surfaces.");
  let top = -Infinity, bottom = Infinity, left = Infinity, right = -Infinity;
  for (const p of points) {
    top = Math.max(top, p.y);
    bottom = Math.min(bottom, p.y);
    left = Math.min(left, p.x);
    right = Math.max(right, p.x);
  }
  const scale = (RES * 0.93) / Math.max(top - bottom, right - left);
  const centreX = (left + right) / 2, centreY = (top + bottom) / 2;
  for (const { vertices } of pieces) {
    for (const p of vertices) {
      p.x = (p.x - centreX) * scale + RES * 0.5;
      p.y = (centreY - p.y) * scale + RES * 0.5;
    }
  }
  return pieces;
}

function texel(index, length, wrap) {
  if (wrap === 33071) return Math.min(length - 1, Math.max(0, index));
  if (wrap === 33648) {
    const period = length * 2;
    const p = ((index % period) + period) % period;
    return p < length ? p : period - 1 - p;
  }
  return ((index % length) + length) % length;
}

function rasterize(pieces, images) {
  const rgba = new Uint8ClampedArray(RES * RES * 4);
  const depth = new Float64Array(RES * RES).fill(-Infinity);
  for (const { vertices, indices, texture, sampler } of pieces) {
    const img = images[texture];
    if (!img) continue;
    const sx = sampler?.wrapS, sy = sampler?.wrapT;
    for (let i = 0; i < indices.length; i += 3) {
      const a = vertices[indices[i]], b = vertices[indices[i + 1]], c = vertices[indices[i + 2]];
      const bx = b.x - a.x, by = b.y - a.y, cx = c.x - a.x, cy = c.y - a.y;
      const area = bx * cy - by * cx;
      if (Math.abs(area) < 1e-5) continue;
      const x0 = Math.max(0, Math.ceil(Math.min(a.x, b.x, c.x) - 0.5));
      const y0 = Math.max(0, Math.ceil(Math.min(a.y, b.y, c.y) - 0.5));
      const x1 = Math.min(RES - 1, Math.floor(Math.max(a.x, b.x, c.x) - 0.5));
      const y1 = Math.min(RES - 1, Math.floor(Math.max(a.y, b.y, c.y) - 0.5));
      for (let y = y0; y <= y1; y++) {
        for (let x = x0; x <= x1; x++) {
          const dx = x + 0.5 - a.x, dy = y + 0.5 - a.y;
          const wb = (dx * cy - dy * cx) / area, wc = (bx * dy - by * dx) / area;
          const wa = 1 - wb - wc;
          if (Math.min(wa, wb, wc) < -1e-8) continue;
          const z = wa * a.z + wb * b.z + wc * c.z;
          const pixel = y * RES + x;
          if (z <= depth[pixel]) continue;
          const u = (wa * a.u + wb * b.u + wc * c.u) * img.width;
          const v = (wa * a.v + wb * b.v + wc * c.v) * img.height;
          const offset = 4 * (texel(Math.floor(v), img.height, sy) * img.width + texel(Math.floor(u), img.width, sx));
          if (img.data[offset + 3] < 128) continue; // Stage GLB uses alphaMode MASK.
          const shade = wa * a.shade + wb * b.shade + wc * c.shade;
          for (let channel = 0; channel < 3; channel++) rgba[4 * pixel + channel] = img.data[offset + channel] * shade;
          rgba[4 * pixel + 3] = 255;
          depth[pixel] = z;
        }
      }
    }
  }
  const high = canvas(RES);
  high.getContext("2d").putImageData(new ImageData(rgba, RES, RES), 0, 0);
  const output = canvas(SIZE);
  const ctx = output.getContext("2d");
  ctx.drawImage(high, 0, 0, SIZE, SIZE);
  return {canvas: output, rgba: ctx.getImageData(0, 0, SIZE, SIZE).data};
}

async function load(source) {
  const asset = await window.__TAURI__.core.invoke("load_custom_portrait_asset", {source});
  if (!asset) return null;
  const binary = Uint8Array.from(atob(asset.model), ch => ch.charCodeAt(0));
  const [gltf, images] = await Promise.all([
    loader.parseAsync(binary.buffer, ""),
    Promise.all(asset.textures.map(pixels))
  ]);
  const mixer = new AnimationMixer(gltf.scene);
  if (gltf.animations[0]) mixer.clipAction(gltf.animations[0]).play();
  mixer.setTime(0);
  gltf.scene.updateMatrixWorld(true);
  const pieces = project(gltf.scene, source);
  return {pieces, images, stock: rasterize(pieces, images).rgba};
}

function model(source) {
  let pending = models.get(source);
  if (pending) models.delete(source);
  else {
    pending = load(source);
    const forget = () => { if (models.get(source) === pending) models.delete(source); };
    pending.then(asset => { if (!asset) forget(); }, forget);
  }
  models.set(source, pending);
  if (models.size > 6) models.delete(models.keys().next().value);
  return pending;
}

function visiblyChanged(stock, edited) {
  let count = 0, opaque = 0;
  for (let i = 0; i < stock.length; i += 4) {
    const a = stock[i + 3] / 255, b = edited[i + 3] / 255;
    if (b > 0.5) opaque++;
    let difference = Math.abs(stock[i + 3] - edited[i + 3]);
    for (let c = 0; c < 3; c++) difference = Math.max(difference, Math.abs(stock[i+c] * a - edited[i+c] * b));
    if (difference > 12) count++;
  }
  return opaque > SIZE && count > SIZE * SIZE * 0.002;
}

/** Render this row's named portable PNG replacements over its original game textures. */
export async function renderCustomPortrait(source, textures) {
  const asset = await model(source);
  if (!asset) return null;
  const images = asset.images.slice();
  await Promise.all(Object.entries(textures).map(async ([name, png]) => {
    const match = /^t(\d+)_(\d+)x(\d+)\.png$/i.exec(name);
    if (!match) throw new Error("Unrecognized portrait texture " + name);
    const index = Number(match[1]), original = images[index];
    if (!original || original.width !== Number(match[2]) || original.height !== Number(match[3])) {
      throw new Error("Texture does not match this character: " + name);
    }
    const image = await pixels(png);
    if (image.width !== original.width || image.height !== original.height) throw new Error("Wrong image dimensions: " + name);
    images[index] = image;
  }));
  const rendered = rasterize(asset.pieces, images);
  return { png: rendered.canvas.toDataURL("image/png").split(",")[1],
    headChanged: visiblyChanged(asset.stock, rendered.rgba) };
}
