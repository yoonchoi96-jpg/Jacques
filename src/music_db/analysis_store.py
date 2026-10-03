"""Common persistence helpers for Jacques music analysis layers.

This module deliberately keeps evidence, observations, and interpretations
separate. Analysis engines can emit JSON payloads without needing to know
the database schema beyond these small adapters.
"""
import json
from typing import Any, Iterable

from .database import get_connection


def store_evidence(
    track_id: str,
    domain: str,
    source: str,
    source_type: str,
    payload: dict[str, Any],
    *,
    method: str | None = None,
    version: str | None = None,
    start_sec: float | None = None,
    end_sec: float | None = None,
    confidence: float | None = None,
) -> int:
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO music_analysis_evidence
              (track_id, domain, source, source_type, method, version,
               start_sec, end_sec, payload_json, confidence, observed_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                track_id, domain, source, source_type, method, version,
                start_sec, end_sec, json.dumps(payload, ensure_ascii=False),
                confidence,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def store_observation(
    track_id: str,
    domain: str,
    observation_type: str,
    *,
    concept_key: str | None = None,
    value_text: str | None = None,
    value: dict[str, Any] | list[Any] | None = None,
    start_sec: float | None = None,
    end_sec: float | None = None,
    confidence: float | None = None,
    evidence_ids: Iterable[int] = (),
) -> int:
    evidence_json = json.dumps(
        {"evidence_ids": list(evidence_ids)}, ensure_ascii=False
    )
    with get_connection() as conn:
        concept_id = None
        if concept_key:
            row = conn.execute(
                "SELECT concept_id FROM music_theory_concepts WHERE concept_key = ?",
                (concept_key,),
            ).fetchone()
            concept_id = row["concept_id"] if row else None
        cur = conn.execute(
            """
            INSERT INTO track_theory_observations
              (track_id, concept_id, domain, observation_type, value_text,
               value_json, start_sec, end_sec, confidence, evidence_json,
               created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
            """,
            (
                track_id, concept_id, domain, observation_type, value_text,
                json.dumps(value, ensure_ascii=False) if value is not None else None,
                start_sec, end_sec, confidence, evidence_json,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)
