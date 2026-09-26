import sqlite3
import requests
import time
import re
import json
from datetime import datetime, timezone
from bs4 import BeautifulSoup


DB = "db/music.db"
BASE_URL = "https://songbpm.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/130.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;"
        "q=0.9,image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://songbpm.com/",
    "Origin": "https://songbpm.com",
    "Content-Type": "application/x-www-form-urlencoded",
}


# ============================================================
# Spotify ID
# ============================================================


def spotify_track_id(url):
    if not url:
        return None

    m = re.search(r"open\.spotify\.com/track/([A-Za-z0-9]+)", url)

    if m:
        return m.group(1)

    return None


# ============================================================
# Text / encoding
# ============================================================

def fix_mojibake(text):
    if not text:
        return text

    try:
        # UTF-8을 latin-1/cp1252 계열로 잘못 읽은 경우 복구
        fixed = text.encode("latin1").decode("utf-8")
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


# ============================================================
# Parsers
# ============================================================

def parse_duration(text):
    m = re.search(r"Duration\s+(\d+):(\d+)", text, re.I)

    if not m:
        return None

    minutes = int(m.group(1))
    seconds = int(m.group(2))

    return (minutes * 60 + seconds) * 1000


def parse_bpm(text):
    m = re.search(r"BPM\s+(\d+(?:\.\d+)?)", text, re.I)

    if not m:
        return None

    return float(m.group(1))


def parse_key(text):
    m = re.search(
        r"Key\s+(.+?)(?=\s+Duration\b|\s+BPM\b|$)",
        text,
        re.I,
    )

    if not m:
        return None

    key = m.group(1).strip()

    return fix_mojibake(key)


# ============================================================
# Search result parser
# ============================================================

def parse_results(html):
    soup = BeautifulSoup(html, "html.parser")

    results = []

    spotify_links = soup.find_all(
        "a",
        href=re.compile(r"open\.spotify\.com/track/")
    )

    seen = set()

    for link in spotify_links:
        href = link.get("href")
        spotify_id = spotify_track_id(href)

        if not spotify_id:
            continue

        if spotify_id in seen:
            continue

        seen.add(spotify_id)

        node = link
        text = ""

        for _ in range(10):
            if node is None:
                break

            current_text = " ".join(node.stripped_strings)

            if (
                "BPM" in current_text
                and "Duration" in current_text
            ):
                text = current_text
                break

            node = node.parent

        if not text:
            continue

        bpm = parse_bpm(text)
        key = parse_key(text)
        duration_ms = parse_duration(text)

        results.append({
            "spotify_track_id": spotify_id,
            "spotify_url": href,
            "bpm": bpm,
            "key": key,
            "duration_ms": duration_ms,
            "text": text,
        })

    return results


# ============================================================
# SongBPM search
# ============================================================

def songbpm_search(session, query):

    response = session.post(
        f"{BASE_URL}/searches",
        data={"query": query},
        allow_redirects=False,
        timeout=30,
    )

    if response.status_code not in (301, 302, 303, 307, 308):
        return {
            "status": "search_failed",
            "http_status": response.status_code,
            "results": [],
            "url": None,
        }

    location = response.headers.get("Location")

    if not location:
        return {
            "status": "search_failed",
            "http_status": response.status_code,
            "results": [],
            "url": None,
        }

    if location.startswith("/"):
        url = BASE_URL + location
    else:
        url = location

    page = session.get(
        url,
        timeout=30,
    )

    if page.status_code != 200:
        return {
            "status": "search_failed",
            "http_status": page.status_code,
            "results": [],
            "url": url,
        }

    # requests가 잘못된 encoding을 잡는 경우 대비
    page.encoding = "utf-8"

    results = parse_results(page.text)

    return {
        "status": "ok",
        "http_status": page.status_code,
        "results": results,
        "url": url,
    }


# ============================================================
# DB
# ============================================================



# ============================================================
# NEW MATCHING LOGIC
# ============================================================

DB = "db/music.db"


