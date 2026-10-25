//! MSSB Clone Maker: the window. The page (`ui/`) calls the commands below; they forward to the
//! backend, which is the mock until the Rust core lands (see `tools/clonemaker/API.md`).
#![cfg_attr(windows, windows_subsystem = "windows")]

mod api;
mod mock;
mod selftest;

use std::path::Path;

use tauri::{AppHandle, Emitter, LogicalSize, Manager, State};

use api::{Failure, GameCheck, Job, Preview, PreviewRequest, SourceInfo, SourceName};

/// The core behind the commands; replacing this alias (and `mock.rs`) wires in the real one.
type Backend = mock::Mock;

/// Runs slow backend work off the main thread so the window keeps painting.
async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> T {
    tauri::async_runtime::spawn_blocking(f)
        .await
        .expect("backend task panicked")
}

#[tauri::command]
async fn open_game(app: AppHandle, path: String) -> GameCheck {
    blocking(move || app.state::<Backend>().open_game(&path)).await
}

#[tauri::command]
fn sources(backend: State<Backend>) -> Vec<SourceName> {
    backend.sources()
}

#[tauri::command]
async fn source_info(app: AppHandle, source: u32) -> Result<SourceInfo, Failure> {
    blocking(move || app.state::<Backend>().source_info(source)).await
}

#[tauri::command]
fn next_free_slot(
    backend: State<Backend>,
    source: u32,
    used: Vec<u32>,
) -> Result<Option<u32>, Failure> {
    backend.next_free_slot(source, &used)
}

#[tauri::command]
async fn preview(app: AppHandle, request: PreviewRequest) -> Result<Preview, Failure> {
    blocking(move || app.state::<Backend>().preview(&request)).await
}

#[tauri::command]
fn default_output(backend: State<Backend>, game_path: String) -> String {
    backend.default_output(&game_path)
}

#[tauri::command]
async fn validate_job(app: AppHandle, job: Job) -> Option<Failure> {
    blocking(move || app.state::<Backend>().validate_job(&job)).await
}

#[tauri::command]
fn create(app: AppHandle, backend: State<Backend>, job: Job) {
    backend.create(job, move |event| {
        // the window may already be closing; nothing left to tell then
        let _ = app.emit(event.name(), &event);
    });
}

#[tauri::command]
fn cancel(backend: State<Backend>) {
    backend.cancel();
}

/// Window-side helper (not core): the "already exists. Replace it?" question.
#[tauri::command]
fn path_exists(path: String) -> bool {
    Path::new(&path).exists()
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

fn main() {
    let options = selftest::Options::from_args();
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(Backend::default())
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
            next_free_slot,
            preview,
            default_output,
            validate_job,
            create,
            cancel,
            path_exists,
            selftest::launch_options,
            selftest::selftest_mark,
            selftest::selftest_report,
        ])
        .run(tauri::generate_context!())
        .expect("error while running the Clone Maker window");
}
