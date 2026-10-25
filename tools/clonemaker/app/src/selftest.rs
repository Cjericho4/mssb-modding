//! `--selftest [--log P]`: the page walks through every feature against a stand-in game, measures
//! resizing, reports here and the app exits 0 (pass) or 1. The CI runs it on the built exe; a
//! `--windows`-subsystem exe has no console, hence `--log`. With `MSSB_SHOT_CMD` set, the page
//! calls that program with a name at each state (screenshots, see `selftest/`).

use std::fs;
use std::io::Write;
use std::path::PathBuf;
use std::process::Command;

use serde::Serialize;
use tauri::{AppHandle, State};

const GAME: &str = "Mario Superstar Baseball.iso";

#[derive(Default)]
pub struct Options {
    selftest: bool,
    log: Option<PathBuf>,
    /// Scratch folder holding the stand-in game; removed when the self-test reports.
    dir: Option<PathBuf>,
}

impl Options {
    pub fn from_args() -> Self {
        let args: Vec<String> = std::env::args().collect();
        if !args.iter().any(|a| a == "--selftest") {
            return Options::default();
        }
        let log = args
            .iter()
            .position(|a| a == "--log")
            .and_then(|i| args.get(i + 1))
            .map(PathBuf::from);
        let dir = std::env::temp_dir().join(format!("mssb-clone-selftest-{}", std::process::id()));
        let mut disc = vec![0u8; 0x440];
        disc[..6].copy_from_slice(b"GYQE01");
        disc[0x1C..0x20].copy_from_slice(&0xC233_9F3Du32.to_be_bytes());
        // the game, and an output that already exists (for the "Replace it?" question)
        let dir = fs::create_dir_all(&dir)
            .and_then(|_| fs::write(dir.join(GAME), disc))
            .and_then(|_| fs::write(dir.join("Old Clones.iso"), b""))
            .is_ok()
            .then_some(dir);
        Options {
            selftest: true,
            log,
            dir,
        }
    }
}

#[derive(Serialize)]
pub struct LaunchOptions {
    selftest: bool,
    /// The scratch folder and the stand-in game in it (null when not self-testing).
    dir: Option<String>,
    game: Option<String>,
}

#[tauri::command]
pub fn launch_options(options: State<Options>) -> LaunchOptions {
    LaunchOptions {
        selftest: options.selftest,
        dir: options
            .dir
            .as_ref()
            .map(|d| d.to_string_lossy().into_owned()),
        game: options
            .dir
            .as_ref()
            .map(|d| d.join(GAME).to_string_lossy().into_owned()),
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
