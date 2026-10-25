//! Data-only character cloning for Mario Superstar Baseball (USA, GYQE01).

pub mod binary;
pub mod colour;
pub mod constants;
pub mod disc;
pub mod lzss;
pub mod menu;
pub mod texture;

/// A plain error message suitable for the CLI or the Clone Maker window.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Error(pub String);
impl std::fmt::Display for Error {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for Error {}
impl From<std::io::Error> for Error {
    fn from(e: std::io::Error) -> Self {
        Self(e.to_string())
    }
}
/// A fallible operation on the game's data.
pub type Result<T> = std::result::Result<T, Error>;
/// Round a game file offset up to a power-of-two alignment.
pub fn align(v: usize, a: usize) -> usize {
    (v + a - 1) & !(a - 1)
}
