from __future__ import annotations

import re
from html import unescape
from typing import Any


_TIME_RANGE_RE = re.compile(
    r"^\s*(\d+(?::\d+)?(?:\.\d+)?)\s*-\s*(\d+(?::\d+)?(?:\.\d+)?)\s*$"
)
_CHORD_VALUE_RE = re.compile(
    r"^[A-G](?:#|b)?(?:maj|min|m|dim|aug|sus|add|\d|\+|-|/)*(?:\(.*\))?$",
    re.I,
)
_REGION_RE = re.compile(
    r"<region\b[^>]*\btitle=[\"']([^\"']+)[\"'][^>]*>",
    re.I,
)
_MARKER_RE = re.compile(
    r"<span\b[^>]*>(.*?)<br\s*/?>\s*</span>",
    re.I | re.S,
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


def _clean_chord(value: str) -> str:
    value = unescape(re.sub(r"<[^>]+>", "", value))
    return re.sub(r"\s+", " ", value).strip()


def parse_chordidentifier_html(html: str) -> list[dict[str, Any]]:
    """Extract timeline by pairing each region with its preceding marker."""
    segments: list[dict[str, Any]] = []
    cursor = 0
    for region in _REGION_RE.finditer(html):
        before = html[cursor:region.start()]
        markers = list(_MARKER_RE.finditer(before))
        if not markers:
            cursor = region.end()
            continue
        chord = _clean_chord(markers[-1].group(1))
        match = _TIME_RANGE_RE.match(region.group(1))
        if not match:
            cursor = region.end()
            continue
        start_sec = _parse_time(match.group(1))
        end_sec = _parse_time(match.group(2))
        if end_sec <= start_sec or not _CHORD_VALUE_RE.match(chord):
            cursor = region.end()
            continue
        segments.append({
            "start_sec": start_sec,
            "end_sec": end_sec,
            "chord": chord,
            "confidence": None,
            "method": "chordidentifier_youtube",
            "raw_region": region.group(1),
        })
        cursor = region.end()
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
        "method": "youtube_browser_analysis",
    }
