//! Data-only character cloning for Mario Superstar Baseball (USA, GYQE01).
#![forbid(unsafe_code)]
#![deny(missing_docs)]

pub mod anims;
pub mod api;
pub mod binary;
pub mod cli;
pub mod clone;
pub mod colour;
pub mod constants;
pub mod custom_textures;
pub mod disc;
pub mod gltf;
pub mod lzss;
pub mod menu;
pub mod sound;
pub mod texture;
pub mod texture_encode;

/// A plain patcher error with optional operating-system details for friendly window messages.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error {
    /// The original patcher or operating-system message.
    pub message: String,
    io: Option<IoFailure>,
}
#[derive(Debug, Clone, PartialEq, Eq)]
struct IoFailure {
    code: Option<i32>,
    reason: String,
    path: Option<std::path::PathBuf>,
}
impl Error {
    /// Construct an explicit validation or patcher failure.
    pub fn new(message: String) -> Self {
        Self { message, io: None }
    }
    /// Attach the affected file's path to an operating-system failure for the window dialog.
    pub fn at_path(mut self, path: &std::path::Path) -> Self {
        if let Some(io) = &mut self.io {
            io.path = Some(path.to_path_buf());
        }
        self
    }
}
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        let message = e.to_string();
        let reason = message
            .split(" (os error ")
            .next()
            .unwrap_or(&message)
            .to_owned();
        Self {
            message,
            io: Some(IoFailure {
                code: e.raw_os_error(),
                reason,
                path: None,
            }),
        }
    }
}
/// A fallible operation on the game's data.
pub type Result<T> = std::result::Result<T, Error>;
/// Round a game file offset up to a power-of-two alignment.
pub fn align(v: usize, a: usize) -> usize {
    (v + a - 1) & !(a - 1)
}

/// Normalize redundant separators and dot components as Python pathlib does for CLI log paths.
pub fn normalize_path(path: &std::path::Path) -> std::path::PathBuf {
    let out: std::path::PathBuf = path
        .components()
        .filter(|c| !matches!(c, std::path::Component::CurDir))
        .collect();
    if out.as_os_str().is_empty() {
        ".".into()
    } else {
        out
    }
}
