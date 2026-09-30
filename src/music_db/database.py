from pathlib import Path
import sqlite3


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DB_PATH = PROJECT_ROOT / "db" / "music.db"


SCHEMA = """
PRAGMA foreign_keys = ON;

-- ============================================================
-- TRACKS
-- ============================================================

CREATE TABLE IF NOT EXISTS tracks (
    track_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    album TEXT,
    release_date TEXT,
    spotify_url TEXT,
    saved INTEGER DEFAULT 0,
    saved_at TEXT,
    last_played TEXT,
    top_short_term INTEGER DEFAULT 0,
    top_medium_term INTEGER DEFAULT 0,
    top_long_term INTEGER DEFAULT 0,
    isrc TEXT,
    musicbrainz_recording_id TEXT,
    musicbrainz_release_id TEXT,
    musicbrainz_artist_id TEXT,
    musicbrainz_match_score INTEGER,
    musicbrainz_match_status TEXT,
    duration_ms INTEGER
);

-- ============================================================
-- ARTISTS
-- ============================================================

CREATE TABLE IF NOT EXISTS artists (
    artist_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    spotify_id TEXT UNIQUE,
    spotify_url TEXT,
    popularity INTEGER,
    genres TEXT,
    image_url TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS track_artists (
    track_id TEXT NOT NULL,
    artist_id INTEGER NOT NULL,
    artist_order INTEGER DEFAULT 0,
    PRIMARY KEY (track_id, artist_id),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE,
    FOREIGN KEY (artist_id)
        REFERENCES artists(artist_id)
        ON DELETE CASCADE
);

-- ============================================================
-- PLAY HISTORY
-- ============================================================

CREATE TABLE IF NOT EXISTS play_history (
    track_id TEXT NOT NULL,
    played_at TEXT NOT NULL,
    PRIMARY KEY (track_id, played_at),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE
);

-- ============================================================
-- AUDIO FEATURES
-- Legacy / canonical track-level features
-- ============================================================

CREATE TABLE IF NOT EXISTS audio_features (
    track_id TEXT PRIMARY KEY,
    duration_ms INTEGER,
    tempo REAL,
    key INTEGER,
    mode INTEGER,
    loudness REAL,
    energy REAL,
    danceability REAL,
    valence REAL,
    acousticness REAL,
    instrumentalness REAL,
    speechiness REAL,
    source TEXT,
    confidence REAL,
    updated_at TEXT,
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE
);

-- ============================================================
-- AUDIO FEATURE SOURCES
-- Raw source-specific audio analysis
-- ============================================================

CREATE TABLE IF NOT EXISTS audio_feature_sources (
    track_id TEXT NOT NULL,
    source TEXT NOT NULL,
    tempo REAL,
    key TEXT,
    mode TEXT,
    loudness REAL,
    energy REAL,
    danceability REAL,
    valence REAL,
    acousticness REAL,
    instrumentalness REAL,
    speechiness REAL,
    confidence REAL,
    source_url TEXT,
    raw_data TEXT,
    created_at TEXT,
    updated_at TEXT,
    tempo_confidence REAL,
    key_confidence REAL,
    PRIMARY KEY (track_id, source),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE
);

-- ============================================================
-- GENRES
-- ============================================================

CREATE TABLE IF NOT EXISTS genres (
    genre_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS track_genres (
    track_id TEXT NOT NULL,
    genre_id INTEGER NOT NULL,
    PRIMARY KEY (track_id, genre_id),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE,
    FOREIGN KEY (genre_id)
        REFERENCES genres(genre_id)
        ON DELETE CASCADE
);

-- ============================================================
-- INTERNAL TAGS
-- ============================================================

CREATE TABLE IF NOT EXISTS tags (
    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS track_tags (
    track_id TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    source TEXT DEFAULT 'manual',
    confidence REAL,
    PRIMARY KEY (track_id, tag_id),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tag_id)
        REFERENCES tags(tag_id)
        ON DELETE CASCADE
);

-- ============================================================
-- EXTERNAL IDS
-- ============================================================

CREATE TABLE IF NOT EXISTS external_ids (
    track_id TEXT NOT NULL,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    match_status TEXT,
    match_score REAL,
    match_method TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (track_id, source, entity_type),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE
);

-- ============================================================
-- EXTERNAL TAGS
-- Raw tags from external sources
-- ============================================================

CREATE TABLE IF NOT EXISTS external_tags (
    tag_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    raw_tag TEXT NOT NULL,
    tag_type TEXT,
    created_at TEXT,
    UNIQUE (source, raw_tag, tag_type)
);

CREATE TABLE IF NOT EXISTS track_external_tags (
    track_id TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    raw_value TEXT,
    created_at TEXT,
    PRIMARY KEY (track_id, tag_id, source),
    FOREIGN KEY (track_id)
        REFERENCES tracks(track_id)
        ON DELETE CASCADE,
    FOREIGN KEY (tag_id)
        REFERENCES external_tags(tag_id)
        ON DELETE CASCADE
);

-- ============================================================
-- TAG NORMALIZATION
-- Currently unused / deterministic future layer
-- ============================================================

CREATE TABLE IF NOT EXISTS tag_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT,
    raw_tag TEXT NOT NULL,
    canonical_tag_id INTEGER NOT NULL,
    confidence REAL DEFAULT 1.0,
    method TEXT,
    created_at TEXT,
    UNIQUE (source, raw_tag)
);

CREATE TABLE IF NOT EXISTS tag_relations (
    parent_tag_id INTEGER NOT NULL,
    child_tag_id INTEGER NOT NULL,
    relation TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    created_at TEXT,
    PRIMARY KEY (parent_tag_id, child_tag_id, relation)
);

CREATE TABLE IF NOT EXISTS tag_normalization_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT,
    finished_at TEXT,
    raw_tag_count INTEGER DEFAULT 0,
    canonical_tag_count INTEGER DEFAULT 0,
    alias_count INTEGER DEFAULT 0,
    relation_count INTEGER DEFAULT 0,
    status TEXT
);

-- ============================================================
-- INDEXES
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_tracks_title
    ON tracks(title);

CREATE INDEX IF NOT EXISTS idx_tracks_last_played
    ON tracks(last_played);

CREATE INDEX IF NOT EXISTS idx_tracks_isrc
    ON tracks(isrc);

CREATE INDEX IF NOT EXISTS idx_artists_name
    ON artists(name);

CREATE INDEX IF NOT EXISTS idx_play_history_played_at
    ON play_history(played_at);

CREATE INDEX IF NOT EXISTS idx_audio_features_tempo
    ON audio_features(tempo);

CREATE INDEX IF NOT EXISTS idx_audio_features_energy
    ON audio_features(energy);

CREATE INDEX IF NOT EXISTS idx_audio_features_danceability
    ON audio_features(danceability);

CREATE INDEX IF NOT EXISTS idx_audio_feature_sources_source
    ON audio_feature_sources(source);

CREATE INDEX IF NOT EXISTS idx_audio_feature_sources_tempo
    ON audio_feature_sources(tempo);

CREATE INDEX IF NOT EXISTS idx_genres_name
    ON genres(name);

CREATE INDEX IF NOT EXISTS idx_tags_name
    ON tags(name);

CREATE INDEX IF NOT EXISTS idx_track_tags_tag
    ON track_tags(tag_id);

CREATE INDEX IF NOT EXISTS idx_track_tags_source
    ON track_tags(source);

CREATE INDEX IF NOT EXISTS idx_external_ids_source
    ON external_ids(source);

CREATE INDEX IF NOT EXISTS idx_external_tags_source
    ON external_tags(source);

CREATE INDEX IF NOT EXISTS idx_track_external_tags_track
    ON track_external_tags(track_id);

CREATE INDEX IF NOT EXISTS idx_track_external_tags_tag
    ON track_external_tags(tag_id);
"""


