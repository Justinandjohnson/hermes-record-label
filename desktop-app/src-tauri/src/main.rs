#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod commands;

use std::path::PathBuf;
use std::process::Command;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .expect("src-tauri has parent")
        .parent()
        .expect("desktop-app has parent")
        .to_path_buf()
}

fn start_label_stack() {
    let repo_root = repo_root();
    let launch_script = repo_root.join("scripts/launch.sh");
    assert!(
        launch_script.exists(),
        "launch script missing at {}",
        launch_script.display()
    );

    let status = Command::new("/bin/bash")
        .arg(launch_script)
        .arg("--no-app")
        .current_dir(&repo_root)
        .spawn();

    if let Err(error) = status {
        panic!("failed to start label stack: {error}");
    }
}

fn main() {
    start_label_stack();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .invoke_handler(tauri::generate_handler![
            // Database reads
            commands::db::get_tracks,
            commands::db::get_feedback,
            commands::db::get_projects,
            commands::db::get_artist_profile,
            commands::db::get_release_states,
            commands::db::get_stats,
            // File handling
            commands::files::handle_file_drop,
            // Path resolution
            commands::paths::get_data_dir,
            commands::paths::get_db_path,
            commands::paths::get_inbox_dir,
            // Session intelligence
            commands::sessions::get_sessions,
            commands::sessions::get_export_events,
            commands::sessions::load_settings,
            commands::sessions::save_settings,
        ])
        .run(tauri::generate_context!())
        .expect("error running tauri application");
}
