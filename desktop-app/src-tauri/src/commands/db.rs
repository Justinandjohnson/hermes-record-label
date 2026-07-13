use rusqlite::{Connection, OpenFlags};
use serde::Serialize;
use tauri::command;

use super::paths;

fn open_db() -> Result<Connection, String> {
    let db = paths::db_path();
    Connection::open_with_flags(&db, OpenFlags::SQLITE_OPEN_READ_ONLY)
        .map_err(|e| format!("Failed to open database at {}: {}", db.display(), e))
}

#[derive(Serialize)]
pub struct Track {
    id: i64,
    title: Option<String>,
    file_path: String,
    file_hash: String,
    file_size: Option<i64>,
    duration_seconds: Option<f64>,
    format: Option<String>,
    parent_track_id: Option<i64>,
    version: i64,
    state: String,
    project_id: Option<i64>,
    created_at: String,
    updated_at: String,
}

#[command]
pub fn get_tracks() -> Result<Vec<Track>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT id, title, file_path, file_hash, file_size, duration_seconds,
                    format, parent_track_id, version, state, project_id, created_at, updated_at
             FROM tracks ORDER BY created_at DESC",
        )
        .map_err(|e| e.to_string())?;

    let tracks = stmt
        .query_map([], |row| {
            Ok(Track {
                id: row.get(0)?,
                title: row.get(1)?,
                file_path: row.get(2)?,
                file_hash: row.get(3)?,
                file_size: row.get(4)?,
                duration_seconds: row.get(5)?,
                format: row.get(6)?,
                parent_track_id: row.get(7)?,
                version: row.get(8)?,
                state: row.get(9)?,
                project_id: row.get(10)?,
                created_at: row.get(11)?,
                updated_at: row.get(12)?,
            })
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    Ok(tracks)
}

#[derive(Serialize)]
pub struct Feedback {
    id: i64,
    track_id: Option<i64>,
    project_id: Option<i64>,
    agent: String,
    message: String,
    channel: String,
    direction: String,
    intent: Option<String>,
    created_at: String,
}

#[command]
pub fn get_feedback(track_id: i64) -> Result<Vec<Feedback>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT id, track_id, project_id, agent, message, channel, direction, intent, created_at
             FROM feedback WHERE track_id = ?1 ORDER BY created_at ASC",
        )
        .map_err(|e| e.to_string())?;

    let msgs = stmt
        .query_map([track_id], |row| {
            Ok(Feedback {
                id: row.get(0)?,
                track_id: row.get(1)?,
                project_id: row.get(2)?,
                agent: row.get(3)?,
                message: row.get(4)?,
                channel: row.get(5)?,
                direction: row.get(6)?,
                intent: row.get(7)?,
                created_at: row.get(8)?,
            })
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    Ok(msgs)
}

#[derive(Serialize)]
pub struct Project {
    id: i64,
    title: String,
    r#type: String,
    state: String,
    target_track_count: Option<i64>,
    target_release_date: Option<String>,
    created_at: String,
}

#[command]
pub fn get_projects() -> Result<Vec<Project>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT id, title, type, state, target_track_count, target_release_date, created_at
             FROM projects ORDER BY created_at DESC",
        )
        .map_err(|e| e.to_string())?;

    let projects = stmt
        .query_map([], |row| {
            Ok(Project {
                id: row.get(0)?,
                title: row.get(1)?,
                r#type: row.get(2)?,
                state: row.get(3)?,
                target_track_count: row.get(4)?,
                target_release_date: row.get(5)?,
                created_at: row.get(6)?,
            })
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    Ok(projects)
}

#[derive(Serialize)]
pub struct ArtistProfile {
    id: i64,
    name: String,
    genre: Option<String>,
    subgenres: Option<String>,
    influences: Option<String>,
    sound_description: Option<String>,
    bandcamp_url: Option<String>,
    quiet_hours_start: Option<String>,
    quiet_hours_end: Option<String>,
    quiet_days: Option<String>,
    timezone: String,
    onboarded_at: Option<String>,
}

#[command]
pub fn get_artist_profile() -> Result<Option<ArtistProfile>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT id, name, genre, subgenres, influences, sound_description,
                    bandcamp_url, quiet_hours_start, quiet_hours_end, quiet_days, timezone, onboarded_at
             FROM artist_profile LIMIT 1",
        )
        .map_err(|e| e.to_string())?;

    let profile = stmt
        .query_row([], |row| {
            Ok(ArtistProfile {
                id: row.get(0)?,
                name: row.get(1)?,
                genre: row.get(2)?,
                subgenres: row.get(3)?,
                influences: row.get(4)?,
                sound_description: row.get(5)?,
                bandcamp_url: row.get(6)?,
                quiet_hours_start: row.get(7)?,
                quiet_hours_end: row.get(8)?,
                quiet_days: row.get(9)?,
                timezone: row.get(10)?,
                onboarded_at: row.get(11)?,
            })
        })
        .ok();

    Ok(profile)
}

#[derive(Serialize)]
pub struct ReleaseStateEntry {
    id: i64,
    track_id: i64,
    from_state: Option<String>,
    to_state: String,
    changed_by: String,
    reason: Option<String>,
    bandcamp_job_id: Option<String>,
    created_at: String,
}

#[command]
pub fn get_release_states(track_id: i64) -> Result<Vec<ReleaseStateEntry>, String> {
    let conn = open_db()?;
    let mut stmt = conn
        .prepare(
            "SELECT id, track_id, from_state, to_state, changed_by, reason, bandcamp_job_id, created_at
             FROM release_states WHERE track_id = ?1 ORDER BY created_at ASC",
        )
        .map_err(|e| e.to_string())?;

    let states = stmt
        .query_map([track_id], |row| {
            Ok(ReleaseStateEntry {
                id: row.get(0)?,
                track_id: row.get(1)?,
                from_state: row.get(2)?,
                to_state: row.get(3)?,
                changed_by: row.get(4)?,
                reason: row.get(5)?,
                bandcamp_job_id: row.get(6)?,
                created_at: row.get(7)?,
            })
        })
        .map_err(|e| e.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|e| e.to_string())?;

    Ok(states)
}

#[derive(Serialize)]
pub struct Stats {
    current_streak: i64,
    longest_streak: i64,
    reputation: i64,
    tracks_in_progress: i64,
    tracks_released: i64,
    completion_rate: f64,
}

#[command]
pub fn get_stats() -> Result<Stats, String> {
    let conn = open_db()?;

    let tracks_in_progress: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM tracks WHERE state != 'RELEASED'",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let tracks_released: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM tracks WHERE state = 'RELEASED'",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    let total = tracks_in_progress + tracks_released;
    let completion_rate = if total > 0 {
        (tracks_released as f64 / total as f64) * 100.0
    } else {
        0.0
    };

    let reputation: i64 = conn
        .query_row(
            "SELECT CAST(value AS INTEGER) FROM artist_stats WHERE stat_type = 'reputation' ORDER BY created_at DESC LIMIT 1",
            [],
            |r| r.get(0),
        )
        .unwrap_or(0);

    Ok(Stats {
        current_streak: 0,
        longest_streak: 0,
        reputation,
        tracks_in_progress,
        tracks_released,
        completion_rate,
    })
}
