use std::process::Command;
use tauri::command;

use super::paths;

#[command]
pub fn send_agent_message(agent: String, message: String) -> Result<String, String> {
    let agent_bin = paths::agent_binary(&agent);

    if !agent_bin.exists() {
        return Err(format!(
            "Agent binary not found at {}. Is Hermes installed?",
            agent_bin.display()
        ));
    }

    let output = Command::new(&agent_bin)
        .args(["chat", "--once", &message])
        .output()
        .map_err(|e| format!("Failed to reach agent {}: {}", agent, e))?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr);
        return Err(format!("Agent {} error: {}", agent, stderr));
    }

    let stdout = String::from_utf8_lossy(&output.stdout).to_string();
    Ok(stdout)
}
