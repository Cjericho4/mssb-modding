"""CharacterStats file (stats.0, ZZZZ.dat+0x06CF8800) <-> editable JSON (standard library only).

Layout and evidence: .agent/stats.md.  The file is 0x46E0 bytes:
  0x0000  54 rows of 0xA0 (CharacterStats, one per charID)
  0x21C0  0x21C0 bytes no code reads as stats (zero in retail; a debug loader sees "rows 54..107")
  0x4380  12 captain blocks of 0x48: 4 lineups x {9 charIDs (0xFF = empty), 9 bytes (0x0A in retail)}

export(data) -> dict, apply(data, obj) -> bytes.  apply() starts from the original bytes and
overwrites only what the JSON names, so export -> apply is byte-identical by construction and a
JSON that lists one field of one character changes exactly that byte.
"""
import struct

ROW, ROWS = 0xA0, 54
UNREF_OFF, CAPT_OFF = ROWS * ROW, 0x4380
FILE_SIZE = 0x46E0
CAPT_BLOCK, LINEUPS = 0x48, 4
FORMAT = "mssb-stats 1"

# CHAR_ID order (include/mssbTypes.h)
NAMES = ["mario", "luigi", "dk", "diddy", "peach", "daisy", "yoshi", "babymario", "babyluigi",
         "bowser", "wario", "waluigi", "koopa_green", "toad_red", "boo", "toadette", "shyguy_red",
         "birdo", "monty", "bowserjr", "paratroopa_red", "pianta_blue", "pianta_red", "pianta_yellow",
         "noki_blue", "noki_red", "noki_green", "bro_hammer", "toadsworth", "toad_blue", "toad_yellow",
         "toad_green", "toad_purple", "magikoopa_blue", "magikoopa_red", "magikoopa_green",
         "magikoopa_yellow", "kingboo", "petey", "dixie", "goomba", "paragoomba", "koopa_red",
         "paratroopa_green", "shyguy_blue", "shyguy_yellow", "shyguy_green", "shyguy_black",
         "drybones_gray", "drybones_green", "drybones_red", "drybones_blue", "bro_fire", "bro_boomerang"]
NAME_ID = {n: i for i, n in enumerate(NAMES)}

HAND = ["right", "left"]
CLASS = ["balanced", "power", "speed", "technique"]
CAPTAIN_STAR = ["none", "mario", "luigi", "wario", "waluigi", "dk", "diddy", "bowser", "bowserjr",
                "yoshi", "birdo", "peach", "daisy"]
STAR_SWING = ["none", "popfly", "grounder", "linedrive"]
STAR_PITCH = ["none", "curve", "fastball", "changeup"]
ABILITIES = ["wall_splat", "wall_jump", "clamber", "sliding_catch", "laser", "quick_throw",
             "super_jump", "magical_catch", "tongue_catch", "suction", "super_catch", "ball_dash",
             "body_check", "super_curve"] + ["bit%d" % b for b in range(14, 32)]

# (offset, key, kind); kind: "u8", "s16", or a list of names (u8 enum, ints also accepted)
FIELDS = [
    (0x00, "curveball_speed", "u8"), (0x01, "fastball_speed", "u8"), (0x02, "cursed_ball", "u8"),
    (0x03, "curve", "u8"), (0x04, "curve_control", "u8"),
    (0x24, "char_id", "s16"), (0x26, "throwing_hand", HAND), (0x27, "batting_hand", HAND),
    (0x28, "slap_contact", "u8"), (0x29, "charge_contact", "u8"), (0x2A, "slap_power", "u8"),
    (0x2B, "charge_power", "u8"), (0x2C, "bunt_contact", "u8"), (0x2D, "trajectory_push_pull", "u8"),
    (0x2E, "trajectory_high_low", "u8"), (0x2F, "speed", "u8"), (0x30, "throwing_arm", "u8"),
    (0x31, "character_class", CLASS), (0x32, "weight", "u8"), (0x33, "captain", "u8"),
    (0x34, "captain_star", CAPTAIN_STAR), (0x35, "star_swing", STAR_SWING),
    (0x36, "star_pitch", STAR_PITCH), (0x37, "batting_bar", "u8"), (0x38, "pitching_bar", "u8"),
    (0x39, "running_bar", "u8"), (0x3A, "fielding_bar", "u8"),
]
ABILITY_OFF, CHEM_OFF = 0x20, 0x3B
# bytes the game never reads as stats (kept so nothing is lost): offset -> length
UNKNOWN = {0x05: 0x1B, 0x71: 0x03, 0x74: 0x2C}


