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
