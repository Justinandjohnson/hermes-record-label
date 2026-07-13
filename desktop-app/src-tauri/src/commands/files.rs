use std::fs;
use std::path::Path;
use tauri::command;

use super::paths;

#[command]
pub fn handle_file_drop(file_path: String) -> Result<String, String> {
    let path = Path::new(&file_path);

    if !path.exists() {
        return Err("File does not exist".into());
    }

    let ext = path
        .extension()
        .and_then(|e| e.to_str())
        .unwrap_or("")
        .to_lowercase();

    let supported = ["wav", "flac", "mp3", "aiff", "aif", "ogg"];
    if !supported.contains(&ext.as_str()) {
        return Err(format!("Unsupported format: .{}", ext));
    }

    // Resolve inbox path at runtime
    let inbox = paths::inbox_dir();
    fs::create_dir_all(&inbox).map_err(|e| format!("Failed to create inbox: {}", e))?;

    let filename = path.file_name().ok_or("Invalid filename")?;
    let dest = inbox.join(filename);
    fs::copy(path, &dest).map_err(|e| format!("Failed to copy file: {}", e))?;

    Ok(format!("File queued: {}", dest.display()))
}
