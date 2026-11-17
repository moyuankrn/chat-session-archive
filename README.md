# Chat Session Archive

A local exporter for chat transcripts. It merges repeated JSONL snapshots by message ID, splits them into daily gzip archives, and writes readable Markdown plus a day index. It can also include user and assistant messages from a SQLite chat database. Extracted from a private backup script and made configurable.

It does not upload files or change its sources.

## Run

Python 3.9 or newer is enough; there are no third-party dependencies.

```sh
python3 export_sessions.py \
  --archive-dir /path/to/archive-snapshots \
  --live-dir /path/to/current-sessions \
  --api-db /path/to/chat.sqlite3 \
  --out ./out \
  --tz Asia/Shanghai --day-start 5 \
  --secrets-file /path/to/secrets.txt
```

Only one input source is required. Omit arguments for sources you do not use. `--secrets-file` is optional and contains one literal secret per line. Its contents are never copied to the output.

The two JSONL directories should contain `session-id.jsonl` files. Each line is a JSON object with `type`, `timestamp` (ISO 8601 with a timezone), `uuid`, and `message.content`. `uuid` is used for deduplication; entries without one use a hash of the event. The optional SQLite database needs a `messages(id, role, text, ts, model)` table. Only `user` and `assistant` rows are read; system prompts and wire logs are excluded.

Output layout:

```text
out/
  README.md
  index.json
  md/YYYY-MM/YYYY-MM-DD.md
  raw/days/YYYY-MM-DD_<session-hash>.jsonl.gz
  raw/days/YYYY-MM-DD_api.jsonl.gz
```

The day begins at `--day-start` in the `--tz` timezone. Repeated runs keep the same gzip bytes when the input is unchanged. Existing output archives are included when rebuilding Markdown, so older exported days remain available even if an old input snapshot is no longer present. Use a fresh output directory if you want a fresh export.

## Redaction and review

Before writing, the exporter omits inline base64 image bytes, masks secrets listed in `--secrets-file`, and applies common token/key patterns. The raw archives still contain conversation and tool records. Pattern matching cannot guarantee removal of every private detail; inspect the output before sharing or committing it. The tool never publishes the output for you.

## Test

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

Tests cover snapshot deduplication, API SQLite selection, timezone day boundaries, redaction, source preservation, and byte-identical repeated exports.

MIT licensed. See [LICENSE](LICENSE).
