import asyncio
import sqlite3
import sys
import types

_mumble_stub = types.ModuleType("stem_separation.mumble_analyzer")
_mumble_stub.MumbleAnalysis = object
_mumble_stub.analyze_mumble = lambda *args, **kwargs: None
sys.modules.setdefault("stem_separation.mumble_analyzer", _mumble_stub)

from audio_analysis.models import AudioAnalysis, MixObservation, NotableMoment

from coordination import dispatcher
from coordination.dispatcher import PipelineError, TrackPipelineDispatcher


def _db(path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE tracks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            file_path TEXT NOT NULL,
            file_hash TEXT NOT NULL,
            file_size INTEGER,
            duration_seconds REAL,
            format TEXT,
            parent_track_id INTEGER,
            version INTEGER DEFAULT 1,
            state TEXT NOT NULL DEFAULT 'DRAFT',
            project_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            vault_reason TEXT,
            vault_date TEXT
        );
        CREATE TABLE feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER,
            project_id INTEGER,
            agent TEXT NOT NULL,
            message TEXT NOT NULL,
            channel TEXT NOT NULL,
            direction TEXT NOT NULL,
            intent TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE release_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            from_state TEXT,
            to_state TEXT NOT NULL,
            changed_by TEXT NOT NULL,
            reason TEXT,
            bandcamp_job_id TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE audio_analyses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            model_used TEXT NOT NULL DEFAULT 'gemini-3.1-pro',
            bpm REAL,
            musical_key TEXT,
            energy_curve TEXT,
            structure TEXT,
            instruments TEXT,
            genre_tags TEXT,
            mood_tags TEXT,
            mix_observations TEXT,
            notable_moments TEXT,
            raw_response TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE track_segments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            start_sec REAL NOT NULL,
            end_sec REAL NOT NULL,
            section_label TEXT,
            energy INTEGER,
            elements_present TEXT,
            mood TEXT,
            production_notes TEXT,
            standout INTEGER NOT NULL DEFAULT 0,
            standout_reason TEXT,
            visual_anchor TEXT,
            model_used TEXT,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE track_audio_features (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            bpm REAL,
            beat_count INTEGER,
            time_signature TEXT,
            musical_key TEXT,
            key_confidence REAL,
            spectral_centroid_mean REAL,
            spectral_rolloff_mean REAL,
            loudness_rms REAL,
            dynamic_range_db REAL,
            mode TEXT,
            hpss_harmonic_ratio REAL,
            onset_density REAL,
            tempo_variability REAL,
            pulse_clarity REAL,
            spectral_flatness_mean REAL,
            spectral_contrast_mean REAL,
            zero_crossing_rate_mean REAL,
            tonnetz_mean TEXT,
            mfcc_mean TEXT,
            mfcc_var TEXT,
            madmom_bpm REAL,
            madmom_beat_confidence REAL,
            madmom_downbeat_count INTEGER,
            madmom_swing_ratio REAL,
            analyzed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX idx_audio_features_track ON track_audio_features (track_id);
        CREATE TABLE track_audio_embeddings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            model TEXT NOT NULL DEFAULT 'CNN14',
            embedding BLOB NOT NULL,
            extracted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE UNIQUE INDEX idx_audio_embeddings_track_model
            ON track_audio_embeddings (track_id, model);
        INSERT INTO tracks (title, file_path, file_hash, state, format, version)
        VALUES ('song', '/tmp/song.wav', 'abc', 'DRAFT', 'wav', 1);
        """
    )
    conn.commit()
    return conn


def _add_optional_post_drop_tables(conn):
    conn.executescript(
        """
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            type TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'active',
            target_track_count INTEGER,
            target_release_date TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE kg_nodes (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL,
            label TEXT NOT NULL,
            properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE kg_edges (
            source TEXT NOT NULL,
            target TEXT NOT NULL,
            relation TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            properties TEXT DEFAULT '{}',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(source, target, relation)
        );
        CREATE TABLE track_comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            version_id INTEGER,
            timestamp_s REAL,
            author TEXT NOT NULL,
            body TEXT NOT NULL,
            resolved INTEGER DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE track_stems (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            track_id INTEGER NOT NULL,
            model TEXT NOT NULL DEFAULT 'htdemucs',
            vocals_path TEXT,
            drums_path TEXT,
            bass_path TEXT,
            other_path TEXT,
            separated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(track_id, model)
        );
        CREATE TABLE pending_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_agent TEXT NOT NULL,
            draft TEXT NOT NULL,
            context TEXT,
            track_id INTEGER,
            priority TEXT DEFAULT 'normal',
            submitted_at TEXT DEFAULT (datetime('now')),
            status TEXT DEFAULT 'pending',
            conductor_reasoning TEXT,
            refined_draft TEXT,
            sent_at TEXT
        );
        """
    )
    conn.commit()


def _fake_analysis(track_id, model):
    return AudioAnalysis(
        track_id=track_id,
        model_used=model,
        bpm=92,
        musical_key="A minor",
        genre_tags=["alt-pop"],
        mood_tags=["late-night"],
        mix_observations=[MixObservation(timestamp="0:45", observation="Vocal is forward.")],
        notable_moments=[
            NotableMoment(
                timestamp="1:10",
                description="Hook opens up.",
                quality_judgment="strength",
            )
        ],
    )


async def _fake_analyze_segments(file_path, db_path, track_id, **kwargs):
    return []


def _fake_extract_audio_features(file_path, db_path, track_id):
    return {"track_id": track_id, "bpm": 90.0, "musical_key": "A minor"}


def _fake_extract_embedding(file_path, db_path, track_id):
    import numpy as np

    return np.zeros(2048, dtype=np.float32)


def _fake_agent_message(*, agent: str, prompt_context: str) -> str:
    return f"{agent} real message"


def _fake_agent_message_bundle(
    *,
    agents: list[str],
    prompt_context: str,
    task_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    return {
        agent: _fake_agent_message(agent=agent, prompt_context=prompt_context) for agent in agents
    }


def _fake_roundtable_round(
    *,
    db_path,
    track_id,
    project_id,
    prompt_context,
    candidate_agents,
    default_intent="roundtable_reply",
    intents=None,
    channel="desktop",
    **_kwargs,
):
    results = []
    with sqlite3.connect(db_path) as conn:
        for agent in candidate_agents:
            message = _fake_agent_message(agent=agent, prompt_context=prompt_context)
            cursor = conn.execute(
                """INSERT INTO feedback
                   (track_id, project_id, agent, message, channel, direction, intent)
                   VALUES (?, ?, ?, ?, ?, 'outbound', ?)""",
                (
                    track_id,
                    project_id,
                    agent,
                    message,
                    channel,
                    (intents or {}).get(agent, default_intent),
                ),
            )
            results.append(
                {"agent": agent, "message": message, "feedback_id": cursor.lastrowid}
            )
    return results


def _mock_agent_generation(monkeypatch):
    monkeypatch.setattr(
        dispatcher, "_generate_agent_message_bundle", _fake_agent_message_bundle
    )
    monkeypatch.setattr(dispatcher, "_run_roundtable_round", _fake_roundtable_round)


def test_every_roundtable_agent_has_visual_conception_guidance():
    assert dispatcher.ROUNDTABLE_AGENTS == [
        "kallman",
        "a_and_r",
        "janick",
        "rhone",
        "rubin",
        "creative_director",
        "manager",
    ]
    assert set(dispatcher.VISUAL_CONCEPTION_TASKS) == set(dispatcher.ROUNDTABLE_AGENTS)
    maren_guidance = dispatcher.VISUAL_CONCEPTION_TASKS["creative_director"]
    for detail in ("scene", "palette", "light", "texture", "composition", "camera"):
        assert detail in maren_guidance


def test_direct_creative_director_addressing_and_pronoun_followup():
    assert dispatcher._addressed_response_agents(
        "I want to hear from the Creative Director only", []
    ) == ["creative_director"]


def test_required_roundtable_agent_speaks_when_selector_stops(monkeypatch):
    async def stop_selector(**_kwargs):
        return "stop"

    async def fake_voice(**_kwargs):
        return "Maren's visual conception. Visually: rain on violet glass."

    persisted = []
    monkeypatch.setattr(dispatcher, "_select_next_speaker_async", stop_selector)
    monkeypatch.setattr(dispatcher, "_generate_agent_message_async", fake_voice)

    result = asyncio.run(
        dispatcher._run_roundtable_round_async(
            prompt_context="{}",
            trigger_text="What does she have to say?",
            stage_label="artist_question",
            candidate_agents=["creative_director"],
            model="fixture-model",
            api_key="fixture-key",
            max_turns=1,
            allow_manager_summary=False,
            require_all_agents=True,
            persist=lambda agent, message: persisted.append((agent, message)) or 99,
        )
    )

    assert [row["agent"] for row in result] == ["creative_director"]
    assert persisted[0][0] == "creative_director"
    assert dispatcher._addressed_response_agents(
        "Okay, what does she have to say?", ["creative_director"]
    ) == ["creative_director"]


def test_new_track_runs_review_pipeline(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    conn.close()

    def fake_analyze(file_path, db_path_arg, *, track_id, model):
        conn2 = sqlite3.connect(db_path_arg)
        mix_observations = '[{"timestamp":"0:45","observation":"Vocal is forward."}]'
        notable_moments = (
            '[{"timestamp":"1:10","description":"Hook opens up.","quality_judgment":"strength"}]'
        )
        conn2.execute(
            """INSERT INTO audio_analyses
               (track_id, model_used, bpm, musical_key, genre_tags, mood_tags,
                mix_observations, notable_moments)
               VALUES (?, ?, 92, 'A minor', '["alt-pop"]', '["late-night"]', ?, ?)""",
            (track_id, model, mix_observations, notable_moments),
        )
        conn2.commit()
        conn2.close()
        return _fake_analysis(track_id, model)

    monkeypatch.setattr(dispatcher, "analyze", fake_analyze)
    monkeypatch.setattr(dispatcher, "analyze_segments", _fake_analyze_segments)
    monkeypatch.setattr(dispatcher, "extract_audio_features", _fake_extract_audio_features)
    monkeypatch.setattr(dispatcher, "extract_embedding", _fake_extract_embedding)
    _mock_agent_generation(monkeypatch)
    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "new_track_detected",
        {"track_id": 1, "file_path": "/tmp/song.wav", "version": 1},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT state FROM tracks WHERE id = 1").fetchone()
    states = conn.execute("SELECT from_state, to_state FROM release_states ORDER BY id").fetchall()
    intents = conn.execute("SELECT intent FROM feedback ORDER BY id").fetchall()

    assert result["handled"] is True
    assert track["state"] == "FEEDBACK_GIVEN"
    assert [(r["from_state"], r["to_state"]) for r in states] == [
        ("DRAFT", "IN_REVIEW"),
        ("IN_REVIEW", "FEEDBACK_GIVEN"),
    ]
    assert [r["intent"] for r in intents] == [
        "intake_complete",
        "new_track_ack",
        # Act 1 - music execs' first reads to the artist
        "early_conviction_feedback",
        "analysis_feedback",
        "vision_assessment",
        "cultural_authenticity_read",
        "essential_question_review",
        # Act 2 - room session (agents talking it out between themselves)
        "room_discussion",
        "room_discussion",
        "room_discussion",
        "room_discussion",
        "room_discussion",
        "room_discussion",
        "room_discussion",
        # Act 3 - Dez closes and opens the floor
        "review_round_summary",
    ]


def test_new_track_runs_optional_post_drop_side_effects(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.close()

    def fake_analyze(file_path, db_path_arg, *, track_id, model):
        conn2 = sqlite3.connect(db_path_arg)
        conn2.execute(
            """INSERT INTO audio_analyses
               (track_id, model_used, bpm, musical_key, genre_tags, mood_tags)
               VALUES (?, ?, 92, 'A minor', '["alt-pop"]', '["late-night"]')""",
            (track_id, model),
        )
        conn2.commit()
        conn2.close()
        return _fake_analysis(track_id, model)

    async def fake_separate_stems(file_path, stems_base, *, force=False):
        return {
            "vocals": str(stems_base / "htdemucs" / "song" / "vocals.wav"),
            "drums": str(stems_base / "htdemucs" / "song" / "drums.wav"),
            "bass": str(stems_base / "htdemucs" / "song" / "bass.wav"),
            "other": str(stems_base / "htdemucs" / "song" / "other.wav"),
        }

    monkeypatch.setattr(dispatcher, "analyze", fake_analyze)
    monkeypatch.setattr(dispatcher, "separate_stems", fake_separate_stems)
    monkeypatch.setattr(dispatcher, "analyze_segments", _fake_analyze_segments)
    monkeypatch.setattr(dispatcher, "extract_audio_features", _fake_extract_audio_features)
    monkeypatch.setattr(dispatcher, "extract_embedding", _fake_extract_embedding)
    _mock_agent_generation(monkeypatch)

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "new_track_detected",
        {"track_id": 1, "file_path": "/tmp/song.wav", "version": 1},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT state, project_id FROM tracks WHERE id = 1").fetchone()
    project = conn.execute("SELECT title, type, target_track_count FROM projects").fetchone()
    nodes = conn.execute("SELECT id, type, label FROM kg_nodes ORDER BY id").fetchall()
    edges = conn.execute(
        "SELECT source, target, relation FROM kg_edges ORDER BY source, target"
    ).fetchall()
    comments = conn.execute("SELECT author, body FROM track_comments ORDER BY id").fetchall()
    stems = conn.execute(
        "SELECT vocals_path, drums_path, bass_path, other_path FROM track_stems"
    ).fetchone()
    feedback = conn.execute("SELECT agent, intent, project_id FROM feedback ORDER BY id").fetchall()

    assert result["handled"] is True
    assert result["state"] == "FEEDBACK_GIVEN"
    assert result["post_analysis_actions"] == [
        "separate_stems",
        "extract_audio_features",
        "extract_embedding",
        "analyze_segments",
        # Act 1 - music execs
        "kallman_review",
        "a_and_r_review",
        "janick_review",
        "rhone_review",
        "rubin_review",
        # Act 2 - room session (execs + creative + release desk)
        "kallman_review",
        "a_and_r_review",
        "janick_review",
        "rhone_review",
        "rubin_review",
        "creative_director_review",
        "bandcamp_review",
        # Act 3 - Dez's close
        "manager_review",
    ]
    assert track["state"] == "FEEDBACK_GIVEN"
    assert track["project_id"] == 1
    assert dict(project) == {"title": "song", "type": "single", "target_track_count": 1}
    assert (stems["vocals_path"], stems["drums_path"], stems["bass_path"], stems["other_path"]) == (
        str(tmp_path / "stems" / "htdemucs" / "song" / "vocals.wav"),
        str(tmp_path / "stems" / "htdemucs" / "song" / "drums.wav"),
        str(tmp_path / "stems" / "htdemucs" / "song" / "bass.wav"),
        str(tmp_path / "stems" / "htdemucs" / "song" / "other.wav"),
    )
    assert ("track:1", "track", "song") in [(r["id"], r["type"], r["label"]) for r in nodes]
    assert ("project:1", "track:1", "contains_track") in [
        (r["source"], r["target"], r["relation"]) for r in edges
    ]
    assert ("track:1", "genre:alt-pop", "has_genre") in [
        (r["source"], r["target"], r["relation"]) for r in edges
    ]
    assert [r["author"] for r in comments] == ["intake", "system"]
    assert ("kallman", "early_conviction_feedback", 1) in [
        (r["agent"], r["intent"], r["project_id"]) for r in feedback
    ]


def test_post_analysis_stem_failure_returns_explicit_error(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.close()

    def fake_analyze(file_path, db_path_arg, *, track_id, model):
        conn2 = sqlite3.connect(db_path_arg)
        conn2.execute(
            "INSERT INTO audio_analyses (track_id, model_used) VALUES (?, ?)",
            (track_id, model),
        )
        conn2.commit()
        conn2.close()
        return _fake_analysis(track_id, model)

    async def fake_separate_stems(file_path, stems_base, *, force=False):
        raise PipelineError("demucs unavailable")

    monkeypatch.setattr(dispatcher, "analyze", fake_analyze)
    monkeypatch.setattr(dispatcher, "separate_stems", fake_separate_stems)

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "new_track_detected",
        {"track_id": 1, "file_path": "/tmp/song.wav", "version": 1},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT state FROM tracks WHERE id = 1").fetchone()
    error = conn.execute("SELECT message FROM feedback WHERE intent = 'pipeline_error'").fetchone()

    assert result["handled"] is True
    assert result["state"] == "IN_REVIEW"
    assert "demucs unavailable" in result["error"]
    assert track["state"] == "IN_REVIEW"
    assert "post-analysis actions" in error["message"]


def test_track_approved_queues_exec_followups(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.execute("UPDATE tracks SET state = 'FEEDBACK_GIVEN' WHERE id = 1")
    conn.commit()
    conn.close()

    _mock_agent_generation(monkeypatch)
    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "track_approved",
        {"track_id": 1, "agent": "a_and_r"},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT state FROM tracks WHERE id = 1").fetchone()
    pending = conn.execute(
        "SELECT from_agent, context FROM pending_messages ORDER BY id"
    ).fetchall()

    assert result["handled"] is True
    assert track["state"] == "ART_NEEDED"
    assert [(row["from_agent"], row["context"]) for row in pending] == [
        ("kallman", "track_approved"),
        ("janick", "track_approved"),
        ("rhone", "track_approved"),
        ("rubin", "track_approved"),
    ]


def test_artist_message_approve_runs_approval_flow(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.execute("UPDATE tracks SET state = 'FEEDBACK_GIVEN' WHERE id = 1")
    conn.execute(
        """INSERT INTO feedback (track_id, project_id, agent, message, channel, direction, intent)
           VALUES (1, NULL, 'a_and_r', 'ship it', 'desktop', 'inbound', 'question')"""
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        TrackPipelineDispatcher,
        "_classify_artist_intent",
        lambda self, message, context: (
            dispatcher.IntentType.APPROVE,
            0.99,
            {},
            "explicit approval",
        ),
    )

    _mock_agent_generation(monkeypatch)

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "artist_message_inbound",
        {"track_id": 1, "message": "ship it", "agent": "a_and_r"},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    track = conn.execute("SELECT state FROM tracks WHERE id = 1").fetchone()
    inbound = conn.execute(
        """SELECT intent FROM feedback
           WHERE track_id = 1 AND direction = 'inbound' AND message = 'ship it'"""
    ).fetchone()
    assert result["handled"] is True
    assert result["intent"] == "approve"
    assert result["feedback_id"] is not None
    assert track["state"] == "ART_NEEDED"
    assert inbound["intent"] == "approval"


def test_artist_question_gets_fresh_roundtable_responses(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.execute("UPDATE tracks SET state = 'FEEDBACK_GIVEN' WHERE id = 1")
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        TrackPipelineDispatcher,
        "_classify_artist_intent",
        lambda self, message, context: (
            dispatcher.IntentType.QUESTION,
            0.98,
            {},
            "artist asked the room",
        ),
    )
    _mock_agent_generation(monkeypatch)

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "artist_message_inbound",
        {"track_id": 1, "message": "What do you all think?", "agent": "a_and_r"},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    messages = conn.execute(
        """SELECT agent, direction, intent, message FROM feedback
           WHERE track_id = 1 ORDER BY id"""
    ).fetchall()
    pending = conn.execute(
        "SELECT COUNT(*) FROM pending_messages WHERE context = 'artist_question'"
    ).fetchone()[0]

    assert result["handled"] is True
    assert result["intent"] == "question"
    assert len(result["response_ids"]) == 7
    assert pending == 0
    assert [(row["agent"], row["direction"], row["intent"]) for row in messages] == [
        ("a_and_r", "inbound", "question"),
        ("kallman", "outbound", "artist_question_response"),
        ("a_and_r", "outbound", "artist_question_response"),
        ("janick", "outbound", "artist_question_response"),
        ("rhone", "outbound", "artist_question_response"),
        ("rubin", "outbound", "artist_question_response"),
        ("creative_director", "outbound", "artist_question_response"),
        ("manager", "outbound", "artist_question_response"),
    ]


def test_artist_pronoun_followup_routes_only_to_creative_director(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.execute("UPDATE tracks SET state = 'FEEDBACK_GIVEN' WHERE id = 1")
    conn.execute(
        """INSERT INTO feedback
           (track_id, agent, message, channel, direction, intent)
           VALUES (1, 'a_and_r', ?, 'desktop', 'inbound', 'question')""",
        ("I want to hear from the Creative Director only",),
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr(
        TrackPipelineDispatcher,
        "_classify_artist_intent",
        lambda self, message, context: (
            dispatcher.IntentType.QUESTION,
            0.98,
            {},
            "artist asked Maren",
        ),
    )
    _mock_agent_generation(monkeypatch)

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "artist_message_inbound",
        {"track_id": 1, "message": "Okay, what does she have to say?", "agent": "a_and_r"},
    )

    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        """SELECT agent FROM feedback
           WHERE direction = 'outbound' AND intent = 'artist_question_response'
           ORDER BY id"""
    ).fetchall()

    assert len(result["response_ids"]) == 1
    assert [row[0] for row in rows] == ["creative_director"]


def test_revision_uploaded_reuses_live_review_pipeline(tmp_path, monkeypatch):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    conn.execute("UPDATE tracks SET state = 'FEEDBACK_GIVEN' WHERE id = 1")
    conn.execute(
        """INSERT INTO tracks
           (title, file_path, file_hash, state, format, version, parent_track_id)
           VALUES ('song', '/tmp/song v2.wav', 'def', 'DRAFT', 'wav', 2, 1)"""
    )
    conn.commit()
    conn.close()

    def fake_analyze(file_path, db_path_arg, *, track_id, model):
        conn2 = sqlite3.connect(db_path_arg)
        conn2.execute(
            """INSERT INTO audio_analyses
               (track_id, model_used, bpm, musical_key, genre_tags, mood_tags)
               VALUES (?, ?, 92, 'A minor', '["alt-pop"]', '["late-night"]')""",
            (track_id, model),
        )
        conn2.commit()
        conn2.close()
        return _fake_analysis(track_id, model)

    monkeypatch.setattr(dispatcher, "analyze", fake_analyze)
    monkeypatch.setattr(dispatcher, "analyze_segments", _fake_analyze_segments)
    monkeypatch.setattr(dispatcher, "extract_audio_features", _fake_extract_audio_features)
    monkeypatch.setattr(dispatcher, "extract_embedding", _fake_extract_embedding)
    _mock_agent_generation(monkeypatch)

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "revision_uploaded",
        {"track_id": 2, "file_path": "/tmp/song v2.wav", "message": "new version ready"},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    revision = conn.execute("SELECT state, parent_track_id FROM tracks WHERE id = 2").fetchone()
    intents = conn.execute("SELECT intent FROM feedback WHERE track_id = 2 ORDER BY id").fetchall()

    assert result["handled"] is True
    assert result["event"] == "revision_uploaded"
    assert result["state"] == "FEEDBACK_GIVEN"
    assert result["revision_of"] == 1
    assert revision["state"] == "FEEDBACK_GIVEN"
    assert revision["parent_track_id"] == 1
    assert [r["intent"] for r in intents][:4] == [
        "revision",
        "revision_ack",
        "intake_complete",
        "new_track_ack",
    ]


def test_conductor_summary_delivery_persists_outbound_feedback(tmp_path):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.execute(
        """INSERT INTO pending_messages
           (from_agent, draft, context, track_id, status, refined_draft)
           VALUES ('manager', 'draft summary', 'weekly_summary:2026-05-19', 1, 'approved',
                   'tight weekly summary')"""
    )
    conn.commit()
    conn.close()

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "conductor_summary_delivered",
        {"message_id": 1, "channel": "sms"},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    feedback = conn.execute(
        """SELECT agent, message, intent, direction, channel
           FROM feedback
           WHERE track_id = 1 AND direction = 'outbound'
           ORDER BY id DESC LIMIT 1"""
    ).fetchone()
    pending = conn.execute("SELECT status, sent_at FROM pending_messages WHERE id = 1").fetchone()

    assert result["handled"] is True
    assert result["delivered_message"] == "tight weekly summary"
    assert dict(feedback) == {
        "agent": "manager",
        "message": "tight weekly summary",
        "intent": "weekly_summary:2026_05_19",
        "direction": "outbound",
        "channel": "sms",
    }
    assert pending["status"] == "approved"
    assert pending["sent_at"] is not None


def test_timeout_feedback_stale_queues_manager_nag(tmp_path):
    db_path = tmp_path / "hermes.db"
    conn = _db(db_path)
    _add_optional_post_drop_tables(conn)
    conn.execute("UPDATE tracks SET state = 'FEEDBACK_GIVEN' WHERE id = 1")
    conn.commit()
    conn.close()

    result = TrackPipelineDispatcher(str(db_path)).process_event(
        "timeout_feedback_stale",
        {"track_id": 1, "entered_at": "2026-05-01T00:00:00+00:00"},
    )

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    pending = conn.execute("SELECT from_agent, draft, context FROM pending_messages").fetchone()

    assert result["handled"] is True
    assert pending["from_agent"] == "manager"
    assert "approve" in pending["draft"].lower()
    assert pending["context"].startswith("timeout_feedback_stale:")


def test_agent_voice_reserves_budget_for_structured_answer(monkeypatch):
    captured = {}

    class Response:
        is_error = False
        status_code = 200
        text = ""

        @staticmethod
        def json():
            content = (
                '{"message":"the song is the point.",'
                '"visual_conception":"one candle in a bare room."}'
            )
            return {"choices": [{"message": {"content": content}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            captured.update(kwargs["json"])
            return Response()

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(
        dispatcher._generate_agent_message_async(
            agent="rubin",
            prompt_context="{}",
            model="google/gemini-3.5-flash",
            api_key="test-key",
        )
    )

    assert result == "the song is the point. Visually: one candle in a bare room."
    assert captured["reasoning"] == {"enabled": False, "exclude": True}
    assert captured["max_tokens"] == 384
    assert captured["response_format"] == {"type": "json_object"}


def test_agent_voice_retries_null_provider_content(monkeypatch):
    calls = 0

    class Response:
        is_error = False
        status_code = 200
        text = ""

        def json(self):
            content = (
                None
                if calls == 1
                else (
                    '{"message":"the chorus opens the world.",'
                    '"visual_conception":"violet light crosses wet pavement."}'
                )
            )
            return {"choices": [{"message": {"content": content}}]}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            nonlocal calls
            calls += 1
            return Response()

    monkeypatch.setattr(dispatcher.httpx, "AsyncClient", lambda **_kwargs: Client())

    result = asyncio.run(
        dispatcher._generate_agent_message_async(
            agent="creative_director",
            prompt_context="{}",
            model="fixture-model",
            api_key="test-key",
        )
    )

    assert calls == 2
    assert result == (
        "the chorus opens the world. Visually: violet light crosses wet pavement."
    )
