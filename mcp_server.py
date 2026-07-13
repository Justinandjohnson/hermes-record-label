"""MCP stdio server exposing AI Record Label tools to Hermes Agent."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Resolve DB path: env var > platform default > fallback to script directory
import os
import platform

def _resolve_data_dir() -> Path:
    explicit = os.environ.get("AI_RECORD_LABEL_DATA")
    if explicit:
        return Path(explicit)
    system = platform.system()
    if system == "Darwin":
        return Path.home() / "Library" / "Application Support" / "ai-record-label"
    elif system == "Windows":
        appdata = os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))
        return Path(appdata) / "ai-record-label"
    else:
        return Path.home() / ".local" / "share" / "ai-record-label"

_DATA_DIR = _resolve_data_dir()
_DATA_DIR.mkdir(parents=True, exist_ok=True)

DB_PATH = os.environ.get("DB_PATH", str(_DATA_DIR / "hermes.db"))

# Simple JSON-RPC over stdio MCP implementation
# Hermes launches this as a subprocess and communicates via stdin/stdout

async def handle_request(request: dict) -> dict | None:
    method = request.get("method", "")
    params = request.get("params", {})
    req_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "ai-record-label", "version": "0.1.0"},
            },
        }

    if method == "notifications/initialized":
        return None  # no response needed

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "analyze_track",
                        "description": "Analyze an audio file using Gemini 2.5 Pro. Returns structured analysis with BPM, key, energy curve, instruments, genre/mood, mix observations, and notable moments.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["file_path", "track_id"],
                            "properties": {
                                "file_path": {"type": "string", "description": "Path to audio file"},
                                "track_id": {"type": "integer", "description": "Track ID in database"},
                            },
                        },
                    },
                    {
                        "name": "analyze_artwork",
                        "description": "Analyze album cover artwork using Gemini Vision. Returns structured review: composition, color palette, mood, typography, technical specs (resolution, compression), Bandcamp readiness, specific issues, and strengths.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["file_path"],
                            "properties": {
                                "file_path": {"type": "string", "description": "Path to image file (JPG, PNG, WEBP, TIFF, BMP)"},
                                "track_id": {"type": "integer", "description": "Optional track ID for context"},
                            },
                        },
                    },
                    {
                        "name": "get_artist_patterns",
                        "description": "Get all audio memory entries sorted by confidence. Shows recurring patterns in the artist's music.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_track_context",
                        "description": "Get context for how a specific track relates to the artist's catalog.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {"track_id": {"type": "integer"}},
                        },
                    },
                    {
                        "name": "get_evolution_arc",
                        "description": "Get how the artist's sound has evolved over time.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_tracks",
                        "description": "Get all tracks from the database with their current state.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_track_feedback",
                        "description": "Get all feedback messages for a specific track.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {"track_id": {"type": "integer"}},
                        },
                    },
                    {
                        "name": "transition_state",
                        "description": "Transition a track to a new release state.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id", "to_state", "changed_by"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "to_state": {"type": "string", "description": "Target state (DRAFT, IN_REVIEW, FEEDBACK_GIVEN, APPROVED, ART_NEEDED, ART_SUBMITTED, ART_APPROVED, RELEASE_READY, PREFLIGHT, UPLOADING, RELEASED)"},
                                "changed_by": {"type": "string", "description": "Agent or actor making the change"},
                                "reason": {"type": "string", "description": "Reason for transition"},
                            },
                        },
                    },
                    {
                        "name": "log_feedback",
                        "description": "Log a feedback message from an agent to the database.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["agent", "message", "channel", "direction"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "project_id": {"type": "integer"},
                                "agent": {"type": "string"},
                                "message": {"type": "string"},
                                "channel": {"type": "string", "enum": ["sms", "desktop", "voice", "internal", "studio_queue"]},
                                "direction": {"type": "string", "enum": ["inbound", "outbound"]},
                                "intent": {"type": "string"},
                            },
                        },
                    },
                    {
                        "name": "get_stats",
                        "description": "Get artist stats: streak, reputation, tracks in progress/released.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_projects",
                        "description": "Get all projects/deals with their status.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_artist_profile",
                        "description": "Get the artist's profile information.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "get_sessions",
                        "description": "Get Ableton work sessions — when the artist worked, how long, how many saves and exports, BPM, and what changed.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "limit": {"type": "integer", "description": "Max sessions to return (default 20)"},
                                "project_name": {"type": "string", "description": "Filter to a specific project"},
                            },
                        },
                    },
                    {
                        "name": "get_export_events",
                        "description": "Get recent audio export events — what files were bounced, whether they changed from the previous version, similarity score.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "limit": {"type": "integer", "description": "Max events to return (default 20)"},
                                "project_name": {"type": "string", "description": "Filter to a specific project"},
                            },
                        },
                    },
                    {
                        "name": "get_session_summary",
                        "description": "Get a natural-language summary of the artist's recent work — total hours, active projects, productivity streaks, and what's changed.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "scan_ableton_project",
                        "description": "Backfill session history by scanning an Ableton project folder's Backup/ directory.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["project_folder"],
                            "properties": {
                                "project_folder": {"type": "string", "description": "Path to the Ableton project folder (the one containing the .als file and Backup/ subfolder)"},
                            },
                        },
                    },
                    # ── Conductor message queue ──────────────────────────────
                    {
                        "name": "submit_message",
                        "description": "Submit a draft message to the studio conductor for review before it reaches the artist. Use this instead of texting the artist directly. The conductor will reason through it, refine if needed, and decide whether/how to deliver it.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["from_agent", "draft", "context"],
                            "properties": {
                                "from_agent": {"type": "string", "description": "Your agent name: 'ravi', 'dez', 'maren', or 'sable'"},
                                "draft": {"type": "string", "description": "The message you want to send to the artist"},
                                "context": {"type": "string", "description": "Why you want to send this — what triggered it, what you're trying to accomplish"},
                                "track_id": {"type": "integer", "description": "Track ID if this message is about a specific track"},
                                "priority": {"type": "string", "enum": ["urgent", "normal", "low"], "description": "Message priority (default: normal)"},
                            },
                        },
                    },
                    {
                        "name": "get_pending_messages",
                        "description": "Get all pending messages in the conductor queue. Used by the studio conductor to review what agents want to say.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "status": {"type": "string", "enum": ["pending", "approved", "rejected", "needs_context", "all"], "description": "Filter by status (default: pending)"},
                            },
                        },
                    },
                    {
                        "name": "approve_message",
                        "description": "Approve a pending message (optionally with refinements) so it gets delivered to the artist. Used by the studio conductor.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["message_id"],
                            "properties": {
                                "message_id": {"type": "integer", "description": "ID from pending_messages"},
                                "refined_draft": {"type": "string", "description": "Improved version of the message (leave blank to use original draft)"},
                                "conductor_reasoning": {"type": "string", "description": "Internal reasoning log — why you approved this, what you changed and why"},
                            },
                        },
                    },
                    {
                        "name": "reject_message",
                        "description": "Reject a pending message — the agent's draft is not worth sending right now. Used by the studio conductor.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["message_id", "conductor_reasoning"],
                            "properties": {
                                "message_id": {"type": "integer"},
                                "conductor_reasoning": {"type": "string", "description": "Why this message shouldn't be sent"},
                            },
                        },
                    },
                    {
                        "name": "add_memory",
                        "description": "Store an observation about the artist in long-term memory. Use this after any meaningful interaction to record what you've learned. Observations accumulate into patterns over time.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["agent", "tag", "content"],
                            "properties": {
                                "agent": {"type": "string", "description": "Your agent name: ravi | dez | maren | sable | intake"},
                                "tag": {"type": "string", "description": "Category: arrangement_habit | deadline_behavior | visual_preference | file_hygiene | etc."},
                                "content": {"type": "string", "description": "The verbatim observation, in your voice. Be specific — vague observations don't help future you."},
                                "confidence": {"type": "number", "description": "0.0–1.0. New observation: 0.5. Seen before: increment by 0.1. Clearly established: 0.8+", "default": 0.5},
                                "track_id": {"type": "integer", "description": "If this observation is about a specific track"},
                            },
                        },
                    },
                    {
                        "name": "search_memory",
                        "description": "Search long-term memory by keyword. Returns relevant observations across all agents or filtered by agent. Use this to recall what you know before giving feedback.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["query"],
                            "properties": {
                                "query": {"type": "string", "description": "Natural language query — what do you want to remember?"},
                                "agent": {"type": "string", "description": "Filter to a specific agent's memories. Omit to search all agents."},
                                "tag": {"type": "string", "description": "Filter to a specific tag/category"},
                                "limit": {"type": "integer", "description": "Max results to return", "default": 10},
                            },
                        },
                    },
                    {
                        "name": "get_agent_memory",
                        "description": "Get all stored memories for an agent, optionally filtered by tag. Returns recent observations and established patterns. Use at session start to reload context.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["agent"],
                            "properties": {
                                "agent": {"type": "string", "description": "Agent name: ravi | dez | maren | sable | intake"},
                                "tag": {"type": "string", "description": "Filter to specific tag. Omit for all tags."},
                                "min_confidence": {"type": "number", "description": "Only return observations at or above this confidence. Default 0.0 (all)", "default": 0.0},
                                "limit": {"type": "integer", "description": "Max observations to return", "default": 20},
                            },
                        },
                    },
                    {
                        "name": "confirm_memory",
                        "description": "Confirm an existing observation — you've seen this pattern again. Increments confidence by 0.1, up to 1.0. Use when you notice something you've already recorded.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["memory_id"],
                            "properties": {
                                "memory_id": {"type": "integer", "description": "The ID of the memory to confirm (from get_agent_memory or search_memory)"},
                            },
                        },
                    },
                    {
                        "name": "get_established_patterns",
                        "description": "Get established patterns — observations confirmed 2+ times with confidence >= 0.6. These are things you know reliably about this artist. Read this before any major feedback session.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "agent": {"type": "string", "description": "Filter to a specific agent. Omit for all agents."},
                            },
                        },
                    },
                    {
                        "name": "update_artist_profile",
                        "description": "Update a key in the shared artist profile snapshot. Use for high-level facts all agents should know (e.g. releases_count, creation_streak).",
                        "inputSchema": {
                            "type": "object",
                            "required": ["key", "value", "updated_by"],
                            "properties": {
                                "key": {"type": "string", "description": "Profile key to update"},
                                "value": {"type": "string", "description": "New value"},
                                "updated_by": {"type": "string", "description": "Your agent name"},
                            },
                        },
                    },
                    # ── Vault tools ──
                    {
                        "name": "vault_track",
                        "description": "Move a track to the vault. Not rejected — archived for future use. Prince kept thousands.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id", "reason"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "reason": {"type": "string", "description": "Why: 'not ready', 'doesn't fit current project', 'interesting idea needs development'"},
                            },
                        },
                    },
                    {
                        "name": "vault_search",
                        "description": "Search the vault for tracks matching a vibe. Uses audio analysis metadata.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "mood": {"type": "string", "description": "Mood to search for (e.g. 'melancholic', 'energetic')"},
                                "key": {"type": "string", "description": "Musical key (e.g. 'Cm', 'F#')"},
                                "genre": {"type": "string", "description": "Genre tag to filter by"},
                                "limit": {"type": "integer", "default": 10},
                            },
                        },
                    },
                    {
                        "name": "resurface_track",
                        "description": "Pull a track back from the vault into active pipeline (DRAFT state).",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id", "reason"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "reason": {"type": "string"},
                            },
                        },
                    },
                    # ── QC Panel tools ──
                    {
                        "name": "manage_panel",
                        "description": "Add, remove, or list members of the human listening panel.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["action"],
                            "properties": {
                                "action": {"type": "string", "enum": ["add", "remove", "list"], "description": "What to do"},
                                "name": {"type": "string"},
                                "phone": {"type": "string"},
                                "imessage_id": {"type": "string"},
                                "relationship": {"type": "string", "description": "friend, musician, producer, casual_listener"},
                                "panelist_id": {"type": "integer", "description": "For remove action"},
                            },
                        },
                    },
                    {
                        "name": "send_to_panel",
                        "description": "Send a track to all active panel members for feedback. Creates a panel session.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "message": {"type": "string", "description": "Personal note to send with the track", "default": "Hey, I'd love your honest take on this track. What do you think?"},
                            },
                        },
                    },
                    {
                        "name": "log_panel_response",
                        "description": "Record a panelist's response to a track they were sent.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["session_id", "panelist_id", "raw_response"],
                            "properties": {
                                "session_id": {"type": "integer"},
                                "panelist_id": {"type": "integer"},
                                "raw_response": {"type": "string"},
                                "sentiment": {"type": "string", "enum": ["positive", "mixed", "negative"]},
                                "would_buy": {"type": "boolean"},
                                "key_quote": {"type": "string"},
                            },
                        },
                    },
                    {
                        "name": "get_panel_results",
                        "description": "Get all responses for a panel session with summary.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["session_id"],
                            "properties": {
                                "session_id": {"type": "integer"},
                            },
                        },
                    },
                    # ── Release Cycle tools ──
                    {
                        "name": "plan_release",
                        "description": "Auto-generate a backward-planned release timeline with milestones at T-4, T-3, T-2, T-1, T-0, and T+2 weeks.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id", "release_date"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "release_date": {"type": "string", "description": "Target release date (YYYY-MM-DD)"},
                            },
                        },
                    },
                    {
                        "name": "get_release_timeline",
                        "description": "Get all milestones for a track's release cycle with dates and status.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {"track_id": {"type": "integer"}},
                        },
                    },
                    # ── Session / Work Pattern tools ──
                    {
                        "name": "log_session",
                        "description": "Record a studio session detected by the file watcher or reported by the artist.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["started_at"],
                            "properties": {
                                "started_at": {"type": "string"},
                                "ended_at": {"type": "string"},
                                "export_count": {"type": "integer", "default": 1},
                                "project_id": {"type": "integer"},
                            },
                        },
                    },
                    {
                        "name": "add_session_note",
                        "description": "Store the artist's post-session reflection.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["session_id", "note"],
                            "properties": {
                                "session_id": {"type": "integer"},
                                "note": {"type": "string"},
                                "mood": {"type": "string", "description": "energized, stuck, exploratory, focused, frustrated"},
                            },
                        },
                    },
                    {
                        "name": "get_work_patterns",
                        "description": "Return creation patterns: session frequency, most productive times, streak status, export counts.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "days": {"type": "integer", "description": "Look back N days", "default": 30},
                            },
                        },
                    },
                    # ── Royalty Registration tools ──
                    {
                        "name": "manage_royalty_registration",
                        "description": "Create, update, or check status of royalty org registrations (ASCAP/BMI, MLC, SoundExchange).",
                        "inputSchema": {
                            "type": "object",
                            "required": ["action"],
                            "properties": {
                                "action": {"type": "string", "enum": ["create", "update_status", "list"], "description": "What to do"},
                                "org_name": {"type": "string", "description": "ASCAP, BMI, MLC, SoundExchange"},
                                "org_type": {"type": "string", "description": "PRO, mechanical, digital_performance"},
                                "status": {"type": "string"},
                                "account_id": {"type": "string"},
                                "notes": {"type": "string"},
                                "registration_id": {"type": "integer", "description": "For update_status"},
                            },
                        },
                    },
                    {
                        "name": "register_work",
                        "description": "Register a specific released track with a royalty organization.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id", "org_name"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "org_name": {"type": "string"},
                                "isrc": {"type": "string"},
                                "status": {"type": "string", "default": "pending"},
                            },
                        },
                    },
                    # ── Album-as-Statement tools ──
                    {
                        "name": "set_project_seed",
                        "description": "Set the thematic seed for an EP or album project. The seed is the statement the project is making.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["project_id", "seed_text"],
                            "properties": {
                                "project_id": {"type": "integer"},
                                "seed_text": {"type": "string", "description": "A few sentences: who you are right now and what this project is saying"},
                            },
                        },
                    },
                    {
                        "name": "get_project_coherence",
                        "description": "Overview of how well tracks in a project serve its thematic seed.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["project_id"],
                            "properties": {"project_id": {"type": "integer"}},
                        },
                    },
                    {
                        "name": "import_catalog_album",
                        "description": "Import a single Bandcamp album into the database as a RELEASED project, including its full tracklist and metadata (tags, description, cover art, release date).",
                        "inputSchema": {
                            "type": "object",
                            "required": ["album_url"],
                            "properties": {
                                "album_url": {
                                    "type": "string",
                                    "description": "Full Bandcamp album URL, e.g. https://artist.bandcamp.com/album/my-album",
                                },
                                "overwrite": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "Overwrite an existing import for this album.",
                                },
                            },
                        },
                    },
                    {
                        "name": "get_catalog",
                        "description": "Return all RELEASED projects (the artist's existing catalog) with their tracks. Optionally include audio analysis data when present.",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "with_analysis": {
                                    "type": "boolean",
                                    "default": False,
                                    "description": "If true, include audio_analyses rows for each track.",
                                }
                            },
                        },
                    },
                    {
                        "name": "get_catalog_insights",
                        "description": "Summarize what the catalog reveals about the artist's style: genre distribution, key distribution, BPM stats, and a narrative description.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    # ── Knowledge Graph tools ──
                    {
                        "name": "kg_add_node",
                        "description": "Add or update a node in the knowledge graph. Nodes represent entities: tracks, albums, genres, moods, instruments, plugins, agents.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["id", "type", "label"],
                            "properties": {
                                "id": {"type": "string", "description": "Unique node ID, e.g. 'track:42', 'genre:lo-fi', 'album:soundscapes-vol-2'"},
                                "type": {"type": "string", "description": "Node type: track, album, genre, mood, instrument, plugin, agent"},
                                "label": {"type": "string", "description": "Human-readable label for this node"},
                                "properties": {"type": "object", "description": "Optional extra properties (JSON)"},
                            },
                        },
                    },
                    {
                        "name": "kg_add_edge",
                        "description": "Add or update an edge (relationship) between two knowledge graph nodes.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["source", "target", "relation"],
                            "properties": {
                                "source": {"type": "string", "description": "Source node ID"},
                                "target": {"type": "string", "description": "Target node ID"},
                                "relation": {"type": "string", "description": "Relationship type, e.g. 'has_genre', 'contains', 'uses_plugin', 'received_feedback_from'"},
                                "weight": {"type": "number", "description": "Edge weight 0.0–1.0 (default 1.0)"},
                                "properties": {"type": "object", "description": "Optional extra properties"},
                            },
                        },
                    },
                    {
                        "name": "kg_search",
                        "description": "Full-text search the knowledge graph by label. Returns matching nodes with their type and ID. Use before loading full track lists.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["query"],
                            "properties": {
                                "query": {"type": "string", "description": "Search terms (e.g. 'lo-fi ambient piano')"},
                                "node_type": {"type": "string", "description": "Filter by node type (optional): track, album, genre, mood, instrument, plugin"},
                                "limit": {"type": "integer", "description": "Max results to return (default 20)"},
                            },
                        },
                    },
                    {
                        "name": "kg_neighbors",
                        "description": "Get all nodes connected to a given node — its direct neighbors in the knowledge graph.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["node_id"],
                            "properties": {
                                "node_id": {"type": "string", "description": "Node ID to get neighbors for"},
                                "relation": {"type": "string", "description": "Filter by relation type (optional)"},
                                "direction": {"type": "string", "enum": ["out", "in", "both"], "description": "Edge direction (default: both)"},
                            },
                        },
                    },
                    # ── File & metadata tools ──
                    {
                        "name": "read_audio_metadata",
                        "description": "Read ID3/FLAC/MP4 tags from an audio file using mutagen. Returns title, artist, album, duration, track number, BPM. Much cheaper than full Gemini analysis — use this first.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["file_path"],
                            "properties": {
                                "file_path": {"type": "string", "description": "Absolute path to the audio file"},
                            },
                        },
                    },
                    {
                        "name": "browse_folder",
                        "description": "List files in a directory. Optionally filter by extension. Use to explore the inbox, music folder, or project directories.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["path"],
                            "properties": {
                                "path": {"type": "string", "description": "Absolute path to the folder"},
                                "pattern": {"type": "string", "description": "File extension filter e.g. '*.mp3' (optional)"},
                                "recursive": {"type": "boolean", "description": "Search recursively (default false)"},
                            },
                        },
                    },
                    # ── Cloud vault tools ──
                    {
                        "name": "vault_get_sync_status",
                        "description": "Check when the cloud vault was last synced and view recent sync operations.",
                        "inputSchema": {"type": "object", "properties": {}},
                    },
                    {
                        "name": "vault_add_comment",
                        "description": "Add a timestamped comment to a track (like a waveform annotation). timestamp_s is optional — omit for a general comment.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id", "body", "author"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "body": {"type": "string", "description": "Comment text"},
                                "author": {"type": "string", "description": "Agent name or 'user'"},
                                "timestamp_s": {"type": "number", "description": "Position in audio (seconds). Omit for general comment."},
                                "version_id": {"type": "integer", "description": "Tie to a specific file version (optional)"},
                            },
                        },
                    },
                    {
                        "name": "vault_get_comments",
                        "description": "Get all unresolved timestamped comments on a track.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer"},
                                "include_resolved": {"type": "boolean", "description": "Include resolved comments (default false)"},
                            },
                        },
                    },
                    # ── Stem separation ────────────────────────────────────────
                    {
                        "name": "separate_stems",
                        "description": (
                            "Separate a track into 4 stems (vocals, drums, bass, other) using Demucs. "
                            "Stores stem file paths in the database and optionally extracts lyrics from "
                            "the vocal stem via Gemini. Takes 1-5 minutes depending on track length. "
                            "Call once per track — subsequent calls return cached paths unless force=true."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer", "description": "Track ID in database"},
                                "extract_lyrics": {"type": "boolean", "description": "Also extract lyrics from vocal stem via Gemini (default true)"},
                                "analyze_instrumental": {"type": "boolean", "description": "Also analyze the instrumental stem via Gemini (default false)"},
                                "force": {"type": "boolean", "description": "Re-run even if stems already exist (default false)"},
                            },
                        },
                    },
                    {
                        "name": "get_stem_paths",
                        "description": "Get the file paths for a track's separated stems. Returns null if stems have not been generated yet — call separate_stems first.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer"},
                            },
                        },
                    },
                    {
                        "name": "get_track_lyrics",
                        "description": "Get the transcribed lyrics and vocal analysis for a track. Returns null if lyrics have not been extracted yet — call separate_stems with extract_lyrics=true first.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer"},
                            },
                        },
                    },
                    {
                        "name": "analyze_mumble",
                        "description": (
                            "Decode a hummed or mumbled vocal take to extract phonetic patterns, "
                            "suggest real words that fit the sounds and syllable stress, identify potential themes, "
                            "and generate complete hook phrase candidates. "
                            "Use this when an artist submits a demo with placeholder sounds rather than real lyrics — "
                            "the melody and rhythm are intentional even if the words aren't finalized. "
                            "Pass the vocals stem path (from get_stem_paths) or the full track path if stems haven't been separated yet."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer", "description": "Track ID — used to look up stem paths and store results"},
                                "use_full_mix": {"type": "boolean", "description": "Analyze the full mix instead of the isolated vocal stem (default: false — uses vocal stem if stems exist)"},
                            },
                        },
                    },
                    {
                        "name": "get_mumble_analysis",
                        "description": "Retrieve a previously generated mumble analysis for a track, including word suggestions, hook candidates, and themes.",
                        "inputSchema": {
                            "type": "object",
                            "required": ["track_id"],
                            "properties": {
                                "track_id": {"type": "integer"},
                            },
                        },
                    },
                ],
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})
        try:
            result = await call_tool(tool_name, args)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps(result, default=str)}]},
            }
        except Exception as e:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": json.dumps({"error": str(e)})}], "isError": True},
            }

    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": f"Unknown method: {method}"}}


async def call_tool(name: str, args: dict) -> dict | list:
    import sqlite3

    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.row_factory = sqlite3.Row

    try:
        if name == "analyze_track":
            from audio_analysis.analyzer import analyze
            result = analyze(args["file_path"], DB_PATH, track_id=args.get("track_id", 0))
            return result.model_dump()

        elif name == "analyze_artwork":
            import mimetypes
            from pathlib import Path as _Path
            from google import genai as _genai
            from google.genai import types as _gtypes

            img_path = _Path(args["file_path"])
            if not img_path.exists():
                raise FileNotFoundError(f"Artwork file not found: {img_path}")

            mime_type = mimetypes.guess_type(str(img_path))[0] or "image/jpeg"
            image_bytes = img_path.read_bytes()

            _api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
            _client_kwargs: dict = {}
            if _api_key:
                _client_kwargs["api_key"] = _api_key
            _client = _genai.Client(**_client_kwargs)

            _artwork_prompt = (
                "Analyze this album cover artwork. Return ONLY valid JSON with these fields:\n"
                "- composition: string describing layout, balance, focal points\n"
                "- color_palette: list of dominant colors\n"
                "- mood: list of mood descriptors\n"
                "- typography: notes on font usage and placement\n"
                "- technical_specs: {quality: 'high|medium|low', compression_artifacts: bool, resolution_sufficient: bool}\n"
                "- style_tags: list of visual style descriptors\n"
                "- bandcamp_ready: bool (needs 3000x3000 minimum, no major quality issues)\n"
                "- issues: list of specific problems to address\n"
                "- strengths: list of visual strengths\n"
                "- overall_notes: 2-3 sentence holistic assessment"
            )

            _resp = await _client.aio.models.generate_content(
                model="gemini-2.5-pro",
                contents=[
                    _gtypes.Content(parts=[
                        _gtypes.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                        _gtypes.Part.from_text(text=_artwork_prompt),
                    ])
                ],
                config=_gtypes.GenerateContentConfig(temperature=0.2, max_output_tokens=2048),
            )

            _raw = (_resp.text or "").strip()
            if _raw.startswith("```"):
                _lines = _raw.split("\n")
                _lines = _lines[1:] if _lines[0].startswith("```") else _lines
                if _lines and _lines[-1].strip() == "```":
                    _lines = _lines[:-1]
                _raw = "\n".join(_lines)
            return json.loads(_raw)

        elif name == "get_artist_patterns":
            from audio_analysis.memory_builder import get_artist_patterns
            min_confidence = float(args.get("min_confidence", 0.4))
            limit = int(args.get("limit", 30))
            result = get_artist_patterns(DB_PATH, min_confidence=min_confidence, limit=limit)
            return result.model_dump()

        elif name == "get_track_context":
            from audio_analysis.memory_builder import get_track_context
            result = get_track_context(DB_PATH, args["track_id"])
            return result.model_dump()

        elif name == "get_evolution_arc":
            from audio_analysis.memory_builder import get_evolution_arc
            result = get_evolution_arc(DB_PATH)
            return [item.model_dump() for item in result]

        elif name == "get_tracks":
            limit = int(args.get("limit", 50))
            state_filter = args.get("state")
            if state_filter:
                rows = conn.execute(
                    "SELECT id, title, version, state, project_id, duration_seconds, format, created_at"
                    " FROM tracks WHERE state = ? ORDER BY created_at DESC LIMIT ?",
                    (state_filter, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, version, state, project_id, duration_seconds, format, created_at"
                    " FROM tracks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

        elif name == "get_track_feedback":
            limit = int(args.get("limit", 15))
            rows = conn.execute(
                "SELECT * FROM feedback WHERE track_id = ? ORDER BY created_at ASC LIMIT ?",
                (args["track_id"], limit),
            ).fetchall()
            return [dict(r) for r in rows]

        elif name == "transition_state":
            track = conn.execute("SELECT state FROM tracks WHERE id = ?", (args["track_id"],)).fetchone()
            if not track:
                raise ValueError(f"Track {args['track_id']} not found")
            from_state = track["state"]
            to_state = args["to_state"]
            changed_by = args["changed_by"]
            reason = args.get("reason", "")
            conn.execute(
                "UPDATE tracks SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (to_state, args["track_id"]),
            )
            conn.execute(
                "INSERT INTO release_states (track_id, from_state, to_state, changed_by, reason) VALUES (?, ?, ?, ?, ?)",
                (args["track_id"], from_state, to_state, changed_by, reason),
            )
            conn.commit()
            return {"track_id": args["track_id"], "from_state": from_state, "to_state": to_state}

        elif name == "log_feedback":
            conn.execute(
                "INSERT INTO feedback (track_id, project_id, agent, message, channel, direction, intent) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (args.get("track_id"), args.get("project_id"), args["agent"], args["message"], args["channel"], args["direction"], args.get("intent")),
            )
            conn.commit()
            return {"status": "logged"}

        elif name == "get_stats":
            in_progress = conn.execute("SELECT COUNT(*) as c FROM tracks WHERE state != 'RELEASED'").fetchone()["c"]
            released = conn.execute("SELECT COUNT(*) as c FROM tracks WHERE state = 'RELEASED'").fetchone()["c"]
            total = in_progress + released
            return {
                "tracks_in_progress": in_progress,
                "tracks_released": released,
                "completion_rate": round((released / total * 100) if total > 0 else 0, 1),
            }

        elif name == "get_projects":
            rows = conn.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

        elif name == "get_artist_profile":
            row = conn.execute("SELECT * FROM artist_profile LIMIT 1").fetchone()
            return dict(row) if row else {}

        elif name == "get_sessions":
            limit = args.get("limit", 20)
            project = args.get("project_name")
            if project:
                rows = conn.execute(
                    "SELECT * FROM ableton_sessions WHERE project_name = ? ORDER BY started_at DESC LIMIT ?",
                    (project, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM ableton_sessions ORDER BY started_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

        elif name == "get_export_events":
            limit = args.get("limit", 20)
            project = args.get("project_name")
            if project:
                rows = conn.execute(
                    "SELECT * FROM export_events WHERE project_name = ? ORDER BY exported_at DESC LIMIT ?",
                    (project, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM export_events ORDER BY exported_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(r) for r in rows]

        elif name == "get_session_summary":
            sessions = conn.execute(
                "SELECT project_name, SUM(duration_minutes) as total_min, COUNT(*) as sessions, MAX(started_at) as last_session FROM ableton_sessions GROUP BY project_name ORDER BY last_session DESC"
            ).fetchall()
            exports_changed = conn.execute("SELECT COUNT(*) as c FROM export_events WHERE changed_from_prev = 1").fetchone()["c"]
            exports_total = conn.execute("SELECT COUNT(*) as c FROM export_events").fetchone()["c"]
            total_hours = sum(r["total_min"] for r in sessions) / 60
            result = {
                "total_hours_tracked": round(total_hours, 1),
                "projects": [
                    {
                        "name": r["project_name"],
                        "hours": round(r["total_min"] / 60, 1),
                        "sessions": r["sessions"],
                        "last_worked": r["last_session"],
                    }
                    for r in sessions
                ],
                "exports_total": exports_total,
                "exports_with_changes": exports_changed,
            }
            return result

        elif name == "scan_ableton_project":
            from session_intelligence.watcher_integration import SessionIntelligenceEmitter
            emitter = SessionIntelligenceEmitter(db_path=DB_PATH)
            count = emitter.scan_project_folder(args["project_folder"])
            return {"sessions_found": count, "project_folder": args["project_folder"]}

        # ── Conductor message queue ──────────────────────────────────────────

        elif name == "submit_message":
            from_agent  = args["from_agent"]
            draft       = args["draft"]
            context     = args.get("context", "")
            track_id    = args.get("track_id")
            priority    = args.get("priority", "normal")
            conn.execute(
                """INSERT INTO pending_messages
                   (from_agent, draft, context, track_id, priority)
                   VALUES (?, ?, ?, ?, ?)""",
                (from_agent, draft, context, track_id, priority),
            )
            conn.commit()
            msg_id = conn.execute("SELECT last_insert_rowid() AS id").fetchone()["id"]
            return {
                "submitted": True,
                "message_id": msg_id,
                "note": "Message queued for conductor review. Do not send this message directly to the artist.",
            }

        elif name == "get_pending_messages":
            status_filter = args.get("status", "pending")
            if status_filter == "all":
                rows = conn.execute(
                    "SELECT * FROM pending_messages ORDER BY submitted_at DESC LIMIT 50"
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM pending_messages WHERE status = ? ORDER BY submitted_at ASC",
                    (status_filter,),
                ).fetchall()
            return [dict(r) for r in rows]

        elif name == "approve_message":
            msg_id          = args["message_id"]
            refined         = args.get("refined_draft")
            reasoning       = args.get("conductor_reasoning", "")
            conn.execute(
                """UPDATE pending_messages
                   SET status = 'approved',
                       refined_draft = ?,
                       conductor_reasoning = ?,
                       sent_at = datetime('now')
                   WHERE id = ?""",
                (refined, reasoning, msg_id),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM pending_messages WHERE id = ?", (msg_id,)).fetchone()
            return {
                "approved": True,
                "message_id": msg_id,
                "deliver_this": refined if refined else row["draft"],
                "from_agent": row["from_agent"],
            }

        elif name == "reject_message":
            msg_id    = args["message_id"]
            reasoning = args["conductor_reasoning"]
            conn.execute(
                """UPDATE pending_messages
                   SET status = 'rejected',
                       conductor_reasoning = ?
                   WHERE id = ?""",
                (reasoning, msg_id),
            )
            conn.commit()
            return {"rejected": True, "message_id": msg_id}

        elif name == "add_memory":
            agent      = args["agent"]
            tag        = args["tag"]
            content    = args["content"]
            confidence = float(args.get("confidence", 0.5))
            track_id   = args.get("track_id")
            cursor = conn.execute(
                """INSERT INTO agent_memory (agent, tag, content, confidence, track_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (agent, tag, content, confidence, track_id),
            )
            conn.commit()
            memory_id = cursor.lastrowid
            return {
                "stored": True,
                "memory_id": memory_id,
                "agent": agent,
                "tag": tag,
                "confidence": confidence,
            }

        elif name == "search_memory":
            query  = args["query"]
            agent  = args.get("agent")
            tag    = args.get("tag")
            limit  = int(args.get("limit", 10))

            # FTS5 keyword search with optional filters applied post-query
            fts_query = " OR ".join(
                f'"{term}"' for term in query.split() if len(term) >= 2
            ) or query

            filters = ["am.archived = 0"]
            bind = []
            if agent:
                filters.append("am.agent = ?")
                bind.append(agent)
            if tag:
                filters.append("am.tag = ?")
                bind.append(tag)

            where_clause = " AND ".join(filters)

            rows = conn.execute(
                f"""SELECT am.id, am.agent, am.tag, am.content, am.confidence,
                           am.track_id, am.created_at, am.updated_at,
                           fts.rank
                    FROM agent_memory_fts fts
                    JOIN agent_memory am ON am.id = fts.rowid
                    WHERE agent_memory_fts MATCH ?
                      AND {where_clause}
                    ORDER BY fts.rank
                    LIMIT ?""",
                (fts_query, *bind, limit),
            ).fetchall()

            return {
                "results": [dict(r) for r in rows],
                "count": len(rows),
                "query": query,
            }

        elif name == "get_agent_memory":
            agent          = args["agent"]
            tag            = args.get("tag")
            min_confidence = float(args.get("min_confidence", 0.0))
            limit          = int(args.get("limit", 20))

            filters = ["agent = ?", "archived = 0", "confidence >= ?"]
            bind    = [agent, min_confidence]
            if tag:
                filters.append("tag = ?")
                bind.append(tag)

            rows = conn.execute(
                f"""SELECT id, agent, tag, content, confidence, track_id,
                           created_at, updated_at, confirmed_at
                    FROM agent_memory
                    WHERE {' AND '.join(filters)}
                    ORDER BY confidence DESC, updated_at DESC
                    LIMIT ?""",
                (*bind, limit),
            ).fetchall()

            # Also fetch established patterns for this agent
            pattern_rows = conn.execute(
                """SELECT agent, tag, observation_count, avg_confidence,
                          last_seen, pattern_summary
                   FROM established_patterns
                   WHERE agent = ?
                   ORDER BY avg_confidence DESC""",
                (agent,),
            ).fetchall()

            return {
                "memories": [dict(r) for r in rows],
                "established_patterns": [dict(r) for r in pattern_rows],
                "agent": agent,
                "count": len(rows),
            }

        elif name == "confirm_memory":
            memory_id = args["memory_id"]
            conn.execute(
                """UPDATE agent_memory
                   SET confidence   = MIN(1.0, confidence + 0.1),
                       confirmed_at = datetime('now'),
                       updated_at   = datetime('now')
                   WHERE id = ?""",
                (memory_id,),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, agent, tag, confidence FROM agent_memory WHERE id = ?",
                (memory_id,),
            ).fetchone()
            return {"confirmed": True, "memory": dict(row) if row else None}

        elif name == "get_established_patterns":
            agent = args.get("agent")
            if agent:
                rows = conn.execute(
                    """SELECT agent, tag, observation_count, avg_confidence,
                              last_seen, pattern_summary
                       FROM established_patterns
                       WHERE agent = ?
                       ORDER BY avg_confidence DESC""",
                    (agent,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT agent, tag, observation_count, avg_confidence,
                              last_seen, pattern_summary
                       FROM established_patterns
                       ORDER BY agent, avg_confidence DESC""",
                ).fetchall()
            return {"patterns": [dict(r) for r in rows], "count": len(rows)}

        elif name == "update_artist_profile":
            key        = args["key"]
            value      = args["value"]
            updated_by = args["updated_by"]
            conn.execute(
                """INSERT INTO artist_context (ctx_key, ctx_value, updated_by)
                   VALUES (?, ?, ?)
                   ON CONFLICT(ctx_key) DO UPDATE SET
                       ctx_value  = excluded.ctx_value,
                       updated_by = excluded.updated_by,
                       updated_at = datetime('now')""",
                (key, value, updated_by),
            )
            conn.commit()
            return {"updated": True, "key": key, "value": value}

        # ── Vault tools ─────────────────────────────────────────────────────

        elif name == "vault_track":
            track_id = args["track_id"]
            reason   = args["reason"]
            track = conn.execute("SELECT id, title, state FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not track:
                raise ValueError(f"Track {track_id} not found")
            from_state = track["state"]
            conn.execute(
                """UPDATE tracks
                   SET state = 'VAULT',
                       vault_reason = ?,
                       vault_date = datetime('now'),
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (reason, track_id),
            )
            conn.execute(
                """INSERT INTO release_states (track_id, from_state, to_state, changed_by, reason)
                   VALUES (?, ?, 'VAULT', 'system', ?)""",
                (track_id, from_state, reason),
            )
            conn.commit()
            return {
                "vaulted": True,
                "track_id": track_id,
                "title": track["title"],
                "from_state": from_state,
                "reason": reason,
            }

        elif name == "vault_search":
            mood  = args.get("mood")
            key   = args.get("key")
            genre = args.get("genre")
            limit = int(args.get("limit", 10))

            # Search vault tracks, joining audio_analyses for metadata filtering
            filters = ["t.state = 'VAULT'"]
            bind = []
            if mood:
                filters.append("(a.mood_tags LIKE ? OR a.energy_curve LIKE ?)")
                bind.extend([f"%{mood}%", f"%{mood}%"])
            if key:
                filters.append("a.musical_key LIKE ?")
                bind.append(f"%{key}%")
            if genre:
                filters.append("(a.genre_tags LIKE ? OR a.mood_tags LIKE ?)")
                bind.extend([f"%{genre}%", f"%{genre}%"])

            rows = conn.execute(
                f"""SELECT t.id, t.title, t.file_path, t.vault_reason, t.vault_date,
                           a.bpm, a.musical_key, a.genre_tags, a.mood_tags
                    FROM tracks t
                    LEFT JOIN audio_analyses a ON a.track_id = t.id
                    WHERE {' AND '.join(filters)}
                    ORDER BY t.vault_date DESC
                    LIMIT ?""",
                (*bind, limit),
            ).fetchall()
            return {"tracks": [dict(r) for r in rows], "count": len(rows)}

        elif name == "resurface_track":
            track_id = args["track_id"]
            reason   = args["reason"]
            track = conn.execute("SELECT id, title, state FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not track:
                raise ValueError(f"Track {track_id} not found")
            if track["state"] != "VAULT":
                raise ValueError(f"Track {track_id} is not in the vault (state: {track['state']})")
            conn.execute(
                """UPDATE tracks
                   SET state = 'DRAFT',
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (track_id,),
            )
            conn.execute(
                """INSERT INTO release_states (track_id, from_state, to_state, changed_by, reason)
                   VALUES (?, 'VAULT', 'DRAFT', 'system', ?)""",
                (track_id, reason),
            )
            conn.commit()
            return {
                "resurfaced": True,
                "track_id": track_id,
                "title": track["title"],
                "new_state": "DRAFT",
                "reason": reason,
            }

        # ── QC Panel tools ──────────────────────────────────────────────────

        elif name == "manage_panel":
            action = args["action"]

            if action == "add":
                name_val = args.get("name")
                if not name_val:
                    raise ValueError("name is required to add a panelist")
                cursor = conn.execute(
                    """INSERT INTO listening_panel (name, phone, imessage_id, relationship)
                       VALUES (?, ?, ?, ?)""",
                    (name_val, args.get("phone"), args.get("imessage_id"), args.get("relationship")),
                )
                conn.commit()
                return {"added": True, "panelist_id": cursor.lastrowid, "name": name_val}

            elif action == "remove":
                panelist_id = args.get("panelist_id")
                if not panelist_id:
                    raise ValueError("panelist_id is required for remove")
                conn.execute(
                    "UPDATE listening_panel SET active = 0 WHERE id = ?",
                    (panelist_id,),
                )
                conn.commit()
                return {"removed": True, "panelist_id": panelist_id}

            elif action == "list":
                rows = conn.execute(
                    "SELECT * FROM listening_panel WHERE active = 1 ORDER BY added_at"
                ).fetchall()
                return {"panelists": [dict(r) for r in rows], "count": len(rows)}

            else:
                raise ValueError(f"Unknown panel action: {action}")

        elif name == "send_to_panel":
            track_id = args["track_id"]
            message  = args.get("message", "Hey, I'd love your honest take on this track. What do you think?")

            # Verify track exists
            track = conn.execute("SELECT id, title, file_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not track:
                raise ValueError(f"Track {track_id} not found")

            # Create panel session
            cursor = conn.execute(
                """INSERT INTO panel_sessions (track_id, status) VALUES (?, 'sent')""",
                (track_id,),
            )
            session_id = cursor.lastrowid

            # Get active panelists
            panelists = conn.execute(
                "SELECT id, name, phone, imessage_id FROM listening_panel WHERE active = 1"
            ).fetchall()
            conn.commit()

            return {
                "session_id": session_id,
                "track_id": track_id,
                "track_title": track["title"],
                "file_path": track["file_path"],
                "message": message,
                "panelists": [dict(p) for p in panelists],
                "panelist_count": len(panelists),
                "note": "Session created. Use iMessage MCP to send the track and message to each panelist. Log responses with log_panel_response.",
            }

        elif name == "log_panel_response":
            session_id   = args["session_id"]
            panelist_id  = args["panelist_id"]
            raw_response = args["raw_response"]
            sentiment    = args.get("sentiment")
            would_buy    = 1 if args.get("would_buy") else (0 if args.get("would_buy") is False else None)
            key_quote    = args.get("key_quote")

            cursor = conn.execute(
                """INSERT INTO panel_responses
                   (session_id, panelist_id, raw_response, sentiment, would_buy, key_quote)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (session_id, panelist_id, raw_response, sentiment, would_buy, key_quote),
            )
            conn.commit()
            return {
                "logged": True,
                "response_id": cursor.lastrowid,
                "session_id": session_id,
                "panelist_id": panelist_id,
                "sentiment": sentiment,
            }

        elif name == "get_panel_results":
            session_id = args["session_id"]

            session = conn.execute(
                """SELECT ps.*, t.title AS track_title
                   FROM panel_sessions ps
                   JOIN tracks t ON t.id = ps.track_id
                   WHERE ps.id = ?""",
                (session_id,),
            ).fetchone()
            if not session:
                raise ValueError(f"Panel session {session_id} not found")

            responses = conn.execute(
                """SELECT pr.*, lp.name AS panelist_name, lp.relationship
                   FROM panel_responses pr
                   JOIN listening_panel lp ON lp.id = pr.panelist_id
                   WHERE pr.session_id = ?
                   ORDER BY pr.received_at""",
                (session_id,),
            ).fetchall()

            # Build summary
            sentiments = [r["sentiment"] for r in responses if r["sentiment"]]
            would_buy_count = sum(1 for r in responses if r["would_buy"])
            key_quotes = [r["key_quote"] for r in responses if r["key_quote"]]

            return {
                "session": dict(session),
                "responses": [dict(r) for r in responses],
                "summary": {
                    "total_responses": len(responses),
                    "sentiments": sentiments,
                    "would_buy_count": would_buy_count,
                    "would_buy_pct": round(would_buy_count / len(responses) * 100) if responses else 0,
                    "key_quotes": key_quotes,
                },
            }

        # ── Release Cycle tools ─────────────────────────────────────────────

        elif name == "plan_release":
            track_id     = args["track_id"]
            release_date = args["release_date"]

            track = conn.execute("SELECT id, title, project_id FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not track:
                raise ValueError(f"Track {track_id} not found")

            project_id = track["project_id"]
            if not project_id:
                raise ValueError(f"Track {track_id} has no project — assign it to a project first")

            # Build backward-planned milestones relative to release_date
            # T-4w: final mix, T-3w: art + panel, T-2w: preflight, T-1w: upload, T-0: release, T+2w: post-release
            from datetime import datetime, timedelta
            rd = datetime.strptime(release_date, "%Y-%m-%d")
            milestones_def = [
                ("T-4w: Final Mix & Master Lock",       rd - timedelta(weeks=4), "ravi"),
                ("T-3w: Cover Art + Panel Send",         rd - timedelta(weeks=3), "maren"),
                ("T-2w: Preflight + Metadata Complete",  rd - timedelta(weeks=2), "sable"),
                ("T-1w: Upload to Bandcamp (Draft)",     rd - timedelta(weeks=1), "sable"),
                ("T-0: Release Day",                     rd,                      "dez"),
                ("T+2w: Post-Release Check-in",          rd + timedelta(weeks=2), "dez"),
            ]

            created = []
            for ms_name, due, gate_agent in milestones_def:
                cursor = conn.execute(
                    """INSERT INTO milestones (project_id, name, gate_agent, state, due_date, milestone_type)
                       VALUES (?, ?, ?, 'pending', ?, 'release_cycle')""",
                    (project_id, ms_name, gate_agent, due.strftime("%Y-%m-%d")),
                )
                created.append({
                    "milestone_id": cursor.lastrowid,
                    "name": ms_name,
                    "gate_agent": gate_agent,
                    "due_date": due.strftime("%Y-%m-%d"),
                    "state": "pending",
                })
            conn.commit()
            return {
                "planned": True,
                "track_id": track_id,
                "track_title": track["title"],
                "release_date": release_date,
                "milestones": created,
            }

        elif name == "get_release_timeline":
            track_id = args["track_id"]
            track = conn.execute("SELECT id, title, project_id FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not track:
                raise ValueError(f"Track {track_id} not found")
            project_id = track["project_id"]
            if not project_id:
                return {"milestones": [], "count": 0, "note": "Track has no project assigned"}
            rows = conn.execute(
                """SELECT m.*, p.title AS project_title
                   FROM milestones m
                   JOIN projects p ON p.id = m.project_id
                   WHERE m.project_id = ?
                   ORDER BY m.due_date ASC NULLS LAST, m.created_at ASC""",
                (project_id,),
            ).fetchall()
            return {"track_title": track["title"], "milestones": [dict(r) for r in rows], "count": len(rows)}

        # ── Session / Work Pattern tools ────────────────────────────────────

        elif name == "log_session":
            started_at   = args["started_at"]
            ended_at     = args.get("ended_at")
            export_count = int(args.get("export_count", 1))
            project_id   = args.get("project_id")

            # Calculate duration if both timestamps provided
            duration = None
            if started_at and ended_at:
                from datetime import datetime
                try:
                    s = datetime.fromisoformat(started_at)
                    e = datetime.fromisoformat(ended_at)
                    duration = int((e - s).total_seconds() / 60)
                except (ValueError, TypeError):
                    pass

            cursor = conn.execute(
                """INSERT INTO session_log
                   (started_at, ended_at, duration_minutes, export_count, project_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (started_at, ended_at, duration, export_count, project_id),
            )
            conn.commit()
            return {
                "logged": True,
                "session_id": cursor.lastrowid,
                "started_at": started_at,
                "duration_minutes": duration,
                "export_count": export_count,
            }

        elif name == "add_session_note":
            session_id = args["session_id"]
            note       = args["note"]
            mood       = args.get("mood")

            session = conn.execute("SELECT id FROM session_log WHERE id = ?", (session_id,)).fetchone()
            if not session:
                raise ValueError(f"Session {session_id} not found")

            conn.execute(
                "UPDATE session_log SET session_note = ?, mood = ? WHERE id = ?",
                (note, mood, session_id),
            )
            conn.commit()
            return {"updated": True, "session_id": session_id, "note": note, "mood": mood}

        elif name == "get_work_patterns":
            days  = int(args.get("days", 30))
            cutoff = f"-{days} days"

            # Session stats
            sessions = conn.execute(
                """SELECT COUNT(*) AS total_sessions,
                          COALESCE(SUM(duration_minutes), 0) AS total_minutes,
                          COALESCE(SUM(export_count), 0) AS total_exports,
                          MAX(started_at) AS last_session
                   FROM session_log
                   WHERE started_at >= datetime('now', ?)""",
                (cutoff,),
            ).fetchone()

            # Sessions by day-of-week (0=Sunday .. 6=Saturday)
            by_dow = conn.execute(
                """SELECT strftime('%w', started_at) AS dow,
                          COUNT(*) AS sessions
                   FROM session_log
                   WHERE started_at >= datetime('now', ?)
                   GROUP BY dow
                   ORDER BY sessions DESC""",
                (cutoff,),
            ).fetchall()

            # Sessions by hour of day
            by_hour = conn.execute(
                """SELECT strftime('%H', started_at) AS hour,
                          COUNT(*) AS sessions
                   FROM session_log
                   WHERE started_at >= datetime('now', ?)
                   GROUP BY hour
                   ORDER BY sessions DESC
                   LIMIT 5""",
                (cutoff,),
            ).fetchall()

            # Current streak: consecutive days with at least one session
            all_dates = conn.execute(
                """SELECT DISTINCT date(started_at) AS d
                   FROM session_log
                   ORDER BY d DESC"""
            ).fetchall()
            streak = 0
            if all_dates:
                from datetime import datetime, timedelta
                prev = datetime.strptime(all_dates[0]["d"], "%Y-%m-%d").date()
                today = datetime.now().date()
                # Only count streak if most recent session was today or yesterday
                if (today - prev).days <= 1:
                    streak = 1
                    for i in range(1, len(all_dates)):
                        d = datetime.strptime(all_dates[i]["d"], "%Y-%m-%d").date()
                        if (prev - d).days == 1:
                            streak += 1
                            prev = d
                        else:
                            break

            # Gap since last session
            gap_hours = None
            if sessions["last_session"]:
                from datetime import datetime
                last = datetime.fromisoformat(sessions["last_session"])
                gap_hours = round((datetime.now() - last).total_seconds() / 3600, 1)

            return {
                "period_days": days,
                "total_sessions": sessions["total_sessions"],
                "total_hours": round(sessions["total_minutes"] / 60, 1),
                "total_exports": sessions["total_exports"],
                "avg_session_minutes": round(sessions["total_minutes"] / sessions["total_sessions"]) if sessions["total_sessions"] else 0,
                "current_streak_days": streak,
                "hours_since_last_session": gap_hours,
                "most_productive_days": [{"day": r["dow"], "sessions": r["sessions"]} for r in by_dow],
                "peak_hours": [{"hour": r["hour"], "sessions": r["sessions"]} for r in by_hour],
            }

        # ── Royalty Registration tools ──────────────────────────────────────

        elif name == "manage_royalty_registration":
            action = args["action"]

            if action == "create":
                org_name = args.get("org_name")
                org_type = args.get("org_type")
                if not org_name or not org_type:
                    raise ValueError("org_name and org_type are required to create a registration")
                cursor = conn.execute(
                    """INSERT INTO royalty_registrations (org_name, org_type, status, notes)
                       VALUES (?, ?, 'not_started', ?)""",
                    (org_name, org_type, args.get("notes")),
                )
                conn.commit()
                return {"created": True, "registration_id": cursor.lastrowid, "org_name": org_name}

            elif action == "update_status":
                reg_id = args.get("registration_id")
                if not reg_id:
                    raise ValueError("registration_id required for update_status")
                updates = []
                bind = []
                if args.get("status"):
                    updates.append("status = ?")
                    bind.append(args["status"])
                if args.get("account_id"):
                    updates.append("account_id = ?")
                    bind.append(args["account_id"])
                if args.get("notes"):
                    updates.append("notes = ?")
                    bind.append(args["notes"])
                updates.append("updated_at = datetime('now')")
                updates.append("last_checked = datetime('now')")
                bind.append(reg_id)
                conn.execute(
                    f"UPDATE royalty_registrations SET {', '.join(updates)} WHERE id = ?",
                    bind,
                )
                conn.commit()
                row = conn.execute("SELECT * FROM royalty_registrations WHERE id = ?", (reg_id,)).fetchone()
                return {"updated": True, "registration": dict(row) if row else None}

            elif action == "list":
                rows = conn.execute(
                    "SELECT * FROM royalty_registrations ORDER BY org_name"
                ).fetchall()
                return {"registrations": [dict(r) for r in rows], "count": len(rows)}

            else:
                raise ValueError(f"Unknown registration action: {action}")

        elif name == "register_work":
            track_id = args["track_id"]
            org_name = args["org_name"]
            isrc     = args.get("isrc")
            status   = args.get("status", "pending")

            track = conn.execute("SELECT id, title FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not track:
                raise ValueError(f"Track {track_id} not found")

            cursor = conn.execute(
                """INSERT INTO works_registrations (track_id, org_name, isrc, status)
                   VALUES (?, ?, ?, ?)""",
                (track_id, org_name, isrc, status),
            )
            conn.commit()
            return {
                "registered": True,
                "work_registration_id": cursor.lastrowid,
                "track_id": track_id,
                "track_title": track["title"],
                "org_name": org_name,
                "isrc": isrc,
                "status": status,
            }

        # ── Album-as-Statement tools ────────────────────────────────────────

        elif name == "set_project_seed":
            project_id = args["project_id"]
            seed_text  = args["seed_text"]

            project = conn.execute("SELECT id, title FROM projects WHERE id = ?", (project_id,)).fetchone()
            if not project:
                raise ValueError(f"Project {project_id} not found")

            conn.execute(
                """UPDATE projects
                   SET thematic_seed = ?,
                       seed_set_at = datetime('now')
                   WHERE id = ?""",
                (seed_text, project_id),
            )
            conn.commit()
            return {
                "set": True,
                "project_id": project_id,
                "project_title": project["title"],
                "thematic_seed": seed_text,
            }

        elif name == "get_project_coherence":
            project_id = args["project_id"]

            project = conn.execute(
                "SELECT id, title, thematic_seed, seed_set_at FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
            if not project:
                raise ValueError(f"Project {project_id} not found")

            # Get all tracks in this project with their analysis data
            tracks = conn.execute(
                """SELECT t.id, t.title, t.state,
                          a.bpm, a.musical_key, a.genre_tags, a.mood_tags,
                          a.energy_curve, a.mix_observations
                   FROM tracks t
                   LEFT JOIN audio_analyses a ON a.track_id = t.id
                   WHERE t.project_id = ?
                   ORDER BY t.created_at""",
                (project_id,),
            ).fetchall()

            return {
                "project_id": project_id,
                "project_title": project["title"],
                "thematic_seed": project["thematic_seed"],
                "seed_set_at": project["seed_set_at"],
                "tracks": [dict(t) for t in tracks],
                "track_count": len(tracks),
                "note": "Use this data + the thematic seed to assess how well each track serves the project's statement. Consider key relationships, mood arcs, and energy flow across the tracklist.",
            }

        elif name == "import_catalog_album":
            # Lazy import so the MCP server doesn't require requests/bs4 unless used.
            sys.path.insert(0, str(Path(__file__).resolve().parent / "scripts"))
            try:
                from import_bandcamp import import_album_from_url  # type: ignore[import-not-found]
            finally:
                if str(Path(__file__).resolve().parent / "scripts") in sys.path:
                    sys.path.remove(str(Path(__file__).resolve().parent / "scripts"))

            album_url = args["album_url"]
            overwrite = bool(args.get("overwrite", False))
            return import_album_from_url(conn, album_url, overwrite=overwrite)

        elif name == "get_catalog":
            with_analysis = bool(args.get("with_analysis", False))
            project_rows = conn.execute(
                """
                SELECT id, title, type, state, release_date, bandcamp_url,
                       bandcamp_id, bandcamp_tags, bandcamp_description,
                       cover_art_url, target_track_count, created_at
                FROM projects
                WHERE state = 'RELEASED'
                ORDER BY COALESCE(release_date, created_at) DESC
                """
            ).fetchall()

            catalog = []
            for proj in project_rows:
                track_rows = conn.execute(
                    """
                    SELECT id, title, track_number, duration_seconds,
                           bandcamp_track_url, bandcamp_streaming_url
                    FROM tracks
                    WHERE project_id = ?
                    ORDER BY COALESCE(track_number, id)
                    """,
                    (proj["id"],),
                ).fetchall()

                tracks_out = []
                for tr in track_rows:
                    track_dict = dict(tr)
                    if with_analysis:
                        analysis = conn.execute(
                            """
                            SELECT bpm, musical_key, genre_tags, mood_tags,
                                   sync_tier, vocal_presence
                            FROM audio_analyses
                            WHERE track_id = ?
                            ORDER BY created_at DESC LIMIT 1
                            """,
                            (tr["id"],),
                        ).fetchone()
                        track_dict["analysis"] = dict(analysis) if analysis else None
                    tracks_out.append(track_dict)

                catalog.append(
                    {
                        **dict(proj),
                        "tracks": tracks_out,
                        "track_count": len(tracks_out),
                    }
                )

            return {"albums": catalog, "album_count": len(catalog)}

        elif name == "get_catalog_insights":
            from collections import Counter

            stats_row = conn.execute(
                """
                SELECT
                    COUNT(DISTINCT p.id) AS total_albums,
                    COUNT(DISTINCT t.id) AS total_tracks,
                    MIN(p.release_date)  AS earliest_release,
                    MAX(p.release_date)  AS most_recent_release
                FROM projects p
                LEFT JOIN tracks t ON t.project_id = p.id
                WHERE p.state = 'RELEASED'
                """
            ).fetchone()

            analysis_rows = conn.execute(
                """
                SELECT a.bpm, a.musical_key, a.genre_tags, a.mood_tags
                FROM audio_analyses a
                JOIN tracks t ON t.id = a.track_id
                JOIN projects p ON p.id = t.project_id
                WHERE p.state = 'RELEASED'
                """
            ).fetchall()

            genre_counter: Counter[str] = Counter()
            mood_counter: Counter[str] = Counter()
            key_counter: Counter[str] = Counter()
            bpms: list[float] = []

            for row in analysis_rows:
                if row["bpm"] is not None:
                    try:
                        bpms.append(float(row["bpm"]))
                    except (TypeError, ValueError):
                        pass
                if row["musical_key"]:
                    key_counter[row["musical_key"]] += 1
                for col, counter in (("genre_tags", genre_counter), ("mood_tags", mood_counter)):
                    raw = row[col]
                    if not raw:
                        continue
                    try:
                        parsed = json.loads(raw)
                        if isinstance(parsed, list):
                            for tag in parsed:
                                if isinstance(tag, str) and tag.strip():
                                    counter[tag.strip().lower()] += 1
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                    for tag in str(raw).split(","):
                        if tag.strip():
                            counter[tag.strip().lower()] += 1

            # Catalog tags (Bandcamp-supplied) supplement the audio-analysis genres.
            tag_rows = conn.execute(
                "SELECT bandcamp_tags FROM projects WHERE state = 'RELEASED' AND bandcamp_tags IS NOT NULL"
            ).fetchall()
            bandcamp_tag_counter: Counter[str] = Counter()
            for row in tag_rows:
                try:
                    parsed = json.loads(row["bandcamp_tags"] or "[]")
                except (json.JSONDecodeError, TypeError):
                    parsed = []
                if isinstance(parsed, list):
                    for tag in parsed:
                        if isinstance(tag, str) and tag.strip():
                            bandcamp_tag_counter[tag.strip().lower()] += 1

            bpm_stats = None
            if bpms:
                bpm_stats = {
                    "avg": round(sum(bpms) / len(bpms), 2),
                    "min": min(bpms),
                    "max": max(bpms),
                    "count": len(bpms),
                }

            total_albums = stats_row["total_albums"] or 0
            total_tracks = stats_row["total_tracks"] or 0
            top_genres = genre_counter.most_common(8)
            top_moods = mood_counter.most_common(8)
            top_bandcamp_tags = bandcamp_tag_counter.most_common(10)

            if total_albums == 0:
                description = "No released catalog has been imported yet. Run import_catalog_album or scripts/import_bandcamp.py first."
            else:
                desc_bits = [
                    f"The catalog spans {total_albums} releases and {total_tracks} tracks"
                ]
                if stats_row["earliest_release"] and stats_row["most_recent_release"]:
                    desc_bits.append(
                        f" between {stats_row['earliest_release']} and {stats_row['most_recent_release']}"
                    )
                if top_genres:
                    desc_bits.append(
                        ". Recurring genres: "
                        + ", ".join(f"{g} ({c})" for g, c in top_genres[:5])
                    )
                elif top_bandcamp_tags:
                    desc_bits.append(
                        ". Bandcamp tags hint at: "
                        + ", ".join(f"{g} ({c})" for g, c in top_bandcamp_tags[:5])
                    )
                if bpm_stats:
                    desc_bits.append(
                        f". Tempo sits around {bpm_stats['avg']} BPM "
                        f"(range {bpm_stats['min']}–{bpm_stats['max']})"
                    )
                if key_counter:
                    most_common_key, k_count = key_counter.most_common(1)[0]
                    desc_bits.append(
                        f". Most frequent musical key: {most_common_key} ({k_count} tracks)"
                    )
                description = "".join(desc_bits) + "."

            return {
                "total_albums": total_albums,
                "total_tracks": total_tracks,
                "earliest_release": stats_row["earliest_release"],
                "most_recent_release": stats_row["most_recent_release"],
                "genre_distribution": top_genres,
                "mood_distribution": top_moods,
                "bandcamp_tag_distribution": top_bandcamp_tags,
                "key_distribution": key_counter.most_common(),
                "bpm_stats": bpm_stats,
                "catalog_description": description,
                "tracks_with_analysis": len(analysis_rows),
            }

        # ── Knowledge Graph ──────────────────────────────────────────────────
        elif name == "kg_add_node":
            node_id = args["id"]
            node_type = args["type"]
            label = args["label"]
            props = args.get("properties", {})
            import json as _json
            conn.execute(
                """INSERT INTO kg_nodes (id, type, label, properties, updated_at)
                   VALUES (?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(id) DO UPDATE SET
                     label=excluded.label,
                     properties=excluded.properties,
                     updated_at=excluded.updated_at""",
                (node_id, node_type, label, _json.dumps(props)),
            )
            conn.commit()
            return {"added": True, "id": node_id, "type": node_type, "label": label}

        elif name == "kg_add_edge":
            src = args["source"]
            tgt = args["target"]
            rel = args["relation"]
            weight = args.get("weight", 1.0)
            props = args.get("properties", {})
            import json as _json
            conn.execute(
                """INSERT INTO kg_edges (source, target, relation, weight, properties)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(source, target, relation) DO UPDATE SET
                     weight=excluded.weight,
                     properties=excluded.properties""",
                (src, tgt, rel, weight, _json.dumps(props)),
            )
            conn.commit()
            return {"added": True, "source": src, "target": tgt, "relation": rel}

        elif name == "kg_search":
            query = args["query"]
            node_type = args.get("node_type")
            limit = args.get("limit", 20)
            # FTS5 search with optional type filter
            if node_type:
                rows = conn.execute(
                    """SELECT n.id, n.type, n.label, n.properties
                       FROM kg_nodes_fts f
                       JOIN kg_nodes n ON n.rowid = f.rowid
                       WHERE kg_nodes_fts MATCH ? AND n.type = ?
                       ORDER BY rank LIMIT ?""",
                    (query, node_type, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT n.id, n.type, n.label, n.properties
                       FROM kg_nodes_fts f
                       JOIN kg_nodes n ON n.rowid = f.rowid
                       WHERE kg_nodes_fts MATCH ?
                       ORDER BY rank LIMIT ?""",
                    (query, limit),
                ).fetchall()
            return [dict(r) for r in rows]

        elif name == "kg_neighbors":
            node_id = args["node_id"]
            rel_filter = args.get("relation")
            direction = args.get("direction", "both")
            results = []
            if direction in ("out", "both"):
                q = "SELECT target as neighbor_id, relation, weight FROM kg_edges WHERE source = ?"
                params: list = [node_id]
                if rel_filter:
                    q += " AND relation = ?"
                    params.append(rel_filter)
                rows = conn.execute(q, params).fetchall()
                for r in rows:
                    results.append({"direction": "out", **dict(r)})
            if direction in ("in", "both"):
                q = "SELECT source as neighbor_id, relation, weight FROM kg_edges WHERE target = ?"
                params = [node_id]
                if rel_filter:
                    q += " AND relation = ?"
                    params.append(rel_filter)
                rows = conn.execute(q, params).fetchall()
                for r in rows:
                    results.append({"direction": "in", **dict(r)})
            return results

        # ── File & metadata ──────────────────────────────────────────────────
        elif name == "read_audio_metadata":
            file_path = args["file_path"]
            from pathlib import Path as _Path
            p = _Path(file_path)
            meta: dict = {"file": p.name, "title": p.stem, "duration_seconds": None,
                          "artist": None, "album": None, "tracknumber": None,
                          "date": None, "bpm": None, "format": p.suffix.lstrip(".").lower()}
            try:
                from mutagen import File as _MutagenFile  # type: ignore[import]
                audio = _MutagenFile(p, easy=True)
                if audio:
                    def _first(key: str) -> str | None:
                        val = audio.get(key)
                        return val[0] if val else None
                    meta["title"] = _first("title") or p.stem
                    meta["artist"] = _first("artist")
                    meta["album"] = _first("album")
                    meta["tracknumber"] = _first("tracknumber")
                    meta["date"] = _first("date")
                    meta["bpm"] = _first("bpm")
                    if hasattr(audio, "info") and hasattr(audio.info, "length"):
                        meta["duration_seconds"] = round(audio.info.length, 2)
            except Exception as e:
                meta["metadata_error"] = str(e)
            return meta

        elif name == "browse_folder":
            from pathlib import Path as _Path
            folder = _Path(args["path"])
            pattern = args.get("pattern", "*")
            recursive = args.get("recursive", False)
            if not folder.is_dir():
                return {"error": f"Not a directory: {args['path']}"}
            glob_fn = folder.rglob if recursive else folder.glob
            files = []
            for p in sorted(glob_fn(pattern)):
                if p.is_file():
                    files.append({
                        "name": p.name,
                        "path": str(p),
                        "size_mb": round(p.stat().st_size / (1024 * 1024), 3),
                        "suffix": p.suffix,
                    })
            return {"path": str(folder), "file_count": len(files), "files": files}

        # ── Cloud vault (SQLite-backed — no B2 credentials needed for these) ─
        elif name == "vault_get_sync_status":
            recent = conn.execute(
                """SELECT operation, status, bytes, error, synced_at
                   FROM cloud_sync_log ORDER BY synced_at DESC LIMIT 20"""
            ).fetchall()
            last_ok = conn.execute(
                """SELECT synced_at FROM cloud_sync_log
                   WHERE status = 'success' AND operation IN ('upload','sync','backup')
                   ORDER BY synced_at DESC LIMIT 1"""
            ).fetchone()
            return {
                "last_successful_sync": last_ok["synced_at"] if last_ok else None,
                "recent_operations": [dict(r) for r in recent],
            }

        elif name == "vault_add_comment":
            import json as _json
            cur = conn.execute(
                """INSERT INTO track_comments (track_id, version_id, timestamp_s, author, body)
                   VALUES (?, ?, ?, ?, ?)""",
                (args["track_id"], args.get("version_id"), args.get("timestamp_s"),
                 args["author"], args["body"]),
            )
            conn.commit()
            return {"added": True, "comment_id": cur.lastrowid}

        elif name == "vault_get_comments":
            track_id = args["track_id"]
            include_resolved = args.get("include_resolved", False)
            q = """SELECT tc.id, tc.timestamp_s, tc.author, tc.body, tc.resolved,
                          tc.created_at, fv.label as version_label
                   FROM track_comments tc
                   LEFT JOIN file_versions fv ON fv.id = tc.version_id
                   WHERE tc.track_id = ?"""
            params_list: list = [track_id]
            if not include_resolved:
                q += " AND tc.resolved = 0"
            q += " ORDER BY tc.timestamp_s ASC NULLS LAST, tc.created_at ASC"
            rows = conn.execute(q, params_list).fetchall()
            return [dict(r) for r in rows]

        # ── Stem separation ──────────────────────────────────────────────────
        elif name == "separate_stems":
            import json as _json
            from pathlib import Path as _Path
            from stem_separation.separator import separate_stems as _do_separate

            track_id = args["track_id"]
            do_lyrics = args.get("extract_lyrics", True)
            do_instrumental = args.get("analyze_instrumental", False)
            force = args.get("force", False)

            # Resolve file path from DB
            row = conn.execute("SELECT file_path FROM tracks WHERE id = ?", (track_id,)).fetchone()
            if not row:
                raise ValueError(f"Track {track_id} not found")
            file_path = row["file_path"]

            stems_base = _DATA_DIR / "stems"
            stem_paths = await _do_separate(file_path, stems_base, force=force)

            # Persist stem paths
            conn.execute(
                """INSERT INTO track_stems (track_id, model, vocals_path, drums_path, bass_path, other_path)
                   VALUES (?, 'htdemucs', ?, ?, ?, ?)
                   ON CONFLICT(track_id, model) DO UPDATE SET
                       vocals_path = excluded.vocals_path,
                       drums_path  = excluded.drums_path,
                       bass_path   = excluded.bass_path,
                       other_path  = excluded.other_path,
                       separated_at = CURRENT_TIMESTAMP""",
                (track_id,
                 stem_paths.get("vocals"), stem_paths.get("drums"),
                 stem_paths.get("bass"),   stem_paths.get("other")),
            )
            conn.commit()

            result: dict = {"track_id": track_id, "stems": stem_paths, "lyrics": None, "instrumental_analysis": None}

            # Optionally extract lyrics from vocal stem
            if do_lyrics and "vocals" in stem_paths:
                try:
                    from stem_separation.lyrics_extractor import extract_lyrics as _extract
                    _api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
                    lyrics = await _extract(stem_paths["vocals"], api_key=_api_key)
                    conn.execute(
                        """INSERT INTO track_lyrics
                               (track_id, lyrics_clean, lyrics_timestamped, vocal_style,
                                vocal_observations, language, explicit)
                           VALUES (?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(track_id) DO UPDATE SET
                               lyrics_clean       = excluded.lyrics_clean,
                               lyrics_timestamped = excluded.lyrics_timestamped,
                               vocal_style        = excluded.vocal_style,
                               vocal_observations = excluded.vocal_observations,
                               language           = excluded.language,
                               explicit           = excluded.explicit,
                               extracted_at       = CURRENT_TIMESTAMP""",
                        (track_id,
                         lyrics.lyrics_clean,
                         _json.dumps(lyrics.lyrics_timestamped),
                         lyrics.vocal_style,
                         _json.dumps(lyrics.vocal_observations),
                         lyrics.language,
                         int(lyrics.explicit)),
                    )
                    conn.commit()
                    result["lyrics"] = lyrics.model_dump()
                except Exception as e:
                    result["lyrics_error"] = str(e)

            # Optionally analyze instrumental stem
            if do_instrumental and "other" in stem_paths:
                try:
                    from google import genai as _genai
                    from google.genai import types as _gtypes
                    from pathlib import Path as _P
                    _prompt_path = _P(__file__).parent / "stem_separation" / "prompts" / "instrumental_analysis.txt"
                    _prompt = _prompt_path.read_text()
                    _api_key2 = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
                    _ck: dict = {"api_key": _api_key2} if _api_key2 else {}
                    _client = _genai.Client(**_ck)
                    _audio_bytes = _Path(stem_paths["other"]).read_bytes()
                    _resp = await _client.aio.models.generate_content(
                        model="gemini-2.5-pro",
                        contents=[_gtypes.Content(parts=[
                            _gtypes.Part.from_bytes(data=_audio_bytes, mime_type="audio/wav"),
                            _gtypes.Part.from_text(text=_prompt),
                        ])],
                        config=_gtypes.GenerateContentConfig(temperature=0.2, max_output_tokens=4096),
                    )
                    _raw = (_resp.text or "").strip()
                    if _raw.startswith("```"):
                        _lines = _raw.split("\n")[1:]
                        if _lines and _lines[-1].strip() == "```":
                            _lines = _lines[:-1]
                        _raw = "\n".join(_lines)
                    _idata = _json.loads(_raw)
                    conn.execute(
                        """INSERT INTO stem_instrumental_analyses
                               (track_id, arrangement_summary, instruments_detailed,
                                production_techniques, arrangement_moments,
                                frequency_balance, stereo_field, dynamic_range, essence_elements)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(track_id) DO UPDATE SET
                               arrangement_summary   = excluded.arrangement_summary,
                               instruments_detailed  = excluded.instruments_detailed,
                               production_techniques = excluded.production_techniques,
                               arrangement_moments   = excluded.arrangement_moments,
                               frequency_balance     = excluded.frequency_balance,
                               stereo_field          = excluded.stereo_field,
                               dynamic_range         = excluded.dynamic_range,
                               essence_elements      = excluded.essence_elements,
                               analyzed_at           = CURRENT_TIMESTAMP""",
                        (track_id,
                         _idata.get("arrangement_summary"),
                         _json.dumps(_idata.get("instruments_detailed", [])),
                         _json.dumps(_idata.get("production_techniques", [])),
                         _json.dumps(_idata.get("arrangement_moments", [])),
                         _idata.get("frequency_balance"),
                         _idata.get("stereo_field"),
                         _idata.get("dynamic_range"),
                         _json.dumps(_idata.get("essence_elements", []))),
                    )
                    conn.commit()
                    result["instrumental_analysis"] = _idata
                except Exception as e:
                    result["instrumental_analysis_error"] = str(e)

            return result

        elif name == "get_stem_paths":
            track_id = args["track_id"]
            row = conn.execute(
                "SELECT * FROM track_stems WHERE track_id = ? AND model = 'htdemucs'",
                (track_id,),
            ).fetchone()
            if not row:
                return {"track_id": track_id, "stems": None, "message": "Stems not yet generated. Call separate_stems first."}
            return {
                "track_id": track_id,
                "model": row["model"],
                "separated_at": row["separated_at"],
                "stems": {
                    "vocals": row["vocals_path"],
                    "drums":  row["drums_path"],
                    "bass":   row["bass_path"],
                    "other":  row["other_path"],
                },
            }

        elif name == "get_track_lyrics":
            track_id = args["track_id"]
            import json as _json
            row = conn.execute(
                "SELECT * FROM track_lyrics WHERE track_id = ?", (track_id,)
            ).fetchone()
            if not row:
                return {"track_id": track_id, "lyrics": None, "message": "Lyrics not yet extracted. Call separate_stems with extract_lyrics=true first."}
            return {
                "track_id": track_id,
                "lyrics_clean": row["lyrics_clean"],
                "lyrics_timestamped": _json.loads(row["lyrics_timestamped"] or "[]"),
                "vocal_style": row["vocal_style"],
                "vocal_observations": _json.loads(row["vocal_observations"] or "[]"),
                "language": row["language"],
                "explicit": bool(row["explicit"]),
                "is_mumble": bool(row["is_mumble"]),
                "extracted_at": row["extracted_at"],
            }

        elif name == "analyze_mumble":
            import json as _json
            track_id = args["track_id"]
            use_full_mix = args.get("use_full_mix", False)

            # Resolve which audio file to use: vocal stem > full mix
            audio_path: str | None = None
            if not use_full_mix:
                stem_row = conn.execute(
                    "SELECT vocals_path FROM track_stems WHERE track_id = ? AND model = 'htdemucs'",
                    (track_id,),
                ).fetchone()
                if stem_row and stem_row["vocals_path"]:
                    audio_path = stem_row["vocals_path"]

            if not audio_path:
                track_row = conn.execute(
                    "SELECT file_path FROM tracks WHERE id = ?", (track_id,)
                ).fetchone()
                if not track_row:
                    raise ValueError(f"Track {track_id} not found")
                audio_path = track_row["file_path"]

            from stem_separation.mumble_analyzer import analyze_mumble as _do_mumble
            _api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
            analysis = await _do_mumble(audio_path, api_key=_api_key)

            # Persist to DB
            conn.execute(
                """INSERT INTO track_mumble_analyses
                       (track_id, is_mumble, mumble_confidence, rhythm_description,
                        global_stress_pattern, segments, potential_themes,
                        hook_candidates, melodic_notes, vowel_palette)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(track_id) DO UPDATE SET
                       is_mumble            = excluded.is_mumble,
                       mumble_confidence    = excluded.mumble_confidence,
                       rhythm_description   = excluded.rhythm_description,
                       global_stress_pattern = excluded.global_stress_pattern,
                       segments             = excluded.segments,
                       potential_themes     = excluded.potential_themes,
                       hook_candidates      = excluded.hook_candidates,
                       melodic_notes        = excluded.melodic_notes,
                       vowel_palette        = excluded.vowel_palette,
                       analyzed_at          = CURRENT_TIMESTAMP""",
                (track_id,
                 int(analysis.is_mumble),
                 analysis.mumble_confidence,
                 analysis.rhythm_description,
                 analysis.global_stress_pattern,
                 _json.dumps([s.model_dump() for s in analysis.segments]),
                 _json.dumps(analysis.potential_themes),
                 _json.dumps(analysis.hook_candidates),
                 analysis.melodic_notes,
                 _json.dumps(analysis.vowel_palette)),
            )
            # Also flag the lyrics row if it exists
            conn.execute(
                "UPDATE track_lyrics SET is_mumble = ? WHERE track_id = ?",
                (int(analysis.is_mumble), track_id),
            )
            conn.commit()

            return analysis.model_dump()

        elif name == "get_mumble_analysis":
            import json as _json
            track_id = args["track_id"]
            row = conn.execute(
                "SELECT * FROM track_mumble_analyses WHERE track_id = ?", (track_id,)
            ).fetchone()
            if not row:
                return {
                    "track_id": track_id,
                    "analysis": None,
                    "message": "No mumble analysis yet. Call analyze_mumble first.",
                }
            return {
                "track_id": track_id,
                "is_mumble": bool(row["is_mumble"]),
                "mumble_confidence": row["mumble_confidence"],
                "rhythm_description": row["rhythm_description"],
                "global_stress_pattern": row["global_stress_pattern"],
                "segments": _json.loads(row["segments"] or "[]"),
                "potential_themes": _json.loads(row["potential_themes"] or "[]"),
                "hook_candidates": _json.loads(row["hook_candidates"] or "[]"),
                "melodic_notes": row["melodic_notes"],
                "vowel_palette": _json.loads(row["vowel_palette"] or "[]"),
                "analyzed_at": row["analyzed_at"],
            }

        else:
            raise ValueError(f"Unknown tool: {name}")
    finally:
        conn.close()


async def main():
    reader = asyncio.StreamReader()
    protocol = asyncio.StreamReaderProtocol(reader)
    await asyncio.get_event_loop().connect_read_pipe(lambda: protocol, sys.stdin)

    while True:
        line = await reader.readline()
        if not line:
            break
        try:
            request = json.loads(line.decode().strip())
        except json.JSONDecodeError:
            continue

        response = await handle_request(request)
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    asyncio.run(main())
