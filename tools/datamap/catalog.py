"""The GYQE01 file catalog: every descriptor table, every file it names, and the coverage of
ZZZZ.dat / aaaa.dat.  Standard library only.

A *table* is a run of 16-byte DiskFileDescs in main.dol or a REL .data section (REL tables are
given as offsets into section 5, the .data section of all three RELs).  A *file* is one
(container, offset) pair; it can be named by many descriptor rows in many tables (the 62 common
UI resources have 9 copies).  A file's id is "<table>.<index>" of its first row in TABLES order;
every other row is an alias.  `replace` rewrites every row that names the file.

Table knowledge comes from the matched/decompiled loaders (see .agent/data_map.md for the trace
of each one).  tools/datamap/dev_tables.py regenerates the raw run list from the asm.
"""
import struct
from collections import OrderedDict

import disc as dm
import formats as fm

NUM_CHARS = 54
CHAR_NAMES = [
    "Mario", "Luigi", "DK", "Diddy", "Peach", "Daisy", "Yoshi", "BabyMario", "BabyLuigi", "Bowser",
    "Wario", "Waluigi", "KoopaR", "ToadR", "Boo", "Toadette", "ShyGuyR", "Birdo", "Monty", "BowserJr",
    "ParatroopaR", "PiantaB", "PiantaR", "PiantaY", "NokiB", "NokiR", "NokiG", "BroH", "ToadsworthR",
    "ToadB", "ToadY", "ToadG", "ToadP", "MagikoopaB", "MagikoopaR", "MagikoopaG", "MagikoopaY",
    "KingBoo", "Petey", "Dixie", "Goomba", "Paragoomba", "KoopaG", "ParatroopaG", "ShyGuyB",
    "ShyGuyY", "ShyGuyG", "ShyGuyBk", "DryBonesGy", "DryBonesG", "DryBonesR", "DryBonesB", "BroF",
    "BroB"]
STADIUMS = ["Mario Stadium", "Bowser Castle", "Wario Palace", "Yoshi Park", "Peach Garden",
            "DK Jungle", "Toy Field"]   # in_game.h EnumStadiumIDs4
SUBFILE_TRACKS = {0: "hi-detail body .gpc", 1: "lo-detail body .gpc", 15: "skeleton/base ANIM bank"}
MASTER_TIERS = ((0, 33, "body"), (33, 54, "texture overlay"), (54, 432, "sub-item (bat/glove) model"),
                (432, 486, "skeleton ANIM bank"), (486, 515, "model event script"))


def _t(tid, module, addr, count, container, title, index, loader, note=""):
    return OrderedDict(id=tid, module=module, addr=addr, count=count, container=container, title=title,
                       index=index, loader=loader, note=note)


