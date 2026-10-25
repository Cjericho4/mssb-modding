"""MusyX sound groups and DSP-ADPCM samples of Mario Superstar Baseball (GYQE01).

A sound-group file (`sound.N`, DOL table `lbl_800EF508`) is stored (not LZSS) and starts with four
`{u32 offset, u32 size}` pairs, which `fn_80021650` / `fn_80021758` turn into pointers and
`fn_80021518` hands to `sndPushGroup(proj, gid, samp, sdir, pool)` (src/Musyx/s_data.c):

    chunk 0  proj  GROUP_DATA list (nextOff, id, type, macroOff, sampleOff, ...) + FX table
    chunk 1  sdir  SDIR_DATA[] (0x20 each, id 0xFFFF ends) + one 0x28-byte SNDADPCMinfo per sample
                   {u16 numCoef = 8, u8 initialPS, u8 loopPS, s16 loopY0, loopY1, s16 coefTab[8][2]}
    chunk 2  pool  POOL_DATA {macroOff, curveOff, keymapOff, layerOff} + MEM_DATA lists
    chunk 3  samp  DSP-ADPCM sample data (sdir.offset is a byte offset into this chunk)

Everything is big endian.  Field layouts are the ones in include/musyx/musyx_priv.h.

Python 3.8+, standard library only.
"""
import math
import struct
import wave

CHUNKS = ("proj", "sdir", "pool", "samp")
SDIR_SIZE = 0x20
ADPCM_INFO_SIZE = 0x28
FRAME_SAMPLES = 14
FRAME_BYTES = 8

# Macro opcodes (synthmacros.c `macHandle` switch) whose para[0] >> 16 names another macro.
MACRO_REF_OPS = {0x02: "IfKey", 0x03: "IfVelocity", 0x06: "Goto", 0x08: "PlayMacro",
                 0x0A: "IfModulation", 0x13: "IfRandom", 0x25: "Gosub", 0x28: "TrapEvent"}
OP_START_SAMPLE = 0x10
OP_END = 0x00


class SoundError(ValueError):
    pass


def _u16(b, o):
    return struct.unpack_from(">H", b, o)[0]


def _u32(b, o):
    return struct.unpack_from(">I", b, o)[0]


def _id_list(b, o):
    """MusyX id list (`ScanIDList`): plain u16 ids, or `0x8000|first, last` ranges, ending 0xFFFF."""
    out = []
    while o + 2 <= len(b):
        v = _u16(b, o)
        if v == 0xFFFF:
            break
        if v & 0x8000:
            first, last = v & 0x3FFF, _u16(b, o + 2)
            out.extend(range(first, last + 1))
            o += 4
        else:
            out.append(v)
            o += 2
    return out


class Sample:
    """One SDIR entry + its ADPCM info + its bytes in the samp chunk."""

    def __init__(self, index, entry, info, data):
        (self.id, self.ref_cnt, self.offset, self.addr, self.info, self.length_word,
         self.loop_start, self.loop_length, self.extra) = struct.unpack(">HHIIIIIII", entry)
        self.index = index
        self.adpcm = info  # 0x28 bytes or None
        self.data = data

    @property
    def rate(self):
        return self.info & 0xFFFF

    @property
    def root_key(self):
        return self.info >> 24

    @property
    def samples(self):
        return self.length_word & 0xFFFFFF

    @property
    def comp_type(self):
        return self.length_word >> 24

    @property
    def coefs(self):
        return struct.unpack_from(">16h", self.adpcm, 8) if self.adpcm else None

    def byte_size(self):
        return adpcm_bytes(self.samples)

    def decode(self):
        if self.comp_type != 0 or not self.adpcm:
            raise SoundError("sample 0x%X: compression type %d not supported (only DSP-ADPCM, 0)"
                             % (self.id, self.comp_type))
        return dsp_decode(self.data[:self.byte_size()], self.samples, self.coefs)