def get_connection():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn



MIGRATION_SCHEMA = """
-- ============================================================
-- NORMALIZED MUSIC ENTITIES
-- ============================================================

CREATE TABLE IF NOT EXISTS albums (
    album_id INTEGER PRIMARY KEY AUTOINCREMENT,
    spotify_id TEXT UNIQUE,
    name TEXT NOT NULL,
    release_date TEXT,
    album_type TEXT,
    spotify_url TEXT,
    image_url TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS track_albums (
    track_id TEXT NOT NULL,
    album_id INTEGER NOT NULL,
    PRIMARY KEY (track_id, album_id),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
    FOREIGN KEY (album_id) REFERENCES albums(album_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS track_credits (
    credit_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    person_name TEXT NOT NULL,
    role TEXT NOT NULL,
    source TEXT NOT NULL,
    source_id TEXT,
    confidence REAL,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (track_id, person_name, role, source),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS track_relations (
    track_id TEXT NOT NULL,
    related_track_id TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    source TEXT NOT NULL,
    confidence REAL,
    note TEXT,
    created_at TEXT,
    PRIMARY KEY (track_id, related_track_id, relation_type, source),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
    FOREIGN KEY (related_track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
    CHECK (track_id != related_track_id)
);

-- ============================================================
-- ENRICHMENT STATUS
-- ============================================================

CREATE TABLE IF NOT EXISTS enrichment_status (
    track_id TEXT NOT NULL,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER DEFAULT 0,
    last_error TEXT,
    last_attempted_at TEXT,
    completed_at TEXT,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (track_id, source, entity_type),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_enrichment_status_source_status
    ON enrichment_status(source, entity_type, status);

CREATE INDEX IF NOT EXISTS idx_enrichment_status_track
    ON enrichment_status(track_id);

-- ============================================================
-- SOURCE REGISTRY / ENRICHMENT RUNS
-- ============================================================

CREATE TABLE IF NOT EXISTS source_registry (
    source TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    priority INTEGER DEFAULT 100,
    enabled INTEGER DEFAULT 1,
    role TEXT,
    notes TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS enrichment_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    candidate_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    no_data_count INTEGER DEFAULT 0,
    not_found_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    status TEXT,
    error TEXT
);

-- ============================================================
-- DATA QUALITY
-- ============================================================

CREATE TABLE IF NOT EXISTS data_quality_issues (
    issue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    issue_type TEXT NOT NULL,
    severity TEXT NOT NULL,
    details TEXT,
    detected_at TEXT,
    resolved_at TEXT,
    UNIQUE (entity_type, entity_id, issue_type)
);

CREATE INDEX IF NOT EXISTS idx_track_albums_album
    ON track_albums(album_id);

CREATE INDEX IF NOT EXISTS idx_track_credits_track
    ON track_credits(track_id);

CREATE INDEX IF NOT EXISTS idx_track_credits_role
    ON track_credits(role);

CREATE INDEX IF NOT EXISTS idx_track_relations_related
    ON track_relations(related_track_id);

CREATE INDEX IF NOT EXISTS idx_track_relations_type
    ON track_relations(relation_type);

CREATE INDEX IF NOT EXISTS idx_enrichment_runs_source
    ON enrichment_runs(source);

CREATE INDEX IF NOT EXISTS idx_quality_entity
    ON data_quality_issues(entity_type, entity_id);

CREATE INDEX IF NOT EXISTS idx_quality_severity
    ON data_quality_issues(severity);

-- ============================================================
-- EDITORIAL / MARKET DATA
-- ============================================================
CREATE TABLE IF NOT EXISTS chart_entries (
    chart_entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    chart_name TEXT NOT NULL,
    chart_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    previous_rank INTEGER,
    peak_rank INTEGER,
    weeks_on_chart INTEGER,
    title TEXT NOT NULL,
    artist_name TEXT,
    album_name TEXT,
    track_id TEXT,
    source_url TEXT,
    raw_data TEXT,
    observed_at TEXT,
    UNIQUE (source, chart_name, chart_date, rank),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_chart_entries_track ON chart_entries(track_id);
CREATE INDEX IF NOT EXISTS idx_chart_entries_source_date ON chart_entries(source, chart_name, chart_date);
CREATE INDEX IF NOT EXISTS idx_chart_entries_title_artist ON chart_entries(title, artist_name);

CREATE TABLE IF NOT EXISTS review_entries (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    external_id TEXT,
    title TEXT,
    artist_name TEXT,
    album_name TEXT,
    score REAL,
    review_date TEXT,
    author TEXT,
    headline TEXT,
    url TEXT NOT NULL,
    track_id TEXT,
    raw_data TEXT,
    observed_at TEXT,
    UNIQUE (source, url),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_review_entries_track ON review_entries(track_id);
CREATE INDEX IF NOT EXISTS idx_review_entries_source_date ON review_entries(source, review_date);

CREATE TABLE IF NOT EXISTS source_records (
    source_record_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    external_id TEXT NOT NULL,
    track_id TEXT,
    artist_name TEXT,
    album_name TEXT,
    title TEXT,
    url TEXT,
    data_json TEXT,
    observed_at TEXT,
    UNIQUE (source, entity_type, external_id),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_source_records_track ON source_records(track_id);
CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source);

-- ============================================================
-- GENERATION LAYER
-- ============================================================
CREATE TABLE IF NOT EXISTS generation_projects (
    project_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_key TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    root_path TEXT NOT NULL,
    reference_track_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (reference_track_id) REFERENCES tracks(track_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_generation_projects_reference
    ON generation_projects(reference_track_id);

CREATE TABLE IF NOT EXISTS generation_assets (
    asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    asset_type TEXT NOT NULL,
    title TEXT,
    audio_path TEXT NOT NULL,
    fingerprint_sha256 TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES generation_projects(project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_generation_assets_project
    ON generation_assets(project_id, asset_type);

CREATE INDEX IF NOT EXISTS idx_generation_assets_fingerprint
    ON generation_assets(fingerprint_sha256);

CREATE TABLE IF NOT EXISTS generation_project_notes (
    note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    note_type TEXT NOT NULL,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES generation_projects(project_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_generation_project_notes_project
    ON generation_project_notes(project_id, note_type);

CREATE TABLE IF NOT EXISTS generation_jobs (
    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'queued',
    prompt TEXT,
    lyrics TEXT,
    bpm REAL,
    key_scale TEXT,
    time_signature TEXT,
    duration_seconds REAL,
    vocal_language TEXT,
    reference_track_id TEXT,
    parent_job_id INTEGER,
    project_id INTEGER,
    provider_task_id TEXT,
    request_json TEXT,
    response_json TEXT,
    error TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    FOREIGN KEY (reference_track_id) REFERENCES tracks(track_id) ON DELETE SET NULL,
    FOREIGN KEY (parent_job_id) REFERENCES generation_jobs(job_id) ON DELETE SET NULL,
    FOREIGN KEY (project_id) REFERENCES generation_projects(project_id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS generation_outputs (
    output_id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id INTEGER NOT NULL,
    output_index INTEGER DEFAULT 0,
    audio_path TEXT,
    audio_url TEXT,
    duration_seconds REAL,
    bpm REAL,
    key_scale TEXT,
    sample_rate INTEGER,
    format TEXT,
    analysis_json TEXT,
    fingerprint_sha256 TEXT,
    project_id INTEGER,
    stage TEXT NOT NULL DEFAULT 'generation',
    created_at TEXT,
    FOREIGN KEY (job_id) REFERENCES generation_jobs(job_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES generation_projects(project_id) ON DELETE SET NULL
);
CREATE TABLE IF NOT EXISTS generation_analysis (
    analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    output_id INTEGER NOT NULL,
    analysis_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT,
    FOREIGN KEY (output_id) REFERENCES generation_outputs(output_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS audio_analysis_cache (
    fingerprint_sha256 TEXT NOT NULL,
    analysis_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    created_at TEXT,
    updated_at TEXT,
    PRIMARY KEY (fingerprint_sha256, analysis_type)
);

CREATE TABLE IF NOT EXISTS generation_relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_output_id INTEGER NOT NULL,
    to_output_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    confidence REAL,
    note TEXT,
    created_at TEXT,
    UNIQUE (from_output_id, to_output_id, relation_type),
    FOREIGN KEY (from_output_id) REFERENCES generation_outputs(output_id) ON DELETE CASCADE,
    FOREIGN KEY (to_output_id) REFERENCES generation_outputs(output_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_provider_status ON generation_jobs(provider, status);
CREATE INDEX IF NOT EXISTS idx_generation_jobs_reference ON generation_jobs(reference_track_id);
CREATE INDEX IF NOT EXISTS idx_generation_outputs_job ON generation_outputs(job_id);
CREATE INDEX IF NOT EXISTS idx_generation_outputs_fingerprint ON generation_outputs(fingerprint_sha256);
CREATE INDEX IF NOT EXISTS idx_generation_analysis_output_type
    ON generation_analysis(output_id, analysis_type);
CREATE INDEX IF NOT EXISTS idx_audio_analysis_cache_type
    ON audio_analysis_cache(analysis_type);
CREATE INDEX IF NOT EXISTS idx_audio_analysis_cache_updated
    ON audio_analysis_cache(updated_at);

"""

