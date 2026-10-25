//! `--selftest [--game P] [--log P]`: the page walks through the window (every widget, the narrow
//! layout, a game that can't be used, timed resizing) and, given a game, through every feature
//! (rows, colours, regions, previews, Advanced, errors, create, cancel), reports here and the app
//! exits 0 (pass) or 1. The CI runs it on the built exe without a game (retail files can't go
//! there); a `--windows`-subsystem exe has no console, hence `--log`. With `MSSB_SHOT_CMD` set,
//! the page calls that program with a name at each state (screenshots, see `selftest/`).

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::Command;

use serde::Serialize;
use tauri::{AppHandle, State};

#[derive(Default)]
pub struct Options {
    selftest: bool,
    log: Option<PathBuf>,
    game: Option<PathBuf>,
    /// Scratch folder for the outputs; removed when the self-test reports.
    dir: Option<PathBuf>,
}

impl Options {
    pub fn from_args() -> Self {
        let args: Vec<String> = std::env::args().collect();
        if !args.iter().any(|a| a == "--selftest") {
            return Options::default();
        }
        let value = |flag: &str| {
            args.iter()
                .position(|a| a == flag)
                .and_then(|i| args.get(i + 1))
                .map(PathBuf::from)
        };
        let dir = std::env::temp_dir().join(format!("mssb-clone-selftest-{}", std::process::id()));
        // an output that already exists, for the "Replace it?" question
        let dir = fs::create_dir_all(&dir)
            .and_then(|_| fs::write(dir.join("Old Clones.iso"), b""))
            .is_ok()
            .then_some(dir);
        Options {
            selftest: true,
            log: value("--log"),
            game: value("--game"),
            dir,
        }
    }
}

#[derive(Serialize)]
pub struct LaunchOptions {
    selftest: bool,
    /// The scratch folder and the game to test with (null when not given).
    dir: Option<PathBuf>,
    game: Option<PathBuf>,
}

#[tauri::command]
pub fn launch_options(options: State<Options>) -> LaunchOptions {
    LaunchOptions {
        selftest: options.selftest,
        dir: options.dir.clone(),
        game: options.game.clone(),
    }
}

/// Runs `MSSB_SHOT_CMD <name>`; returns why it failed, if it did (a missing screenshot is noted in
/// the report, not a failed test).
#[tauri::command]
pub async fn selftest_mark(name: String) -> Option<String> {
    let cmd = std::env::var("MSSB_SHOT_CMD").ok()?;
    let run = move || match Command::new(&cmd).arg(name).output() {
        Ok(out) if out.status.success() => None,
        Ok(out) => Some(format!(
            "{} ({})",
            String::from_utf8_lossy(&out.stderr).trim(),
            out.status
        )),
        Err(e) => Some(format!("{cmd}: {e}")),
    };
    tauri::async_runtime::spawn_blocking(run)
        .await
        .unwrap_or_else(|e| Some(e.to_string()))
}

#[tauri::command]
pub fn selftest_report(app: AppHandle, options: State<Options>, ok: bool, lines: Vec<String>) {
    let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
    match &options.log {
        Some(path) => {
            if let Err(e) = fs::write(path, &text) {
                eprintln!("selftest: can't write {}: {e}", path.display());
            }
        }
        None => {
            let _ = std::io::stdout().write_all(text.as_bytes());
        }
    }
    if let Some(dir) = &options.dir {
        let _ = fs::remove_dir_all(dir);
    }
    app.exit(if ok { 0 } else { 1 });
}
