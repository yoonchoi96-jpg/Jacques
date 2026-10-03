# Jacques Melody Provider Policy

Jacques accepts released-track analysis inputs only through Spotify track identity/streaming URLs and YouTube URLs.

## Hard rule

Jacques must not:

- download released-track audio locally;
- store released-track audio files;
- run local DSP/transcription on released-track audio;
- use yt-dlp or equivalent local extraction as an analysis dependency.

An external provider may fetch/process the media internally when its service accepts the public URL. Jacques stores only structured evidence, provenance, timestamps, confidence, and provider result URLs/IDs.

## Melody candidates

### Klangio Melody Scanner

Klangio documents YouTube import and Lead Sheet Mode for generating chords and melodies. Its Universal Mode can transcribe a selected instrument from a YouTube URL. It currently requires browser/account interaction and has no verified Jacques API/export automation path, so the source is registered but disabled.

### Songscription

Songscription documents YouTube URL transcription for piano, guitar, vocals and other instruments, with sheet music, MIDI and MusicXML outputs. The source is registered but disabled until a stable browser automation/export path is verified.

### Acousterr

Acousterr documents YouTube URL transcription with notes, chords, BPM and key. The source is registered but disabled until a stable automated result extraction path is verified.

## Evidence rule

When a provider becomes operational, raw provider output belongs in `music_analysis_evidence` under the relevant melody domain. Normalized observations belong in `melody_analysis`, `scale_mode_analysis`, and `track_theory_observations`.

Provider estimates are evidence, not ground truth. Melody/chord disagreements must remain visible for later consensus.