def get_tracks(conn):
    rows = conn.execute(
        """
        SELECT
            t.track_id,
            t.title,
            t.duration_ms,
            COALESCE(
                (
                    SELECT GROUP_CONCAT(a.name, '|||')
                    FROM track_artists ta
                    JOIN artists a
                      ON a.artist_id = ta.artist_id
                    WHERE ta.track_id = t.track_id
                    ORDER BY ta.artist_order
                ),
                ''
            ) AS artist
        FROM tracks t
        ORDER BY t.title
        """
    ).fetchall()

    return rows


def normalize_text(text):
    if text is None:
        return ""

    text = fix_mojibake(str(text))
    text = text.lower()

    replacements = {
        "&": "and",
        "’": "'",
        "‘": "'",
        "–": "-",
        "—": "-",
    }

    for old, new in replacements.items():
        text = text.replace(old, new)

    import re

    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[[^\]]*\]", " ", text)
    text = re.sub(r"[^a-z0-9가-힣]+", " ", text)

    return " ".join(text.split())


def token_match_score(target, candidate):
    """
    제목/아티스트가 같은 곡인지 판단하기 위한 단순 유사도.

    1.0 = 사실상 동일
    0.0 = 관련 없음
    """

    a = normalize_text(target)
    b = normalize_text(candidate)

    if not a or not b:
        return 0.0

    if a == b:
        return 1.0

    a_tokens = set(a.split())
    b_tokens = set(b.split())

    if not a_tokens or not b_tokens:
        return 0.0

    intersection = len(a_tokens & b_tokens)

    return intersection / max(len(a_tokens), len(b_tokens))


def duration_difference_seconds(db_duration_ms, candidate_duration_ms):
    if db_duration_ms is None or candidate_duration_ms is None:
        return None

    try:
        return abs(
            float(db_duration_ms) -
            float(candidate_duration_ms)
        ) / 1000.0
    except (TypeError, ValueError):
        return None


def title_without_parentheses(title):
    """
    제목 비교용 정규화.

    괄호 안 내용은 제거한다.
    예:
        Finesse
        Finesse (feat. BNXN)
        Finesse (Justin Credible Mix)

    → 모두 Finesse

    또한 공백/구두점 차이는 제거한다.
    """
    title = str(title or "")

    # 괄호 + 괄호 안 내용 제거
    title = re.sub(r"\([^)]*\)", "", title)

    # 대소문자 무시
    title = title.lower()

    # 공백 제거
    title = re.sub(r"\s+", "", title)

    # 비교에 방해되는 기본 구두점 제거
    title = re.sub(r"[^0-9a-z가-힣]", "", title)

    return title


