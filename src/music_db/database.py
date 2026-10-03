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
-- HARMONY ANALYSIS
-- External chord sources + audio-derived analysis + fused result
-- ============================================================

CREATE TABLE IF NOT EXISTS harmony_sources (
    harmony_source_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_url TEXT,
    section_name TEXT,
    start_sec REAL,
    end_sec REAL,
    chord TEXT,
    key TEXT,
    confidence REAL,
    raw_data TEXT,
    observed_at TEXT,
    UNIQUE (track_id, source, section_name, start_sec, end_sec, chord),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS harmony_segments (
    segment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    source TEXT NOT NULL,
    section_name TEXT,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    chord TEXT,
    root TEXT,
    quality TEXT,
    bass_note TEXT,
    confidence REAL,
    method TEXT,
    raw_data TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (track_id, source, start_sec, end_sec),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS harmony_consensus (
    consensus_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    section_name TEXT,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    chord TEXT,
    chord_family TEXT,
    confidence REAL,
    agreement REAL,
    source_count INTEGER DEFAULT 0,
    evidence_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE (track_id, section_name, start_sec, end_sec),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS harmony_profiles (
    track_id TEXT PRIMARY KEY,
    key TEXT,
    mode TEXT,
    harmonic_rhythm REAL,
    chord_change_rate REAL,
    loop_bars REAL,
    progression_json TEXT,
    sections_json TEXT,
    extensions_json TEXT,
    bass_motion_json TEXT,
    consensus_confidence REAL,
    analysis_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_harmony_sources_track
    ON harmony_sources(track_id, source);

CREATE INDEX IF NOT EXISTS idx_harmony_segments_track
    ON harmony_segments(track_id, source, start_sec);

CREATE INDEX IF NOT EXISTS idx_harmony_consensus_track
    ON harmony_consensus(track_id, start_sec);

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
    days_on_chart INTEGER,
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
-- MEDIA LINKS / STREAMING-ONLY INPUTS
-- ============================================================

CREATE TABLE IF NOT EXISTS track_media_links (
    link_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    url TEXT NOT NULL,
    media_type TEXT NOT NULL DEFAULT 'track',
    is_primary INTEGER DEFAULT 0,
    status TEXT DEFAULT 'active',
    metadata_json TEXT,
    last_checked_at TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(track_id, provider, url),
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_track_media_links_track ON track_media_links(track_id, provider);
CREATE INDEX IF NOT EXISTS idx_track_media_links_provider ON track_media_links(provider, status);

-- ============================================================
-- MUSIC THEORY / ANALYSIS KNOWLEDGE LAYER
-- Evidence, normalized concepts, and derived analyses are kept
-- separate so measurements never get confused with interpretation.
-- ============================================================

CREATE TABLE IF NOT EXISTS music_analysis_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    source TEXT NOT NULL,
    source_type TEXT NOT NULL,
    method TEXT,
    version TEXT,
    start_sec REAL,
    end_sec REAL,
    payload_json TEXT NOT NULL,
    confidence REAL,
    observed_at TEXT,
    created_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_music_evidence_track_domain
    ON music_analysis_evidence(track_id, domain, start_sec);
CREATE INDEX IF NOT EXISTS idx_music_evidence_source
    ON music_analysis_evidence(source, domain);

CREATE TABLE IF NOT EXISTS music_theory_concepts (
    concept_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    concept_key TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    parent_concept_id INTEGER,
    definition TEXT,
    metadata_json TEXT,
    created_at TEXT,
    FOREIGN KEY (parent_concept_id)
        REFERENCES music_theory_concepts(concept_id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS track_theory_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    concept_id INTEGER,
    domain TEXT NOT NULL,
    observation_type TEXT NOT NULL,
    value_text TEXT,
    value_json TEXT,
    start_sec REAL,
    end_sec REAL,
    confidence REAL,
    evidence_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE,
    FOREIGN KEY (concept_id) REFERENCES music_theory_concepts(concept_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_track_theory_track_domain
    ON track_theory_observations(track_id, domain, start_sec);
CREATE INDEX IF NOT EXISTS idx_track_theory_concept
    ON track_theory_observations(concept_id);

-- Harmony extensions: root/bass/upper-structure are intentionally
-- separate from chord labels so pitch-set equivalents are not merged.
CREATE TABLE IF NOT EXISTS harmony_structures (
    structure_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    root TEXT,
    bass_note TEXT,
    chord_quality TEXT,
    inversion TEXT,
    upper_structure_root TEXT,
    upper_structure_quality TEXT,
    upper_structure_notes TEXT,
    pitch_classes TEXT,
    roman_candidate TEXT,
    function_candidate TEXT,
    secondary_function TEXT,
    borrowed_from_mode TEXT,
    confidence REAL,
    evidence_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_harmony_structures_track
    ON harmony_structures(track_id, start_sec);

CREATE TABLE IF NOT EXISTS melody_analysis (
    melody_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    section_name TEXT,
    start_sec REAL,
    end_sec REAL,
    key_candidate TEXT,
    mode_candidate TEXT,
    scale_candidate TEXT,
    range_semitones REAL,
    mean_midi REAL,
    contour TEXT,
    pitch_class_distribution_json TEXT,
    scale_degree_distribution_json TEXT,
    interval_distribution_json TEXT,
    motif_json TEXT,
    non_chord_tone_json TEXT,
    chord_tone_ratio REAL,
    chromaticism REAL,
    confidence REAL,
    evidence_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_melody_analysis_track
    ON melody_analysis(track_id, start_sec);

CREATE TABLE IF NOT EXISTS scale_mode_analysis (
    analysis_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    section_name TEXT,
    start_sec REAL,
    end_sec REAL,
    tonic TEXT,
    mode TEXT,
    scale TEXT,
    parent_scale TEXT,
    scale_degrees_json TEXT,
    modal_interchange INTEGER DEFAULT 0,
    modulation_from TEXT,
    modulation_to TEXT,
    confidence REAL,
    evidence_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_scale_mode_track
    ON scale_mode_analysis(track_id, start_sec);

-- Production / mixing / mastering measurements.
CREATE TABLE IF NOT EXISTS production_analysis (
    production_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    source TEXT NOT NULL,
    start_sec REAL,
    end_sec REAL,
    sample_rate INTEGER,
    bit_depth INTEGER,
    peak_dbfs REAL,
    true_peak_dbtp REAL,
    rms_dbfs REAL,
    lufs_momentary REAL,
    lufs_short_term REAL,
    lufs_integrated REAL,
    loudness_range_lu REAL,
    vu_reference_dbfs REAL,
    vu_average REAL,
    crest_factor_db REAL,
    dynamic_range_db REAL,
    phase_correlation REAL,
    stereo_width REAL,
    mono_compatibility REAL,
    dc_offset REAL,
    clipping_samples INTEGER,
    eq_balance_json TEXT,
    spectrum_json TEXT,
    dynamics_json TEXT,
    stereo_json TEXT,
    processing_chain_json TEXT,
    confidence REAL,
    evidence_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_production_analysis_track_stage
    ON production_analysis(track_id, stage, start_sec);

CREATE TABLE IF NOT EXISTS production_observations (
    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    track_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    category TEXT NOT NULL,
    parameter TEXT NOT NULL,
    value REAL,
    unit TEXT,
    reference_value REAL,
    reference_unit TEXT,
    interpretation TEXT,
    confidence REAL,
    evidence_json TEXT,
    created_at TEXT,
    FOREIGN KEY (track_id) REFERENCES tracks(track_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_production_observations_track
    ON production_observations(track_id, stage, category);

-- Canonical knowledge about measurement/analysis concepts, kept distinct
-- from per-track observations. This is the future Jacques knowledge graph.
CREATE TABLE IF NOT EXISTS production_theory_concepts (
    concept_id INTEGER PRIMARY KEY AUTOINCREMENT,
    concept_key TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    name TEXT NOT NULL,
    definition TEXT,
    unit TEXT,
    typical_context TEXT,
    relationships_json TEXT,
    created_at TEXT
);

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



-- Remove the deprecated local released-track audio path.
DROP TABLE IF EXISTS audio_library_assets;
DELETE FROM music_analysis_evidence WHERE domain = 'audio_file';
DELETE FROM source_registry WHERE source IN ('local_audio_library', 'acoustid');"""

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
    ('mureka', 'api', 200, 0, 'generation',
     'Cloud music generation provider adapter', CURRENT_TIMESTAMP),
    ('ace_step', 'api', 210, 0, 'generation',
     'ACE-Step 1.5 local/self-hosted generation adapter', CURRENT_TIMESTAMP),
    ('librosa_harmony', 'local', 100, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; use Spotify/YouTube-linked evidence instead', CURRENT_TIMESTAMP),
    ('chordino', 'local', 110, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; use Spotify/YouTube-linked evidence instead', CURRENT_TIMESTAMP),
    ('lv_chordia', 'local', 120, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; use Spotify/YouTube-linked evidence instead', CURRENT_TIMESTAMP),
    ('human_chord_chart', 'web', 130, 0, 'harmony_evidence',
     'Human-authored chord chart evidence; never treated as official notation', CURRENT_TIMESTAMP),
    ('official_notation', 'primary', 5, 0, 'primary_music_evidence',
     'Official score/notation when legitimately available', CURRENT_TIMESTAMP),
    ('melody_analyzer', 'local', 140, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; use Spotify/YouTube-linked evidence instead', CURRENT_TIMESTAMP),
    ('scale_mode_analyzer', 'local', 150, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; use Spotify/YouTube-linked evidence instead', CURRENT_TIMESTAMP),
    ('production_meter', 'local', 160, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; production measurements must come from external/link-based evidence', CURRENT_TIMESTAMP),
    ('spectral_analyzer', 'local', 170, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; production measurements must come from external/link-based evidence', CURRENT_TIMESTAMP),
    ('stereo_analyzer', 'local', 180, 0, 'legacy_audio_analysis',
     'Disabled: Jacques never ingests local audio; production measurements must come from external/link-based evidence', CURRENT_TIMESTAMP),
    ('youtube_link', 'web', 15, 1, 'media_input',
     'YouTube URLs are accepted as analysis inputs; Jacques does not download or store the underlying audio', CURRENT_TIMESTAMP),
    ('chordidentifier', 'web', 25, 1, 'harmony_evidence',
     'YouTube-linked chord timeline provider; preview/full-song availability depends on provider access', CURRENT_TIMESTAMP),
    ('magic_chords', 'web', 26, 1, 'harmony_evidence',
     'YouTube/media-URL chord analysis provider; returns structured chord/key/tempo evidence', CURRENT_TIMESTAMP),
    ('methodic_truth', 'web', 27, 1, 'harmony_evidence',
     'YouTube-linked chord/key/BPM/structure provider; external processing, structured evidence only', CURRENT_TIMESTAMP),
    ('klangio_melody_scanner', 'web', 135, 0, 'melody_evidence',
     'YouTube-linked melody/lead-sheet transcription candidate; disabled until browser automation/export path is verified', CURRENT_TIMESTAMP),
    ('songscription', 'web', 136, 0, 'melody_evidence',
     'YouTube-linked transcription candidate for notes/MIDI/MusicXML; disabled until browser automation/export path is verified', CURRENT_TIMESTAMP),
    ('acousterr', 'web', 137, 0, 'melody_evidence',
     'YouTube-linked notes/chords/key transcription candidate; disabled until browser automation/export path is verified', CURRENT_TIMESTAMP),
    ('youtube_loudness_console', 'web', 165, 0, 'production_evidence',
     'Browser-based YouTube playback metering candidate for LUFS/peak/spectrum/stereo; disabled until reproducible automated extraction is verified', CURRENT_TIMESTAMP),
    ('spotify_stream', 'web', 10, 1, 'media_input',
     'Spotify track/stream URLs identify playback targets; Jacques does not download or store the underlying audio', CURRENT_TIMESTAMP)
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

    # Seed stable music-theory/production vocabulary. Definitions are
    # intentionally compact; detailed knowledge can be expanded later
    # without changing the per-track observation schema.
    concept_seeds = [
        ("harmony", "harmony.triad", "Triad", None, "Three-note chord built from stacked thirds or an equivalent pitch collection."),
        ("harmony", "harmony.seventh_chord", "Seventh chord", "harmony.triad", "Four-note chord containing a seventh above its root."),
        ("harmony", "harmony.upper_structure_triad", "Upper Structure Triad", "harmony.triad", "A triad interpreted above a distinct lower harmonic structure, commonly over a bass/root context."),
        ("harmony", "harmony.inversion", "Inversion", "harmony.triad", "A chord voicing in which a chord tone other than the root occupies the bass."),
        ("harmony", "harmony.slash_chord", "Slash chord", None, "Chord notation that explicitly specifies a bass note; not automatically equivalent to an upper-structure interpretation."),
        ("harmony", "harmony.secondary_dominant", "Secondary dominant", None, "Dominant-function chord tonicizing a diatonic chord other than the global tonic."),
        ("harmony", "harmony.borrowed_chord", "Borrowed chord", None, "Chord borrowed from a parallel mode or tonal collection."),
        ("harmony", "harmony.cadence", "Cadence", None, "Harmonic/phrase closure pattern characterized by its tonal and melodic resolution behavior."),
        ("scale", "scale.major", "Major scale", None, "Seven-note diatonic scale with the major-mode interval pattern."),
        ("scale", "scale.natural_minor", "Natural minor", None, "Aeolian-type seven-note minor collection."),
        ("mode", "mode.ionian", "Ionian", None, "Major-mode diatonic mode."),
        ("mode", "mode.dorian", "Dorian", None, "Minor-mode diatonic mode with a raised sixth relative to natural minor."),
        ("mode", "mode.phrygian", "Phrygian", None, "Minor-mode diatonic mode with a lowered second degree."),
        ("mode", "mode.lydian", "Lydian", None, "Major-mode diatonic mode with a raised fourth degree."),
        ("mode", "mode.mixolydian", "Mixolydian", None, "Major-mode diatonic mode with a lowered seventh degree."),
        ("mode", "mode.aeolian", "Aeolian", None, "Natural-minor mode."),
        ("mode", "mode.locrian", "Locrian", None, "Minor-type diatonic mode with lowered second and fifth degrees."),
        ("scale", "scale.harmonic_minor", "Harmonic minor", None, "Minor scale with a raised seventh degree."),
        ("scale", "scale.melodic_minor", "Melodic minor", None, "Minor-scale collection with raised sixth and seventh degrees in its ascending form."),
        ("scale", "scale.pentatonic", "Pentatonic", None, "Five-note scale family."),
        ("scale", "scale.blues", "Blues scale", None, "Blues-derived scale containing characteristic chromatic inflection."),
        ("scale", "scale.whole_tone", "Whole tone", None, "Symmetric six-note scale built from whole steps."),
        ("scale", "scale.diminished", "Diminished scale", None, "Symmetric alternating whole-step/half-step or half-step/whole-step collection."),
        ("scale", "scale.altered", "Altered scale", None, "Dominant-oriented scale containing altered extensions."),
        ("melody", "melody.chord_tone", "Chord tone", None, "Melodic pitch belonging to the active harmonic structure."),
        ("melody", "melody.non_chord_tone", "Non-chord tone", None, "Melodic pitch not belonging to the active harmonic structure, interpreted by context."),
        ("melody", "melody.passing_tone", "Passing tone", "melody.non_chord_tone", "Stepwise non-chord tone connecting two chord tones."),
        ("melody", "melody.neighbor_tone", "Neighbor tone", "melody.non_chord_tone", "Non-chord tone that departs from and returns to a neighboring chord tone."),
        ("production", "production.vu_meter", "VU meter", None, "Average-oriented level measurement with a defined calibration/reference context."),
        ("production", "production.lufs_integrated", "Integrated LUFS", None, "Integrated loudness measurement over a defined program duration."),
        ("production", "production.lufs_short_term", "Short-term LUFS", None, "Loudness measurement over a short moving time window."),
        ("production", "production.true_peak", "True peak", None, "Estimated inter-sample peak level, normally reported in dBTP."),
        ("production", "production.crest_factor", "Crest factor", None, "Difference or ratio between peak magnitude and an average level measure, depending on definition."),
        ("production", "production.dynamic_range", "Dynamic range", None, "Description of level variation over a defined signal or program context."),
        ("production", "production.eq_balance", "EQ balance", None, "Distribution of spectral energy across frequency regions, interpreted relative to context/reference."),
        ("production", "production.phase_correlation", "Phase correlation", None, "Measure describing similarity/opposition of left/right signal phase relationships."),
        ("production", "production.mono_compatibility", "Mono compatibility", None, "How the stereo mix behaves when summed or reproduced in mono."),
    ]
    for domain, key, name, parent_key, definition in concept_seeds:
        parent_id = None
        if parent_key:
            parent_row = conn.execute(
                "SELECT concept_id FROM music_theory_concepts WHERE concept_key = ?",
                (parent_key,),
            ).fetchone()
            parent_id = parent_row[0] if parent_row else None
        conn.execute(
            """
            INSERT INTO music_theory_concepts
                (domain, concept_key, name, parent_concept_id, definition, created_at)
            VALUES (?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(concept_key) DO UPDATE SET
                domain = excluded.domain,
                name = excluded.name,
                definition = excluded.definition,
                parent_concept_id = excluded.parent_concept_id
            """,
            (domain, key, name, parent_id, definition),
        )

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
    chart_columns = {
        row[1] for row in conn.execute(
            "PRAGMA table_info(chart_entries)"
        ).fetchall()
    }
    if "days_on_chart" not in chart_columns:
        conn.execute(
            "ALTER TABLE chart_entries ADD COLUMN days_on_chart INTEGER"
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