SOURCE_SEED = """
INSERT INTO source_registry
    (source, source_type, priority, enabled, role, notes, updated_at)
VALUES
    ('spotify', 'api', 10, 1, 'canonical_identity',
     'Spotify track/artist/listening source', CURRENT_TIMESTAMP),
    ('freqblog', 'api', 20, 1, 'audio_primary',
     'Canonical audio-feature source', CURRENT_TIMESTAMP),
    ('songbpm', 'web', 30, 1, 'audio_fallback',
     'Fallback only when FreqBlog has terminal not_found', CURRENT_TIMESTAMP),
    ('lastfm', 'api', 40, 1, 'tags_secondary',
     'Track/artist/album community tags', CURRENT_TIMESTAMP),
    ('musicbrainz', 'api', 50, 1, 'identity_metadata',
     'ISRC identity, MBIDs, tags/genres', CURRENT_TIMESTAMP),
    ('billboard', 'web', 60, 1, 'chart_metadata',
     'Chart-history adapter; Billboard public API is not relied upon', CURRENT_TIMESTAMP),
    ('pitchfork', 'web', 70, 1, 'review_metadata',
     'Official RSS + metadata-only review adapter', CURRENT_TIMESTAMP),
    ('rhythmer', 'web', 80, 0, 'production_metadata',
     'Reserved production metadata adapter', CURRENT_TIMESTAMP),
    ('apple_music', 'api', 55, 1, 'catalog_chart_metadata',
     'Apple Music catalog/search/charts adapter', CURRENT_TIMESTAMP),
    ('discogs', 'api', 75, 0, 'release_credit_metadata',
     'Optional release/label/credit metadata adapter', CURRENT_TIMESTAMP),
    ('acoustid', 'api', 90, 0, 'audio_identity',
     'Optional audio fingerprint identification', CURRENT_TIMESTAMP),
    ('mureka', 'api', 200, 0, 'generation',
     'Cloud music generation provider adapter', CURRENT_TIMESTAMP),
    ('ace_step', 'api', 210, 0, 'generation',
     'ACE-Step 1.5 local/self-hosted generation adapter', CURRENT_TIMESTAMP)
ON CONFLICT(source) DO UPDATE SET
    source_type = excluded.source_type,
    priority = excluded.priority,
    role = excluded.role,
    notes = excluded.notes,
    updated_at = excluded.updated_at;
"""

