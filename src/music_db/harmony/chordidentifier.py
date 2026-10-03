from __future__ import annotations

import re
from html import unescape
from typing import Any


_CHORD_RE = re.compile(
    r"<span[^>]*>\s*"
    r"([A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add|\d|\+|-|/)*?)"
    r"<br\s*/?>\s*</span>",
    re.I,
)
_REGION_RE = re.compile(
    r"<region\b[^>]*\btitle=[\"']([^\"']+)[\"'][^>]*>",
    re.I,
)


def _parse_time(value: str) -> float:
    parts = value.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    if len(parts) == 3:
        hours, minutes, seconds = parts
        return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
    return float(value)


def parse_chordidentifier_html(html: str) -> list[dict[str, Any]]:
    """Extract the visible chord timeline emitted by ChordIdentifier's YouTube UI."""
    labels = [unescape(x).strip() for x in _CHORD_RE.findall(html)]
    regions = _REGION_RE.findall(html)

    segments: list[dict[str, Any]] = []
    for chord, region in zip(labels, regions):
        match = re.match(r"^\s*(\d+(?::\d+)?(?:\.\d+)?)\s*-\s*(\d+(?::\d+)?(?:\.\d+)?)\s*$", region)
        if not match:
            continue
        start_sec = _parse_time(match.group(1))
        end_sec = _parse_time(match.group(2))
        if end_sec <= start_sec or not chord:
            continue
        segments.append({
            "start_sec": start_sec,
            "end_sec": end_sec,
            "chord": chord,
            "confidence": None,
            "method": "chordidentifier_youtube",
            "raw_region": region,
        })
    return segments


def build_harmony_payload(
    html: str,
    *,
    source_url: str,
    youtube_url: str,
) -> dict[str, Any]:
    segments = parse_chordidentifier_html(html)
    return {
        "source": "chordidentifier",
        "source_url": source_url,
        "youtube_url": youtube_url,
        "confidence": None,
        "segments": segments,
        "segment_count": len(segments),
        "method": "youtube_browser_analysis",
    }