class Group:
    """A parsed sound-group file."""

    def __init__(self, data):
        data = bytes(data)
        if len(data) < 0x20 or _u32(data, 0) != 0x20:
            raise SoundError("not a MusyX group file (header word 0 must be 0x20)")
        self.raw = data
        self.chunks = {}
        for i, name in enumerate(CHUNKS):
            o, s = struct.unpack_from(">II", data, i * 8)
            if o + s > len(data):
                raise SoundError("chunk %s runs past the end of the file" % name)
            self.chunks[name] = data[o:o + s]
        self._parse_proj()
        self._parse_sdir()
        self._parse_pool()

    # -- proj ------------------------------------------------------------------------------------
    def _parse_proj(self):
        p = self.chunks["proj"]
        self.groups = []
        o = 0
        while o + 0x28 <= len(p):
            nxt, gid, gtype, mac, smp, cur, km, lay, d0 = struct.unpack_from(">IHHIIIIII", p, o)
            if nxt == 0xFFFFFFFF:
                break
            g = {"id": gid, "type": gtype, "macros": _id_list(p, mac), "samples": _id_list(p, smp),
                 "curves": _id_list(p, cur), "keymaps": _id_list(p, km), "layers": _id_list(p, lay),
                 "fx": []}
            if gtype == 1:  # FX group: data.fx.tableOff -> FX_DATA {u16 num, u16 pad, FX_TAB[num]}
                n = _u16(p, d0)
                for k in range(n):
                    fid, mac_id, maxv, prio, vol, pan, key, vgrp = struct.unpack_from(">HHBBBBBB", p, d0 + 4 + k * 10)
                    g["fx"].append({"id": fid, "macro": mac_id, "max_voices": maxv, "priority": prio,
                                    "volume": vol, "pan": pan, "key": key, "vgroup": vgrp})
            self.groups.append(g)
            if nxt <= o:            # nextOff is relative to the proj start (sndPushGroup)
                break
            o = nxt

    # -- sdir ------------------------------------------------------------------------------------
    def _parse_sdir(self):
        s, samp = self.chunks["sdir"], self.chunks["samp"]
        self.samples = []
        o = 0
        while o + 2 <= len(s) and _u16(s, o) != 0xFFFF:
            entry = s[o:o + SDIR_SIZE]
            extra = _u32(entry, 0x1C)
            info = s[extra:extra + ADPCM_INFO_SIZE] if extra else None
            off = _u32(entry, 4)
            n = (_u32(entry, 0x10) & 0xFFFFFF)
            smp = Sample(len(self.samples), entry, info, samp[off:off + adpcm_bytes(n)])
            self.samples.append(smp)
            o += SDIR_SIZE

    def sample(self, sid):
        for s in self.samples:
            if s.id == sid:
                return s
        raise SoundError("no sample 0x%X in this group" % sid)

    # -- pool ------------------------------------------------------------------------------------
    def _mem_list(self, off):
        p, out = self.chunks["pool"], {}
        o = off
        while o + 8 <= len(p):
            nxt, mid = _u32(p, o), _u16(p, o + 4)
            if nxt == 0xFFFFFFFF:
                break
            out[mid] = (o + 8, o + nxt)
            if nxt == 0:
                break
            o += nxt
        return out

    def _parse_pool(self):
        p = self.chunks["pool"]
        if len(p) < 0x10:
            self.macros, self.keymaps, self.layers = {}, {}, {}
            return
        mac, cur, km, lay = struct.unpack_from(">IIII", p, 0)
        self.macros = self._mem_list(mac)
        self.keymaps = self._mem_list(km)
        self.layers = self._mem_list(lay)

    def macro_steps(self, mid):
        if mid not in self.macros:
            return []
        p = self.chunks["pool"]
        a, b = self.macros[mid]
        return [struct.unpack_from(">II", p, o) for o in range(a, b - 7, 8)]

    def macro_samples(self, mid, seen=None):
        """Sample ids started by macro `mid` and every macro it jumps to or calls."""
        seen = set() if seen is None else seen
        if mid in seen:
            return []
        seen.add(mid)
        out = []
        for w0, w1 in self.macro_steps(mid):
            op = w0 & 0x7F
            if op == OP_START_SAMPLE:
                sid = (w0 >> 8) & 0xFFFF
                if sid not in out:
                    out.append(sid)
            elif op in MACRO_REF_OPS:
                for sid in self.macro_samples(w0 >> 16, seen):
                    if sid not in out:
                        out.append(sid)
        return out

    def object_samples(self, oid):
        """Samples of an FX/keymap/layer/macro object id (0x4000 = keymap, 0x8000 = layer)."""
        if oid & 0x8000:
            if oid not in self.layers:
                return []
            p = self.chunks["pool"]
            a, b = self.layers[oid]
            n = _u32(p, a)
            out = []
            for k in range(n):
                sub = _u16(p, a + 4 + k * 12)
                out += [s for s in self.object_samples(sub) if s not in out]
            return out
        if oid & 0x4000:
            if oid not in self.keymaps:
                return []
            p = self.chunks["pool"]
            a, b = self.keymaps[oid]
            out = []
            for k in range(128):
                o = a + k * 8
                if o + 2 > b:
                    break
                sub = _u16(p, o)
                if sub != 0xFFFF:
                    out += [s for s in self.object_samples(sub) if s not in out]
            return out
        return self.macro_samples(oid)

    def fx_table(self):
        """[(fx dict, [sample ids])] for every SFX of every FX group in the file."""
        out = []
        for g in self.groups:
            for fx in g["fx"]:
                out.append((g["id"], fx, self.object_samples(fx["macro"])))
        return out

    # -- rebuild ---------------------------------------------------------------------------------
    def with_samples(self, replacements):
        """New file bytes with samples replaced.  `replacements` = {sample id: Encoded}.

        Each replaced sample gets new data, rate, length and ADPCM info; its loop is kept as a
        whole-sample loop if the original looped, otherwise cleared.  Root key, proj and pool are
        unchanged.  The samp chunk is rebuilt in SDIR order with 0x20-byte alignment (retail
        layout), so `with_samples({})` returns the original file.
        """
        sdir = bytearray(self.chunks["sdir"])
        ids = {s.id for s in self.samples}
        for sid in replacements:
            if sid not in ids:
                raise SoundError("no sample 0x%X in this group" % sid)
        blobs, pos = [], 0
        for s in self.samples:
            eo = s.index * SDIR_SIZE
            data = s.data
            if s.id in replacements:
                e = replacements[s.id]
                if not s.extra:
                    raise SoundError("sample 0x%X has no ADPCM info slot" % s.id)
                loop = bool(s.loop_length)
                sdir[s.extra:s.extra + ADPCM_INFO_SIZE] = e.info(loop)
                struct.pack_into(">I", sdir, eo + 0x0C, (s.info & 0xFFFF0000) | e.rate)
                struct.pack_into(">III", sdir, eo + 0x10, e.samples & 0xFFFFFF,
                                 0, e.samples if loop else 0)
                data = e.data
            struct.pack_into(">I", sdir, eo + 4, pos)
            data = data + b"\0" * (-len(data) % 0x20)
            blobs.append(data)
            pos += len(data)
        chunks = dict(self.chunks, sdir=bytes(sdir), samp=b"".join(blobs))
        return build_group(chunks)


    # -- retarget (voice swap) ---------------------------------------------------------------------
    def retarget(self, gid, fx_base, id_base):
        """Copy of a one-group FX file under a new group id, FX ids fx_base.., sample and macro ids
        id_base.. (in sorted order).  Curve (ADSR table) ids are kept.  Sample data is copied
        byte for byte, so the result plays exactly like the source group.
        """
        if len(self.groups) != 1 or self.groups[0]["type"] != 1:
            raise SoundError("retarget needs a file with exactly one FX group")
        g = self.groups[0]
        smap = {sid: id_base + k for k, sid in enumerate(sorted(s.id for s in self.samples))}
        mmap = {mid: id_base + k for k, mid in enumerate(sorted(self.macros))}
        if max(list(smap.values()) + list(mmap.values()) + [0]) >= 0x4000:
            raise SoundError("id_base too high")
        # sdir: ids (entries stay in ascending id order because the map preserves order)
        sdir = bytearray(self.chunks["sdir"])
        for s in self.samples:
            struct.pack_into(">H", sdir, s.index * SDIR_SIZE, smap[s.id])
        # pool: macro ids and the ids inside macro steps
        pool = bytearray(self.chunks["pool"])
        for mid, (a, b) in self.macros.items():
            struct.pack_into(">H", pool, a - 4, mmap[mid])
            for o in range(a, b - 7, 8):
                w0 = _u32(pool, o)
                op = w0 & 0x7F
                if op == OP_START_SAMPLE:
                    old = (w0 >> 8) & 0xFFFF
                    if old in smap:
                        w0 = (w0 & 0xFF0000FF) | (smap[old] << 8)
                elif op in MACRO_REF_OPS and (w0 >> 16) in mmap:
                    w0 = (w0 & 0xFFFF) | (mmap[w0 >> 16] << 16)
                struct.pack_into(">I", pool, o, w0)
        fx = [dict(f, id=fx_base + k, macro=mmap.get(f["macro"], f["macro"])) for k, f in enumerate(g["fx"])]
        new = dict(g, id=gid, macros=sorted(mmap.values()), samples=sorted(smap.values()), fx=fx)
        chunks = dict(self.chunks, proj=build_proj([new]), sdir=bytes(sdir), pool=bytes(pool))
        return build_group(chunks)


