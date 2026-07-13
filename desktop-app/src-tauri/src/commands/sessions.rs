use serde::Serialize;
use tauri::command;

use super::paths;

fn open_db() -> Result<rusqlite::Connection, String> {
    let db = paths::db_path();
    rusqlite::Connection::open_with_flags(&db, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("Failed to open database at {}: {}", db.display(), e))
}

#[derive(Serialize)]
pub struct AbletonSession {
    id: i64,
    project_name: String,
    project_path: String,
    session_date: String,
    started_at: String,
    ended_at: String,
    duration_minutes: i64,
    save_count: i64,
    export_count: i64,
    bpm: Option<f64>,
    musical_key: Option<String>,
    track_count: Option<i64>,
}

#[command]
pub fn get_sessions(limit: Option<i64>) -> Result<Vec<AbletonSession>, String> {
    let conn = match open_db() {
        Ok(c) => c,
        // Table may not exist yet — return empty list gracefully
        Err(_) => return Ok(vec![]),
    };
    let n = limit.unwrap_or(50);
    let mut stmt = conn
        .prepare(
            "SELECT id, project_name, project_path, session_date, started_at, ended_at,
                    duration_minutes, save_count, export_count, bpm, musical_key, track_count
             FROM ableton_sessions ORDER BY started_at DESC LIMIT ?1",
        )
        .map_err(|e| e.to_string())?;

    let rows = stmt
        .query_map([n], |row| {
            Ok(AbletonSession {
                id: row.get(0)?,
                project_name: row.get(1)?,
                project_path: row.get(2)?,
                session_date: row.get(3)?,
                started_at: row.get(4)?,
                ended_at: row.get(5)?,
                duration_minutes: row.get(6)?,
                save_count: row.get(7)?,
                export_count: row.get(8)?,
                bpm: row.get(9)?,
                musical_key: row.get(10)?,
                track_count: row.get(11)?,
            })
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    Ok(rows)
}

#[derive(Serialize)]
pub struct ExportEvent {
    id: i64,
    project_name: Option<String>,
    file_path: String,
    file_hash: String,
    changed_from_prev: i64,
    similarity_score: Option<f64>,
    file_size: Option<i64>,
    duration_seconds: Option<f64>,
    exported_at: String,
}

#[command]
pub fn get_export_events(limit: Option<i64>) -> Result<Vec<ExportEvent>, String> {
    let conn = match open_db() {
        Ok(c) => c,
        Err(_) => return Ok(vec![]),
    };
    let n = limit.unwrap_or(50);
    let mut stmt = conn
        .prepare(
            "SELECT id, project_name, file_path, file_hash, changed_from_prev,
                    similarity_score, file_size, duration_seconds, exported_at
             FROM export_events ORDER BY exported_at DESC LIMIT ?1",
        )
        .map_err(|e| e.to_string())?;

    let rows = stmt
        .query_map([n], |row| {
            Ok(ExportEvent {
                id: row.get(0)?,
                project_name: row.get(1)?,
                file_path: row.get(2)?,
                file_hash: row.get(3)?,
                changed_from_prev: row.get(4)?,
                similarity_score: row.get(5)?,
                file_size: row.get(6)?,
                duration_seconds: row.get(7)?,
                exported_at: row.get(8)?,
            })
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    Ok(rows)
}

#[derive(serde::Deserialize, Serialize, Clone)]
pub struct AppSettings {
    pub ableton_project_folder: String,
    pub ableton_export_folder: String,
    pub artist_name: String,
    pub artist_phone: String,
    pub quiet_hours_start: String,
    pub quiet_hours_end: String,
    pub quiet_days: Vec<String>,
    pub dnd_enabled: bool,
}

impl Default for AppSettings {
    fn default() -> Self {
        Self {
            ableton_project_folder: String::new(),
            ableton_export_folder: String::new(),
            artist_name: String::new(),
            artist_phone: String::new(),
            quiet_hours_start: "22:00".into(),
            quiet_hours_end: "09:00".into(),
            quiet_days: vec![],
            dnd_enabled: false,
        }
    }
}

#[command]
pub fn load_settings() -> Result<AppSettings, String> {
    let path = paths::data_dir().join("settings.json");
    if !path.exists() {
        return Ok(AppSettings::default());
    }
    let raw = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    serde_json::from_str(&raw).map_err(|e| e.to_string())
}

#[command]
pub fn save_settings(settings: AppSettings) -> Result<(), String> {
    let path = paths::data_dir().join("settings.json");
    let raw = serde_json::to_string_pretty(&settings).map_err(|e| e.to_string())?;
    std::fs::write(&path, raw).map_err(|e| e.to_string())
}
