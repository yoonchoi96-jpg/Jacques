# Jacques Generation Pipeline

## Flow

Spotify/reference metadata and optional local audio feed a provider-neutral generation job:

1. Create a `generation_jobs` row.
2. Submit to Mureka or ACE-Step 1.5.
3. Normalize provider output references.
4. Download generated audio into `generated_audio/`.
5. Create `generation_outputs`.
6. Compute SHA-256 audio identity.
7. Run local DSP analysis.
8. Store versioned analysis in `generation_analysis`.
9. Link outputs with explicit lineage relations.
10. Export lineage to Obsidian.

## Audio identity

Local generated/reference audio is identified by SHA-256. The hash is stored on
`generation_outputs.fingerprint_sha256` and indexed for duplicate detection.

## Analysis versions

- `audio_features_v2`: core loudness, spectral, onset, tempo and key metrics.
- `audio_features_v3`: v2 plus estimated dynamic range, key confidence,
  MFCC summaries and spectral-contrast summaries.

Analysis is deliberately versioned so future DSP upgrades do not silently
overwrite historical measurements.

## Lineage

Supported relation types:

- `reference_of`
- `generated_from`
- `edited_from`
- `variant_of`
- `finalized_from`

Use `tools/link_generation.py` to create or update a relation.

## Commands

Dry run:

```bash
PYTHONPATH=src python tools/generate.py --provider mureka --prompt "..." --dry-run
```

Analyze existing audio:

```bash
PYTHONPATH=src python tools/generate.py --provider mureka --audio /path/to/file.wav --dry-run
```

Fingerprint an existing output:

```bash
PYTHONPATH=src python tools/fingerprint_audio.py --output-id 1 --audio /path/to/file.wav
```

Generation health:

```PYTHONPATH=src python tools/generation_health.py --json
```

Source health:

```PYTHONPATH=src python tools/source_health.py --json
```

## Providers

### Mureka

Requires `MUREKA_API_KEY`.

### ACE-Step

Defaults to `http://127.0.0.1:8000`. Override with
`ACESTEP_BASE_URL`; optionally set `ACESTEP_API_KEY`.

Provider adapters must remain isolated from the database schema and use the
provider-neutral generation job/output layer.

## Operational rule

Generated audio and analysis are local/provenance data. Provider responses,
external URLs and analysis payloads are retained as metadata; provider identity
is never confused with the canonical Spotify track identity.