def _encode_ids(ids):
    """MusyX id list: runs of 3+ consecutive ids as (0x8000|first, last), else single ids; 0xFFFF
    (the retail encoding)."""
    out, ids, i = bytearray(), sorted(ids), 0
    while i < len(ids):
        j = i
        while j + 1 < len(ids) and ids[j + 1] == ids[j] + 1:
            j += 1
        if j - i >= 2:
            out += struct.pack(">HH", 0x8000 | ids[i], ids[j])
            i = j + 1
        else:
            out += struct.pack(">H", ids[i])
            i += 1
    return bytes(out + b"\xFF\xFF")


def build_proj(groups):
    """proj chunk in the retail layout: per group a 0x28-byte GROUP_DATA, its five id lists, and
    (FX groups) a 4-aligned FX_DATA table; then the 0xFFFFFFFF terminator."""
    out = bytearray()
    for g in groups:
        start = len(out)
        body = bytearray()
        offs = []
        for key in ("macros", "samples", "curves", "keymaps", "layers"):
            offs.append(start + 0x28 + len(body))
            body += _encode_ids(g[key])
        body += b"\0" * (-(start + 0x28 + len(body)) % 4)
        fx_off = start + 0x28 + len(body)
        if g["type"] == 1:
            body += struct.pack(">HH", len(g["fx"]), 0)
            for f in g["fx"]:
                body += struct.pack(">HHBBBBBB", f["id"], f["macro"], f["max_voices"], f["priority"],
                                    f["volume"], f["pan"], f["key"], f["vgroup"])
            body += b"\0" * (-(start + 0x28 + len(body)) % 4)
        else:
            raise SoundError("build_proj only writes FX groups")
        nxt = start + 0x28 + len(body)
        out += struct.pack(">IHHIIIIIIII", nxt, g["id"], g["type"], *offs, fx_off, 0, 0)
        out += body
    out += b"\xFF\xFF\xFF\xFF"
    return bytes(out)


