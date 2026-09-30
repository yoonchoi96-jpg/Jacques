from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import requests

BASE = "https://charts.spotify.com/api/charts/regional/global/daily"
SOURCE = "spotify_global"


def _track_id(uri: str | None) -> str | None:
    if not uri:
        return None
    match = re.search(r"spotify:track:([A-Za-z0-9]+)", uri)
    return match.group(1) if match else None


def fetch_daily_chart(day: date, session: requests.Session | None = None) -> dict:
    session = session or requests.Session()
    url = f"{BASE}/{day.isoformat()}"
    response = session.get(
        url,
        headers={"User-Agent": "Jacques/1.0 (+music database)"},
        timeout=30,
    )
    response.raise_for_status()

    reader = csv.DictReader(io.StringIO(response.text))
    rows = []
    for raw in reader:
        track_id = _track_id(raw.get("uri"))
        if not track_id:
            continue
        rows.append(
            {
                "rank": int(raw["rank"]),
                "title": (raw.get("track_name") or "").strip(),
                "artist": (raw.get("artist_names") or "").strip(),
                "track_id": track_id,
                "spotify_uri": raw.get("uri"),
                "streams": int(raw["streams"]) if raw.get("streams") else None,
                "previous_rank": int(raw["previous_rank"]) if raw.get("previous_rank") else None,
                "peak_rank": int(raw["peak_rank"]) if raw.get("peak_rank") else None,
                "weeks_on_chart": int(raw["weeks_on_chart"]) if raw.get("weeks_on_chart") else None,
            }
        )

    return {
        "source": SOURCE,
        "chart_name": "global_daily",
        "chart_date": day.isoformat(),
        "url": url,
        "items": rows,
    }


def fetch_latest_chart(
    *,
    reference_day: date | None = None,
    lookback_days: int = 7,
    session: requests.Session | None = None,
) -> dict:
    reference_day = reference_day or date.today()
    session = session or requests.Session()

    last_error = None
    for offset in range(lookback_days + 1):
        day = reference_day - timedelta(days=offset)
        try:
            data = fetch_daily_chart(day, session)
            if data["items"]:
                return data
        except requests.RequestException as exc:
            last_error = exc

    raise RuntimeError(
        f"Spotify Global daily chart unavailable for the last "
        f"{lookback_days + 1} days: {last_error}"
    )
