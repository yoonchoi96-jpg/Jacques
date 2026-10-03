# Jacques Production Workflow

Jacques has two distinct media paths:

1. **Released-track analysis** is streaming/link-only.
2. **Generated production projects** may store local generated/reference assets when they are owned project assets.

## Released-track analysis

```text
Spotify track URL / YouTube URL
        ↓
external provider
        ↓
raw evidence
        ↓
consensus
        ↓
music-theory / production interpretation
        ↓
SQLite
        ↓
Obsidian
```

Do not download, cache, fingerprint, or locally DSP-analyze released music. A provider may process a public URL on its own infrastructure, but Jacques stores only the link and returned structured evidence.

## 1. Create a generation project

```bash
python tools/create_project.py "My Track"
```

This creates:

```text
projects/My_Track/
├── project.json
├── reference/
├── generations/
├── edits/
└── final/
```

The project directories are for **owned/generated production assets**, not for downloading released reference tracks.

## 2. Generate

Mureka:

```bash
python tools/generate.py \
  --provider mureka \
  --project "My Track" \
  --stage generations \
  --prompt "..."
```

ACE-Step:

```bash
python tools/generate.py \
  --provider ace_step \
  --project "My Track" \
  --stage generations \
  --prompt "..."
```

Generated outputs are attached to the project and recorded in SQLite.

## 3. Continue a lineage

Pass the parent job when a generation is derived from an earlier generation. Jacques records the output relation so the production tree can be reconstructed later.

## 4. Promote a version

```bash
python tools/promote_output.py <output_id> --stage edits
python tools/promote_output.py <output_id> --stage final
```

Only one output may occupy the `final` stage of a project at a time.

## 5. Track production decisions

```bash
python tools/project_note.py <project_id> "Keep the second chorus arrangement; reduce vocal reverb."
```

Notes are stored in SQLite and exported to Obsidian.

## 6. Inspect the project

```bash
python tools/project_status.py <project_id>
python tools/project_status.py <project_id> --json
```

## 7. Analyze released references

For released tracks, register the Spotify/YouTube link and let an external provider produce evidence.

```bash
python tools/register_media_link.py <track_id> <spotify-or-youtube-url>
```

External analysis results belong in:

- `music_analysis_evidence`
- `harmony_segments`
- `harmony_consensus`
- `harmony_profiles`
- `harmony_structures`
- `melody_analysis`
- `scale_mode_analysis`
- `production_analysis`

The underlying released audio must not be stored by Jacques.

## 8. Export to Obsidian

The Obsidian export is the knowledge/research view of the database. Generated production metadata, notes, lineage, and analysis evidence can be exported without making local released-track audio part of the export.

## Storage principle

- **Generated project directory:** authoritative location for owned/generated production assets.
- **SQLite:** authoritative production metadata, analysis evidence, jobs, outputs, relations, and notes.
- **Obsidian:** research/knowledge view.
- **GitHub:** Jacques code, schema, tests, and documentation.

This separation keeps the released-music analysis pipeline streaming-only while allowing the generation subsystem to maintain its own owned local assets.