class Encoded:
    """DSP-ADPCM sample ready to put into a group: data, 16 coefficients, rate, sample count."""

    def __init__(self, data, coefs, rate, samples):
        if not 1 <= rate <= 0xFFFF:
            raise SoundError("sample rate %d out of range (1..65535)" % rate)
        if samples > 0xFFFFFF:
            raise SoundError("sample too long (%d samples)" % samples)
        self.data, self.coefs, self.rate, self.samples = bytes(data), list(coefs), rate, samples

    @classmethod
    def from_pcm(cls, pcm, rate):
        data, coefs, _ = dsp_encode(pcm)
        return cls(data, coefs, rate, len(pcm))

    @classmethod
    def from_sample(cls, s):
        """Lossless copy of a retail sample (for moving a line between groups)."""
        return cls(s.data[:s.byte_size()], s.coefs, s.rate, s.samples)

    def info(self, loop=False):
        """SNDADPCMinfo: whole-sample loop starts at frame 0 with empty history."""
        ps = self.data[0] if self.data else 0
        return struct.pack(">HBBhh16h", 8, ps, ps if loop else 0, 0, 0, *self.coefs)


def build_group(chunks):
    """Header + chunks, each 0x20-aligned (the retail layout)."""
    out = bytearray(0x20)
    for i, name in enumerate(CHUNKS):
        out += b"\0" * (-len(out) % 0x20)
        struct.pack_into(">II", out, i * 8, len(out), len(chunks[name]))
        out += chunks[name]
    return bytes(out)


