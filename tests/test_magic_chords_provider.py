import unittest

from tools.magic_chords_provider import MagicChordsJobError, MagicChordsResultError, MagicChordsTimeout, _segments, analyze


class FakePage:
    def __init__(self, statuses):
        self.statuses = iter(statuses)
        self.waits = []

    def goto(self, *args, **kwargs):
        return None

    def evaluate(self, script, arg=None):
        if "analyze/url" in script:
            return {"job_id": "job-123", "status": "processing"}
        if "/result" in script:
            return {
                "key": "C major",
                "tempo": 120,
                "segments": [{"start": 0, "end": 2, "chord": "C"}],
            }
        return {"status": next(self.statuses)}

    def wait_for_timeout(self, milliseconds):
        self.waits.append(milliseconds)


class MagicChordsProviderTests(unittest.TestCase):
    def test_normalizes_chord_timeline(self):
        result = {
            "chords": [
                {"start": 0, "end": 2.5, "chord": "Cmaj7", "confidence": 0.91},
                {"start": 2.5, "end": 5, "symbol": "Am7"},
                {"start": 5, "end": 5, "chord": "ignored"},
            ]
        }
        rows = _segments(result)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["chord"], "Cmaj7")
        self.assertEqual(rows[1]["chord"], "Am7")
        self.assertEqual(rows[0]["start_sec"], 0.0)
        self.assertEqual(rows[1]["end_sec"], 5.0)

    def test_accepts_nested_timeline(self):
        result = {"timeline": {"items": [{"startTime": 1, "endTime": 3, "label": "Dm"}]}}
        rows = _segments(result)
        self.assertEqual(rows[0]["chord"], "Dm")
        self.assertEqual(rows[0]["start_sec"], 1.0)
        self.assertEqual(rows[0]["end_sec"], 3.0)

    def test_completed_job_returns_result(self):
        page = FakePage(["processing", "completed"])
        result = analyze(page, "https://www.youtube.com/watch?v=test", polls=3, wait=1)
        self.assertEqual(result["job_id"], "job-123")
        self.assertEqual(result["tempo"], 120)
        self.assertEqual(result["segments"][0]["chord"], "C")
        self.assertTrue(page.waits)

    def test_timeout_exposes_job_id_for_poll_retry(self):
        page = FakePage(["processing", "processing"])
        with self.assertRaises(MagicChordsTimeout) as ctx:
            analyze(page, "https://www.youtube.com/watch?v=test", polls=2, wait=1)
        self.assertEqual(ctx.exception.job_id, "job-123")

    def test_status_poll_error_exposes_job_id(self):
        class StatusErrorPage(FakePage):
            def evaluate(self, script, arg=None):
                if "/result" in script:
                    return super().evaluate(script, arg)
                if "/jobs/" in script:
                    raise RuntimeError("503")
                return super().evaluate(script, arg)

        page = StatusErrorPage([])
        with self.assertRaises(MagicChordsJobError) as ctx:
            analyze(page, "https://www.youtube.com/watch?v=test", polls=1, wait=1)
        self.assertEqual(ctx.exception.job_id, "job-123")

    def test_result_fetch_error_exposes_job_id(self):
        class ResultErrorPage(FakePage):
            def evaluate(self, script, arg=None):
                if "/result" in script:
                    raise RuntimeError("503")
                return super().evaluate(script, arg)

        page = ResultErrorPage(["completed"])
        with self.assertRaises(MagicChordsResultError) as ctx:
            analyze(page, "https://www.youtube.com/watch?v=test", polls=1, wait=1)
        self.assertEqual(ctx.exception.job_id, "job-123")

    def test_reusing_job_skips_submit(self):
        class ReusePage(FakePage):
            def evaluate(self, script, arg=None):
                if "analyze/url" in script:
                    raise AssertionError("must not submit a second job")
                return super().evaluate(script, arg)

        page = ReusePage(["completed"])
        result = analyze(
            page, "https://www.youtube.com/watch?v=test",
            polls=1, wait=1, job_id="job-existing"
        )
        self.assertEqual(result["job_id"], "job-existing")


if __name__ == "__main__":
    unittest.main()