def run_migrations(conn):
    conn.executescript(MIGRATION_SCHEMA)
    conn.executescript(SOURCE_SEED)

    # Lightweight additive migrations for existing databases.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS generation_project_notes (
            note_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            note_type TEXT NOT NULL,
            body TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES generation_projects(project_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generation_project_notes_project "
        "ON generation_project_notes(project_id, note_type)"
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS generation_assets (
            asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            asset_type TEXT NOT NULL,
            title TEXT,
            audio_path TEXT NOT NULL,
            fingerprint_sha256 TEXT,
            metadata_json TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (project_id) REFERENCES generation_projects(project_id) ON DELETE CASCADE
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generation_assets_project "
        "ON generation_assets(project_id, asset_type)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generation_assets_fingerprint "
        "ON generation_assets(fingerprint_sha256)"
    )

    job_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(generation_jobs)"
        ).fetchall()
    }
    if "project_id" not in job_columns:
        conn.execute(
            "ALTER TABLE generation_jobs ADD COLUMN project_id INTEGER"
        )

    columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(generation_outputs)"
        ).fetchall()
    }
    if "fingerprint_sha256" not in columns:
        conn.execute(
            "ALTER TABLE generation_outputs ADD COLUMN fingerprint_sha256 TEXT"
        )
    if "project_id" not in columns:
        conn.execute(
            "ALTER TABLE generation_outputs ADD COLUMN project_id INTEGER"
        )
    if "stage" not in columns:
        conn.execute(
            "ALTER TABLE generation_outputs ADD COLUMN stage TEXT NOT NULL DEFAULT 'generation'"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generation_outputs_project "
        "ON generation_outputs(project_id, stage)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_generation_outputs_fingerprint "
        "ON generation_outputs(fingerprint_sha256)"
    )
    conn.commit()

