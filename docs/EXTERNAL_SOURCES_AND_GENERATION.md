# Jacques external data and generation architecture

## Source layers

- Spotify: canonical listening identity and user-state source.
- FreqBlog: primary audio-feature source.
- SongBPM: fallback only after terminal FreqBlog status not_found.
- Last.fm: community tags and secondary metadata.
- MusicBrainz: identity, MBIDs, ISRC, release/relationship metadata.
- Billboard: weekly chart-history adapter. It does not depend on a stable public Billboard API.
- Pitchfork: official RSS + metadata-only review adapter. Review body text is intentionally not copied into Jacques.
- Apple Music: catalog/search/charts adapter when APPLE_MUSIC_DEVELOPER_TOKEN exists.
- Discogs: optional release/label/credit metadata when DISCOGS_TOKEN exists.
- AcoustID: reserved for local-audio fingerprint identification.
- RHYTHMER: reserved production-metadata slot.

## Editorial sync

.github/workflows/editorial.yml runs weekly and writes:

- chart_entries
- review_entries
- source_records

Matching is best-effort and never replaces Spotify identity.

## Generation layer

src/music_db/generation/ is provider-neutral.

Current adapters:

- mureka.py: Mureka cloud generation.
- ace_step.py: ACE-Step 1.5 HTTP generation and polling.

Generation lineage is stored in:

- generation_jobs
- generation_outputs
- generation_analysis
- generation_relations

This supports:

Spotify Track -> reference -> Generation Job -> Generated Audio -> Analysis -> next generation

## Secrets

Optional GitHub Actions secrets:

- APPLE_MUSIC_DEVELOPER_TOKEN
- DISCOGS_TOKEN

Generation credentials are intentionally not used by scheduled DB jobs:

- MUREKA_API_KEY
- ACESTEP_API_KEY
- ACESTEP_BASE_URL

They will be consumed only when a generation job is explicitly submitted.

## Important source-policy decisions

Billboard currently has no stable public API that Jacques should depend on, so the adapter targets the public chart page and fails soft.

Pitchfork exposes official RSS feeds. Jacques stores review metadata (score/date/author/URL) rather than reproducing review text.

Apple Music API is appropriate for catalog and chart queries. Apple Music Feed bulk exports are intentionally not used because Apple documents restrictions on using Feed data for internal systems/analysis.


## 2026-09 expansion: generation execution + audio intelligence

Jacques now includes a provider-neutral generation execution layer:

- `tools/generate.py` creates generation jobs and can submit to Mureka or ACE-Step 1.5.
- `generation_outputs` stores generated audio paths/URLs.
- `generation_analysis` stores analysis payloads separately from the output record.
- `generation_relations` supports lineage such as reference → generated, generated → edited, and version-to-version derivation.
- `src/music_db/generation/audio_analysis.py` provides optional local analysis for BPM, estimated key, loudness/RMS, crest factor, spectral centroid, spectral rolloff, zero-crossing rate, onset rate, and beat count.
- The optional audio stack is isolated in `requirements-audio.txt` so the normal Spotify/editorial GitHub runners do not need heavy DSP packages.

## Audio identity

AcoustID is implemented as an optional local-file identity layer. It uses Chromaprint/fpcalc to fingerprint an actual audio file and then queries AcoustID for MusicBrainz-linked identity. It is deliberately not part of the Spotify metadata schedule because Spotify catalog metadata alone is not an audio fingerprint.

AcoustID's public service is rate-limited to 3 requests/second and is free for non-commercial use; commercial deployment requires registration.

## External-source boundary

Apple Music remains API-first for catalog/charts; its official API supports catalog songs, albums, artists, search, charts and storefront-specific catalog access.

Beatport has an official v4 developer portal, but the current portal requires Beatport login. Jacques therefore does not invent undocumented endpoints or scrape it as a pretend API. A dedicated Beatport adapter can be added once authenticated endpoint details/credentials are available.

Bandcamp's documented API is primarily for labels/merchandise fulfillment and requires approved OAuth access, so it is not treated as a general public catalog API for Jacques.

MusicBrainz requests must be rate-aware; Jacques should use a meaningful User-Agent and avoid synchronized bulk polling.

## Automated quality gate

`.github/workflows/quality.yml` now runs on relevant pushes and manual dispatch. It compiles the codebase, imports the major modules, initializes/checks the SQLite schema, runs `PRAGMA integrity_check`, and executes the existing DB quality checks.


## Source registry

`source_registry.enabled` is persistent operator state. Database initialization refreshes source metadata without overwriting an existing enabled/disabled choice.

The live enrichment dispatcher, bulk enrichment engine, editorial sync, and AcoustID identification honor this flag. SongBPM remains eligible only after a terminal FreqBlog `not_found`; transient FreqBlog errors do not trigger fallback. Disabled sources are skipped without creating API calls.
