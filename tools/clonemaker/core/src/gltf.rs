//! "Export 3D model": one character's textured body, skeleton and animations as a
//! self-contained glTF 2.0 file. A port of the Python reference `tools/datamap/export_gltf.py`
//! (with the ANIM and actor-layout readers of `tools/datamap/animations.py`); every function
//! below is named after the Python function it ports, and `difftest.py --gltf` checks that both
//! write the same file. Formats and retail evidence: mssb-dtk `.agent/animations.md`.
//!
//! The body is the high-detail model of disc track 0 (`lbl_800F1D78[c*19]`), the banks are
//! tracks 2..=18 of the same table. Textures reuse the Clone Maker decoder (`texture.rs`).
use crate::api::png_bytes;
use crate::binary::Dol;
use crate::custom_textures;
use crate::disc::Disc;
use crate::texture::{Gpc, decode_texture};
use crate::{Error, Result};
use base64::Engine;
use serde_json::{Value, json};
use std::collections::HashMap;
use std::path::Path;

/// Version word of ANIM banks and actor layouts.
const VERSION: u32 = 0x007B_7960;
/// Version word of a `.gpc` geometry palette.
const GEO_VERSION: u32 = 0x005B_BC61;
/// Animations are sampled at every raw ANIM frame, 30 per second.
const FPS: f64 = 30.0;

// .agent/animations.md §1 in mssb-dtk maps gameplay IDs to bank sequence slots.
// The labels below are the reverse-engineered EnumCharacterAnimation_short names
// from mssb-dtk/in_game.h, not names stored in the retail ANIM sequences
// (copied from tools/datamap/export_gltf.py GAMEPLAY_NAME_GROUPS).
const GAMEPLAY_NAME_GROUPS: &[(u32, &str)] = &[
    (
        0,
        "FielderStandStill FielderHoldBallStill FielderStandReady
FielderStandReadyToCatchOverhead FielderLookDissapointed FielderRunForwards
FielderRunForwardsLookUpLeft FielderWalkForwards FielderWalkForwardsLookUp
FielderWalkBackwards FielderSidleLeft FielderSidleRight FielderWalkForwardLow
FielderRunForwardsLookDownRight FielderRunForwardsLookDownLeft FielderTPose
FielderOnFire FielderCatchHighBall FielderCatchMidBall FielderCatchLowBall
FielderCatchHighRightBall FielderCatchMidRightBall FielderCatchLowRightBall
FielderCatchHighLeftBall FielderCatchMidLeftBall FielderCatchLowLeftBall
FielderPickUpBall FielderDive FielderBallKnockout FielderBallThrow
FielderBallThrow_Super FielderBallLob FielderTPose2 FielderTagBase
FielderRunToTagOut FielderStunned FielderEnvironmentKnockout
FielderRecoverFromKnockout FielderJump FielderLandFromJump FielderWallHang
FielderWallCatchBallAndRelease FielderWallClimb FielderWallSidleRight
FielderWallSidleLeft",
    ),
    (
        45,
        "RunnerRun Runner_Tpose RunnerWalk RunnerSkidStop RunnerTurnAround
RunnerDiveTurnAround Runner_BodyCheck RunnerDiveTurnSide
RunnerWalkAwayFrom2ndBase Runner_Tpose3 Runner_Tpose4 RunnerWait RunnerReady
RunnerExcited RunnerCelebration RunnerHomeRunCelebration",
    ),
    (61, "PitcherIdleLeft PitcherIdleRight"),
    (
        63,
        "PitcherIdle PitcherChargeThrow PitcherChargeThrowRecover
PitcherRegularThrow PitcherRegularThrowRecover PitcherResultHappy
PitcherResultSad PitcherTired PitcherStretch PitcherTaunt PitcherAwait
PitcherAwait_CockedHead",
    ),
    (
        75,
        "BatterIdle BatterReadyBat BatterSwingBat BatterResetAfterSwing
BatterUnsure_ResetAfterSwing BatterCoolDownAfterSwing BatterStrikePose
BatterHoldPose BatterSmallSwipe BatterSwipeCoolDown BatterHoldCharge
BatterHoldReady BatterGetReady BatterUnsure_Reset BatterReadyBunt
BatterHoldBunt BatterResetBunt BatterSwipeReset BatterBuntStrikeout
BatterSwingStrikeout BatterNoSwingStrikeout BatterChargeStrikout
BatterBallWalked BatterHitByBall BatterUnsure_Unhappy2
BatterUnsure_SwingLefty BatterPickOff3rdOut BatterReadyIntro
BatterWalkUpToPlate BatterUnsure_Recovery2",
    ),
    (107, "WinningCelebration"),
];

/// Disc track → gameplay ID of its sequence 0. Normal-match defaults are 2/3/4/5/15; related
/// mode variants use the same gameplay ID partition; 7/8 have no proven request path or mapping.
fn bank_gameplay_base(track: usize) -> Option<u32> {
    Some(match track {
        2 | 9 | 10 => 75,
        3 | 11 | 12 | 14 => 45,
        4 | 13 => 0,
        5 | 18 => 63,
        6 => 61,
        15..=17 => 105,
        _ => return None,
    })
}

/// The banks `--all-animations` exports: batting, running, fielding, pitching, extras.
pub const DEFAULT_ANIMATION_BANKS: [usize; 5] = [2, 3, 4, 5, 15];
/// The single clip exported without `--all-animations`: the normal swing.
const DEFAULT_CLIP: (usize, usize) = (2, 2);

/// Name a disc-bank sequence by the gameplay ID it occupies, if known (`animation_name`).
pub fn animation_name(track: usize, sequence: usize) -> String {
    let mut name = format!("t{track}_s{sequence:02}");
    let Some(base) = bank_gameplay_base(track) else {
        return name;
    };
    let id = base + sequence as u32;
    name += &format!("_id{id}");
    for (start, names) in GAMEPLAY_NAME_GROUPS {
        let names: Vec<_> = names.split_whitespace().collect();
        if (*start..*start + names.len() as u32).contains(&id) {
            return format!("{name}_{}", names[(id - start) as usize]);
        }
    }
    name
}

fn bad(message: impl Into<String>) -> Error {
    Error::new(message.into())
}

/// Bounded big-endian reads relative to one file (`animations.Reader`); floats must be finite.
struct Reader<'a>(&'a [u8]);
impl Reader<'_> {
    fn check(&self, offset: usize, size: usize) -> Result<()> {
        match offset.checked_add(size) {
            Some(end) if end <= self.0.len() => Ok(()),
            _ => Err(bad(format!("out-of-range span 0x{offset:X} + 0x{size:X}"))),
        }
    }
    fn bytes<const N: usize>(&self, offset: usize) -> Result<[u8; N]> {
        self.check(offset, N)?;
        Ok(self.0[offset..offset + N].try_into().unwrap())
    }
    fn u8(&self, offset: usize) -> Result<u8> {
        Ok(self.bytes::<1>(offset)?[0])
    }
    fn u16(&self, offset: usize) -> Result<u16> {
        Ok(u16::from_be_bytes(self.bytes(offset)?))
    }
    fn u32(&self, offset: usize) -> Result<u32> {
        Ok(u32::from_be_bytes(self.bytes(offset)?))
    }
    fn us(&self, offset: usize) -> Result<usize> {
        Ok(self.u32(offset)? as usize)
    }
    fn f32(&self, offset: usize) -> Result<f64> {
        let v = f32::from_be_bytes(self.bytes(offset)?);
        if !v.is_finite() {
            return Err(bad(format!("non-finite number at 0x{offset:X}")));
        }
        Ok(v as f64)
    }
    /// Check the NUL-terminated sequence name.
    fn string(&self, offset: usize) -> Result<()> {
        self.check(offset, 1)?;
        if !self.0[offset..].contains(&0) {
            return Err(bad(format!("unterminated sequence name at 0x{offset:X}")));
        }
        Ok(())
    }
    /// GetData (0x800B5CF0) copies f32 as stored; integer formats are multiplied by 2**-shift.
    fn numbers(&self, offset: usize, count: usize, quantization: u8) -> Result<Vec<f64>> {
        let (kind, shift) = (quantization >> 4, quantization & 15);
        let width = quant_width(quantization)?;
        self.check(offset, count * width)?;
        let scale = (1u32 << shift) as f64;
        (0..count)
            .map(|i| {
                let o = offset + i * width;
                Ok(match kind {
                    0 => self.u8(o)? as f64 / scale,
                    1 => self.u8(o)? as i8 as f64 / scale,
                    2 => self.u16(o)? as f64 / scale,
                    3 => self.u16(o)? as i16 as f64 / scale,
                    _ => self.f32(o)?,
                })
            })
            .collect()
    }
}

fn quant_width(quantization: u8) -> Result<usize> {
    match quantization >> 4 {
        0 | 1 => Ok(1),
        2 | 3 => Ok(2),
        4 => Ok(4),
        _ => Err(bad(format!(
            "unsupported quantization 0x{quantization:02X}"
        ))),
    }
}

// ------------------------------------------------------------------ actor layout

/// One 0x1C-byte ActorBone; links are indices into the layout's bone list.
struct Bone {
    bone_id: u16,
    parent: Option<usize>,
    next: Option<usize>,
    child: Option<usize>,
    geometry_id: u16,
    control: usize,
}

/// A body's actor layout (`parse_layout`).
struct Layout {
    offset: usize,
    root: Option<usize>,
    skin_file_id: u16,
    bones: Vec<Bone>,
}

