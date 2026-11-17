import gzip
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from zoneinfo import ZoneInfo

import export_sessions as archive


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old = self.root / "archive"
        self.live = self.root / "live"
        self.out = self.root / "output"
        self.old.mkdir()
        self.live.mkdir()
        self.secrets = self.root / "secrets.txt"
        self.secrets.write_text("demo-secret-123456\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write_events(self, directory, events):
        path = directory / "session-abc.jsonl"
        path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
        return path

    def test_merge_redact_day_boundary_and_repeatability(self):
        first = {"type": "user", "uuid": "first", "timestamp": "2026-10-29T20:59:59Z",
                 "message": {"content": "A <script>bad()</script> demo-secret-123456"}}
        second = {"type": "assistant", "uuid": "second", "timestamp": "2026-10-29T21:00:00Z",
                  "message": {"content": [{"type": "text", "text": "Reply"}, {"type": "image", "source": {"type": "base64", "data": "very-private-pixels"}}]}}
        third = {"type": "user", "uuid": "third", "timestamp": "2026-10-29T21:00:01Z",
                 "message": {"content": "A new message"}}
        old_path = self.write_events(self.old, [first, second])
        live_path = self.write_events(self.live, [first, third])
        database = self.root / "api.db"
        with sqlite3.connect(database) as db:
            db.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY,role TEXT,text TEXT,ts TEXT,model TEXT)")
            db.execute("CREATE TABLE wire_turns(messages TEXT)")
            db.execute("INSERT INTO wire_turns VALUES('SYSTEM_PROMPT_MUST_NOT_EXPORT')")
            db.executemany("INSERT INTO messages VALUES(?,?,?,?,?)", [
                (1, "user", "API says sk-abcdefghijklmnopqrstuvwxyz", "2026-10-29T21:00:02Z", "demo"),
                (2, "system", "SYSTEM_PROMPT_MUST_NOT_EXPORT", "2026-10-29T21:00:03Z", "demo"),
            ])
        sources_before = [old_path.read_bytes(), live_path.read_bytes(), database.read_bytes()]
        kwargs = dict(archive_dir=self.old, live_dir=self.live, out_dir=self.out, api_db=database,
                      tz_name="Asia/Shanghai", day_start_hour=5, secrets_file=self.secrets)
        index = archive.export(**kwargs)
        self.assertEqual([row["day"] for row in index], ["2026-10-29", "2026-10-30"])
        files_before = {str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(archive.export(**kwargs), index)
        files_after = {str(p.relative_to(self.out)): p.read_bytes() for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(files_after, files_before)
        self.assertEqual([old_path.read_bytes(), live_path.read_bytes(), database.read_bytes()], sources_before)
        all_output = "".join(gzip.decompress(raw).decode() if path.endswith(".gz") else raw.decode()
                             for path, raw in files_after.items())
        self.assertNotIn("demo-secret-123456", all_output)
        self.assertNotIn("sk-abcdefghijklmnopqrstuvwxyz", all_output)
        self.assertNotIn("very-private-pixels", all_output)
        self.assertNotIn("SYSTEM_PROMPT_MUST_NOT_EXPORT", all_output)
        self.assertIn(archive.MASK, all_output)
        md = (self.out / "md/2026-10/2026-10-29.md").read_text()
        self.assertIn("&lt;script&gt;", md)
        raw_events = [e for p in (self.out / "raw/days").glob("*.jsonl.gz") for e in archive.read_jsonl(p)]
        self.assertEqual(sum(e.get("uuid") == "first" for e in raw_events), 1)

    def test_invalid_timestamp_and_missing_database_fail(self):
        self.write_events(self.old, [{"type": "user", "uuid": "bad", "timestamp": "not-a-date",
                                      "message": {"content": "Hello"}}])
        with self.assertRaises(ValueError):
            archive.export(self.old, self.live, self.out)
        with self.assertRaises(FileNotFoundError):
            archive.api_events(self.root / "missing.sqlite3")

    def test_day_of_requires_timezone_offset(self):
        tz = ZoneInfo("Asia/Shanghai")
        self.assertEqual(archive.day_of("2026-10-29T20:59:59Z", tz, 5), "2026-10-29")
        self.assertEqual(archive.day_of("2026-10-29T21:00:00Z", tz, 5), "2026-10-30")
        with self.assertRaises(ValueError):
            archive.day_of("2026-10-29T21:00:00", tz, 5)


if __name__ == "__main__":
    unittest.main()
