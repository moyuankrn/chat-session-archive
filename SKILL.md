---
name: chat-session-archive
description: Export overlapping local chat JSONL snapshots and optional SQLite messages into deduplicated daily gzip and readable Markdown archives. Use when the user asks to back up or inspect their own chat history locally.
---

# Chat session archive

Use the bundled `export_sessions.py` for a local, read-only export from supported inputs. The script writes only to the chosen output directory. It needs Python 3.9+ and no third-party packages.

## Work on the user's archive

1. Confirm which source the user wants archived: directories of `session-id.jsonl` files, a SQLite database with `messages(id, role, text, ts, model)`, or both. Inspect a small redacted sample of the schema before running. Choose the timezone and day boundary with the user if they matter; otherwise use UTC and midnight.
2. Choose a local output directory outside any published or synchronized repository unless the user explicitly requests otherwise. If the output directory already contains prior archives, the script includes them when rebuilding the Markdown view; use a fresh directory for a clean export.
3. Run the bundled script with the relevant `--archive-dir`, `--live-dir`, `--api-db`, `--out`, `--tz`, and `--day-start` options. If the user supplies known literal secrets, place them in a private one-secret-per-line file and pass `--secrets-file`. Never paste secrets into chat or command arguments.
4. Check `index.json` and a sample day's Markdown, then inspect a sample gzip raw archive. Verify overlapping snapshots were deduplicated by UUID, the selected day boundary is correct, and source files are unchanged. A repeat export with unchanged inputs should yield byte-identical output.
5. Report the source types, day count, output path, checks performed, and any skipped or malformed records. Tell the user that raw gzip archives retain conversation and tool records even after pattern-based redaction; require their own review before sharing or committing the output.

## Boundaries

- The JSONL reader expects top-level `session-id.jsonl` files with `type`, timezone-aware `timestamp`, optional `uuid`, and `message.content`. It does not automatically parse every chat provider's export format or recurse into directories.
- The optional SQLite reader selects only user and assistant rows from the specified `messages` table. It does not export system prompts or arbitrary schema tables.
- Redaction removes inline base64 image bytes and common token patterns, but cannot guarantee that all private details are removed. Do not upload the output to a third-party service as part of this workflow.

The reference implementation is MIT licensed: https://github.com/moyuankrn/chat-session-archive .