fn parse_layout(data: &[u8]) -> Result<Layout> {
    let offset = Reader(data).us(4)?;
    Reader(data).check(offset, 0x20)?;
    let r = Reader(&data[offset..]);
    if r.u32(0)? != VERSION {
        return Err(bad("unsupported actor-layout version"));
    }
    let count = r.u16(6)? as usize;
    r.check(0x20, count * 0x1C)?;
    let index = |pointer: usize| -> Result<Option<usize>> {
        if pointer == 0 {
            return Ok(None);
        }
        if pointer < 0x20
            || !(pointer - 0x20).is_multiple_of(0x1C)
            || (pointer - 0x20) / 0x1C >= count
        {
            return Err(bad("hierarchy pointer does not name an ActorBone"));
        }
        Ok(Some((pointer - 0x20) / 0x1C))
    };
    let mut bones = Vec::new();
    for i in 0..count {
        let b = 0x20 + i * 0x1C;
        let control = r.us(b)?;
        index(r.us(b + 4)?)?;
        let bone = Bone {
            control,
            next: index(r.us(b + 8)?)?,
            parent: index(r.us(b + 0xC)?)?,
            child: index(r.us(b + 0x10)?)?,
            geometry_id: r.u16(b + 0x14)?,
            bone_id: r.u16(b + 0x16)?,
        };
        if control != 0 {
            // CTRLControl is a one-byte type, three padding bytes, then floats.
            r.check(control, 0x44)?;
        }
        bones.push(bone);
    }
    Ok(Layout {
        offset,
        root: index(r.us(0xC)?)?,
        skin_file_id: r.u16(0x14)?,
        bones,
    })
}

/// The game's bone pointer array is filled by a depth-first tree walk (`runtime_bone_order`).
fn runtime_bone_order(layout: &Layout) -> Result<Vec<usize>> {
    fn visit(
        layout: &Layout,
        mut index: Option<usize>,
        seen: &mut [bool],
        order: &mut Vec<usize>,
    ) -> Result<()> {
        while let Some(i) = index {
            if seen[i] {
                return Err(bad("cycle in bone tree"));
            }
            seen[i] = true;
            order.push(i);
            visit(layout, layout.bones[i].child, seen, order)?;
            index = layout.bones[i].next;
        }
        Ok(())
    }
    let mut seen = vec![false; layout.bones.len()];
    let mut order = Vec::new();
    visit(layout, layout.root, &mut seen, &mut order)?;
    if order.len() != layout.bones.len() {
        return Err(bad("bone tree does not contain every bone"));
    }
    Ok(order)
}

// ------------------------------------------------------------------ quaternions and matrices

type Quat = [f64; 4];
type Mat = [[f64; 4]; 4];

fn sum(values: impl IntoIterator<Item = f64>) -> f64 {
    values.into_iter().fold(0.0, |a, b| a + b)
}

fn unit(q: &[f64]) -> Result<Vec<f64>> {
    let length = sum(q.iter().map(|x| x * x)).sqrt();
    if !length.is_finite() || length == 0.0 {
        return Err(bad("invalid rest quaternion"));
    }
    Ok(q.iter().map(|x| x / length).collect())
}

fn multiply(a: Quat, b: Quat) -> Quat {
    let [ax, ay, az, aw] = a;
    let [bx, by, bz, bw] = b;
    [
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
        aw * bw - ax * bx - ay * by - az * bz,
    ]
}

fn euler_xyz(degrees: &[f64]) -> Result<Vec<f64>> {
    let [x, y, z] = [0, 1, 2].map(|i| degrees[i].to_radians() / 2.0);
    let qx = [x.sin(), 0.0, 0.0, x.cos()];
    let qy = [0.0, y.sin(), 0.0, y.cos()];
    let qz = [0.0, 0.0, z.sin(), z.cos()];
    unit(&multiply(multiply(qz, qy), qx))
}

fn inverse(q: &[f64]) -> Vec<f64> {
    vec![-q[0], -q[1], -q[2], q[3]]
}

fn matmul(a: &Mat, b: &Mat) -> Mat {
    std::array::from_fn(|r| std::array::from_fn(|c| sum((0..4).map(|k| a[r][k] * b[k][c]))))
}

fn translation_matrix(v: &[f64]) -> Mat {
    [
        [1.0, 0.0, 0.0, v[0]],
        [0.0, 1.0, 0.0, v[1]],
        [0.0, 0.0, 1.0, v[2]],
        [0.0, 0.0, 0.0, 1.0],
    ]
}

fn rotation_matrix(q: &[f64]) -> Mat {
    let (x, y, z, w) = (q[0], q[1], q[2], q[3]);
    [
        [
            1.0 - 2.0 * (y * y + z * z),
            2.0 * (x * y - z * w),
            2.0 * (x * z + y * w),
            0.0,
        ],
        [
            2.0 * (x * y + z * w),
            1.0 - 2.0 * (x * x + z * z),
            2.0 * (y * z - x * w),
            0.0,
        ],
        [
            2.0 * (x * z - y * w),
            2.0 * (y * z + x * w),
            1.0 - 2.0 * (x * x + y * y),
            0.0,
        ],
        [0.0, 0.0, 0.0, 1.0],
    ]
}

