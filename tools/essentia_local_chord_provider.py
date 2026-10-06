from __future__ import annotations

import argparse
import json
import subprocess
import tempfile
from pathlib import Path

import essentia.standard as es

SAMPLE_RATE = 44100
FRAME_SIZE = 4096
HOP_SIZE = 2048


def download_audio(url: str, output_dir: Path) -> Path:
    output = output_dir / "audio.%(ext)s"
    subprocess.run(
        [
            "yt-dlp", "--no-playlist", "--no-warnings",
            "--extract-audio", "--audio-format", "wav", "--audio-quality", "0",
            "--output", str(output), url,
        ],
        check=True, capture_output=True, text=True,
    )
    candidates = sorted(output_dir.glob("audio.*"))
    if not candidates:
        raise RuntimeError("yt-dlp completed without producing an audio file")
    return candidates[0]


def analyze_audio(audio_path: Path) -> dict:
    audio = es.MonoLoader(filename=str(audio_path), sampleRate=SAMPLE_RATE)()
    spectrum = es.Spectrum(size=FRAME_SIZE)
    window = es.Windowing(type="blackmanharris62")
    peaks = es.SpectralPeaks(
        orderBy="magnitude", magnitudeThreshold=0.00001,
        minFrequency=20, maxFrequency=5000, maxPeaks=60,
    )
    hpcp = es.HPCP(
        size=12, referenceFrequency=440, harmonics=8, bandPreset=True,
        minFrequency=20, maxFrequency=5000, weightType="cosine", nonLinear=True,
    )

    hpcps = []
    for frame in es.FrameGenerator(
        audio, frameSize=FRAME_SIZE, hopSize=HOP_SIZE, startFromZero=True
    ):
        spec = spectrum(window(frame))
        frequencies, magnitudes = peaks(spec)
        hpcps.append(hpcp(frequencies, magnitudes))

    if len(hpcps) < 2:
        raise RuntimeError("Audio is too short for HPCP/chord analysis")

    bpm, ticks, beat_confidence, _, _ = es.RhythmExtractor2013(
        method="multifeature"
    )(audio)
    duration = len(audio) / SAMPLE_RATE
    ticks = sorted(set(
        float(x) for x in ticks if 0.0 <= float(x) < duration
    ))
    if not ticks or ticks[0] > 0.05:
        ticks = [0.0] + ticks
    else:
        ticks[0] = 0.0
    if len(ticks) < 2:
        raise RuntimeError("Beat tracker did not return enough beat positions")

    chords, strengths = es.ChordsDetectionBeats(
        hpcps, ticks, chromaPick="interbeat_median",
        hopSize=HOP_SIZE, sampleRate=SAMPLE_RATE,
    )

    segments = []
    for i, chord in enumerate(chords):
        start = ticks[i]
        end = ticks[i + 1] if i + 1 < len(ticks) else duration
        if end <= start:
            continue
        strength = float(strengths[i]) if i < len(strengths) else None
        segments.append({
            "start_sec": round(start, 4),
            "end_sec": round(min(end, duration), 4),
            "chord": str(chord),
            "confidence": strength,
            "method": "essentia_chords_detection_beats",
        })

    return {
        "source": "essentia_local",
        "source_url": "https://essentia.upf.edu/",
        "confidence": None,
        "tempo": float(bpm),
        "tempo_confidence": float(beat_confidence),
        "segments": segments,
        "segment_count": len(segments),
        "audio_duration_sec": round(duration, 4),
        "sample_rate": SAMPLE_RATE,
        "method": "local_audio_essentia_hpcp_chords_detection_beats",
        "model_family": "Essentia ChordsDetectionBeats",
        "limitations": ["triad-level major/minor chord vocabulary", "experimental chord estimator"],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="jacques-essentia-") as temp:
        audio_path = download_audio(args.url, Path(temp))
        payload = analyze_audio(audio_path)
    payload["youtube_url"] = args.url
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