def initialize_database():
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        run_migrations(conn)

        # Rebuild the canonical audio_features projection from the
        # source-specific tables. FreqBlog wins field-by-field; SongBPM
        # fills only fields still missing.
        conn.execute(
            """
            INSERT INTO audio_features (
                track_id, duration_ms, tempo, key, mode, loudness,
                energy, danceability, valence, acousticness,
                instrumentalness, speechiness, source, confidence, updated_at
            )
            SELECT
                t.track_id,
                t.duration_ms,
                COALESCE(f.tempo, s.tempo),
                COALESCE(f.key, s.key),
                COALESCE(f.mode, s.mode),
                COALESCE(f.loudness, s.loudness),
                COALESCE(f.energy, s.energy),
                COALESCE(f.danceability, s.danceability),
                COALESCE(f.valence, s.valence),
                COALESCE(f.acousticness, s.acousticness),
                COALESCE(f.instrumentalness, s.instrumentalness),
                COALESCE(f.speechiness, s.speechiness),
                CASE WHEN f.track_id IS NOT NULL THEN 'freqblog' ELSE 'songbpm' END,
                COALESCE(f.confidence, s.confidence),
                CURRENT_TIMESTAMP
            FROM tracks t
            LEFT JOIN audio_feature_sources f
              ON f.track_id = t.track_id AND f.source = 'freqblog'
            LEFT JOIN audio_feature_sources s
              ON s.track_id = t.track_id AND s.source = 'songbpm'
            WHERE f.track_id IS NOT NULL OR s.track_id IS NOT NULL
            ON CONFLICT(track_id) DO UPDATE SET
                duration_ms = excluded.duration_ms,
                tempo = excluded.tempo,
                key = excluded.key,
                mode = excluded.mode,
                loudness = excluded.loudness,
                energy = excluded.energy,
                danceability = excluded.danceability,
                valence = excluded.valence,
                acousticness = excluded.acousticness,
                instrumentalness = excluded.instrumentalness,
                speechiness = excluded.speechiness,
                source = excluded.source,
                confidence = excluded.confidence,
                updated_at = excluded.updated_at
            """
        )
        conn.commit()


def database_path():
    return DB_PATH
