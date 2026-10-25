//! The payloads of the window <-> core contract (`tools/clonemaker/API.md`).

use serde::{Deserialize, Serialize};

/// `{ "error": "<friendly message>" }`: what a failing command returns.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct Failure {
    pub error: String,
}

impl Failure {
    pub fn new(message: impl Into<String>) -> Self {
        Failure {
            error: message.into(),
        }
    }
}

#[derive(Debug, Clone, Serialize)]
pub struct GameCheck {
    pub ok: bool,
    pub message: String,
    pub kind: Option<&'static str>,
}

#[derive(Debug, Clone, Serialize)]
pub struct SourceName {
    pub id: u32,
    pub name: String,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Region {
    pub index: u32,
    pub label: String,
    pub default: bool,
    pub swatch: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct Slot {
    pub id: u32,
    pub label: String,
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Clone {
    pub slot: u32,
    pub colour: String,
}

#[derive(Debug, Clone, Serialize)]
pub struct SourceInfo {
    pub regions: Vec<Region>,
    pub slots: Vec<Slot>,
    pub default_rows: Vec<Clone>,
}

#[derive(Debug, Clone, Deserialize)]
pub struct PreviewRequest {
    pub source: u32,
    pub slot: u32,
    pub colour: String,
    pub keeplum: f64,
    /// Ticked colour regions; `None` = the source's default ones.
    pub regions: Option<Vec<u32>>,
}

#[derive(Debug, Clone, Serialize)]
pub struct Preview {
    pub before_png: String,
    pub after_png: String,
    pub caption: String,
}

#[derive(Debug, Clone, Deserialize)]
pub struct Job {
    pub game: String,
    pub output: String,
    pub source: u32,
    pub clones: Vec<Clone>,
    pub keeplum: f64,
    pub regions: Option<Vec<u32>>,
    pub ui_source: bool,
    pub copy_stats: bool,
    pub keep_colour_word: bool,
    pub keep_work: bool,
}

/// What a running `create` reports; each variant is emitted as the event `name()` with its fields.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(untagged)]
pub enum JobEvent {
    Log {
        line: String,
    },
    Progress {
        fraction: f64,
        label: String,
    },
    Done {
        iso: String,
        work: Option<String>,
        hint: String,
    },
    Error {
        message: String,
        cancelled: bool,
    },
}

impl JobEvent {
    pub fn name(&self) -> &'static str {
        match self {
            JobEvent::Log { .. } => "log",
            JobEvent::Progress { .. } => "progress",
            JobEvent::Done { .. } => "done",
            JobEvent::Error { .. } => "error",
        }
    }
}
