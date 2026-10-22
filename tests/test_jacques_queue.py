"""Jacques queue validation: offline tests; no personal data or network."""
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jacques_queue import validate_issue, parse_task, digest, export_public_items

READ = {
    "version": 1, "target": "spotify", "operation": "playlist.items",
    "path_values": {"playlist_id": "1U4uM0l6mwcyCdiFoV3RPV"},
    "params": {"limit": 50}, "all_pages": True,
}

def issue(task, owner="yoonchoi96-jpg"):
    return {"title": "[Jacques Queue] test", "body": json.dumps(task),
            "user": {"login": owner}, "state": "open"}

class TaskTests(unittest.TestCase):
    def test_owner_valid_read(self):
        task = validate_issue(issue(READ), "yoonchoi96-jpg")
        self.assertEqual(task["operation"], "playlist.items")
        self.assertFalse(task["public_export"])

    def test_untrusted_author_blocked(self):
        with self.assertRaises(ValueError):
            validate_issue(issue(READ, owner="attacker"), "yoonchoi96-jpg")

    def test_closed_issue_blocked(self):
        x = issue(READ); x["state"] = "closed"
        with self.assertRaises(ValueError):
            validate_issue(x, "yoonchoi96-jpg")

    def test_unknown_operation_blocked(self):
        with self.assertRaises(ValueError):
            parse_task(json.dumps(dict(READ, operation="mac.shell")))

    def test_no_arbitrary_mac_command(self):
        for name in ("mac.shell", "mac.exec", "mac.python"):
            with self.assertRaises(ValueError):
                parse_task(json.dumps({"version": 1, "target": "mac", "operation": name}))

    def test_write_requires_snapshot(self):
        d = dict(READ, operation="playlist.add", all_pages=False,
                 body={"uris": ["spotify:track:ABC123"]})
        with self.assertRaisesRegex(ValueError, "expected_snapshot"):
            parse_task(json.dumps(d))
        d["expected_snapshot_id"] = "ABCDEF123"
        self.assertEqual(parse_task(json.dumps(d))["operation"], "playlist.add")

    def test_public_export_only_playlist_items(self):
        x = parse_task(json.dumps(dict(READ, public_export=True)))
        self.assertTrue(x["public_export"])
        with self.assertRaises(ValueError):
            parse_task(json.dumps(dict(READ, operation="profile.me", public_export=True)))

    def test_digest_stable_and_sensitive_to_changes(self):
        a = parse_task(json.dumps(READ))
        b = parse_task(json.dumps(dict(READ, params={"limit": 10})))
        self.assertEqual(digest(a), digest(parse_task(json.dumps(READ))))
        self.assertNotEqual(digest(a), digest(b))

    def test_public_export_does_not_leak_ids(self):
        rows = [{"item": {"name": "Song", "artists": [{"name": "Artist"}],
                 "id": "privateId"}, "added_by": {"id": "secret"}}]
        value = export_public_items(rows)
        self.assertIn("Song", value)
        self.assertNotIn("privateId", value)
        self.assertNotIn("secret", value)

    def test_no_unknown_secret_fields(self):
        with self.assertRaises(ValueError):
            parse_task(json.dumps(dict(READ, access_token="secret")))

if __name__ == "__main__":
    unittest.main()
