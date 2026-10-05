from __future__ import annotations

import math

from .pipeline import QUALITY_INTERVALS, _chord_identity_key


PARSER_VERSION = "harmony-provider-normalizer-v1"
DATA_STATUSES = {"success_with_data", "success_empty"}


def normalize_provider_result(provider: str, result: dict, duration_sec=None):
    """Validate a provider's parsed timeline and assign a data-aware state."""
    result = dict(result or {})
    payload = result.get("harmony_payload")
    if payload is None and isinstance(result.get("segments"), list):
        payload = result
    if payload is not None and not isinstance(payload, dict):
        return result, {
            "status": "invalid_result",
            "segments": [],
            "error_type": "InvalidPayload",
            "error_message": "Harmony payload must be an object.",
            "raw_result_available": True,
        }

    source_status = str(result.get("status") or "").lower()
    if payload is None:
        if source_status in {"unsupported_provider", "not_configured"}:
            status = "not_configured"
        elif source_status in {"not_found", "no_match"}:
            status = "not_found"
        elif source_status in {"timeout", "poll_timeout"}:
            status = "timeout"
        elif source_status in {"accepted_or_processing", "submitted_unknown_result"}:
            status = "timeout"
        elif source_status in {"exception", "rate_limited", "result_fetch_error", "provider_error_or_rejection"}:
            status = "failed"
        else:
            status = "invalid_result"
        return result, {
            "status": status,
            "segments": [],
            "error_type": result.get("error_type"),
            "error_message": result.get("error"),
            "raw_result_available": bool(result.get("final_text_excerpt") or result.get("initial_text_excerpt")),
        }

    raw_segments = payload.get("segments", payload.get("chords"))
    if raw_segments is None:
        raw_segments = []
    if not isinstance(raw_segments, list):
        return result, {
            "status": "invalid_result",
            "segments": [],
            "error_type": "InvalidTimeline",
            "error_message": "Provider timeline must be a list.",
            "raw_result_available": True,
        }

    normalized = []
    try:
        invalid = int(payload.get("invalid_segment_count") or 0)
        raw_segment_count = int(payload.get("raw_segment_count", len(raw_segments)))
        if invalid < 0 or raw_segment_count < 0:
            raise ValueError
    except (TypeError, ValueError, OverflowError):
        return result, {
            "status": "invalid_result",
            "segments": [],
            "error_type": "InvalidTimelineMetadata",
            "error_message": "Provider timeline counts must be non-negative integers.",
            "raw_result_available": True,
        }
    for segment in raw_segments:
        if not isinstance(segment, dict):
            invalid += 1
            continue
        chord = str(segment.get("chord") or segment.get("symbol") or segment.get("label") or "").strip()
        try:
            start = float(segment.get("start_sec", segment.get("start")))
            end = float(segment.get("end_sec", segment.get("end")))
        except (TypeError, ValueError):
            invalid += 1
            continue
        if (
            not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
            or (duration_sec is not None and end > duration_sec + 2.0)
            or _chord_identity_key(chord) is None
            or _chord_identity_key(chord)[1] not in QUALITY_INTERVALS
        ):
            invalid += 1
            continue
        confidence = segment.get("confidence", payload.get("confidence"))
        if confidence is not None:
            try:
                confidence = float(confidence)
            except (TypeError, ValueError):
                invalid += 1
                continue
            if not math.isfinite(confidence) or not 0 <= confidence <= 1:
                invalid += 1
                continue
        normalized.append(
            {
                **segment,
                "start_sec": start,
                "end_sec": end,
                "chord": chord,
                "confidence": confidence,
                "method": segment.get("method") or payload.get("method") or provider,
            }
        )

    normalized.sort(key=lambda item: (item["start_sec"], item["end_sec"], item["chord"]))
    unique = []
    seen = set()
    duplicate_count = 0
    for segment in normalized:
        key = (segment["start_sec"], segment["end_sec"], segment["chord"])
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        unique.append(segment)

    result_payload = dict(payload)
    result_payload["segments"] = unique
    result_payload["segment_count"] = len(unique)
    result_payload["parser_version"] = (
        result_payload.get("parser_version") or PARSER_VERSION
    )
    result["harmony_payload"] = result_payload
    source_status = str(payload.get("status") or result.get("status") or "").lower()
    if unique:
        state = "success_with_data"
    elif invalid or duplicate_count or (
        provider == "chordidentifier" and payload.get("raw_region_count", 0)
    ):
        state = "invalid_result"
    elif source_status in {"success", "completed", "complete", "done"}:
        state = "success_empty"
    elif source_status in {"accepted_or_processing", "submitted_unknown_result"}:
        state = "timeout"
    else:
        state = "invalid_result"

    error_message = None
    error_type = None
    if invalid:
        error_type = "InvalidSegments"
        error_message = f"Rejected {invalid} malformed provider segment(s)."
    elif duplicate_count:
        error_type = "DuplicateSegments"
        error_message = f"Removed {duplicate_count} duplicate provider segment(s)."

    return result, {
        "status": state,
        "segments": unique,
        "error_type": error_type,
        "error_message": error_message,
        "raw_result_available": bool(
            raw_segments is not None or raw_segment_count > 0
        ),
        "invalid_segment_count": invalid,
        "duplicate_segment_count": duplicate_count,
        "parser_version": PARSER_VERSION,
    }