# Order matters: the first table naming a file gives it its id.
TABLES = [
    # ---- main.dol ---------------------------------------------------------------------------
    _t("rel", "dol", 0x800E8AA8, 3, "aaaa", "REL modules (LZSS in aaaa.dat)",
       "0 menus.rel, 1 game.rel, 2 challenge.rel", "fn_800097A0 (boot state machine) -> ARAMTransfer"),
    _t("boot", "dol", 0x800E8AD8, 3, "ZZZZ", "boot files",
       "0 .gpc model with one 1024x132 CI4 texture (LZSS 14/5), 1 stored binary, 2 small CI4 texture archive",
       "fn_800097A0 (boot state machine)"),
    _t("res", "dol", 0x800E8B08, 62, "ZZZZ", "common UI resources (resource id 0..61)",
       "resource id: 1 HUD portraits (charID->30+c), 5 lineup portraits, 15 menu common (names/mini "
       "images), 29 select-grid portraits, 30 title/main menu, 38..44 stadium art (38 + stadium); "
       "guesses: 0 HUD, 6..13 full-screen screens, 16..28 captain banners/emblems, 45..61 stills",
       "fn_800097A0 / fn_80035838(desc, slot); the same 62 rows are copied into game.rel/challenge.rel"),
    _t("res_dol2", "dol", 0x800EEFD0, 62, "ZZZZ", "common UI resources (copy 2)",
       "resource id (same as res)", "fn_80020278 / fn_80020388: res_dol2[lbl_803CB7CC[stadium] + 0x26]"),
    _t("game_common", "dol", 0x800EE910, 5, "ZZZZ", "field/game common set",
       "0..1 0x140-header data, 2..3 actor sets (guess), 4 text bank (23 minigame strings)", "fn_800188C0"),
    _t("sound", "dol", 0x800EF508, 48, "ZZZZ", "MusyX sound groups (stored)",
       "sound group slot (each file = proj+pool+sdir+samp; group id at +0x24)", "fn_800215F8 / fn_80021758"),
    _t("stadium", "dol", 0x800EFBE8, 21, "ZZZZ", "stadium models",
       "stadium*3 + variant (g_d_GameSettings.unk9 / unkA): " + ", ".join(STADIUMS),
       "game.rel manageStadiumLoading -> ARAMTransfer; relocated by fn_3_5EC0"),
    _t("master", "dol", 0x800EFD38, 515, "blob", "ARAM master descriptors (blob-relative)",
       "0..32 bodies, 33..53 texture overlays (via lbl_800F73B8[charID]), 54+charID*7+k attachments "
       "(k 0..3 models, 4..6 event scripts), 432+charID track-15 ANIM bank, 486+id per-model event "
       "scripts (id = lbl_800EEB18[charID])",
       "fn_800227E4 streams the blob to ARAM; fn_8002204C / fn_8002254C / fn_80021BBC / fn_8002244C / "
       "fn_80021ADC read members"),
    _t("blob", "dol", 0x800F1D68, 1, "ZZZZ", "ARAM blob (master[515])", "-",
       "fn_800227E4 (ARAM alloc size hard-coded at 0x800227EC/F4)"),
    _t("subfile", "dol", 0x800F1D78, NUM_CHARS * 19, "ZZZZ", "per-character disc subfiles",
       "charID*19 + track (0/1 body hi/lo, 2..14 & 16..18 animation banks, 15 skeleton)",
       "fn_8001758C / fn_80016344 / fn_80016710 / fn_80016B60 / fn_80021BBC"),
    _t("subitem", "dol", 0x800F5D98, NUM_CHARS * 6, "ZZZZ", "per-character sub-item models (bat/glove)",
       "charID*6 + itemSlot", "fn_80021CA4 / fn_8002244C (disc path)"),
    _t("model_script", "dol", 0x800F71D8, 30, "ZZZZ", "per-model event scripts (disc fallback of master 486+id)",
       "model id from lbl_800EEB18[charID] (colour variants share their base's id)", "fn_80021ADC"),
    _t("menu_title", "dol", 0x800FB5B0, 1, "ZZZZ", "title/main menu UI (copy)", "-", "fn_8004A1BC"),
    _t("text_sys", "dol", 0x80109044, 1, "ZZZZ", "system text bank (11 strings, to ARAM)", "-",
       "fn_800697B0"),
    _t("opening", "dol", 0x801092C8, 3, "ZZZZ", "opening movie set",
       "0 opening HVQM4 movie (82 MB, stored), 1 0x20-rooted data, 2 UI resource (= res.34)", "fn_8006AF9C"),
    # ---- menus.rel ------------------------------------------------------------------------------
    _t("stats", "menus", 0xC8, 1, "ZZZZ", "CharacterStats block (54 x 0xA0 + tail)", "-",
       "fn_2_554 (menus boot); game.rel maybeProcessTeamData reads the same file"),
    _t("menu_set", "menus", 0x444, 16, "ZZZZ", "menu scene resource set (16 logical slots)",
       "logical UI slot: 0 title/main menu (res.30), 1 grid portraits (res.29), 2 res.33, 3 res.16, "
       "4..15 res.17..28", "fn_2_27E0 (and 8 identical copies, see aliases)"),
    _t("menu_set_b", "menus", 0xD14, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot", "fn_2_6D3C"),
    _t("menu_set_c", "menus", 0x1314, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot", "fn_2_11340"),
    _t("menu_set_d", "menus", 0x18B4, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot",
       "inside lbl_2_data_15C8 (no direct code reference)"),
    _t("menu_set_e", "menus", 0x1CD4, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot", "fn_2_16460"),
    _t("menu_set_f", "menus", 0x2A9B4, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot",
       "inside lbl_2_data_2A3C0 (no direct code reference)"),
    _t("menu_set_g", "menus", 0x2B3DC, 16, "ZZZZ", "menu scene resource set (character select)",
       "logical UI slot (1 = grid portraits)", "fn_2_73994 / fn_2_760CC / fn_2_7BFF8 (character select)"),
    _t("menu_set_h", "menus", 0x2E1EC, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot", "fn_2_834F0"),
    _t("menu_set_i", "menus", 0x302BC, 16, "ZZZZ", "menu scene resource set (copy)", "logical UI slot",
       "inside lbl_2_data_2FFF8 (no direct code reference)"),
    _t("menu_char_a", "menus", 0x241C, NUM_CHARS, "ZZZZ", "per-character menu 3D scene A", "charID (use: guess, select/intro poses)",
       "fn_2_17C88 / fn_2_17ED8 / fn_2_18148 / fn_2_188EC"),
    _t("menu_model42", "menus", 0x4B90, 1, "ZZZZ", "shared .gpc model (42 textures)", "-", "fn_2_1B1BC"),
    _t("movie", "menus", 0x12170, 2, "ZZZZ", "HVQM4 movies (stored)", "0 short movie, 1 attract/intro",
       "fn_2_37630 -> sNp movie streamer (fn_800A6E24)"),
    _t("text_menu", "menus", 0x12298, 1, "ZZZZ", "menu text bank (954 strings)", "-", "fn_2_409CC"),
    _t("menu_char_b", "menus", 0x12EC0, NUM_CHARS, "ZZZZ", "per-character menu 3D scene B (same files as A)", "charID",
       "fn_2_47540 / fn_2_477C0 / fn_2_478F4 / fn_2_481F4"),
    _t("challenge_ui", "menus", 0x1323C, 11, "ZZZZ", "Challenge-mode UI resources",
       "3 Challenge scene (lbl_2_data_1326C), 10 Challenge Special ending / staff roll (lbl_2_data_132DC)",
       "fn_2_4EB64 / fn_2_4E970 / fn_2_4EABC -> fn_80035838"),
    _t("menu_1f884", "menus", 0x1F884, 2, "ZZZZ", "menu UI pair", "-", "fn_2_53408"),
    _t("menu_1ff84", "menus", 0x1FF84, 3, "ZZZZ", "options/records set", "0 UI, 1 text bank (599 strings), 2 scene",
       "fn_2_549B4"),
    _t("menu_20bf0", "menus", 0x20BF0, 2, "ZZZZ", "lineup screen set", "0 lineup portraits UI, 1 text bank",
       "fn_2_5ED3C"),
    _t("menu_scene_2e9a4", "menus", 0x2E9A4, 1, "ZZZZ", "menu 3D scene", "-", "fn_2_8563C / fn_2_85D6C"),
    _t("menu_2f944", "menus", 0x2F944, 2, "ZZZZ", "menu UI pair", "-", "fn_2_8ABFC"),
    _t("menu_scene_2f990", "menus", 0x2F990, 67, "ZZZZ", "menu 3D scene models (67)", "scene id",
       "fn_2_8DB14 / fn_2_8DC00 / fn_2_8DCD8 / fn_2_8E8A4 / fn_2_8EA80"),
    _t("menu_2fe48", "menus", 0x2FE48, 2, "ZZZZ", "menu 3D scene pair", "-", "fn_2_8E478"),
    # ---- game.rel -------------------------------------------------------------------------------
    _t("stats_game", "game", 0x0, 1, "ZZZZ", "CharacterStats block (copy)", "-", "maybeProcessTeamData"),
    _t("stadium_game", "game", 0x7EC, 21, "ZZZZ", "stadium models (copy)", "stadium*3 + variant", "fn_3_64DC"),
    _t("game_set", "game", 0x3D60, 69, "ZZZZ", "game resource set",
       "0..1 field files, 2..63 = res 0..61, 64..68 field effect/text files", "fn_3_59F40 / fn_3_5A28C"),
    _t("game_res_a", "game", 0x6130, 62, "ZZZZ", "common UI resources (copy)", "resource id", "fn_3_5BD40"),
    _t("game_res_b", "game", 0xF538, 62, "ZZZZ", "common UI resources (copy)", "resource id", "fn_3_AF10C"),
    _t("game_res_c", "game", 0x10578, 64, "ZZZZ", "results/replay resource set",
       "0 text bank (168 strings), 1 actor set, 2..63 = res 0..61", "fn_3_B43E8"),
    _t("field", "game", 0x10ACC, 7, "ZZZZ", "per-stadium field object sets", "stadium (0..6)",
       "fn_3_B9BB4 (g_hugeAnimStruct.stadiumObjectLoadingPtr)"),
    _t("game_model42", "game", 0x11380, 1, "ZZZZ", "shared .gpc model (copy)", "-", "fn_3_BF878"),
    _t("game_115ec", "game", 0x115EC, 1, "ZZZZ", "44-section data file", "-", "fn_3_BEFF8"),
    _t("camera", "game", 0x20FDC, 1, "ZZZZ", "camera data", "-", "fn_3_106E50 (g_Camera.unk1B4)"),
    _t("minigame_res", "game", 0x21EC8, 62, "ZZZZ", "common UI resources (minigame copy)",
       "resource id (minigame index + 6 picks a screen)", "fn_3_10B8D0 / fn_3_10F3D8 / fn_3_10FDC8"),
    _t("game_225f0", "game", 0x225F0, 3, "ZZZZ", "field effect/actor set (copy)", "0..1 actor sets, 2 text",
       "fn_3_116840 ... (g_hugeAnimStruct.ActorPointer)"),
    _t("game_284e8", "game", 0x284E8, 2, "ZZZZ", "results text + actor set", "0 text bank (168), 1 actor set",
       "fn_3_1665E4"),
    # ---- challenge.rel -------------------------------------------------------------------------
    _t("field_ch", "challenge", 0x0, 7, "ZZZZ", "per-stadium field object sets (copy)", "stadium", "fn_1_1538"),
    _t("stadium_ch", "challenge", 0x8D0, 21, "ZZZZ", "stadium models (copy)", "stadium*3 + variant", "fn_1_73B8"),
    _t("menu_title_ch", "challenge", 0xB70, 1, "ZZZZ", "title/main menu UI (copy)", "-", "fn_1_88D0"),
    _t("challenge_map_ui", "challenge", 0xF0C, 11, "ZZZZ", "Challenge map UI pieces", "piece id",
       "fn_1_8CD4 / fn_1_8DBC / fn_1_8F34"),
    _t("sound_ch", "challenge", 0x196C, 50, "ZZZZ", "MusyX sound groups (Challenge copy)", "sound group slot",
       "fn_1_9F04"),
    _t("challenge_board", "challenge", 0xA3D8, 86, "ZZZZ", "Challenge board data",
       "0..1 = game_common.0/1, 2..31 = model_script.0..29, 32..85 board pieces (use not traced)", "fn_1_16978"),
    _t("challenge_a950", "challenge", 0xA950, 1, "ZZZZ", "menu 3D scene (copy)", "-", "fn_1_10560"),
    _t("challenge_model42", "challenge", 0xADE0, 1, "ZZZZ", "shared .gpc model (copy)", "-", "fn_1_10560"),
    _t("challenge_models", "challenge", 0xF968, 24, "ZZZZ", "Challenge map models", "model id", "fn_1_1CBE4"),
    _t("debug_browser", "challenge", 0xFB98, 63, "ZZZZ", "sprite/resource debug browser list",
       "browser ordinal (0 = ending archive, 1.. = res)", "fn_1_26AF8 / fn_1_26928 / fn_1_267F4"),
    _t("challenge_104f4", "challenge", 0x104F4, 1, "ZZZZ", "small data file", "-", "fn_1_20F8C"),
    _t("stadium_ch2", "challenge", 0x10518, 21, "ZZZZ", "stadium models (copy)", "stadium*3 + variant", "fn_1_2004C"),
    _t("stadium_ch3", "challenge", 0x10674, 21, "ZZZZ", "stadium models (copy)", "stadium*3 + variant", "fn_1_1DCE4"),
    _t("challenge_model42b", "challenge", 0x108F8, 1, "ZZZZ", "shared .gpc model (copy)", "-", "fn_1_27BB8"),
    _t("movie_viewer", "challenge", 0x112D8, 1, "ZZZZ", "debug movie viewer (TEST MOVIE 0)", "movie id",
       "challenge rep_7BA0 (0x28398) -> fn_800ADB04"),
    _t("partial_ch", "challenge", 0xA940, 1, "ZZZZ", "header read (0x200 bytes of a 0x15450-byte file)",
       "-", "fn_1_10560"),
]

# Rows inside the tables above that are not descriptors (checked at load time).
FST_MUSIC_TABLE = ("challenge", 0xAC, 15)   # {0, size, cumulative offset, size} per snd/*.adp


def is_partial(desc):
    """A stored row that reads fewer bytes than the file holds (challenge partial_ch: 0x200 of
    0x15450).  Retail stored rows otherwise have packed == align(size, 4)."""
    return desc[0] == 0 and not desc[1] & dm.SIZE_COMPRESSED and desc[3] > dm.align(desc[1] & dm.SIZE_MASK, 0x20)


class Ref:
    __slots__ = ("table", "index", "image", "file_off", "desc")

    def __init__(self, table, index, image, file_off, desc):
        self.table, self.index, self.image, self.file_off, self.desc = table, index, image, file_off, desc

    @property
    def name(self):
        return "%s.%d" % (self.table["id"], self.index)


class File:
    def __init__(self, container, offset):
        self.container = container
        self.offset = offset
        self.refs = []
        self.id = None
        self.fmt = None
        self.summary = None

    @property
    def desc(self):
        """The descriptor of the whole file: the widest non-partial row (a partial read names only
        a prefix, see is_partial)."""
        full = [r.desc for r in self.refs if not is_partial(r.desc)] or [r.desc for r in self.refs]
        return max(full, key=lambda d: (d[1] & dm.SIZE_MASK, d[3]))

    @property
    def partial_only(self):
        return all(is_partial(r.desc) for r in self.refs)

    @property
    def flags(self):
        return self.desc[0]

    @property
    def size(self):
        return self.desc[1] & dm.SIZE_MASK

    @property
    def packed(self):
        return self.desc[3]

    @property
    def compressed(self):
        return bool(self.desc[1] & dm.SIZE_COMPRESSED)

    @property
    def extent(self):
        return max(max(r.desc[3], r.desc[1] & dm.SIZE_MASK if not r.desc[1] & dm.SIZE_COMPRESSED else 0)
                   for r in self.refs)

    def meaning(self):
        r = self.refs[0]
        t = r.table
        tid, i = t["id"], r.index
        if tid == "subfile":
            c, tr = divmod(i, 19)
            return "%s track %d (%s)" % (CHAR_NAMES[c], tr, SUBFILE_TRACKS.get(tr, "animation bank"))
        if tid == "subitem":
            c, k = divmod(i, 6)
            return "%s sub-item %d" % (CHAR_NAMES[c], k)
        if tid == "master":
            for lo, hi, name in MASTER_TIERS:
                if lo <= i < hi:
                    if name == "sub-item (bat/glove) model":
                        c, k = divmod(i - 54, 7)
                        return "%s sub-item %d (master %d)" % (CHAR_NAMES[c], k, i)
                    if name == "skeleton ANIM bank":
                        return "%s skeleton (master %d)" % (CHAR_NAMES[i - 432], i)
                    return "%s %d (master %d)" % (name, i - lo, i)
        if tid.startswith("stadium"):
            s, v = divmod(i, 3)
            return "%s, variant %d" % (STADIUMS[s] if s < len(STADIUMS) else "stadium %d" % s, v)
        if tid in ("field", "field_ch"):
            return STADIUMS[i] if i < len(STADIUMS) else "stadium %d" % i
        if tid == "model_script":
            return "model script %d" % i
        if tid in ("menu_char_a", "menu_char_b"):
            return CHAR_NAMES[i]
        if tid == "rel":
            return ("menus.rel", "game.rel", "challenge.rel")[i]
        if tid == "res":
            return "common resource %d" % i
        return "%s[%d]" % (t["title"], i)


class Catalog:
    def __init__(self, disc):
        self.disc = disc
        self.files = OrderedDict()   # (container, offset) -> File
        self.by_id = {}
        self.problems = []
        for t in TABLES:
            img = disc.image(t["module"])
            for i in range(t["count"]):
                fo = img.ram_off(t["addr"] + i * 16) if t["module"] == "dol" else img.file_off(5, t["addr"] + i * 16)
                d = img.desc_at(fo)
                csize = disc.container_size(t["container"])
                ok = dm.plausible(d, csize, absolute=t["container"] != "blob")
                if not ok and t["id"] == "partial_ch":
                    ok = d[0] == 0 and d[2] % dm.SECTOR == 0 and d[2] + d[3] <= csize
                if not ok:
                    self.problems.append("%s.%d at %s is not a descriptor: %s" % (
                        t["id"], i, img.where(fo), " ".join("%08X" % x for x in d)))
                    continue
                key = (t["container"], d[2])
                f = self.files.get(key)
                if f is None:
                    f = self.files[key] = File(t["container"], d[2])
                f.refs.append(Ref(t, i, img.name, fo, d))
        for f in self.files.values():
            f.id = f.refs[0].name
            for r in f.refs:
                self.by_id[r.name] = f

    # ---- lookup ---------------------------------------------------------------------------------
    def find(self, key):
        if key in self.by_id:
            return self.by_id[key]
        k = key.lower()
        cont = "ZZZZ"
        if ":" in k:
            cont, k = k.split(":", 1)
            cont = {"zzzz": "ZZZZ", "aaaa": "aaaa", "blob": "blob"}.get(cont, cont)
        try:
            off = int(k, 16) if k.startswith("0x") else None
        except ValueError:
            off = None
        if off is not None and (cont, off) in self.files:
            return self.files[(cont, off)]
        raise SystemExit("no file %r (use an id from `list`, e.g. res.15, or ZZZZ:0x1A9FE800)" % key)

    def sorted_files(self):
        order = {"aaaa": 0, "ZZZZ": 1, "blob": 2}
        return sorted(self.files.values(), key=lambda f: (order[f.container], f.offset))

    # ---- reading --------------------------------------------------------------------------------
    def raw(self, f):
        return self.disc.read(f.container, f.offset, f.packed)

    def data(self, f):
        return dm.unpack(f.desc, self.raw(f))

    def head(self, f, n=0x2000):
        d = f.desc
        raw = self.disc.read(f.container, f.offset, min(d[3], n + n // 8 + 0x40))
        return dm.unpack(d, raw, n)

    def sniff(self, f):
        if f.fmt is None:
            try:
                f.fmt, f.summary = fm.sniff(self.head(f), f.size, not f.compressed)
            except Exception as e:  # noqa: BLE001
                f.fmt, f.summary = "error", "decode error: %s" % e
            if any(r.table["id"].startswith("stats") for r in f.refs):
                f.fmt, f.summary = "stats", "CharacterStats: 54 x 0xA0 rows + 0x%X-byte tail" % (f.size - 54 * 0xA0)
            if f.id.startswith("blob."):
                f.fmt, f.summary = "blob", "ARAM blob: master entries 0..514 live inside"
        return f.fmt, f.summary

    # ---- descriptor copies --------------------------------------------------------------------
    def copies(self, desc, container):
        """Every location (image, file_off) in a data section holding exactly this 16-byte tuple.
        Blob-relative tuples are only matched inside the master table."""
        pat = struct.pack(">4I", *desc)
        out = []
        for img in self.disc.images():
            for i, o, s, ex in img.data_sections():
                p = img.data.find(pat, o, o + s)
                while p != -1:
                    if p % 4 == 0:
                        out.append((img.name, p))
                    p = img.data.find(pat, p + 1, o + s)
        if container == "blob":
            lo = self.disc.dol.ram_off(dm.MASTER_ADDR)
            out = [(n, p) for n, p in out if n == "dol" and lo <= p < lo + 515 * 16]
        else:
            lo = self.disc.dol.ram_off(dm.MASTER_ADDR)
            out = [(n, p) for n, p in out if not (n == "dol" and lo <= p < lo + 515 * 16)]
        return out

    # ---- coverage --------------------------------------------------------------------------------
    def coverage(self, container):
        """(covered_bytes, padding_bytes, gaps[(start, end)]) for ZZZZ or aaaa."""
        size = self.disc.container_size(container)
        spans = sorted((f.offset, f.offset + f.extent) for f in self.files.values() if f.container == container)
        covered = 0
        pad = 0
        gaps = []
        pos = 0
        for a, b in spans:
            if a > pos:
                # sector slack after the previous file counts as padding, the rest is a gap
                slack_end = min(a, dm.align(pos, dm.SECTOR)) if pos else 0
                pad += max(0, slack_end - pos)
                if a > slack_end:
                    gaps.append((slack_end, a))
            covered += max(0, b - max(a, pos))
            pos = max(pos, b)
        if pos < size:
            slack_end = min(size, dm.align(pos, dm.SECTOR))
            pad += slack_end - pos
            if size > slack_end:
                gaps.append((slack_end, size))
        return covered, pad, gaps


def blob_members(cat):
    return [f for f in cat.files.values() if f.container == "blob"]
