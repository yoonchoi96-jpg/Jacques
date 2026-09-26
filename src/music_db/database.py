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


def initialize_database():
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        conn.commit()


def database_path():
    return DB_PATH