fn scale_matrix(v: &[f64]) -> Mat {
    [
        [v[0], 0.0, 0.0, 0.0],
        [0.0, v[1], 0.0, 0.0],
        [0.0, 0.0, v[2], 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]
}

fn inverse_affine(m: &Mat) -> Result<Mat> {
    let [a, b, c, _] = m[0];
    let [d, e, f, _] = m[1];
    let [g, h, i, _] = m[2];
    let cof = [
        [e * i - f * h, c * h - b * i, b * f - c * e],
        [f * g - d * i, a * i - c * g, c * d - a * f],
        [d * h - e * g, b * g - a * h, a * e - b * d],
    ];
    let det = a * cof[0][0] + b * cof[1][0] + c * cof[2][0];
    if det.abs() < 1e-12 {
        return Err(bad("singular bone rest transform"));
    }
    let inv = cof.map(|row| row.map(|x| x / det));
    let offset: [f64; 3] = std::array::from_fn(|r| -sum((0..3).map(|k| inv[r][k] * m[k][3])));
    Ok([
        [inv[0][0], inv[0][1], inv[0][2], offset[0]],
        [inv[1][0], inv[1][1], inv[1][2], offset[1]],
        [inv[2][0], inv[2][1], inv[2][2], offset[2]],
        [0.0, 0.0, 0.0, 1.0],
    ])
}

// ------------------------------------------------------------------ rest pose and skeleton

/// A bone's ControlExt rest pose: T·R·Qs·S·Qs⁻¹.
struct Pose {
    translation: Vec<f64>,
    rotation: Vec<f64>,
    scale: Vec<f64>,
    scale_quaternion: Vec<f64>,
}

/// Read the file's ControlExt rest pose, whose pointer is layout-relative (`read_control`).
fn read_control(body: &[u8], layout: &Layout, bone: &Bone) -> Result<Pose> {
    let mut pose = Pose {
        translation: vec![0.0; 3],
        rotation: vec![0.0, 0.0, 0.0, 1.0],
        scale: vec![1.0; 3],
        scale_quaternion: vec![0.0, 0.0, 0.0, 1.0],
    };
    if bone.control == 0 {
        return Ok(pose);
    }
    let r = Reader(body);
    let offset = layout.offset + bone.control;
    let kind = r.u8(offset)?;
    let floats = |o: usize, n: usize| (0..n).map(|i| r.f32(o + 4 * i)).collect::<Result<Vec<_>>>();
    if kind & 0x10 != 0 {
        return Err(bad(format!(
            "rest matrix control unsupported on bone {}",
            bone.bone_id
        )));
    }
    if kind & 1 != 0 {
        pose.scale = floats(offset + 4, 3)?;
    }
    if kind & 4 != 0 {
        pose.rotation = unit(&floats(offset + 16, 4)?)?;
    } else if kind & 2 != 0 {
        pose.rotation = euler_xyz(&floats(offset + 16, 3)?)?;
    }
    if kind & 8 != 0 {
        pose.translation = floats(offset + 32, 3)?;
    }
    if kind & 0x20 != 0 {
        pose.scale_quaternion = unit(&floats(offset + 44, 4)?)?;
    }
    Ok(pose)
}

/// Inverse of each joint's complete rest transform, including scale axis, in runtime order.
fn inverse_bind_matrices(layout: &Layout, poses: &[Pose]) -> Result<Vec<Mat>> {
    let order = runtime_bone_order(layout)?;
    let mut worlds: HashMap<usize, Mat> = HashMap::new();
    for &index in &order {
        let pose = &poses[index];
        let mut local = translation_matrix(&pose.translation);
        for part in [
            rotation_matrix(&pose.rotation),
            rotation_matrix(&pose.scale_quaternion),
            scale_matrix(&pose.scale),
            rotation_matrix(&inverse(&pose.scale_quaternion)),
        ] {
            local = matmul(&local, &part);
        }
        let world = match layout.bones[index].parent {
            Some(parent) => matmul(
                worlds
                    .get(&parent)
                    .ok_or_else(|| bad("a bone comes before its parent"))?,
                &local,
            ),
            None => local,
        };
        worlds.insert(index, world);
    }
    order.iter().map(|i| inverse_affine(&worlds[i])).collect()
}

// ------------------------------------------------------------------ glTF buffer

/// One binary buffer with its views and accessors (`Buffer`).
#[derive(Default)]
struct Buffer {
    data: Vec<u8>,
    views: Vec<Value>,
    accessors: Vec<Value>,
}

const FLOAT: u32 = 5126;
const USHORT: u32 = 5123;
const ARRAY_BUFFER: u32 = 34962;
const ELEMENT_ARRAY_BUFFER: u32 = 34963;

fn width(shape: &str) -> usize {
    match shape {
        "SCALAR" => 1,
        "VEC2" => 2,
        "VEC3" => 3,
        "VEC4" => 4,
        _ => 16,
    }
}

impl Buffer {
    fn view(&mut self, bytes: &[u8], target: Option<u32>) -> usize {
        while !self.data.len().is_multiple_of(4) {
            self.data.push(0);
        }
        let start = self.data.len();
        self.data.extend_from_slice(bytes);
        let mut view = json!({"buffer": 0, "byteOffset": start, "byteLength": bytes.len()});
        if let Some(target) = target {
            view["target"] = json!(target);
        }
        self.views.push(view);
        self.views.len() - 1
    }
    fn accessor(&mut self, view: usize, component: u32, count: usize, shape: &str) -> Value {
        json!({"bufferView": view, "componentType": component, "count": count, "type": shape})
    }
    fn push(&mut self, accessor: Value) -> usize {
        self.accessors.push(accessor);
        self.accessors.len() - 1
    }
    /// f32 values; bounds describe the stored f32 values, not their f64 inputs.
    fn floats(&mut self, values: &[f64], shape: &str, target: Option<u32>) -> Result<usize> {
        let n = width(shape);
        if !values.len().is_multiple_of(n) {
            return Err(bad("accessor component count mismatch"));
        }
        let stored: Vec<f32> = values.iter().map(|&v| v as f32).collect();
        if stored.iter().any(|v| !v.is_finite()) {
            return Err(bad("non-finite value in the export"));
        }
        let bytes: Vec<u8> = stored.iter().flat_map(|v| v.to_le_bytes()).collect();
        let view = self.view(&bytes, target);
        let mut accessor = self.accessor(view, FLOAT, stored.len() / n, shape);
        if shape != "MAT4" && !stored.is_empty() {
            let bound = |better: fn(f32, f32) -> bool| -> Vec<f64> {
                (0..n)
                    .map(|i| {
                        let mut best = stored[i];
                        for chunk in stored.chunks(n) {
                            if better(chunk[i], best) {
                                best = chunk[i];
                            }
                        }
                        best as f64
                    })
                    .collect()
            };
            accessor["min"] = json!(bound(|a, b| a < b));
            accessor["max"] = json!(bound(|a, b| a > b));
        }
        Ok(self.push(accessor))
    }
    fn shorts(&mut self, values: &[u16], shape: &str, target: Option<u32>) -> Result<usize> {
        let n = width(shape);
        if !values.len().is_multiple_of(n) {
            return Err(bad("accessor component count mismatch"));
        }
        let bytes: Vec<u8> = values.iter().flat_map(|v| v.to_le_bytes()).collect();
        let view = self.view(&bytes, target);
        let mut accessor = self.accessor(view, USHORT, values.len() / n, shape);
        if !values.is_empty() {
            let column = |i: usize| values.chunks(n).map(move |c| c[i]);
            accessor["min"] = json!((0..n).map(|i| column(i).min()).collect::<Vec<_>>());
            accessor["max"] = json!((0..n).map(|i| column(i).max()).collect::<Vec<_>>());
        }
        Ok(self.push(accessor))
    }
}

/// A 5 cm marker shared by every bone, sufficient to inspect the hierarchy (`cube`).
fn cube(buffer: &mut Buffer) -> Result<Value> {
    let h = 0.025;
    let vertices = [
        -h, -h, -h, h, -h, -h, h, h, -h, -h, h, -h, -h, -h, h, h, -h, h, h, h, h, -h, h, h,
    ];
    let indices = [
        0, 2, 1, 0, 3, 2, 4, 5, 6, 4, 6, 7, 0, 1, 5, 0, 5, 4, 3, 7, 6, 3, 6, 2, 0, 4, 7, 0, 7, 3,
        1, 2, 6, 1, 6, 5,
    ];
    let positions = buffer.floats(&vertices, "VEC3", Some(ARRAY_BUFFER))?;
    let faces = buffer.shorts(&indices, "SCALAR", Some(ELEMENT_ARRAY_BUFFER))?;
    Ok(json!({"name": "bone markers", "primitives": [
        {"attributes": {"POSITION": positions}, "indices": faces, "mode": 4}]}))
}

/// Node indices of one bone: the bone, its scale axis, its scale and the axis inverse (leaf).
type Handles = [usize; 4];

/// The document under construction.
struct Doc {
    buffer: Buffer,
    nodes: Vec<Value>,
    meshes: Vec<Value>,
    scene: Vec<usize>,
    skins: Option<Value>,
    animations: Vec<Value>,
    textures: Option<Textures>,
}

fn skeleton(body: &[u8]) -> Result<(Doc, Layout, Vec<Handles>, Vec<Pose>)> {
    let layout = parse_layout(body)?;
    let mut buffer = Buffer::default();
    let meshes = vec![cube(&mut buffer)?];
    let mut doc = Doc {
        buffer,
        nodes: Vec::new(),
        meshes,
        scene: Vec::new(),
        skins: None,
        animations: Vec::new(),
        textures: None,
    };
    let mut handles = Vec::new();
    let mut poses = Vec::new();
    for bone in &layout.bones {
        let pose = read_control(body, &layout, bone)?;
        let id = bone.bone_id;
        let main = doc.nodes.len();
        let (axis, scaled, leaf) = (main + 1, main + 2, main + 3);
        doc.nodes.extend([
            json!({"name": format!("bone_{id}"), "translation": pose.translation,
                   "rotation": pose.rotation, "mesh": 0, "children": [axis],
                   "extras": {"bone_id": id}}),
            json!({"name": format!("bone_{id}_scale_axis"),
                   "rotation": pose.scale_quaternion, "children": [scaled]}),
            json!({"name": format!("bone_{id}_scale"), "scale": pose.scale,
                   "children": [leaf]}),
            json!({"name": format!("bone_{id}_scale_axis_inverse"),
                   "rotation": inverse(&pose.scale_quaternion), "children": []}),
        ]);
        handles.push([main, axis, scaled, leaf]);
        poses.push(pose);
    }
    for (i, bone) in layout.bones.iter().enumerate() {
        match bone.parent {
            None => doc.scene.push(handles[i][0]),
            Some(parent) => children(&mut doc.nodes[handles[parent][3]]).push(json!(handles[i][0])),
        }
    }
    Ok((doc, layout, handles, poses))
}

fn children(node: &mut Value) -> &mut Vec<Value> {
    node["children"]
        .as_array_mut()
        .expect("bone nodes have children")
}

// ------------------------------------------------------------------ skin weights

/// Up to eight (joint, normalized weight) pairs per skin vertex, zero-padded.
type Influence = ([u16; 8], [f64; 8]);

/// Decode SK1, SK2 and accumulator influences for the skin position array (`skin_weights`).
fn skin_weights(
    body: &[u8],
    layout: &Layout,
    vertex_count: usize,
) -> Result<HashMap<usize, Influence>> {
    let r = Reader(body);
    let skin = r.us(12)?;
    if skin == 0 {
        return Err(bad("actor has no skin section"));
    }
    let (n1, n2, na) = (
        r.u16(skin)? as usize,
        r.u16(skin + 2)? as usize,
        r.u16(skin + 4)? as usize,
    );
    r.u8(skin + 6)?;
    if n1.max(n2).max(na) > 1024 {
        return Err(bad("invalid skin list count"));
    }
    let (p1, p2, pa) = (r.us(skin + 8)?, r.us(skin + 12)?, r.us(skin + 16)?);
    r.check(skin + p1, n1 * 0x40)?;
    r.check(skin + p2, n2 * 0x74)?;
    r.check(skin + pa, na * 0x44)?;
    let mut influences: HashMap<usize, Vec<(u16, u8)>> = HashMap::new();
    let vertices = |dst: usize, offset: usize, count: usize| {
        if !(dst + offset).is_multiple_of(12) {
            return Err(bad("unaligned skin destination"));
        }
        let start = (dst + offset) / 12;
        if start + count > vertex_count {
            return Err(bad("skin destination exceeds position array"));
        }
        Ok(start..start + count)
    };
    for i in 0..n1 {
        let o = skin + p1 + i * 0x40 + 0x30;
        let (dst, bone, count, offset) =
            (r.us(o + 4)?, r.u16(o + 8)?, r.u16(o + 10)?, r.u8(o + 12)?);
        for index in vertices(dst, offset as usize, count as usize)? {
            if influences.insert(index, vec![(bone, 255)]).is_some() {
                return Err(bad("overlapping primary skin lists"));
            }
        }
    }
    for i in 0..n2 {
        let o = skin + p2 + i * 0x74 + 0x60;
        let (weights, dst) = (r.us(o + 4)?, r.us(o + 8)?);
        let (bone0, bone1, count, offset) = (
            r.u16(o + 12)?,
            r.u16(o + 14)?,
            r.u16(o + 16)?,
            r.u8(o + 18)?,
        );
        r.check(skin + weights, 2 * count as usize)?;
        for (j, index) in vertices(dst, offset as usize, count as usize)?.enumerate() {
            if influences.contains_key(&index) {
                return Err(bad("overlapping primary skin lists"));
            }
            let (w0, w1) = (
                r.u8(skin + weights + 2 * j)?,
                r.u8(skin + weights + 2 * j + 1)?,
            );
            influences.insert(index, vec![(bone0, w0), (bone1, w1)]);
        }
    }
    for i in 0..na {
        let o = skin + pa + i * 0x44 + 0x30;
        let (indices, weights) = (r.us(o + 4)?, r.us(o + 12)?);
        let (bone, count) = (r.u16(o + 16)?, r.u16(o + 18)? as usize);
        r.check(skin + indices, count * 2)?;
        r.check(skin + weights, count)?;
        for j in 0..count {
            let index = r.u16(skin + indices + 2 * j)? as usize;
            let weight = r.u8(skin + weights + j)?;
            if index >= vertex_count {
                return Err(bad("accumulator vertex index exceeds position array"));
            }
            influences.entry(index).or_default().push((bone, weight));
        }
    }
    let bones = runtime_bone_order(layout)?.len();
    if influences
        .values()
        .flatten()
        .any(|(b, _)| *b as usize >= bones)
    {
        return Err(bad("skin joint index exceeds bone count"));
    }
    if influences.values().any(|v| v.len() > 8) {
        return Err(bad("more than eight skin influences"));
    }
    influences
        .into_iter()
        .map(|(index, values)| {
            let total: u32 = values.iter().map(|(_, w)| *w as u32).sum();
            if total == 0 {
                return Err(bad("zero skin weight"));
            }
            let mut joints = [0; 8];
            let mut weights = [0.0; 8];
            for (k, (b, w)) in values.iter().enumerate() {
                joints[k] = *b;
                weights[k] = *w as f64 / total as f64;
            }
            Ok((index, (joints, weights)))
        })
        .collect()
}

// ------------------------------------------------------------------ ANIM banks

/// Decoded setting (or in/out/ease payload) of one key, per channel.
#[derive(Clone, Default)]
struct Channels {
    scale: Option<Vec<f64>>,
    scale_quaternion: Option<Vec<f64>>,
    quaternion: Option<Vec<f64>>,
    euler: Option<Vec<f64>>,
    translation: Option<Vec<f64>>,
}

/// Bezier offsets, Hermite tangents or Squad controls of one channel, and its ease pair.
#[derive(Clone)]
struct Aux {
    inside: Vec<f64>,
    out: Vec<f64>,
    ease: Option<[f64; 2]>,
}

#[derive(Clone, Default)]
struct Auxiliary {
    scale: Option<Aux>,
    quaternion: Option<Aux>,
    euler: Option<Aux>,
    translation: Option<Aux>,
}

#[derive(Clone, Copy, PartialEq)]
enum Channel {
    Scale,
    Euler,
    Translation,
    Quaternion,
}

impl Channels {
    fn get(&self, c: Channel) -> &Option<Vec<f64>> {
        match c {
            Channel::Scale => &self.scale,
            Channel::Euler => &self.euler,
            Channel::Translation => &self.translation,
            Channel::Quaternion => &self.quaternion,
        }
    }
    fn set(&mut self, c: Channel, v: Vec<f64>) {
        *match c {
            Channel::Scale => &mut self.scale,
            Channel::Euler => &mut self.euler,
            Channel::Translation => &mut self.translation,
            Channel::Quaternion => &mut self.quaternion,
        } = Some(v);
    }
}

impl Auxiliary {
    fn get(&self, c: Channel) -> Result<&Aux> {
        match c {
            Channel::Scale => &self.scale,
            Channel::Euler => &self.euler,
            Channel::Translation => &self.translation,
            Channel::Quaternion => &self.quaternion,
        }
        .as_ref()
        .ok_or_else(|| bad("missing interpolation payload"))
    }
}

struct Key {
    time: f64,
    channels: Channels,
    auxiliary: Auxiliary,
}

struct Track {
    bone_id: u16,
    duration: f64,
    interpolation: u8,
    keys: Vec<Key>,
}

struct Bank {
    sequences: Vec<Vec<Track>>,
}

/// Decode setting channels in the order used by Do*Animation (`_settings`).
fn settings(r: &Reader, mut offset: usize, quantization: u8, kind: u8) -> Result<Channels> {
    if kind & !0x3F != 0 {
        return Err(bad(format!("unsupported animation type 0x{kind:02X}")));
    }
    let width = quant_width(quantization)?;
    let mut out = Channels::default();
    if kind & 0x10 != 0 {
        // ANIMTick's matrix branch reads twelve floats, independent of kind; nothing to export.
        for i in 0..12 {
            r.f32(offset + 4 * i)?;
        }
        return Ok(out);
    }
    if kind & 2 != 0 {
        out.scale = Some(r.numbers(offset, 3, quantization)?);
        offset += 3 * width;
        if kind & 0x20 != 0 {
            out.scale_quaternion = Some(r.numbers(offset, 4, quantization)?);
            offset += 4 * width;
        }
    }
    if kind & 8 != 0 {
        // Retail DoQuatAnimation uses fixed s16 / 16384, not quantizeInfo.
        out.quaternion = Some(r.numbers(offset, 4, 0x3E)?);
        offset += 8;
    } else if kind & 4 != 0 {
        out.euler = Some(r.numbers(offset, 3, quantization)?);
        offset += 3 * width;
    }
    if kind & 1 != 0 {
        out.translation = Some(r.numbers(offset, 3, quantization)?);
    }
    Ok(out)
}

/// Decode key +8 in scale, rotation, translation order (`_auxiliary`).
fn auxiliary(
    r: &Reader,
    mut offset: usize,
    quantization: u8,
    kind: u8,
    interpolation: u8,
) -> Result<Auxiliary> {
    let mut out = Auxiliary::default();
    if kind & 0x10 != 0 {
        return Ok(out);
    }
    let width = quant_width(quantization)?;
    let vectors =
        |offset: &mut usize, count: usize, quant: u8, element: usize, eased: bool| -> Result<Aux> {
            if *offset == 0 {
                return Err(bad("missing interpolation payload"));
            }
            let mut aux = Aux {
                inside: r.numbers(*offset, count, quant)?,
                out: r.numbers(*offset + count * element, count, quant)?,
                ease: None,
            };
            *offset += 2 * count * element;
            if eased {
                let ease = r.numbers(*offset, 2, 0x3E)?;
                *offset += 4;
                aux.ease = Some([ease[0], ease[1]]);
            }
            Ok(aux)
        };
    let scale_mode = (interpolation >> 2) & 3;
    if kind & 2 != 0 && matches!(scale_mode, 2 | 3) {
        out.scale = Some(vectors(
            &mut offset,
            3,
            quantization,
            width,
            scale_mode == 3,
        )?);
    }
    let rotation_mode = (interpolation >> 4) & 7;
    if kind & 8 != 0 && matches!(rotation_mode, 4 | 5) {
        // Quaternion controls are always s16/16384.
        out.quaternion = Some(vectors(&mut offset, 4, 0x3E, 2, rotation_mode == 5)?);
    } else if kind & 4 != 0 && matches!(rotation_mode, 2 | 3) {
        if rotation_mode == 3 {
            // Reserved by offset arithmetic; DoEulerAnimation panics instead.
            r.check(offset, 6 * width + 4)?;
            offset += 6 * width + 4;
        } else {
            out.euler = Some(vectors(&mut offset, 3, quantization, width, false)?);
        }
    }
    let translation_mode = interpolation & 3;
    if kind & 1 != 0 && matches!(translation_mode, 2 | 3) {
        out.translation = Some(vectors(
            &mut offset,
            3,
            quantization,
            width,
            translation_mode == 3,
        )?);
    }
    Ok(out)
}

/// Every sequence and bone track of a bank, with each key decoded (`parse_bank`).
fn parse_bank(data: &[u8]) -> Result<Bank> {
    let r = Reader(data);
    if r.u32(0)? != VERSION {
        return Err(bad(format!("unsupported ANIM version 0x{:08X}", r.u32(0)?)));
    }
    // ANIMGet relocates contiguous tables beginning at +0x18, regardless of +4.
    if r.u32(4)? != 0x18 {
        return Err(bad("sequence table must begin at 0x18 for retail ANIMGet"));
    }
    let (ns, nt, nk) = (
        r.u16(0xA)? as usize,
        r.u16(0xC)? as usize,
        r.u16(0xE)? as usize,
    );
    let (user_size, user_offset) = (r.us(0x10)?, r.us(0x14)?);
    let track_base = 0x18 + ns * 12;
    let key_base = track_base + nt * 16;
    r.check(0x18, ns * 12 + nt * 16 + nk * 12)?;
    if user_size != 0 {
        r.check(user_offset, user_size)?;
    }
    let mut sequences = Vec::new();
    for s in 0..ns {
        let o = 0x18 + s * 12;
        let (name, tracks, count) = (r.us(o)?, r.us(o + 4)?, r.u16(o + 8)? as usize);
        if tracks < track_base
            || !(tracks - track_base).is_multiple_of(16)
            || tracks + count * 16 > key_base
        {
            return Err(bad("sequence track span is outside track table"));
        }
        r.string(name)?;
        let mut sequence = Vec::new();
        for t in 0..count {
            let o = tracks + t * 16;
            let duration = r.f32(o)?;
            let (keys, total, bone_id) = (r.us(o + 4)?, r.u16(o + 8)? as usize, r.u16(o + 10)?);
            let (quant, kind, interp) = (r.u8(o + 12)?, r.u8(o + 13)?, r.u8(o + 14)?);
            if keys < key_base
                || !(keys - key_base).is_multiple_of(12)
                || keys + total * 12 > key_base + nk * 12
            {
                return Err(bad("track keyframe span is outside keyframe table"));
            }
            let mut previous = f64::NEG_INFINITY;
            let mut frames = Vec::new();
            for k in 0..total {
                let o = keys + 12 * k;
                let (time, setting, aux) = (r.f32(o)?, r.us(o + 4)?, r.us(o + 8)?);
                if time < previous {
                    return Err(bad("keyframe times decrease"));
                }
                previous = time;
                r.check(setting, 1)?;
                if aux != 0 {
                    r.check(aux, 1)?;
                }
                frames.push(Key {
                    time,
                    channels: settings(&r, setting, quant, kind)?,
                    auxiliary: auxiliary(&r, aux, quant, kind, interp)?,
                });
            }
            sequence.push(Track {
                bone_id,
                duration,
                interpolation: interp,
                keys: frames,
            });
        }
        sequences.push(sequence);
    }
    Ok(Bank { sequences })
}

// ------------------------------------------------------------------ curves

fn lerp(a: &[f64], b: &[f64], u: f64) -> Vec<f64> {
    a.iter()
        .zip(b)
        .map(|(x, y)| (1.0 - u) * x + u * y)
        .collect()
}

/// The retail ease remap, including its discontinuous coefficient (`ease_time`).
fn ease_time(u: f64, mut a: f64, mut b: f64) -> f64 {
    if u == 0.0 || u == 1.0 || a + b == 0.0 {
        return u;
    }
    let total = a + b;
    if total > 1.0 {
        a /= total;
        b /= total;
    }
    let coefficient = 0.5 - 1.0 / total;
    if u < a {
        return u * u * coefficient / a;
    }
    if u < 1.0 - b {
        return coefficient * (2.0 * u - a);
    }
    1.0 - (1.0 - u).powf(2.0) * coefficient / b
}

fn bezier(a: &[f64], b: &[f64], out: &[f64], inside: &[f64], u: f64) -> Vec<f64> {
    (0..a.len())
        .map(|i| {
            let (x, y) = (a[i], b[i]);
            (1.0 - u).powf(3.0) * x
                + 3.0 * u * (1.0 - u).powf(2.0) * (x + out[i])
                + 3.0 * u * u * (1.0 - u) * (y + inside[i])
                + u.powf(3.0) * y
        })
        .collect()
}

fn hermite(a: &[f64], b: &[f64], out: &[f64], inside: &[f64], u: f64) -> Vec<f64> {
    let u3 = u.powf(3.0);
    (0..a.len())
        .map(|i| {
            (2.0 * u3 - 3.0 * u * u + 1.0) * a[i]
                + (3.0 * u * u - 2.0 * u3) * b[i]
                + (u3 - 2.0 * u * u + u) * out[i]
                + (u3 - u * u) * inside[i]
        })
        .collect()
}

fn slerp(a: &[f64], b: &[f64], u: f64) -> Vec<f64> {
    let dot = sum(a.iter().zip(b).map(|(x, y)| x * y));
    if 1.0 + dot <= 1e-6 {
        let perp = [-b[1], b[0], -b[3], b[2]];
        let (s0, s1) = (
            ((1.0 - u) * std::f64::consts::PI / 2.0).sin(),
            (u * std::f64::consts::PI / 2.0).sin(),
        );
        return a.iter().zip(perp).map(|(x, y)| s0 * x + s1 * y).collect();
    }
    if 1.0 - dot <= 1e-6 {
        return lerp(a, b, u);
    }
    let angle = dot.clamp(-1.0, 1.0).acos();
    let divisor = angle.sin();
    a.iter()
        .zip(b)
        .map(|(x, y)| (((1.0 - u) * angle).sin() * x + (u * angle).sin() * y) / divisor)
        .collect()
}

fn squad(a: &[f64], out: &[f64], inside: &[f64], b: &[f64], u: f64) -> Vec<f64> {
    slerp(&slerp(a, b, u), &slerp(out, inside, u), 2.0 * u * (1.0 - u))
}

/// The keys around `time` and the fraction between them (`key_pair`); equal indices: hold.
fn key_pair(track: &Track, time: f64) -> Result<(usize, usize, f64)> {
    let keys = &track.keys;
    if keys.is_empty() {
        return Err(bad("track has no keys"));
    }
    if keys.len() == 1 || time <= keys[0].time {
        return Ok((0, 0, 0.0));
    }
    for i in 0..keys.len() - 1 {
        let (current, following) = (&keys[i], &keys[i + 1]);
        if time < following.time {
            let span = following.time - current.time;
            let u = if span != 0.0 {
                (time - current.time) / span
            } else {
                0.0
            };
            return Ok((i, i + 1, u));
        }
    }
    Ok((keys.len() - 1, keys.len() - 1, 0.0))
}

fn sample_vector(
    channel: Channel,
    mode: u8,
    current: &Key,
    following: &Key,
    u: f64,
) -> Result<Vec<f64>> {
    let missing = || bad("missing channel in a key");
    let mut a = current.channels.get(channel).clone().ok_or_else(missing)?;
    let mut b = following
        .channels
        .get(channel)
        .clone()
        .ok_or_else(missing)?;
    if mode == 0 {
        return Ok(a);
    }
    if channel == Channel::Euler && mode == 1 {
        // Retail wraps before interpolating.
        for i in 0..3 {
            if a[i] - b[i] > 180.0 {
                a[i] -= 360.0;
            } else if a[i] - b[i] < -180.0 {
                b[i] -= 360.0;
            }
        }
    }
    match mode {
        1 => Ok(lerp(&a, &b, u)),
        2 => Ok(bezier(
            &a,
            &b,
            &current.auxiliary.get(channel)?.out,
            &following.auxiliary.get(channel)?.inside,
            u,
        )),
        3 => {
            let (out, inside) = (
                current.auxiliary.get(channel)?,
                following.auxiliary.get(channel)?,
            );
            let (ease_in, ease_out) = segment_ease(out, inside)?;
            Ok(hermite(
                &a,
                &b,
                &out.out,
                &inside.inside,
                ease_time(u, ease_in, ease_out),
            ))
        }
        _ => Err(bad(format!("unsupported interpolation mode {mode}"))),
    }
}

/// The game's (ease-in, ease-out) pair for one key segment (`segment_ease`).
fn segment_ease(current: &Aux, following: &Aux) -> Result<(f64, f64)> {
    match (current.ease, following.ease) {
        (Some(c), Some(f)) => Ok((c[1], f[0])),
        _ => Err(bad("missing ease payload")),
    }
}

/// Sample a bone track at raw ANIM time; return only its animated channels (`sample_track`).
fn sample_track(track: &Track, time: f64) -> Result<Channels> {
    let (i, j, u) = key_pair(track, time)?;
    let (current, following) = (&track.keys[i], &track.keys[j]);
    if i == j {
        return Ok(current.channels.clone());
    }
    let channels = &current.channels;
    let mut out = Channels::default();
    for (channel, shift, mask) in [
        (Channel::Scale, 2, 3),
        (Channel::Euler, 4, 7),
        (Channel::Translation, 0, 3),
    ] {
        if channels.get(channel).is_some() {
            let mode = (track.interpolation >> shift) & mask;
            out.set(
                channel,
                sample_vector(channel, mode, current, following, u)?,
            );
        }
    }
    if let Some(a) = &channels.scale_quaternion {
        // The scale axis follows the scale channel's time; retail scale-quat tracks use mode 1.
        let b = following
            .channels
            .scale_quaternion
            .as_ref()
            .ok_or_else(|| bad("missing channel in a key"))?;
        out.scale_quaternion = Some(if (track.interpolation >> 2) & 3 == 0 {
            a.clone()
        } else {
            slerp(a, b, u)
        });
    }
    if let Some(a) = &channels.quaternion {
        let b = following
            .channels
            .quaternion
            .as_ref()
            .ok_or_else(|| bad("missing channel in a key"))?;
        let mode = (track.interpolation >> 4) & 7;
        out.quaternion = Some(match mode {
            0 => a.clone(),
            6 => slerp(a, b, u),
            4 | 5 => {
                let (out, inside) = (
                    current.auxiliary.get(Channel::Quaternion)?,
                    following.auxiliary.get(Channel::Quaternion)?,
                );
                let u = if mode == 5 {
                    let (ease_in, ease_out) = segment_ease(out, inside)?;
                    ease_time(u, ease_in, ease_out)
                } else {
                    u
                };
                squad(a, &out.out, &inside.inside, b, u)
            }
            _ => {
                return Err(bad(format!(
                    "unsupported quaternion interpolation mode {mode}"
                )));
            }
        });
    }
    Ok(out)
}

/// Sample one sequence at every raw frame onto the bone nodes (`add_animation`).
fn add_animation(
    doc: &mut Doc,
    sequence: &[Track],
    layout: &Layout,
    handles: &[Handles],
    name: String,
) -> Result<()> {
    let duration = if sequence.is_empty() {
        0.0
    } else {
        sequence
            .iter()
            .map(|t| t.duration)
            .fold(f64::NEG_INFINITY, f64::max)
    };
    if !duration.is_finite() || duration < 0.0 {
        return Err(bad("invalid animation duration"));
    }
    let mut frames: Vec<f64> = (0..=duration.floor() as usize).map(|t| t as f64).collect();
    if frames.last() != Some(&duration) {
        frames.push(duration);
    }
    let seconds: Vec<f64> = frames.iter().map(|t| t / FPS).collect();
    let mut time_accessor = None;
    let mut samplers = Vec::new();
    let mut channels = Vec::new();
    let mut bone_by_id = HashMap::new();
    for (index, bone) in layout.bones.iter().enumerate() {
        bone_by_id.entry(bone.bone_id).or_insert(index);
    }
    let buffer = &mut doc.buffer;
    let mut channel = |node: usize, path: &str, values: Vec<Vec<f64>>| -> Result<()> {
        let input = match time_accessor {
            Some(a) => a,
            None => *time_accessor.insert(buffer.floats(&seconds, "SCALAR", None)?),
        };
        let shape = if path == "rotation" { "VEC4" } else { "VEC3" };
        let output = buffer.floats(&values.concat(), shape, None)?;
        channels.push(json!({"sampler": samplers.len(), "target": {"node": node, "path": path}}));
        samplers.push(json!({"input": input, "output": output, "interpolation": "LINEAR"}));
        Ok(())
    };
    for track in sequence {
        // Aux tracks such as 0xFFFF have no actor bone.
        let Some(&index) = bone_by_id.get(&track.bone_id) else {
            continue;
        };
        let samples = frames
            .iter()
            .map(|&t| sample_track(track, t))
            .collect::<Result<Vec<_>>>()?;
        let [main, axis, scaled, leaf] = handles[index];
        let first = &samples[0];
        let all = |f: fn(&Channels) -> &Option<Vec<f64>>| -> Result<Vec<Vec<f64>>> {
            samples
                .iter()
                .map(|s| {
                    f(s).clone()
                        .ok_or_else(|| bad("missing channel in a sample"))
                })
                .collect()
        };
        if first.translation.is_some() {
            channel(main, "translation", all(|s| &s.translation)?)?;
        }
        if first.quaternion.is_some() || first.euler.is_some() {
            let rotations = samples
                .iter()
                .map(|s| match (&s.quaternion, &s.euler) {
                    (Some(q), _) => unit(q),
                    (None, Some(e)) => euler_xyz(e),
                    _ => Err(bad("missing channel in a sample")),
                })
                .collect::<Result<Vec<_>>>()?;
            channel(main, "rotation", rotations)?;
        }
        if first.scale.is_some() {
            channel(scaled, "scale", all(|s| &s.scale)?)?;
        }
        if first.scale_quaternion.is_some() {
            let axes = all(|s| &s.scale_quaternion)?
                .iter()
                .map(|q| unit(q))
                .collect::<Result<Vec<_>>>()?;
            let inverses = axes.iter().map(|q| inverse(q)).collect();
            channel(axis, "rotation", axes)?;
            channel(leaf, "rotation", inverses)?;
        }
    }
    if !channels.is_empty() {
        doc.animations.push(
            json!({"name": name, "samplers": samplers, "channels": channels,
            "extras": {"raw_duration": duration, "sample_rate_hz": 30}}),
        );
    }
    Ok(())
}

// ------------------------------------------------------------------ geometry

/// One display state's draw group of a geometry descriptor.
struct Group {
    positions: Vec<[f64; 3]>,
    normals: Vec<[f64; 3]>,
    uvs: Vec<[f64; 2]>,
    source_ids: Vec<usize>,
    triangles: Vec<[usize; 3]>,
    texture_id: Option<usize>,
}

/// Triangulate GX quads, triangles, strips and fans (`gx_triangles`).
fn gx_triangles(op: u8, v: &[usize]) -> Result<Vec<[usize; 3]>> {
    Ok(match op {
        0x80 => {
            if !v.len().is_multiple_of(4) {
                return Err(bad("GX quad count is not a multiple of four"));
            }
            v.chunks(4)
                .flat_map(|q| [[q[0], q[1], q[2]], [q[0], q[2], q[3]]])
                .collect()
        }
        0x90 => {
            if !v.len().is_multiple_of(3) {
                return Err(bad("GX triangle count is not a multiple of three"));
            }
            v.chunks(3).map(|t| [t[0], t[1], t[2]]).collect()
        }
        0x98 => (2..v.len())
            .map(|i| {
                if i % 2 == 0 {
                    [v[i - 2], v[i - 1], v[i]]
                } else {
                    [v[i - 1], v[i - 2], v[i]]
                }
            })
            .collect(),
        0xA0 => (2..v.len()).map(|i| [v[0], v[i - 1], v[i]]).collect(),
        _ => return Err(bad(format!("unsupported GX primitive 0x{op:02X}"))),
    })
}

/// Position, normal and TEX0 indices of each vertex in a display list.
type Record = (usize, Option<usize>, Option<usize>);

/// Byte widths of POS, NRM, CLR0, CLR1, TEX0 and TEX1 in a display list's vertices
/// (`vertex_layout`). Display state 2 holds the list's vertex descriptor: 2-bit fields from bit
/// 0, the first (matrix index) unused, then POS, NRM, CLR0, CLR1, TEX0, TEX1; 0 none, 2 index8,
/// 3 index16. Every retail body (54 characters, 938 lists) uses only these.
fn vertex_layout(vcd: u32) -> Result<[usize; 6]> {
    let field = |k: u32| (vcd >> (2 * k)) & 3;
    if field(0) != 0 || vcd >> 14 != 0 || (0..16).any(|k| field(k) == 1) || field(1) == 0 {
        return Err(bad(format!("unsupported vertex descriptor 0x{vcd:X}")));
    }
    Ok(std::array::from_fn(|i| match field(i as u32 + 1) {
        0 => 0,
        2 => 1,
        _ => 2,
    }))
}

/// Read GX FIFO position, normal and TEX0 indices laid out as `widths` (`decode_display_list`).
fn decode_display_list(
    data: &[u8],
    offset: usize,
    size: usize,
    widths: &[usize; 6],
    position_count: usize,
) -> Result<(Vec<Record>, Vec<[usize; 3]>)> {
    let r = Reader(data);
    r.check(offset, size)?;
    let end = offset + size;
    let stride: usize = widths.iter().sum();
    let mut cursor = offset;
    let mut vertices = Vec::new();
    let mut triangles = Vec::new();
    while cursor < end {
        let op = data[cursor];
        match op {
            0 => cursor += 1, // 32-byte display-list padding
            0x61 => {
                // BP register write: opcode + u32
                r.check(cursor, 5)?;
                cursor += 5;
            }
            0x08 => {
                // CP register write: opcode + u8 + u32
                r.check(cursor, 6)?;
                cursor += 6;
            }
            0x80 | 0x90 | 0x98 | 0xA0 => {
                r.check(cursor, 3)?;
                let count = r.u16(cursor + 1)? as usize;
                if cursor + 3 + count * stride > end {
                    return Err(bad("GX vertex list exceeds its declared size"));
                }
                let first = vertices.len();
                for i in 0..count {
                    let mut address = cursor + 3 + i * stride;
                    let mut indices = [None; 6];
                    for (index, &width) in indices.iter_mut().zip(widths) {
                        *index = match width {
                            0 => None,
                            1 => Some(data[address] as usize),
                            _ => Some(r.u16(address)? as usize),
                        };
                        address += width;
                    }
                    let pos = indices[0].unwrap_or_default();
                    if pos >= position_count {
                        return Err(bad(format!("GX position index {pos} >= {position_count}")));
                    }
                    vertices.push((pos, indices[1], indices[4]));
                }
                let ids: Vec<usize> = (first..first + count).collect();
                triangles.extend(gx_triangles(op, &ids)?);
                cursor += 3 + count * stride;
            }
            _ => {
                return Err(bad(format!(
                    "unsupported GX command 0x{op:02X} at 0x{cursor:X}"
                )));
            }
        }
    }
    if triangles.is_empty() {
        return Err(bad("display list has no triangles"));
    }
    Ok((vertices, triangles))
}

/// One geometry descriptor: its index, draw groups, vertex strides and position count.
type Descriptor = (usize, Vec<Group>, Vec<usize>, usize);

/// Read an s16 attribute array as `count` vectors of `n` values.
fn attribute(
    r: &Reader,
    at: usize,
    total: usize,
    quant: u8,
    n: usize,
    step: usize,
) -> Result<Vec<Vec<f64>>> {
    (0..total)
        .map(|j| {
            let o = at + j * step;
            (0..n)
                .map(|k| Ok(r.u16(o + 2 * k)? as i16 as f64 / (1u32 << (quant & 15)) as f64))
                .collect()
        })
        .collect()
}

/// Read unrelocated .gpc geo palette offsets (palette and layout relative) (`geometry_descriptors`).
fn geometry_descriptors(body: &[u8]) -> Result<Vec<Descriptor>> {
    let r = Reader(body);
    let geo = r.us(8)?;
    let (version, count, descriptors) = (r.u32(geo)?, r.us(geo + 12)?, r.us(geo + 16)?);
    if version != GEO_VERSION || count > 1024 {
        return Err(bad("invalid geo palette"));
    }
    r.check(geo + descriptors, count * 8)?;
    let mut out = Vec::new();
    for i in 0..count {
        let layout = geo + r.us(geo + descriptors + i * 8)?;
        let (pos, tex, light, display) = (
            r.us(layout)?,
            r.us(layout + 8)?,
            r.us(layout + 12)?,
            r.us(layout + 16)?,
        );
        if pos == 0 || display == 0 {
            continue;
        }
        let (array, total) = (r.us(layout + pos)?, r.u16(layout + pos + 4)? as usize);
        let (quant, components) = (r.u8(layout + pos + 6)?, r.u8(layout + pos + 7)? as usize);
        if quant >> 4 != 3 || !matches!(components, 3 | 6) {
            return Err(bad(format!(
                "unsupported position encoding 0x{quant:02X}/{components}"
            )));
        }
        r.check(layout + array, total * components * 2)?;
        let vertices = attribute(&r, layout + array, total, quant, 3, components * 2)?;
        let mut normals = Vec::new();
        if light != 0 {
            let (array, count) = (r.us(layout + light)?, r.u16(layout + light + 4)? as usize);
            let (quant, components) = (
                r.u8(layout + light + 6)?,
                r.u8(layout + light + 7)? as usize,
            );
            if quant >> 4 != 3 || !matches!(components, 3 | 6) {
                return Err(bad("unsupported normal encoding"));
            }
            r.check(layout + array, count * components * 2)?;
            for xyz in attribute(&r, layout + array, count, quant, 3, components * 2)? {
                let length = sum(xyz.iter().map(|v| v * v)).sqrt();
                normals.push(if length != 0.0 {
                    [xyz[0] / length, xyz[1] / length, xyz[2] / length]
                } else {
                    [0.0, 0.0, 1.0]
                });
            }
        }
        let mut texcoords = Vec::new();
        if tex != 0 && r.u8(layout + 0x14)? != 0 {
            let (array, count) = (r.us(layout + tex)?, r.u16(layout + tex + 4)? as usize);
            let (quant, components) = (r.u8(layout + tex + 6)?, r.u8(layout + tex + 7)?);
            if quant >> 4 != 3 || components != 2 {
                return Err(bad("unsupported TEX0 encoding"));
            }
            r.check(layout + array, count * 4)?;
            texcoords = attribute(&r, layout + array, count, quant, 2, 4)?;
        }
        let (states, num_states) = (
            r.us(layout + display + 4)?,
            r.u16(layout + display + 8)? as usize,
        );
        if num_states > 1024 {
            return Err(bad("invalid display state count"));
        }
        let mut groups = Vec::new();
        let mut formats = Vec::new();
        let mut texture_id = None;
        let mut vcd = None;
        for j in 0..num_states {
            let s = layout + states + j * 16;
            let (state_id, setting) = (r.u8(s)?, r.u32(s + 4)?);
            let (primitive, size) = (r.us(s + 8)?, r.us(s + 12)?);
            // State 1 binds texture (low 13 bits) to texture map (bits 13-15). 0x1511 on map 0
            // is the primary texture; map 1 (0x2000, mostly 0x1500 settings) is an additional
            // TEV stage that the simple glTF material omits (King Boo's 0x1511200A is one).
            if state_id == 1 && setting >> 16 == 0x1511 && setting & 0xE000 == 0 {
                texture_id = Some((setting & 0x1FFF) as usize);
            }
            if state_id == 2 {
                vcd = Some(setting);
            }
            if primitive == 0 || size == 0 {
                continue;
            }
            let widths = vertex_layout(
                vcd.ok_or_else(|| bad("display list before its vertex descriptor"))?,
            )?;
            let (records, triangles) =
                decode_display_list(body, layout + primitive, size, &widths, total)?;
            let stride = widths.iter().sum();
            if !formats.contains(&stride) {
                formats.push(stride);
            }
            let mut group = Group {
                positions: Vec::new(),
                normals: Vec::new(),
                uvs: Vec::new(),
                source_ids: Vec::new(),
                triangles,
                texture_id,
            };
            for (p, n, t) in records {
                if let Some(n) = n {
                    let normal = normals
                        .get(n)
                        .ok_or_else(|| bad("GX normal index exceeds normal array"))?;
                    group.normals.push(*normal);
                }
                if let Some(t) = t {
                    let uv = texcoords
                        .get(t)
                        .ok_or_else(|| bad("GX TEX0 index exceeds texture coordinate array"))?;
                    group.uvs.push([uv[0], uv[1]]);
                }
                let v = &vertices[p];
                group.positions.push([v[0], v[1], v[2]]);
                group.source_ids.push(p);
            }
            groups.push(group);
        }
        formats.sort();
        if !groups.is_empty() {
            out.push((i, groups, formats, total));
        }
    }
    Ok(out)
}

// ------------------------------------------------------------------ textures and materials

struct Textures {
    images: Vec<Value>,
    samplers: Vec<Value>,
    textures: Vec<Value>,
    materials: Vec<Value>,
}

/// Embed every body texture, using the Clone Maker GX decoder; a painted `tNN_WxH.png` in
/// `painted` replaces its texture as is (`add_textures`). Returns the untextured material.
fn add_textures(doc: &mut Doc, body: &[u8], painted: Option<&Path>) -> Result<usize> {
    let archive = Gpc::new(body.to_vec())?;
    let wrap = |mode: u8| match mode {
        0 => Ok(33071),
        1 => Ok(10497),
        2 => Ok(33648),
        _ => Err(bad("unsupported texture wrap mode")),
    };
    let filter = |mode: u8| if mode == 0 { 9728 } else { 9729 };
    let mut t = Textures {
        images: Vec::new(),
        samplers: Vec::new(),
        textures: Vec::new(),
        materials: Vec::new(),
    };
    for item in &archive.textures {
        let index = item.index;
        let edited = painted.map(|dir| dir.join(custom_textures::file_name(item)));
        let png = match edited {
            Some(path) if path.is_file() => {
                let png = std::fs::read(&path).map_err(|e| Error::from(e).at_path(&path))?;
                let size =
                    |o: usize| u32::from_be_bytes(png[o..o + 4].try_into().unwrap()) as usize;
                if png.len() < 24
                    || png[..8] != *b"\x89PNG\r\n\x1a\n"
                    || png[12..16] != *b"IHDR"
                    || (size(16), size(20)) != (item.w, item.h)
                {
                    return Err(bad(format!(
                        "{} is not a {}x{} PNG",
                        path.display(),
                        item.w,
                        item.h
                    )));
                }
                png
            }
            _ => png_bytes(
                item.w,
                item.h,
                &decode_texture(&item.bytes(&archive.data)?, item)?,
            )?,
        };
        t.images.push(json!({"name": format!("texture_{index:02}"),
            "uri": format!("data:image/png;base64,{}", base64::engine::general_purpose::STANDARD.encode(png))}));
        let hdr = Reader(body).bytes::<4>(item.hdr + 12)?;
        t.samplers
            .push(json!({"wrapS": wrap(hdr[0])?, "wrapT": wrap(hdr[1])?,
            "minFilter": filter(hdr[2]), "magFilter": filter(hdr[3])}));
        t.textures.push(json!({"source": index, "sampler": index}));
        t.materials.push(
            json!({"name": format!("texture_{index:02}"), "doubleSided": true,
            "alphaMode": "MASK", "alphaCutoff": 0.5,
            "pbrMetallicRoughness": {"baseColorTexture": {"index": index},
                "metallicFactor": 0, "roughnessFactor": 1}}),
        );
    }
    let plain = t.materials.len();
    t.materials
        .push(json!({"name": "untextured body", "doubleSided": true,
        "pbrMetallicRoughness": {"baseColorFactor": [0.7, 0.7, 0.7, 1],
            "metallicFactor": 0, "roughnessFactor": 1}}));
    doc.textures = Some(t);
    Ok(plain)
}

/// Attach textured skin geometry and rigid geometry below its bone (`add_geometry`).
fn add_geometry(
    doc: &mut Doc,
    body: &[u8],
    layout: &Layout,
    handles: &[Handles],
    poses: &[Pose],
    painted: Option<&Path>,
) -> Result<()> {
    let skin_id = layout.skin_file_id as usize;
    let plain = add_textures(doc, body, painted)?;
    let mut rigid = HashMap::new();
    for (i, bone) in layout.bones.iter().enumerate() {
        if bone.geometry_id != 0xFFFF {
            rigid.entry(bone.geometry_id as usize).or_insert(i);
        }
    }
    for (descriptor, groups, strides, total) in geometry_descriptors(body)? {
        let mesh = doc.meshes.len();
        let skinned = descriptor == skin_id;
        let influences = if skinned {
            skin_weights(body, layout, total)?
        } else {
            HashMap::new()
        };
        let mut primitives = Vec::new();
        for group in &groups {
            if group.positions.len() > 65535 {
                return Err(bad("geometry group needs u32 indices"));
            }
            let b = &mut doc.buffer;
            let mut attributes = serde_json::Map::new();
            attributes.insert(
                "POSITION".into(),
                json!(b.floats(&group.positions.concat(), "VEC3", Some(ARRAY_BUFFER))?),
            );
            if !group.normals.is_empty() {
                attributes.insert(
                    "NORMAL".into(),
                    json!(b.floats(&group.normals.concat(), "VEC3", Some(ARRAY_BUFFER))?),
                );
            }
            if !group.uvs.is_empty() {
                attributes.insert(
                    "TEXCOORD_0".into(),
                    json!(b.floats(&group.uvs.concat(), "VEC2", Some(ARRAY_BUFFER))?),
                );
            }
            if skinned {
                let mut missing: Vec<_> = group
                    .source_ids
                    .iter()
                    .filter(|i| !influences.contains_key(i))
                    .copied()
                    .collect();
                if !missing.is_empty() {
                    missing.sort();
                    missing.dedup();
                    return Err(bad(format!(
                        "display list references {} unweighted skin vertices: {:?}",
                        missing.len(),
                        &missing[..missing.len().min(10)]
                    )));
                }
                let sets = 1 + group
                    .source_ids
                    .iter()
                    .any(|i| influences[i].1[4..].iter().any(|w| *w != 0.0))
                    as usize;
                for set in 0..sets {
                    let start = set * 4;
                    let joints: Vec<u16> = group
                        .source_ids
                        .iter()
                        .flat_map(|i| influences[i].0[start..start + 4].to_vec())
                        .collect();
                    let weights: Vec<f64> = group
                        .source_ids
                        .iter()
                        .flat_map(|i| influences[i].1[start..start + 4].to_vec())
                        .collect();
                    attributes.insert(
                        format!("JOINTS_{set}"),
                        json!(b.shorts(&joints, "VEC4", Some(ARRAY_BUFFER))?),
                    );
                    attributes.insert(
                        format!("WEIGHTS_{set}"),
                        json!(b.floats(&weights, "VEC4", Some(ARRAY_BUFFER))?),
                    );
                }
            }
            let faces: Vec<u16> = group.triangles.concat().iter().map(|&i| i as u16).collect();
            let indices = b.shorts(&faces, "SCALAR", Some(ELEMENT_ARRAY_BUFFER))?;
            let material = match group.texture_id {
                None => plain,
                Some(t) if t >= plain => {
                    return Err(bad(format!(
                        "display state references texture {t} >= {plain}"
                    )));
                }
                Some(t) => t,
            };
            primitives.push(json!({"attributes": attributes, "indices": indices,
                "mode": 4, "material": material}));
        }
        doc.meshes
            .push(json!({"name": format!("geo_{descriptor}"), "primitives": primitives}));
        let node = doc.nodes.len();
        doc.nodes
            .push(json!({"name": format!("geo_{descriptor}"), "mesh": mesh,
            "extras": {"descriptor": descriptor, "vertex_strides": strides}}));
        if skinned {
            if doc.skins.is_none() {
                let matrices = inverse_bind_matrices(layout, poses)?;
                // glTF matrices are column-major
                let values: Vec<f64> = matrices
                    .iter()
                    .flat_map(|m| (0..4).flat_map(move |c| (0..4).map(move |r| m[r][c])))
                    .collect();
                let ibm = doc.buffer.floats(&values, "MAT4", None)?;
                let order = runtime_bone_order(layout)?;
                let root = layout.root.ok_or_else(|| bad("actor has no root bone"))?;
                doc.skins = Some(json!({"name": "actor skin", "inverseBindMatrices": ibm,
                    "joints": order.iter().map(|&i| handles[i][3]).collect::<Vec<_>>(),
                    "skeleton": handles[root][0]}));
            }
            doc.nodes[node]["skin"] = json!(0);
            doc.scene.push(node);
        } else if let Some(&bone) = rigid.get(&descriptor) {
            children(&mut doc.nodes[handles[bone][3]]).push(json!(node));
        } else {
            return Err(bad(format!(
                "unowned rigid geometry descriptor {descriptor}"
            )));
        }
    }
    Ok(())
}

// ------------------------------------------------------------------ export

/// A finished export: the glTF text and what it holds.
pub struct Export {
    /// The self-contained glTF 2.0 JSON (buffers and images embedded as data URIs).
    pub json: String,
    /// Number of bones in the skeleton.
    pub bones: usize,
    /// Number of animations (Blender Actions).
    pub animations: usize,
}

/// Export charID `c` from a clean game: textured body, skeleton and either the normal swing or
/// every non-empty sequence of `DEFAULT_ANIMATION_BANKS`. `painted` is a folder of
/// `tNN_WxH.png` pictures (Clone Maker's "Export textures") used instead of those textures.
pub fn export(
    disc: &Disc,
    dol: &Dol,
    c: usize,
    all_animations: bool,
    painted: Option<&Path>,
) -> Result<Export> {
    if c >= 54 {
        return Err(bad(format!("character id out of range: {c}")));
    }
    let body = disc.load_subfile(dol, c * 19)?;
    let (mut doc, layout, handles, poses) = skeleton(&body)?;
    add_geometry(&mut doc, &body, &layout, &handles, &poses, painted)?;
    let clips: Vec<(usize, Option<usize>)> = if all_animations {
        DEFAULT_ANIMATION_BANKS.iter().map(|&t| (t, None)).collect()
    } else {
        vec![(DEFAULT_CLIP.0, Some(DEFAULT_CLIP.1))]
    };
    for (track, only) in clips {
        let bank = parse_bank(&disc.load_subfile(dol, c * 19 + track)?)
            .map_err(|e| bad(format!("animation track {track}: {}", e.message)))?;
        for (s, sequence) in bank.sequences.iter().enumerate() {
            if only.is_some_and(|o| o != s) || (only.is_none() && sequence.is_empty()) {
                continue;
            }
            add_animation(
                &mut doc,
                sequence,
                &layout,
                &handles,
                animation_name(track, s),
            )?;
        }
        if let Some(o) = only
            && o >= bank.sequences.len()
        {
            return Err(bad(format!("sequence {o} is outside bank")));
        }
    }
    let Doc {
        buffer,
        nodes,
        meshes,
        scene,
        skins,
        animations,
        textures,
    } = doc;
    let count = animations.len();
    let mut out = json!({
        "asset": {"version": "2.0", "generator": "MSSB Clone Maker"},
        "scene": 0, "scenes": [{"nodes": scene}], "nodes": nodes, "meshes": meshes,
        "extras": {"character_id": c, "bone_count": layout.bones.len(),
                   "coordinate_system": "game and glTF: right-handed, +Y up"},
        "buffers": [{"byteLength": buffer.data.len(),
            "uri": format!("data:application/octet-stream;base64,{}",
                base64::engine::general_purpose::STANDARD.encode(&buffer.data))}],
        "bufferViews": buffer.views, "accessors": buffer.accessors,
    });
    if let Some(t) = textures {
        out["images"] = json!(t.images);
        out["samplers"] = json!(t.samplers);
        out["textures"] = json!(t.textures);
        out["materials"] = json!(t.materials);
    }
    if let Some(skin) = skins {
        out["skins"] = json!([skin]);
    }
    if !animations.is_empty() {
        out["animations"] = json!(animations);
    }
    Ok(Export {
        json: out.to_string() + "\n",
        bones: layout.bones.len(),
        animations: count,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A translation key; `aux` is (in, out, ease).
    fn key(time: f64, value: Vec<f64>, aux: Option<Aux>) -> Key {
        Key {
            time,
            channels: Channels {
                translation: Some(value),
                ..Default::default()
            },
            auxiliary: Auxiliary {
                translation: aux,
                ..Default::default()
            },
        }
    }
    fn aux(inside: [f64; 3], out: [f64; 3], ease: Option<[f64; 2]>) -> Option<Aux> {
        Some(Aux {
            inside: inside.to_vec(),
            out: out.to_vec(),
            ease,
        })
    }

    #[test]
    fn hold_lerp_bezier_and_eased_hermite() {
        let track = |mode, keys| Track {
            bone_id: 0,
            duration: 10.0,
            interpolation: mode,
            keys,
        };
        let keys = || {
            vec![
                key(0.0, vec![0.0; 3], aux([0.0; 3], [2.0, 0.0, 0.0], None)),
                key(
                    10.0,
                    vec![10.0, 0.0, 0.0],
                    aux([-2.0, 0.0, 0.0], [0.0; 3], None),
                ),
            ]
        };
        let at = |mode, t| {
            sample_track(&track(mode, keys()), t)
                .unwrap()
                .translation
                .unwrap()
        };
        assert_eq!(at(0, 5.0), [0.0; 3]);
        assert_eq!(at(1, 5.0), [5.0, 0.0, 0.0]);
        assert_eq!(at(2, 5.0), [5.0, 0.0, 0.0]);
        assert_eq!(ease_time(0.5, 0.25, 0.25), -1.125);
        assert_eq!(ease_time(0.5, 0.0, 0.0), 0.5);
        let eased = vec![
            key(
                0.0,
                vec![0.0; 3],
                aux([0.0; 3], [4.0, 0.0, 0.0], Some([0.0, 0.25])),
            ),
            key(
                10.0,
                vec![10.0, 0.0, 0.0],
                aux([2.0, 0.0, 0.0], [0.0; 3], Some([0.25, 0.0])),
            ),
        ];
        let v = sample_track(&track(3, eased), 5.0)
            .unwrap()
            .translation
            .unwrap();
        assert!((v[0] - hermite(&[0.0], &[10.0], &[4.0], &[2.0], -1.125)[0]).abs() < 1e-12);
    }

    #[test]
    fn slerp_handles_opposite_quaternions() {
        let q = slerp(&[0.0, 0.0, 0.0, 1.0], &[0.0, 0.0, 0.0, -1.0], 0.5);
        assert!((q[2] - 0.5f64.sqrt()).abs() < 1e-12 && (q[3] - 0.5f64.sqrt()).abs() < 1e-12);
    }

    #[test]
    fn gx_primitives_and_names() {
        assert_eq!(
            gx_triangles(0x98, &[0, 1, 2, 3]).unwrap(),
            [[0, 1, 2], [2, 1, 3]]
        );
        assert_eq!(
            gx_triangles(0x80, &[0, 1, 2, 3]).unwrap(),
            [[0, 1, 2], [0, 2, 3]]
        );
        let compact = [0xA0, 0, 3, 0, 0, 1, 0, 2, 0, 0, 0, 0, 0, 0, 0, 0];
        let layout = vertex_layout(0x28).unwrap();
        assert_eq!(layout, [1, 1, 0, 0, 0, 0]);
        assert_eq!(vertex_layout(0x2CBC).unwrap(), [2, 2, 1, 0, 2, 1]);
        assert!(vertex_layout(0x2D).is_err());
        assert_eq!(
            decode_display_list(&compact, 0, compact.len(), &layout, 3).unwrap(),
            (
                vec![(0, Some(0), None), (1, Some(0), None), (2, Some(0), None)],
                vec![[0, 1, 2]]
            )
        );
        assert_eq!(animation_name(2, 2), "t2_s02_id77_BatterSwingBat");
        assert_eq!(animation_name(15, 2), "t15_s02_id107_WinningCelebration");
        assert_eq!(animation_name(7, 1), "t7_s01");
        assert_eq!(animation_name(15, 0), "t15_s00_id105");
        assert_eq!(animation_name(3, 40), "t3_s40_id85_BatterHoldCharge");
    }
}