def candidate_matches_metadata(
    title,
    artists,
    duration_ms,
    candidate,
):
    """
    SongBPM 후보 필터.

    핵심 규칙:

    1. 후보 카드 전체 text에서 DB 아티스트를 직접 찾는다.
       첫 단어를 artist로 가정하지 않는다.

    2. 공동 아티스트는 ||| 로 분리해서 각각 검사한다.

    3. 후보 제목은 아티스트 부분을 제거한 뒤 추출한다.

    4. 제목 비교 시 괄호 안 내용은 제거한다.
       예:
           Finesse
           Finesse (feat. BNXN)
           Finesse (Justin Credible Mix)
       → 모두 Finesse

    5. 괄호 제외 제목의 글자 수가 다르면 후보 탈락.

    6. 제목이 실제로 일치/유사하지 않으면 탈락.

    7. 통과 후보만 duration/BPM/Key 등을 이용해 ranking한다.
    """

    from difflib import SequenceMatcher

    raw_text = str(candidate.get("text", "") or "").strip()

    if not raw_text:
        return None

    # --------------------------------------------------------
    # Key 앞부분까지만 실제 곡 정보로 사용
    # --------------------------------------------------------
    key_pos = raw_text.find(" Key ")

    if key_pos != -1:
        metadata_text = raw_text[:key_pos].strip()
    else:
        metadata_text = raw_text

    # --------------------------------------------------------
    # 불필요한 서비스 링크 텍스트 제거
    # --------------------------------------------------------
    metadata_text = re.sub(
        r"\bListen on Spotify\b.*$",
        "",
        metadata_text,
        flags=re.IGNORECASE,
    ).strip()

    normalized_metadata = normalize_text(metadata_text)

    # --------------------------------------------------------
    # DB 아티스트 후보
    #
    # "Alleh|||ELENA ROSE"
    # → ["Alleh", "ELENA ROSE"]
    # --------------------------------------------------------
    artist_parts = [
        normalize_text(a)
        for a in str(artists).split("|||")
        if normalize_text(a)
    ]

    if not artist_parts:
        return None

    # --------------------------------------------------------
    # 아티스트를 실제 문자열에서 찾는다.
    #
    # 가장 긴 아티스트명을 먼저 검사해서
    #
    # "ELENA"
    # "ELENA ROSE"
    #
    # 같은 경우 ELENA ROSE가 먼저 잡히도록 한다.
    # --------------------------------------------------------
    artist_matches = []

    for artist in sorted(
        artist_parts,
        key=len,
        reverse=True,
    ):
        if artist in normalized_metadata:
            artist_matches.append(artist)

    if not artist_matches:
        return None

    # --------------------------------------------------------
    # 후보 artist/title 분리
    #
    # SongBPM 카드가
    #   Drake Make Them Cry
    # 라면
    #
    # artist = Drake
    # title  = Make Them Cry
    #
    # ELENA ROSE TUTUTU라면
    #
    # artist = ELENA ROSE
    # title  = TUTUTU
    # --------------------------------------------------------
    selected_artist = artist_matches[0]

    artist_pos = normalized_metadata.find(selected_artist)

    candidate_title = normalized_metadata[
        artist_pos + len(selected_artist):
    ].strip()

    if not candidate_title:
        return None

    # --------------------------------------------------------
    # 후보 제목 정규화
    # --------------------------------------------------------
    db_title = title_without_parentheses(title)
    candidate_title_normalized = title_without_parentheses(
        candidate_title
    )

    if not db_title or not candidate_title_normalized:
        return None

    # --------------------------------------------------------
    # 제목 비교
    #
    # 괄호 안 정보는 title_without_parentheses()에서 제거.
    #
    # 제목 길이가 다르다는 이유만으로 바로 탈락시키지 않는다.
    # SongBPM의 제목 표기가 조금 다른 경우를 살려두고,
    # 실제 제목 유사도로 최종 판단한다.
    # --------------------------------------------------------
    if db_title == candidate_title_normalized:
        title_similarity = 1.0

    elif (
        db_title in candidate_title_normalized
        or candidate_title_normalized in db_title
    ):
        title_similarity = 0.90

    else:
        title_similarity = SequenceMatcher(
            None,
            db_title,
            candidate_title_normalized,
        ).ratio()

        if title_similarity < 0.80:
            return None

    # --------------------------------------------------------
    # duration
    # --------------------------------------------------------
    duration_diff = duration_difference_seconds(
        duration_ms,
        candidate.get("duration_ms"),
    )

    # --------------------------------------------------------
    # remix / mix / edit 여부
    # --------------------------------------------------------
    candidate_lower = normalized_metadata

    remix_words = (
        "remix",
        "mix",
        "edit",
        "extended",
        "radio edit",
        "club mix",
        "instrumental",
        "acoustic version",
    )

    is_remix = any(
        word in candidate_lower
        for word in remix_words
    )

    return {
        "candidate": candidate,
        "duration_diff": duration_diff,
        "title_similarity": title_similarity,
        "artist_match": True,
        "is_remix": is_remix,
        "parsed_artist": selected_artist,
        "parsed_title": candidate_title,
    }


