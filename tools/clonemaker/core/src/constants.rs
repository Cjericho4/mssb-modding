//! Retail table addresses and source-defined character names.
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_SUBFILES: u32 = 0x800F1D78;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_MASTER: u32 = 0x800EFD38;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_PAIRS: u32 = 0x800F73B8;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_ROWKIND: u32 = 0x800F743C;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_SUBITEM_FILES: u32 = 0x800F5D98;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_GLOVE: u32 = 0x800EEAAC;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_MODELMAP: u32 = 0x800EEB18;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_JUMPTABLE: u32 = 0x800EEC40;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_ANIMPARAMS: u32 = 0x800FB798;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_FAMILY: u32 = 0x800E8558;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_GAME_REL_DESC: u32 = 0x800E8AB8;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_BLOB_LIS: u32 = 0x800227EC;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_BLOB_ADDI: u32 = 0x800227F4;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_FINDCHAR: u32 = 0x800698F8;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_PORTRAIT_DESC: u32 = 0x800EEFE0;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_PORTRAIT_DESC2: u32 = 0x800E8B18;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_MENU_COMMON_DESC: u32 = 0x800E8BF8;
/// Runtime address of the game table used by the reference patcher.
pub const ADDR_MENU_GRID_DESC: u32 = 0x800E8CD8;
/// MusyX sound-group descriptors `lbl_800EF508` (row 5 + unique index = that voice bank).
pub const ADDR_SOUND_GROUPS: u32 = 0x800EF508;
/// game.rel .data offset of the per-charID voice SFX base table (`lbl_3_data_8168`, u16[54]).
pub const REL_VOICE_BASE: usize = 0x8168;
/// Character names in charID order.
pub const CHAR_NAMES: [&str; 54] = [
    "Mario",
    "Luigi",
    "DK",
    "Diddy",
    "Peach",
    "Daisy",
    "Yoshi",
    "BabyMario",
    "BabyLuigi",
    "Bowser",
    "Wario",
    "Waluigi",
    "GreenKoopa",
    "RedToad",
    "Boo",
    "Toadette",
    "RedShyGuy",
    "Birdo",
    "Monty",
    "BowserJr",
    "RedParatroopa",
    "BluePianta",
    "RedPianta",
    "YellowPianta",
    "BlueNoki",
    "RedNoki",
    "GreenNoki",
    "HammerBro",
    "Toadsworth",
    "BlueToad",
    "YellowToad",
    "GreenToad",
    "PurpleToad",
    "BlueMagikoopa",
    "RedMagikoopa",
    "GreenMagikoopa",
    "YellowMagikoopa",
    "KingBoo",
    "Petey",
    "Dixie",
    "Goomba",
    "Paragoomba",
    "RedKoopa",
    "GreenParatroopa",
    "BlueShyGuy",
    "YellowShyGuy",
    "GreenShyGuy",
    "BlackShyGuy",
    "GrayDryBones",
    "GreenDryBones",
    "RedDryBones",
    "BlueDryBones",
    "FireBro",
    "BoomerangBro",
];
/// Colour family names in family-table order.
pub const FAMILY_NAMES: [&str; 10] = [
    "-",
    "Koopa",
    "Toad",
    "ShyGuy",
    "Paratroopa",
    "Pianta",
    "Noki",
    "Bros",
    "Magikoopa",
    "DryBones",
];
/// Named recolour hue presets in UI order.
pub const PRESETS: [(&str, f64); 8] = [
    ("red", 0.0),
    ("orange", 0.08),
    ("yellow", 0.16),
    ("green", 0.33),
    ("cyan", 0.5),
    ("blue", 0.62),
    ("purple", 0.76),
    ("pink", 0.9),
];
/// Source-defined replacement for findCharacterID.
pub const FINDCHAR_PATCH: &[u8] = &[
    60, 128, 128, 15, 56, 132, 133, 88, 28, 3, 0, 6, 124, 132, 2, 20, 136, 100, 0, 2, 60, 128, 128,
    53, 56, 132, 233, 160, 136, 4, 72, 174, 44, 3, 0, 17, 64, 130, 0, 16, 40, 0, 0, 0, 65, 130, 0,
    8, 56, 96, 0, 32, 56, 0, 0, 0, 152, 4, 72, 174, 78, 128, 0, 32,
];
