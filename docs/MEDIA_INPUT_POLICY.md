# Jacques media input policy

Jacques is **streaming/link-only** for analysis of released music.

## Allowed inputs

- Spotify track/stream URLs
- YouTube / YouTube Music URLs
- External web/API evidence derived from those links
- Human-authored or official web notation/chord evidence
- Spotify catalog/listening metadata

## Explicitly prohibited

Jacques must not ingest, download, fingerprint, cache, or analyze a local copy of a released track's audio.

Do not add a local path such as `~/Music/Jacques` as a track-analysis input.

## Analysis model

`Spotify/YouTube URL → provider/web analysis → raw evidence → consensus → theory/production interpretation → DB → Obsidian`

A provider may internally process media under its own service terms, but Jacques itself stores the **URL and returned evidence**, not the underlying audio.

## Production measurements

LUFS, VU, true peak, RMS, spectrum, phase, stereo width, EQ balance, etc. must come from an external/link-based provider or documented source. Jacques must not calculate these by downloading a released track.

## Evidence rule

Measurements, provider outputs, and interpretations remain separate. Provider disagreement is preserved rather than silently merged.