# ------------------------------------------------------------------------------------------------
# DSP-ADPCM
# ------------------------------------------------------------------------------------------------
def adpcm_bytes(n):
    return (n + FRAME_SAMPLES - 1) // FRAME_SAMPLES * FRAME_BYTES


def _clamp16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def dsp_decode(data, n, coefs, hist1=0, hist2=0):
    """DSP-ADPCM bytes -> list of n signed 16-bit samples."""
    out = []
    frames = (n + FRAME_SAMPLES - 1) // FRAME_SAMPLES
    for f in range(frames):
        o = f * FRAME_BYTES
        if o + FRAME_BYTES > len(data):
            raise SoundError("ADPCM data too short")
        ps = data[o]
        scale = 1 << (ps & 0xF)
        c1, c2 = coefs[(ps >> 4) * 2], coefs[(ps >> 4) * 2 + 1]
        for k in range(FRAME_SAMPLES):
            if len(out) == n:
                break
            byte = data[o + 1 + k // 2]
            nib = byte >> 4 if k % 2 == 0 else byte & 0xF
            if nib >= 8:
                nib -= 16
            v = _clamp16(((nib * scale) << 11) + 1024 + c1 * hist1 + c2 * hist2 >> 11)
            hist2, hist1 = hist1, v
            out.append(v)
    return out


def _frame_lpc(pcm, start, h1, h2):
    """Least-squares (c1, c2) for one 14-sample frame given its two history samples."""
    r11 = r12 = r22 = b1 = b2 = 0.0
    p1, p2 = h1, h2
    for k in range(start, min(start + FRAME_SAMPLES, len(pcm))):
        x = pcm[k]
        r11 += p1 * p1
        r12 += p1 * p2
        r22 += p2 * p2
        b1 += x * p1
        b2 += x * p2
        p2, p1 = p1, x
    det = r11 * r22 - r12 * r12
    if abs(det) < 1e-9 or r11 < 1.0:
        return None, r11
    return ((b1 * r22 - b2 * r12) / det, (b2 * r11 - b1 * r12) / det), r11


def _design_coefs(pcm):
    """Eight predictor pairs (fixed point, 11 fractional bits) clustered from per-frame LPC fits."""
    fits = []
    h1 = h2 = 0
    for start in range(0, len(pcm), FRAME_SAMPLES):
        c, w = _frame_lpc(pcm, start, h1, h2)
        if c is not None:
            fits.append((max(-2.0, min(1.999, c[0])), max(-2.0, min(1.999, c[1])), w))
        end = min(start + FRAME_SAMPLES, len(pcm))
        h1 = pcm[end - 1] if end - 1 >= 0 else 0
        h2 = pcm[end - 2] if end - 2 >= 0 else 0
    # Seeds: silence predictor, first-order, second-order (as in the SDK encoder's useful range).
    centres = [(0.0, 0.0), (1.0, 0.0), (2.0 - 0.0005, -1.0), (0.5, 0.0), (1.5, -0.6), (1.8, -0.85),
               (1.2, -0.3), (0.9, 0.05)]
    if fits:
        for _ in range(12):
            acc = [[0.0, 0.0, 0.0] for _ in centres]
            for x, y, w in fits:
                best = min(range(len(centres)), key=lambda i: (centres[i][0] - x) ** 2 + (centres[i][1] - y) ** 2)
                ww = math.log1p(w)
                acc[best][0] += x * ww
                acc[best][1] += y * ww
                acc[best][2] += ww
            centres = [(a / w, b / w) if w > 0 else c for (a, b, w), c in zip(acc, centres)]
        centres[0] = (0.0, 0.0)
    coefs = []
    for x, y in centres:
        coefs += [_clamp16(int(round(x * 2048))), _clamp16(int(round(y * 2048)))]
    return coefs


def _encode_frame(frame, coefs, h1, h2):
    """Best (bytes, err, h1, h2) over the 8 predictors, with the smallest scale that fits."""
    best = None
    for p in range(8):
        c1, c2 = coefs[2 * p], coefs[2 * p + 1]
        # residual magnitude with ideal history -> starting scale
        a1, a2, peak = h1, h2, 0
        for x in frame:
            r = x - ((c1 * a1 + c2 * a2) >> 11)
            peak = max(peak, abs(r))
            a2, a1 = a1, x
        s = 0
        while s < 12 and (7 << s) < peak:
            s += 1
        for sc in (s, s + 1) if s < 12 else (s,):
            scale = 1 << sc
            a1, a2, err = h1, h2, 0
            nibs = []
            for x in frame:
                pred = c1 * a1 + c2 * a2
                q = int(round(((x << 11) - pred - 1024) / (scale << 11)))
                q = -8 if q < -8 else 7 if q > 7 else q
                v = _clamp16(((q * scale) << 11) + 1024 + pred >> 11)
                err += (v - x) * (v - x)
                nibs.append(q & 0xF)
                a2, a1 = a1, v
                if best is not None and err >= best[1]:
                    break
            else:
                if best is None or err < best[1]:
                    nibs += [0] * (FRAME_SAMPLES - len(nibs))
                    b = bytes([(p << 4) | sc] + [(nibs[i] << 4) | nibs[i + 1] for i in range(0, 14, 2)])
                    best = (b, err, a1, a2)
    return best


def dsp_encode(pcm):
    """list of signed 16-bit samples -> (ADPCM bytes, 16 coefs, first frame's predictor/scale)."""
    pcm = [_clamp16(int(v)) for v in pcm]
    if not pcm:
        raise SoundError("empty sample")
    coefs = _design_coefs(pcm)
    out = bytearray()
    h1 = h2 = 0
    for start in range(0, len(pcm), FRAME_SAMPLES):
        frame = pcm[start:start + FRAME_SAMPLES]
        b, _, h1, h2 = _encode_frame(frame, coefs, h1, h2)
        out += b
    return bytes(out), coefs, out[0]


# ------------------------------------------------------------------------------------------------
# WAV
# ------------------------------------------------------------------------------------------------
def write_wav(path, pcm, rate):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(pcm), *pcm))


