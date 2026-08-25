-- Tag a feedback message to a position in its track's audio (seconds from start).
-- NULL = untagged (comment applies to the whole track).
ALTER TABLE feedback ADD COLUMN timestamp_sec REAL;
