from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import requests

BASE = "https://charts.spotify.com/api/charts/regional/global/daily"
LEGACY_LATEST = "https://spotifycharts.com/regional/global/daily/latest/download"
SOURCE = "spotify_global"


def _track_id(uri: str | None) -> str | None:
    if not uri:
        return None
    match = re.search(r"spotify:track:([A-Za-z0-9]+)", uri)
    return match.group(1) if match else None



def _parse_legacy_latest(response: requests.Response) -> dict:
    text = response.text.lstrip("\ufeff")
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        raise RuntimeError("Spotify legacy chart returned an empty response.")

    header_index = 0
    for index, row in enumerate(rows[:5]):
        normalized = [str(x).strip().casefold() for x in row]
        if any("track" in x for x in normalized) and any("artist" in x for x in normalized):
            header_index = index
            break

    header = [str(x).strip().casefold() for x in rows[header_index]]
    data_rows = rows[header_index + 1:]

    def idx(*names):
        for name in names:
            if name in header:
                return header.index(name)
        return None

    rank_i = idx("rank", "position")
    title_i = idx("track_name", "track name")
    artist_i = idx("artist_names", "artist", "artist name")
    streams_i = idx("streams")
    uri_i = idx("uri", "url", "spotify url")

    if rank_i is None or title_i is None or artist_i is None:
        # Very old CSV exports are stable positional data.
        rank_i, title_i, artist_i, streams_i, uri_i = 0, 1, 2, 3, 4

    filename = response.headers.get("Content-Disposition", "")
    match = re.search(r"(\\d{4}-\\d{2}-\\d{2})", filename)
    chart_date = match.group(1) if match else date.today().isoformat()

    items = []
    for row in data_rows:
        if len(row) <= max(rank_i, title_i, artist_i, streams_i or 0, uri_i or 0):
            continue
        try:
            rank = int(str(row[rank_i]).strip())
        except (TypeError, ValueError):
            continue

        raw_uri = row[uri_i].strip() if uri_i is not None and uri_i < len(row) else ""
        if raw_uri.startswith("https://open.spotify.com/track/"):
            track_id = raw_uri.rsplit("/", 1)[-1].split("?", 1)[0]
            spotify_uri = f"spotify:track:{track_id}"
        else:
            track_id = _track_id(raw_uri)
            spotify_uri = raw_uri

        if not track_id:
            continue

        stream_text = row[streams_i].strip().replace(",", "") if streams_i is not None else ""
        items.append(
            {
                "rank": rank,
                "title": row[title_i].strip(),
                "artist": row[artist_i].strip(),
                "track_id": track_id,
                "spotify_uri": spotify_uri,
                "streams": int(stream_text) if stream_text.isdigit() else None,
                "previous_rank": None,
                "peak_rank": None,
                "days_on_chart": None,
            }
        )

    return {
        "source": SOURCE,
        "chart_name": "global_daily",
        "chart_date": chart_date,
        "url": LEGACY_LATEST,
        "items": items,
    }

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
                "days_on_chart": int(raw["days_on_chart"]) if raw.get("days_on_chart") else None,
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

    # Spotify's current chart UI still exposes a "latest" CSV endpoint even
    # when date-addressed endpoints return 404. Use it as the final fallback.
    try:
        response = session.get(
            LEGACY_LATEST,
            headers={"User-Agent": "Jacques/1.0 (+music database)"},
            timeout=30,
        )
        response.raise_for_status()
        data = _parse_legacy_latest(response)
        if data["items"]:
            return data
    except requests.RequestException as exc:
        last_error = exc

    raise RuntimeError(
        f"Spotify Global daily chart unavailable for the last "
        f"{lookback_days + 1} days and latest fallback: {last_error}"
    )
