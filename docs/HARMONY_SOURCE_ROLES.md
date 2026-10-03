# Jacques Harmony Source Roles

Jacques treats chord/progression data as evidence rather than official ground truth.

## Source classes

### 1. Human-authored chord charts
Examples include chord/tab websites and other published transcriptions.

- Strength: can preserve intentional harmonic naming, inversions, extensions, and song-level musical context.
- Weakness: transcription quality varies; public APIs may be unofficial or third-party.
- Rule: never label these as official artist/publisher notation unless the source itself is an official score or release document.

### 2. Independent audio chord-recognition models
Examples include Chordino, librosa/template analysis, and future large-vocabulary models such as LV-Chordia.

- Strength: independent evidence derived directly from audio.
- Weakness: model errors, especially around inversions, extensions, borrowed chords, and root ambiguity.
- Rule: store model name/version and preserve raw output.

### 3. Future official/primary notation
Licensed scores, official songbooks, publisher notation, or artist-provided material can be treated as a distinct primary-source class.

- Rule: primary notation is evidence with higher provenance, but Jacques still records the exact source and does not silently overwrite conflicting evidence.

## Consensus

The fusion layer considers up to five independent source streams. At least two distinct streams must agree on the same chord identity (root + quality + bass) before a chord is promoted into the consensus progression.

Pitch-set-equivalent names are not merged. For example, Am7 and C6 may contain the same pitch classes while implying different roots and harmonic functions. Jacques preserves both the selected identity and the competing interpretation.

## Planned knowledge layer

Future harmony enrichment should add:

- chord construction and pitch-class sets
- root / inversion / bass distinctions
- Roman numerals
- tonic / predominant / dominant function
- secondary dominants
- modal interchange / borrowed chords
- cadences and common progression patterns
- voice-leading observations
- section-level harmonic behavior
- confidence and provenance

This layer is analytical metadata and should remain separate from source transcription evidence.
