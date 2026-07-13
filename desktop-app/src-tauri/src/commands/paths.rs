//! Cross-platform path resolution for the AI Record Label.
//!
//! All paths are resolved at runtime — nothing is hardcoded.
//!
//! Layout:
//!   DATA_DIR/                     (platform-specific, see below)
//!     hermes.db                   database
//!     inbox/                      file watcher drop folder
//!     config.json                 user-editable settings
//!
//! DATA_DIR resolution order:
//!   1. $AI_RECORD_LABEL_DATA env var (explicit override)
//!   2. Platform default:
//!        macOS:   ~/Library/Application Support/ai-record-label
//!        Windows: %APPDATA%\ai-record-label
//!        Linux:   ~/.local/share/ai-record-label

use std::path::PathBuf;
use std::sync::OnceLock;
use tauri::command;

static DATA_DIR: OnceLock<PathBuf> = OnceLock::new();

/// Resolve and cache the data directory.
pub fn data_dir() -> &'static PathBuf {
    DATA_DIR.get_or_init(|| {
        // 1. Explicit env override
        if let Ok(dir) = std::env::var("AI_RECORD_LABEL_DATA") {
            let p = PathBuf::from(dir);
            std::fs::create_dir_all(&p).ok();
            return p;
        }

        // 2. Platform default
        let base = if cfg!(target_os = "macos") {
            dirs::data_dir().unwrap_or_else(|| PathBuf::from("."))
        } else if cfg!(target_os = "windows") {
            dirs::config_dir().unwrap_or_else(|| PathBuf::from("."))
        } else {
            dirs::data_local_dir().unwrap_or_else(|| PathBuf::from("."))
        };

        let p = base.join("ai-record-label");
        std::fs::create_dir_all(&p).ok();
        p
    })
}

/// Path to the SQLite database.
pub fn db_path() -> PathBuf {
    data_dir().join("hermes.db")
}

/// Path to the inbox folder where audio files are dropped.
pub fn inbox_dir() -> PathBuf {
    let p = data_dir().join("inbox");
    std::fs::create_dir_all(&p).ok();
    p
}

/// Tauri command: expose the resolved data dir to the frontend.
#[command]
pub fn get_data_dir() -> String {
    data_dir().to_string_lossy().into_owned()
}

/// Tauri command: expose the resolved DB path to the frontend.
#[command]
pub fn get_db_path() -> String {
    db_path().to_string_lossy().into_owned()
}

/// Tauri command: expose the inbox path to the frontend.
#[command]
pub fn get_inbox_dir() -> String {
    inbox_dir().to_string_lossy().into_owned()
}