def read_wav(path):
    """(mono 16-bit samples, rate) from a PCM WAV; stereo is averaged, 8/24/32-bit converted."""
    with wave.open(str(path), "rb") as w:
        ch, width, rate, n = w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()
        raw = w.readframes(n)
    if width == 1:
        vals = [(b - 128) << 8 for b in raw]
    elif width == 2:
        vals = list(struct.unpack("<%dh" % (len(raw) // 2), raw))
    elif width == 3:
        vals = [int.from_bytes(raw[i:i + 3], "little", signed=True) >> 8 for i in range(0, len(raw), 3)]
    elif width == 4:
        vals = [v >> 16 for v in struct.unpack("<%di" % (len(raw) // 4), raw)]
    else:
        raise SoundError("unsupported WAV sample width %d" % width)
    if ch > 1:
        vals = [sum(vals[i:i + ch]) // ch for i in range(0, len(vals), ch)]
    return vals, rate


# ------------------------------------------------------------------------------------------------
# What each sound row is (lbl_800EF508 index), from the loaders; see .agent/sounds.md
# ------------------------------------------------------------------------------------------------
GRID_NAMES = ["Mario", "Luigi", "DK", "Diddy", "Peach", "Daisy", "Yoshi", "BabyMario", "BabyLuigi",
              "Bowser", "Wario", "Waluigi", "Koopa", "Toad", "Boo", "Toadette", "ShyGuy", "Birdo",
              "Monty", "BowserJr", "Paratroopa", "Pianta", "Noki", "Bro", "Toadsworth", "Magikoopa",
              "KingBoo", "Petey", "Dixie", "Goomba", "Paragoomba", "DryBones"]
VOICE_ROW0 = 5          # voice bank of unique index u = sound row 5 + u (fn_3_90F48)
STADIUM_ROW0 = 0x27     # stadium SFX = row 0x27 + stadium id (fn_3_90A18 / fn_3_90798)
STADIUMS = ["Mario Stadium", "Bowser Castle", "Wario Palace", "Yoshi Park", "Peach Garden", "DK Jungle",
            "Toy Field"]
VOICE_LINES = 13        # every voice group has 13 SFX: characterSoundArray[charID] + 0..12
# Voice line index -> what plays it (fn_3_90220 `playCharacterSound(charID, line)` call sites).
LINE_USES = {
    0: "positive: good catch (fn_3_261E8, fn_3_4DC14), chemistry throw (fn_3_ABDD0), home-run end / "
       "post-replay celebration (fn_3_23CEC, fn_3_240F8)",
    3: "pitcher result sad (pitcher anim 69, fn_3_64BDC)",
    4: "pitcher result happy (pitcher anim 68, fn_3_64BDC)",
    5: "regular throw (fn_3_ABDD0)",
    7: "championship / win screen (fn_3_22C20), fn_3_657E4",
    9: "fn_3_657E4 via fn_3_90150 (distance-scaled volume)",
    10: "negative: strikeout (batter anims 93..96), hit by pitch (98), knocked down by hazards, "
        "body check / slide collision, failed catch, anti-chemistry throw",
    11: "big hit: fielder set on fire (anim 16), wall splat (fn_3_2F924)",
}


def row_meaning(row):
    if VOICE_ROW0 <= row < VOICE_ROW0 + len(GRID_NAMES):
        return "voice bank: %s (unique index %d)" % (GRID_NAMES[row - VOICE_ROW0], row - VOICE_ROW0)
    if STADIUM_ROW0 <= row < STADIUM_ROW0 + len(STADIUMS):
        return "stadium SFX: %s" % STADIUMS[row - STADIUM_ROW0]
    return {
        0: "boot-resident SFX (pushed by fn_8002158C at boot)",
        1: "in-match common SFX (gid 28) + 19 sequenced in-match songs (gid 29, arrangements game_set.0)",
        2: "sequenced menu songs (gid 31; menus fn_2_554 / fn_2_10FC)",
        3: "alternate common SFX (gid 28) + song group 54 (game.rel fn_3_59C2C loader)",
        4: "menus options/records SFX (gid 0; fn_800627C4)",
        37: "announcer: character-name calls 0x1C4..0x1E5, menu music 0x1E4 (stereo, 76.6 s), 0x1E6",
        38: "menu captain clips (gid 49): copies of captains' voice samples + menu SFX",
        46: "in-match SFX outside Toy Field (gid 51; fn_3_90764)",
        47: "small SFX group (gid 53; fn_80069A68)",
    }.get(row, "")


# ------------------------------------------------------------------------------------------------
# DVD-streamed music (snd/my_snd_h/*.adp): Nintendo stream ADPCM, stereo, 48 kHz
# ------------------------------------------------------------------------------------------------
ADP_RATE = 48000        # AIInit sets the stream rate to 48 kHz; the game never changes it
_ADP_FILTERS = ((0, 0), (0x3C, 0), (0x73, -0x34), (0x62, -0x37))


def adp_decode(data):
    """.adp stream -> (left, right) lists of 16-bit samples.  32-byte frames: header bytes
    (L, R, L, R) = filter << 4 | shift, then 28 bytes of nibbles (low = left, high = right)."""
    if len(data) % 32:
        raise SoundError("ADP stream length is not a multiple of 32")
    out = ([], [])
    hist = [[0, 0], [0, 0]]
    for o in range(0, len(data), 32):
        hdr = (data[o], data[o + 1])
        for ch in (0, 1):
            f1, f2 = _ADP_FILTERS[(hdr[ch] >> 4) & 3]
            shift = hdr[ch] & 0xF
            h = hist[ch]
            dst = out[ch]
            for k in range(28):
                b = data[o + 4 + k]
                nib = (b & 0xF) if ch == 0 else (b >> 4)
                pred = (h[0] * f1 + h[1] * f2 + 0x20) >> 6
                pred = -0x200000 if pred < -0x200000 else 0x1FFFFF if pred > 0x1FFFFF else pred
                s = (((nib << 12) ^ 0x8000) - 0x8000) >> shift
                cur = (s << 6) + pred
                h[1], h[0] = h[0], cur
                v = cur >> 6
                dst.append(-32768 if v < -32768 else 32767 if v > 32767 else v)
    return out


def write_wav_stereo(path, left, right, rate):
    inter = [0] * (2 * len(left))
    inter[0::2], inter[1::2] = left, right
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(struct.pack("<%dh" % len(inter), *inter))

# voice-swap: retargeted sample/macro ids go to 0x400 + uniqueIndex * 0x20 (retail ids end at 0x34E)
SWAP_ID_BASE = 0x400
SWAP_ID_STRIDE = 0x20
