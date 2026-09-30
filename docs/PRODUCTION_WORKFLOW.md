# Jacques Production Workflow

Jacques treats a music production as a persistent project rather than a single generation request.

## 1. Create a project

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

## 2. Import reference audio

```bash
python tools/import_audio.py <project_id> /path/to/reference.wav --type reference
```

The source is copied into the project and fingerprinted with SHA-256.

## 3. Generate

```bash
python tools/generate.py \
  --provider mureka \
  --project "My Track" \
  --stage generations \
  --prompt "..."
```

Generated outputs are attached to the project, fingerprinted, analyzed, and recorded in SQLite.

ACE-Step uses the same project storage:

```bash
python tools/generate.py \
  --provider ace_step \
  --project "My Track" \
  --stage generations \
  --prompt "..."
```

## 4. Continue a lineage

Pass the parent job when a generation is derived from an earlier generation. Jacques records the output relation so the production tree can be reconstructed later.

## 5. Promote a version

Move/copy a chosen output into another project stage:

```bash
python tools/promote_output.py <output_id> --stage edits
python tools/promote_output.py <output_id> --stage final
```

Only one output may occupy the `final` stage of a project at a time.

## 6. Track production decisions

```bash
python tools/project_note.py <project_id> "Keep the second chorus arrangement; reduce vocal reverb."
```

Notes are stored in SQLite and exported to Obsidian.

## 7. Inspect the project

```bash
python tools/project_status.py <project_id>
python tools/project_status.py <project_id> --json
```

## 8. Export to Obsidian

The existing Obsidian export includes project metadata, assets, production notes, generation outputs, fingerprints, and stages.

## Storage principle

- Local project directory: authoritative location for production audio files.
- SQLite: authoritative production metadata, fingerprints, analysis, jobs, outputs, relations, and notes.
- Obsidian: research/knowledge view of the production history.
- GitHub: Jacques code, schema, tests, and documentation; production audio is excluded from Git.

This keeps large audio files local while retaining a complete machine-readable production history.
