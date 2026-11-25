//! CharacterStats in GYQE01 stats.0. Offsets follow tools/datamap/stats.py.
use crate::{Error, Result};
use serde::Serialize;
use std::collections::BTreeMap;

pub(crate) const OFFSET: u64 = 0x6cf8800;
pub(crate) const ROW: usize = 0xa0;
pub(crate) const COUNT: usize = 54;
pub(crate) const SIZE: usize = COUNT * ROW;
/// Sparse edits keyed by the field names in stats.0.
pub type Overrides = BTreeMap<String, u8>;

/// Editable baseline values for one character in the selected game.
#[derive(Debug, Serialize)]
pub struct CharacterStats {
    /// Editable fields in display order.
    pub fields: Vec<StatField>,
}

/// One control and its inherited value from the selected input disc.
#[derive(Debug, Serialize)]
pub struct StatField {
    /// Field name used for saved overrides and command line edits.
    pub key: &'static str,
    /// Human-readable name for the editor.
    pub label: &'static str,
    /// Lowest accepted override.
    pub min: u8,
    /// Highest accepted override.
    pub max: u8,
    /// Current byte in the selected character row.
    pub value: u8,
}

// Key, label, byte offset, accepted minimum and maximum.
// Numeric gameplay values occupy a byte. Table selectors have a narrower domain.
const FIELDS: &[(&str, &str, usize, u8, u8)] = &[
    ("slap_contact", "Slap contact", 0x28, 0, 255),
    ("charge_contact", "Charge contact", 0x29, 0, 255),
    ("slap_power", "Slap power", 0x2a, 0, 255),
    ("charge_power", "Charge power", 0x2b, 0, 255),
    ("bunt_contact", "Bunt contact", 0x2c, 0, 255),
    ("trajectory_push_pull", "Pull / push trajectory", 0x2d, 0, 2),
    ("trajectory_high_low", "High / low trajectory", 0x2e, 0, 2),
    ("curveball_speed", "Curveball speed", 0x00, 0, 255),
    ("fastball_speed", "Fastball speed", 0x01, 0, 255),
    ("cursed_ball", "Cursed ball strength", 0x02, 0, 255),
    ("curve", "Curve", 0x03, 0, 255),
    ("curve_control", "Curve control", 0x04, 0, 255),
    ("speed", "Running speed", 0x2f, 0, 255),
    ("throwing_arm", "Throwing arm strength", 0x30, 0, 255),
    (
        "throwing_hand",
        "Throwing hand (0 right, 1 left)",
        0x26,
        0,
        1,
    ),
    ("batting_hand", "Batting hand (0 right, 1 left)", 0x27, 0, 1),
    (
        "character_class",
        "Class (0 balanced, 1 power, 2 speed, 3 technique)",
        0x31,
        0,
        3,
    ),
    ("weight", "Weight class", 0x32, 0, 4),
    ("batting_bar", "Batting display bar", 0x37, 1, 10),
    ("pitching_bar", "Pitching display bar", 0x38, 1, 10),
    ("running_bar", "Running display bar", 0x39, 1, 10),
    ("fielding_bar", "Fielding display bar", 0x3a, 1, 10),
];

/// The editable range of a tournament stat before a player selects a disc.
#[derive(Serialize)]
pub struct StatDefinition {
    key: &'static str,
    label: &'static str,
    min: u8,
    max: u8,
}

/// Return the same fields and bounds used to validate and apply clone edits.
pub fn schema() -> Vec<StatDefinition> {
    FIELDS
        .iter()
        .map(|&(key, label, _, min, max)| StatDefinition {
            key,
            label,
            min,
            max,
        })
        .collect()
}

/// Check that each override names an editable stat and fits its supported range.
pub fn validate(edits: &Overrides) -> Result<()> {
    for (key, &value) in edits {
        let &(_, _, _, min, max) = FIELDS
            .iter()
            .find(|f| f.0 == key)
            .ok_or_else(|| Error::new(format!("Unknown clone stat '{key}'.")))?;
        if !(min..=max).contains(&value) {
            return Err(Error::new(format!(
                "Clone stat '{key}' must be in {min}..{max}."
            )));
        }
    }
    Ok(())
}

/// Read [batting hand, throwing hand] for all 54 roster IDs from the caller's open final ISO.
pub fn roster_hands(file: &mut std::fs::File, path: &std::path::Path) -> Result<Vec<[u8; 2]>> {
    hands_from_table(&crate::disc::read_usa_iso_range(
        file, path, "ZZZZ.dat", OFFSET, SIZE,
    )?)
}

fn hands_from_table(table: &[u8]) -> Result<Vec<[u8; 2]>> {
    validate_table(table)?;
    table
        .as_chunks::<ROW>()
        .0
        .iter()
        .enumerate()
        .map(|(id, row)| {
            let hands = [row[0x27], row[0x26]];
            if hands.into_iter().any(|hand| hand > 1) {
                return Err(Error::new(format!(
                    "Invalid handedness in stats.0 row {id}."
                )));
            }
            Ok(hands)
        })
        .collect()
}