def choose_best_candidate(
    title,
    artists,
    duration_ms,
    candidates,
):
    """
    후보 선택 순서:

    1. 괄호 제외 제목 길이가 다른 후보 제거
    2. 실제 제목 유사도
    3. 아티스트 일치
    4. duration 가까운 후보
    5. 원곡 우선
    6. SongBPM 검색 순서
    """

    scored = []

    for index, candidate in enumerate(candidates):
        result = candidate_matches_metadata(
            title,
            artists,
            duration_ms,
            candidate,
        )

        if result is None:
            continue

        result["index"] = index
        scored.append(result)

    if not scored:
        return None

    def sort_key(item):
        duration = item["duration_diff"]

        if duration is None:
            duration_rank = 999999.0
        else:
            duration_rank = duration

        # 원곡 우선
        remix_rank = 1 if item["is_remix"] else 0

        # 제목 유사도가 높은 후보 우선
        title_rank = -item["title_similarity"]

        return (
            duration_rank,
            remix_rank,
            title_rank,
            item["index"],
        )

    scored.sort(key=sort_key)

    return scored[0]


def classify_result(
    track_id,
    title,
    artists,
    duration_ms,
    candidates,
):
    """
    최종 상태는 딱 4개만 사용한다.

      EXACT MATCH
      FUZZY MATCH
      NO MATCH
      SEARCH FAILED

    REVIEW / CANDIDATE ONLY / HIGH CONFIDENCE CANDIDATE
    는 사용하지 않는다.
    """

    # --------------------------------------------------------
    # 1. Spotify Track ID 완전 동일
    # --------------------------------------------------------

    for candidate in candidates:

        if candidate.get("spotify_track_id") == track_id:

            return {
                "status": "EXACT MATCH",
                "candidate": candidate,
                "confidence": 1.0,
                "duration_diff": 0.0,
                "match_method": "spotify_track_id",
            }

    # --------------------------------------------------------
    # 2. ID가 다르면 metadata fuzzy matching
    # --------------------------------------------------------

    best = choose_best_candidate(
        title,
        artists,
        duration_ms,
        candidates,
    )

    if best is None:

        return {
            "status": "NO MATCH",
            "candidate": None,
            "confidence": 0.0,
            "duration_diff": None,
            "match_method": None,
        }

    # 후보 하나가 살아남았으면 자동 채택
    return {
        "status": "FUZZY MATCH",
        "candidate": best["candidate"],
        "confidence": 1.0,
        "duration_diff": best["duration_diff"],
        "match_method": "title_artist_duration",
    }


def save_match(conn, item):
    candidate = item["candidate"]

    raw_data = json.dumps(
        candidate,
        ensure_ascii=False,
    )

    now = datetime.now(
        timezone.utc
    ).isoformat()

    conn.execute(
        """
        INSERT INTO audio_feature_sources (
            track_id,
            source,
            tempo,
            key,
            created_at,
            updated_at,
            raw_data
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)

        ON CONFLICT(track_id, source)
        DO UPDATE SET
            tempo = excluded.tempo,
            key = excluded.key,
            updated_at = excluded.updated_at,
            raw_data = excluded.raw_data
        """,
        (
            item["track_id"],
            "songbpm",
            candidate.get("bpm"),
            candidate.get("key"),
            now,
            now,
            raw_data,
        ),
    )


