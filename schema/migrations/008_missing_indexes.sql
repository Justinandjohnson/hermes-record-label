-- 008_missing_indexes.sql

CREATE INDEX IF NOT EXISTS idx_pending_messages_from_agent
    ON pending_messages (from_agent);

CREATE INDEX IF NOT EXISTS idx_pending_messages_priority
    ON pending_messages (priority, submitted_at)
    WHERE status = 'pending';

CREATE INDEX IF NOT EXISTS idx_creation_streaks_started
    ON creation_streaks (started_at);

CREATE INDEX IF NOT EXISTS idx_listening_panel_active
    ON listening_panel (active)
    WHERE active = 1;

CREATE INDEX IF NOT EXISTS idx_royalty_registrations_status
    ON royalty_registrations (status);

CREATE INDEX IF NOT EXISTS idx_royalty_registrations_org
    ON royalty_registrations (org_name);

CREATE INDEX IF NOT EXISTS idx_works_reg_org_status
    ON works_registrations (org_name, status);

CREATE INDEX IF NOT EXISTS idx_sync_submissions_status
    ON sync_submissions (status);

CREATE INDEX IF NOT EXISTS idx_panel_responses_panelist
    ON panel_responses (panelist_id);

CREATE INDEX IF NOT EXISTS idx_feedback_agent_track
    ON feedback (agent, track_id);

CREATE INDEX IF NOT EXISTS idx_export_events_fingerprint
    ON export_events (fingerprint)
    WHERE fingerprint IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_ableton_sessions_duration
    ON ableton_sessions (duration_minutes)
    WHERE duration_minutes IS NOT NULL;
