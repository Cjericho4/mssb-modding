//! MSSB Clone Maker: the window. The page (`ui/`) calls the commands below; they forward to the
//! core (`tools/clonemaker/core`, `api::Core`) as described in `tools/clonemaker/API.md`.
#![cfg_attr(windows, windows_subsystem = "windows")]

mod selftest;

use std::path::PathBuf;

use mssb_clone_core::api::{
    AnimChoice, ApiError, Core, Event, ExportedTextures, Job, OpenGame, Preview, PreviewRequest,
    Source, SourceInfo,
};
use tauri::{AppHandle, Emitter, LogicalSize, Manager, State};

/// Runs slow core work (reading the disc, unpacking textures) off the main thread so the window
/// keeps painting.
async fn blocking<T: Send + 'static>(
    f: impl FnOnce(&Core) -> T + Send + 'static,
    app: AppHandle,
) -> T {
    tauri::async_runtime::spawn_blocking(move || f(&app.state::<Core>()))
        .await
        .expect("core task panicked")
}

#[tauri::command]
async fn open_game(app: AppHandle, path: PathBuf) -> OpenGame {
    blocking(move |core| core.open_game(&path), app).await
}

#[tauri::command]
fn sources(core: State<Core>) -> Vec<Source> {
    core.sources()
}

#[tauri::command]
async fn source_info(app: AppHandle, source: usize) -> Result<SourceInfo, ApiError> {
    blocking(move |core| core.source_info(source), app).await
}

#[tauri::command]
async fn anim_choices(app: AppHandle, source: usize) -> Result<Vec<AnimChoice>, ApiError> {
    blocking(move |core| core.anim_choices(source), app).await
}

#[tauri::command]
fn next_free_slot(
    core: State<Core>,
    source: usize,
    used: Vec<usize>,
) -> Result<Option<usize>, ApiError> {
    core.next_free_slot(source, &used)
}

#[tauri::command]
async fn preview(app: AppHandle, request: PreviewRequest) -> Result<Preview, ApiError> {
    blocking(move |core| core.preview(&request), app).await
}

#[tauri::command]
fn default_output(core: State<Core>, game_path: PathBuf) -> PathBuf {
    core.default_output(&game_path)
}

#[tauri::command]
async fn validate_job(app: AppHandle, job: Job) -> Option<ApiError> {
    blocking(move |core| core.validate_job(&job), app).await
}

/// Starts the job; its events reach the page as `log`, `progress`, `done` and `error`.
#[tauri::command]
fn create(app: AppHandle, core: State<Core>, job: Job) -> Result<(), ApiError> {
    core.create(job, move |event: Event| {
        // the core serialises events as { event, payload }
        if let Ok(value) = serde_json::to_value(&event) {
            if let Some(name) = value["event"].as_str() {
                // the window may already be closing; nothing left to tell then
                let _ = app.emit(name, &value["payload"]);
            }
        }
    })
    .map(drop)
    .map_err(ApiError::from)
}

/// Writes the source's body textures as PNGs into a new "<Name> textures" folder in `folder`.
#[tauri::command]
async fn export_textures(
    app: AppHandle,
    source: usize,
    folder: PathBuf,
) -> Result<ExportedTextures, ApiError> {
    blocking(move |core| core.export_textures(source, &folder), app).await
}

#[tauri::command]
fn cancel(core: State<Core>) {
    core.cancel();
}

/// Window-side helper (not core): the "already exists. Replace it?" question.
#[tauri::command]
fn path_exists(path: PathBuf) -> bool {
    path.exists()
}

/// Opens at 1320 x 780, or the screen minus a taskbar's height if that is smaller.
fn fit_to_screen(window: &tauri::WebviewWindow) -> tauri::Result<()> {
    if let Some(monitor) = window.current_monitor()? {
        let screen = monitor.size().to_logical::<f64>(monitor.scale_factor());
        let size = LogicalSize::new(screen.width.min(1320.0), (screen.height - 80.0).min(780.0));
        window.set_size(size)?;
        window.center()?;
    }
    Ok(())
}

/// Opens the releases page in the browser (fixed URL: the page cannot open anything else).
#[tauri::command]
fn open_releases() {
    open_url("https://github.com/Cjericho4/mssb-modding/releases/latest");
}

/// Opens the GitHub page for a bug report or an idea.
#[tauri::command]
fn open_feedback() {
    open_url("https://github.com/Cjericho4/mssb-modding/issues/new/choose");
}

/// Fixed URLs only: the window never asks Rust to open a link it chose itself.
fn open_url(url: &str) {
    #[cfg(windows)]
    let mut cmd = std::process::Command::new("explorer");
    #[cfg(target_os = "macos")]
    let mut cmd = std::process::Command::new("open");
    #[cfg(all(unix, not(target_os = "macos")))]
    let mut cmd = std::process::Command::new("xdg-open");
    let _ = cmd.arg(url).spawn();
}

fn main() {
    let options = selftest::Options::from_args();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Core::default())
        .manage(options)
        .setup(|app| {
            if let Some(window) = app.get_webview_window("main") {
                fit_to_screen(&window)?;
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            open_game,
            sources,
            source_info,
            anim_choices,
            next_free_slot,
            preview,
            default_output,
            validate_job,
            create,
            cancel,
            export_textures,
            path_exists,
            open_releases,
            open_feedback,
            selftest::launch_options,
            selftest::selftest_mark,
            selftest::selftest_report,
            selftest::selftest_edit_png,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Clone Maker window");
}