class StatsError(ValueError):
    pass


def _enum_out(names, v):
    return names[v] if v < len(names) else v


def _enum_in(names, v, where):
    if isinstance(v, str):
        if v not in names:
            raise StatsError("%s: %r is not one of %s" % (where, v, ", ".join(names)))
        return names.index(v)
    return _int(v, 0, 255, where)


def _int(v, lo, hi, where):
    if isinstance(v, bool) or not isinstance(v, int) or not lo <= v <= hi:
        raise StatsError("%s: %r is not an integer in %d..%d" % (where, v, lo, hi))
    return v


def _hex(v, n, where):
    try:
        b = bytes.fromhex(v)
    except (TypeError, ValueError):
        raise StatsError("%s: not a hex string" % where)
    if len(b) != n:
        raise StatsError("%s: need %d bytes, got %d" % (where, n, len(b)))
    return b


def _char(v, where):
    if v is None:
        return 0xFF
    if isinstance(v, str):
        if v not in NAME_ID:
            raise StatsError("%s: unknown character %r" % (where, v))
        return NAME_ID[v]
    return _int(v, 0, 255, where)


def export(data):
    if len(data) != FILE_SIZE:
        raise StatsError("stats file is 0x%X bytes, expected 0x%X" % (len(data), FILE_SIZE))
    chars = []
    for c in range(ROWS):
        r = data[c * ROW:(c + 1) * ROW]
        o = {"id": c, "name": NAMES[c]}
        for off, key, kind in FIELDS:
            if kind == "s16":
                o[key] = struct.unpack_from(">h", r, off)[0]
            elif kind == "u8":
                o[key] = r[off]
            else:
                o[key] = _enum_out(kind, r[off])
        mask = struct.unpack_from(">I", r, ABILITY_OFF)[0]
        o["fielding_abilities"] = [ABILITIES[b] for b in range(32) if mask >> b & 1]
        o["chemistry"] = {NAMES[k]: r[CHEM_OFF + k] for k in range(ROWS)}
        o["unknown"] = {"0x%02X" % off: r[off:off + n].hex() for off, n in sorted(UNKNOWN.items())}
        chars.append(o)
    teams = []
    for b in range(12):
        blk = data[CAPT_OFF + b * CAPT_BLOCK:CAPT_OFF + (b + 1) * CAPT_BLOCK]
        lineups = []
        for i in range(LINEUPS):
            ids = blk[i * 18:i * 18 + 9]
            lineups.append({"members": [None if x == 0xFF else (NAMES[x] if x < ROWS else x) for x in ids],
                            "extra": blk[i * 18 + 9:i * 18 + 18].hex()})
        head = blk[3 * 18]                     # lineup 3 is the captain alone in retail
        teams.append({"block": b, "captain": NAMES[head] if head < ROWS else None, "lineups": lineups})
    out = {"format": FORMAT,
           "help": "Edit values and run stats-import. Missing keys keep the disc's value. "
                   "Field meanings and ranges: .agent/stats.md (mssb-dtk).",
           "characters": chars, "captain_teams": teams}
    unref = data[UNREF_OFF:CAPT_OFF]
    if any(unref):
        out["unreferenced"] = unref.hex()
    return out


