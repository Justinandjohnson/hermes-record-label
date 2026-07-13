-- Migration 010: Add phone numbers to artist_profile
-- artist phone_number    = artist's personal cell (SMS destination)
-- agent_phone_number     = the Twilio number agents send FROM
--
-- Both are also stored in .env (ARTIST_PHONE_NUMBER, TWILIO_PHONE_NUMBER)
-- but keeping them in the DB lets agents look them up via SQL without
-- needing env var access.

-- SQLite does not support ADD COLUMN IF NOT EXISTS — use plain ADD COLUMN.
-- Running this twice on an existing DB will fail silently (handled by launch.sh || true).
ALTER TABLE artist_profile ADD COLUMN phone_number TEXT;
ALTER TABLE artist_profile ADD COLUMN agent_phone_number TEXT;

-- Seed timezone default if a profile already exists.
-- Phone numbers come from .env via scripts/set_artist_phone.sh — never hardcoded here.
UPDATE artist_profile
SET timezone = COALESCE(timezone, 'America/Chicago')
WHERE id = 1;
