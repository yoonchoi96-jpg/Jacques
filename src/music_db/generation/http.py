from __future__ import annotations

import time
import requests


RETRYABLE = {429, 500, 502, 503, 504}


def post_json(url, *, json_body=None, headers=None, timeout=60, attempts=3):
    last = None
    for attempt in range(attempts):
        try:
            response = requests.post(
                url, json=json_body, headers=headers or {}, timeout=timeout
            )
            if response.status_code not in RETRYABLE:
                return response
            last = response
            if response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                try:
                    delay = min(float(retry_after), 30.0) if retry_after else 2 ** attempt
                except ValueError:
                    delay = 2 ** attempt
            else:
                delay = 2 ** attempt
        except requests.RequestException as exc:
            last = exc
        if attempt + 1 < attempts:
            time.sleep(delay)
    if isinstance(last, BaseException):
        raise last
    return last