fn validate_table(table: &[u8]) -> Result<()> {
    if table.len() != SIZE
        || table
            .as_chunks::<ROW>()
            .0
            .iter()
            .enumerate()
            .any(|(id, r)| r[0x24..0x26] != (id as u16).to_be_bytes())
    {
        return Err(Error::new(
            "Unexpected character IDs or length in stats.0.".into(),
        ));
    }
    Ok(())
}

pub(crate) fn describe(row: &[u8], id: usize) -> Result<CharacterStats> {
    if row.len() != ROW || row[0x24..0x26] != (id as u16).to_be_bytes() {
        return Err(Error::new(format!(
            "Invalid stats.0 row for character {id}."
        )));
    }
    Ok(CharacterStats {
        fields: FIELDS
            .iter()
            .map(|&(key, label, offset, min, max)| StatField {
                key,
                label,
                min,
                max,
                value: row[offset],
            })
            .collect(),
    })
}

pub(crate) fn validate_slots(slots: &[usize], edits: &BTreeMap<usize, Overrides>) -> Result<()> {
    for (&slot, fields) in edits {
        if !slots.contains(&slot) {
            return Err(Error::new(format!(
                "Stat overrides target slot {slot}, which is not a clone."
            )));
        }
        validate(fields)?;
    }
    Ok(())
}

pub(crate) fn patch(
    table: &mut [u8],
    slots: &[usize],
    sources: &[usize],
    copy_stats: bool,
    edits: &BTreeMap<usize, Overrides>,
) -> Result<()> {
    validate_table(table)?;
    validate_slots(slots, edits)?;
    if copy_stats {
        // Sources cannot also be donors. Copy all rows before incoming chemistry columns.
        for (&slot, &source) in slots.iter().zip(sources) {
            table.copy_within(source * ROW..(source + 1) * ROW, slot * ROW);
            table[slot * ROW + 0x24..slot * ROW + 0x26]
                .copy_from_slice(&(slot as u16).to_be_bytes());
        }
        for (&slot, &source) in slots.iter().zip(sources) {
            for row in table.as_chunks_mut::<ROW>().0 {
                row[0x3b + slot] = row[0x3b + source];
            }
        }
    }
    for (&slot, fields) in edits {
        for &(key, _, offset, _, _) in FIELDS {
            if let Some(&value) = fields.get(key) {
                table[slot * ROW + offset] = value;
            }
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn table() -> Vec<u8> {
        let mut result = vec![0; SIZE];
        for (id, row) in result.as_chunks_mut::<ROW>().0.iter_mut().enumerate() {
            row.fill(id as u8);
            row[0x24..0x26].copy_from_slice(&(id as u16).to_be_bytes());
            for j in 0..COUNT {
                row[0x3b + j] = (id + j) as u8;
            }
        }
        result
    }
    #[test]
    fn two_clones_keep_independent_stats_and_identity() {
        let mut data = table();
        let edits = BTreeMap::from([
            (29, Overrides::from([("speed".into(), 88)])),
            (30, Overrides::from([("speed".into(), 31)])),
        ]);
        patch(&mut data, &[29, 30], &[4, 4], true, &edits).unwrap();
        for (id, speed) in [(29, 88), (30, 31), (4, 4)] {
            let row = &data[id * ROW..(id + 1) * ROW];
            assert_eq!(row[0x2f], speed);
            assert_eq!(row[0x2b], 4);
            assert_eq!(row[0x3b + 29], 8);
            assert_eq!(row[0x25], id as u8);
        }
    }
    #[test]
    fn team_hands_use_batting_then_throwing_from_each_final_slot() {
        let mut input = table();
        for row in input.as_chunks_mut::<ROW>().0 {
            row[0x26] = 0;
            row[0x27] = 0;
        }
        input[29 * ROW + 0x26] = 1;
        input[30 * ROW + 0x27] = 1;
        let hands = hands_from_table(&input).unwrap();
        assert_eq!(hands.len(), 54);
        assert_eq!((hands[29], hands[30]), ([0, 1], [1, 0]));
        input[29 * ROW + 0x27] = 2;
        assert!(hands_from_table(&input).is_err());
        input[29 * ROW + 0x27] = 0;
        input[30 * ROW + 0x25] = 33;
        assert!(hands_from_table(&input).is_err());
    }
    #[test]
    fn donor_baseline_changes_only_specified_bytes() {
        let before = table();
        let mut edited = before.clone();
        let edits = BTreeMap::from([(29, Overrides::from([("batting_bar".into(), 9)]))]);
        patch(&mut edited, &[29], &[4], false, &edits).unwrap();
        let mut expected = before;
        expected[29 * ROW + 0x37] = 9;
        assert_eq!(edited, expected);
    }
    #[test]
    fn invalid_edit_cannot_change_any_row() {
        let baseline = table();
        for (field, value, slot) in [
            ("char_id", 0, 29),
            ("pitching_bar", 0, 29),
            ("speed", 20, 4),
        ] {
            let mut data = baseline.clone();
            let edits = BTreeMap::from([(slot, Overrides::from([(field.into(), value)]))]);
            assert!(patch(&mut data, &[29], &[4], true, &edits).is_err());
            assert_eq!(data, baseline);
        }
    }
}
