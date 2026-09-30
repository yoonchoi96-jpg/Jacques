from __future__ import annotations

import csv
import io
import re
from datetime import date, timedelta

import requests

BASE = "https://charts.spotify.com/api/charts/regional/global/daily"
LEGACY_LATEST = "https://spotifycharts.com/regional/global/daily/latest/download"
MIRROR_LATEST = "https://spotify-top.com/"
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


def _fetch_mirror_latest(session: requests.Session) -> dict:
    from bs4 import BeautifulSoup

    response = session.get(
        MIRROR_LATEST,
        headers={"User-Agent": "Jacques/1.0 (+music database)"},
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")

    heading = soup.find(
        string=re.compile(r"Spotify Daily Top Songs", re.I)
    )
    page_text = soup.get_text(" ", strip=True)
    match = re.search(r"Chart date\\s+(\\d{4}-\\d{2}-\\d{2})", page_text)
    chart_date = match.group(1) if match else date.today().isoformat()

    items = []
    for row in soup.select("tr"):
        cells = row.find_all(["th", "td"])
        if len(cells) < 4:
            continue
        try:
            rank = int(cells[0].get_text(" ", strip=True).split()[0])
        except (TypeError, ValueError, IndexError):
            continue

        song_links = [
            a for a in cells[2].find_all("a", href=True)
            if "/song/" in a.get("href", "")
        ]
        if not song_links:
            continue

        title = song_links[0].get_text(" ", strip=True)
        artists = [
            a.get_text(" ", strip=True)
            for a in song_links[1:]
            if a.get_text(" ", strip=True)
        ]
        if not artists and len(song_links) == 1:
            artists = [""]

        stream_text = cells[3].get_text(" ", strip=True).replace(",", "")
        stream_match = re.search(r"\\d[\\d,]*", stream_text)
        streams = int(stream_match.group().replace(",", "")) if stream_match else None

        rank_change = cells[1].get_text(" ", strip=True)
        change_match = re.search(r"([+-]?)\\d+", rank_change)
        previous_rank = None
        if change_match and change_match.group(1) in {"+", "-"}:
            delta = int(change_match.group().replace("+", "").replace("-", ""))
            previous_rank = rank - delta if change_match.group(1) == "+" else rank + delta

        peak_text = cells[5].get_text(" ", strip=True) if len(cells) > 5 else ""
        peak_match = re.search(r"\\d+", peak_text)
        peak_rank = int(peak_match.group()) if peak_match else None

        days_text = cells[6].get_text(" ", strip=True) if len(cells) > 6 else ""
        days_match = re.search(r"\\d+", days_text)
        days_on_chart = int(days_match.group()) if days_match else None

        items.append(
            {
                "rank": rank,
                "title": title,
                "artist": ", ".join(artists),
                "track_id": None,
                "spotify_uri": None,
                "streams": streams,
                "previous_rank": previous_rank,
                "peak_rank": peak_rank,
                "days_on_chart": days_on_chart,
            }
        )

    return {
        "source": SOURCE,
        "chart_name": "global_daily",
        "chart_date": chart_date,
        "url": MIRROR_LATEST,
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
    except (requests.RequestException, RuntimeError) as exc:
        last_error = exc

    try:
        data = _fetch_mirror_latest(session)
        if data["items"]:
            return data
    except (requests.RequestException, RuntimeError) as exc:
        last_error = exc

    raise RuntimeError(
        f"Spotify Global daily chart unavailable for the last "
        f"{lookback_days + 1} days and latest fallback: {last_error}"
    )