def apply(data, obj):
    """Return a copy of `data` (the disc's stats file) with every value named in `obj` written."""
    if len(data) != FILE_SIZE:
        raise StatsError("stats file is 0x%X bytes, expected 0x%X" % (len(data), FILE_SIZE))
    if not isinstance(obj, dict) or obj.get("format") != FORMAT:
        raise StatsError("not a %r file (missing or wrong \"format\")" % FORMAT)
    known = {"format", "help", "characters", "captain_teams", "unreferenced"}
    extra = set(obj) - known
    if extra:
        raise StatsError("unknown top-level keys: %s" % ", ".join(sorted(extra)))
    out = bytearray(data)
    by_key = {key: (off, kind) for off, key, kind in FIELDS}
    for o in obj.get("characters", []):
        if "id" not in o:
            raise StatsError("a character entry has no \"id\"")
        c = _int(o["id"], 0, ROWS - 1, "id")
        w = "character %d (%s)" % (c, NAMES[c])
        if "name" in o and o["name"] != NAMES[c]:
            raise StatsError("%s: name %r does not match id" % (w, o["name"]))
        base = c * ROW
        for key, v in o.items():
            where = "%s.%s" % (w, key)
            if key in ("id", "name"):
                continue
            if key in by_key:
                off, kind = by_key[key]
                if kind == "s16":
                    struct.pack_into(">h", out, base + off, _int(v, -32768, 32767, where))
                elif kind == "u8":
                    out[base + off] = _int(v, 0, 255, where)
                else:
                    out[base + off] = _enum_in(kind, v, where)
            elif key == "fielding_abilities":
                mask = 0
                for a in v:
                    if a not in ABILITIES:
                        raise StatsError("%s: unknown ability %r (known: %s)" % (where, a, ", ".join(ABILITIES[:14])))
                    mask |= 1 << ABILITIES.index(a)
                struct.pack_into(">I", out, base + ABILITY_OFF, mask)
            elif key == "chemistry":
                for other, val in v.items():
                    if other not in NAME_ID:
                        raise StatsError("%s: unknown character %r" % (where, other))
                    out[base + CHEM_OFF + NAME_ID[other]] = _int(val, 0, 255, "%s.%s" % (where, other))
            elif key == "unknown":
                for k, hx in v.items():
                    off = int(k, 16)
                    if off not in UNKNOWN:
                        raise StatsError("%s: no unknown block at %s" % (where, k))
                    out[base + off:base + off + UNKNOWN[off]] = _hex(hx, UNKNOWN[off], "%s.%s" % (where, k))
            else:
                raise StatsError("%s: unknown field" % where)
    for t in obj.get("captain_teams", []):
        b = _int(t.get("block"), 0, 11, "captain_teams.block")   # "captain" is informational
        for i, lu in enumerate(t.get("lineups", [])[:LINEUPS]):
            p = CAPT_OFF + b * CAPT_BLOCK + i * 18
            where = "captain_teams[%d].lineups[%d]" % (b, i)
            if "members" in lu:
                m = lu["members"]
                if len(m) != 9:
                    raise StatsError("%s.members: need 9 entries (null = empty)" % where)
                out[p:p + 9] = bytes(_char(x, where) for x in m)
            if "extra" in lu:
                out[p + 9:p + 18] = _hex(lu["extra"], 9, where + ".extra")
    if "unreferenced" in obj:
        out[UNREF_OFF:CAPT_OFF] = _hex(obj["unreferenced"], CAPT_OFF - UNREF_OFF, "unreferenced")
    return bytes(out)


def warnings(data):
    """Cheap sanity notes for an edited file (asymmetric chemistry, bars out of 1..10)."""
    notes = []
    for a in range(ROWS):
        for b in range(a + 1, ROWS):
            x, y = data[a * ROW + CHEM_OFF + b], data[b * ROW + CHEM_OFF + a]
            if x != y:
                notes.append("chemistry %s->%s is %d but %s->%s is %d (retail is symmetric)" % (
                    NAMES[a], NAMES[b], x, NAMES[b], NAMES[a], y))
        for off in range(0x37, 0x3B):
            if not 1 <= data[a * ROW + off] <= 10:
                notes.append("%s: stat bar at +0x%X is %d (menus show 1..10)" % (NAMES[a], off, data[a * ROW + off]))
    return notes