def main():

    conn = sqlite3.connect(DB)

    tracks = get_tracks(conn)

    conn.close()

    session = requests.Session()
    session.headers.update(HEADERS)

    exact_matches = []
    fuzzy_matches = []
    no_matches = []
    search_failed = []

    print("=" * 80)
    print("SongBPM Sync V2 — DRY RUN")
    print("=" * 80)
    print(f"Total tracks : {len(tracks)}")
    print()

    for index, (
        track_id,
        title,
        duration_ms,
        artists,
    ) in enumerate(tracks, 1):

        clean_artists = artists.replace(
            "|||",
            " ",
        )

        query = (
            f"{title} - {clean_artists}"
        ).strip()

        print(
            f"[{index:02d}/{len(tracks)}] "
            f"{title} - {artists}"
        )

        try:

            data = songbpm_search(
                session,
                query,
            )

        except Exception as e:

            print(
                f"  SEARCH FAILED | {e}"
            )

            search_failed.append({
                "track_id": track_id,
                "title": title,
                "artist": artists,
                "query": query,
                "error": str(e),
            })

            time.sleep(1)

            continue

        if data.get("status") != "ok":

            print(
                "  SEARCH FAILED"
                f" | status={data.get('status')}"
                f" | http={data.get('http_status')}"
            )

            search_failed.append({
                "track_id": track_id,
                "title": title,
                "artist": artists,
                "query": query,
            })

            time.sleep(1)

            continue

        candidates = data.get(
            "results",
            [],
        )

        result = classify_result(
            track_id,
            title,
            artists,
            duration_ms,
            candidates,
        )

        status = result["status"]
        candidate = result["candidate"]

        if status == "EXACT MATCH":

            print(
                "  EXACT MATCH"
                f" | BPM={candidate.get('bpm')}"
                f" | KEY={candidate.get('key')}"
                f" | DUR={candidate.get('duration_ms')}"
            )

            exact_matches.append({
                "track_id": track_id,
                "title": title,
                "artist": artists,
                "query": query,
                "candidate": candidate,
                "url": data.get("url"),
                "confidence": result["confidence"],
                "match_method": result["match_method"],
                "duration_diff": result["duration_diff"],
            })

        elif status == "FUZZY MATCH":

            diff = result["duration_diff"]

            diff_text = (
                f"{diff:.2f}s"
                if diff is not None
                else "N/A"
            )

            print(
                "  FUZZY MATCH"
                f" | duration_diff={diff_text}"
                f" | BPM={candidate.get('bpm')}"
                f" | KEY={candidate.get('key')}"
                f" | DUR={candidate.get('duration_ms')}"
            )

            print(
                f"    -> {candidate.get('text', '')[:120]}"
            )

            fuzzy_matches.append({
                "track_id": track_id,
                "title": title,
                "artist": artists,
                "query": query,
                "candidate": candidate,
                "url": data.get("url"),
                "confidence": result["confidence"],
                "match_method": result["match_method"],
                "duration_diff": result["duration_diff"],
            })

        else:

            print(
                "  NO MATCH"
                f" | candidates={len(candidates)}"
            )

            no_matches.append({
                "track_id": track_id,
                "title": title,
                "artist": artists,
                "query": query,
            })

        time.sleep(1)

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("SUMMARY")
    print("=" * 80)

    print(
        f"EXACT MATCH   : {len(exact_matches)}"
    )

    print(
        f"FUZZY MATCH   : {len(fuzzy_matches)}"
    )

    print(
        f"NO MATCH      : {len(no_matches)}"
    )

    print(
        f"SEARCH FAILED : {len(search_failed)}"
    )

    print("=" * 80)

    if fuzzy_matches:

        print()
        print("=" * 80)
        print("FUZZY MATCHES")
        print("=" * 80)

        for item in fuzzy_matches:

            diff = item["duration_diff"]

            diff_text = (
                f"{diff:.2f}s"
                if diff is not None
                else "N/A"
            )

            print(
                f"{item['artist']} - "
                f"{item['title']}"
            )

            print(
                f"  duration_diff={diff_text}"
                f" | {item['candidate'].get('text', '')[:120]}"
            )

    if no_matches:

        print()
        print("=" * 80)
        print("NO MATCH")
        print("=" * 80)

        for item in no_matches:
            print(
                f"{item['artist']} - "
                f"{item['title']}"
            )

    if search_failed:

        print()
        print("=" * 80)
        print("SEARCH FAILED")
        print("=" * 80)

        for item in search_failed:
            print(
                f"{item['artist']} - "
                f"{item['title']}"
            )

    # --------------------------------------------------------
    # 실제 저장은 아직 하지 않는다.
    # V2 매칭 결과 검증 후 저장.
    # --------------------------------------------------------

    print()
    print("=" * 80)
    print("DRY RUN ONLY — DATABASE NOT MODIFIED")
    print("=" * 80)


if __name__ == "__main__":
    main()
