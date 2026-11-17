#!/usr/bin/env python3
"""Merge chat JSONL snapshots and optional API SQLite messages into daily archives.

No source files are modified. Output is local and contains conversation content.
"""

import argparse
from collections import defaultdict
from datetime import datetime, timedelta
import gzip
import hashlib
import html
import json
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo


SECRET_PATTERNS = [
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}"),
    re.compile(r"\bgithub_pat_[A-Za-z0-9_]{40,}"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{35}"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abpr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{20,}"),
    re.compile(r"(?i)((?:api[_-]?key|secret|password|passwd|token)[\"']?\s*[:=]\s*[\"']?)[A-Za-z0-9._~+/=-]{16,}"),
]
MASK = "«redacted»"


def load_secrets(path):
    if path is None:
        return []
    return sorted({line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines()
                   if len(line.strip()) >= 4}, key=len, reverse=True)


def redact(text, secrets=()):
    for secret in secrets:
        text = text.replace(secret, MASK)
    for pattern in SECRET_PATTERNS:
        text = pattern.sub((lambda match: match.group(1) + MASK) if pattern.groups else MASK, text)
    return text


def safe_event(value, secrets=()):
    """Remove inline image bytes and redact every remaining string value."""
    if isinstance(value, dict):
        if value.get("type") == "base64" and "data" in value:
            return {key: ("«image bytes omitted»" if key == "data" else safe_event(item, secrets))
                    for key, item in value.items()}
        return {key: safe_event(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_event(item, secrets) for item in value]
    return redact(value, secrets) if isinstance(value, str) else value


def read_jsonl(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_gz(path, events, secrets=()):
    """Deterministic gzip: unchanged input yields identical bytes."""
    with open(path, "wb") as handle, gzip.GzipFile(filename="", fileobj=handle, mode="wb", mtime=0) as zipped:
        for event in events:
            line = json.dumps(safe_event(event, secrets), ensure_ascii=False, sort_keys=True) + "\n"
            zipped.write(line.encode("utf-8"))


def day_of(timestamp, tz, day_start_hour):
    try:
        instant = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if instant.tzinfo is None:
            raise ValueError("timestamp needs an offset")
        return str((instant.astimezone(tz) - timedelta(hours=day_start_hour)).date())
    except (AttributeError, ValueError) as error:
        raise ValueError(f"invalid timestamp: {timestamp!r}") from error


def event_id(event):
    if event.get("uuid"):
        return str(event["uuid"])
    payload = json.dumps(event, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def api_events(database):
    """Read only saved user/assistant messages, never runtime prompts or wire logs."""
    if database is None:
        return []
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        rows = connection.execute(
            "SELECT id,role,text,ts,model FROM messages WHERE role IN ('user','assistant') ORDER BY id"
        ).fetchall()
    finally:
        connection.close()
    return [{"type": role, "timestamp": ts, "uuid": f"api:message:{ident}", "source": "api",
             "model": model, "message": {"content": text}}
            for ident, role, text, ts, model in rows]


def session_files(*directories):
    sources = defaultdict(list)
    for directory in directories:
        if directory is None:
            continue
        folder = Path(directory)
        if not folder.is_dir():
            raise NotADirectoryError(folder)
        for path in sorted(folder.glob("*.jsonl")):
            sources[path.stem].append(path)
    return sources


def render_blocks(content):
    if isinstance(content, str):
        return [content], []
    texts, tools = [], []
    for block in content or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and block.get("text", "").strip():
            texts.append(block["text"])
        elif block.get("type") == "tool_use":
            tools.append(str(block.get("name") or "tool"))
        elif block.get("type") == "image":
            texts.append("[image]")
    return texts, tools


def day_markdown(day, events, tz, day_start_hour, secrets=()):
    heading = f"# {day}\n\nDay boundary: {day_start_hour:02d}:00 ({tz.key})\n"
    lines = [heading]
    counts = {"user": 0, "assistant": 0}
    for event in events:
        role = event.get("type")
        if role not in counts:
            continue
        content = (event.get("message") or {}).get("content")
        if role == "user" and not isinstance(content, str):
            continue  # Tool returns and image payloads are not conversation text.
        texts, tools = render_blocks(content)
        body = "\n\n".join(texts).strip()
        if not body and not tools:
            continue
        stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00")).astimezone(tz).strftime("%H:%M")
        if body:
            counts[role] += 1
            safe = html.escape(redact(body, secrets), quote=False)
            lines.append(f"`{stamp}` **{role.title()}**\n> " + safe.replace("\n", "\n> ") + "\n")
        if tools:
            lines.append("<sub>Tools: " + html.escape(redact(" · ".join(tools), secrets)) + "</sub>\n")
    return "\n".join(lines), counts


def export(archive_dir, live_dir, out_dir, api_db=None, tz_name="UTC", day_start_hour=0, secrets_file=None):
    if not 0 <= day_start_hour <= 23:
        raise ValueError("day_start_hour must be between 0 and 23")
    tz = ZoneInfo(tz_name)
    secrets = load_secrets(secrets_file)
    out = Path(out_dir)
    raw_dir, md_dir = out / "raw" / "days", out / "md"
    raw_dir.mkdir(parents=True, exist_ok=True)
    md_dir.mkdir(parents=True, exist_ok=True)

    for session, paths in session_files(archive_dir, live_dir).items():
        short = hashlib.sha256(session.encode()).hexdigest()[:12]
        previous = sorted(raw_dir.glob(f"*_{short}.jsonl.gz"))
        deduped = {}
        for path in [*previous, *paths]:
            for event in read_jsonl(path):
                deduped.setdefault(event_id(safe_event(event, secrets)), event)
        by_day = defaultdict(list)
        for event in sorted(deduped.values(), key=lambda item: (item.get("timestamp") or "", event_id(item))):
            by_day[day_of(event.get("timestamp"), tz, day_start_hour)].append(event)
        for day, events in by_day.items():
            write_gz(raw_dir / f"{day}_{short}.jsonl.gz", events, secrets)

    if api_db is not None:
        by_day = defaultdict(list)
        for event in api_events(api_db):
            by_day[day_of(event["timestamp"], tz, day_start_hour)].append(event)
        for day, events in by_day.items():
            write_gz(raw_dir / f"{day}_api.jsonl.gz", events, secrets)

    # Rebuild the readable view from every saved daily archive, including old
    # days that are no longer present in the source directories.
    by_day = defaultdict(dict)
    for path in sorted(raw_dir.glob("*.jsonl.gz")):
        for event in read_jsonl(path):
            by_day[day_of(event.get("timestamp"), tz, day_start_hour)].setdefault(event_id(event), event)
    index = []
    for day, events in sorted(by_day.items()):
        ordered = sorted(events.values(), key=lambda item: (item.get("timestamp") or "", event_id(item)))
        markdown, counts = day_markdown(day, ordered, tz, day_start_hour, secrets)
        if not sum(counts.values()):
            continue
        folder = md_dir / day[:7]
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{day}.md").write_text(markdown, encoding="utf-8")
        index.append({"day": day, **counts})
    (out / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    rows = ["# Chat archive", "", f"Day boundary: {day_start_hour:02d}:00 ({tz.key})", "",
            "| Day | User | Assistant |", "|---|---:|---:|"]
    rows += [f"| [{row['day']}](md/{row['day'][:7]}/{row['day']}.md) | {row['user']} | {row['assistant']} |"
             for row in reversed(index)]
    (out / "README.md").write_text("\n".join(rows) + "\n", encoding="utf-8")
    return index


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-dir", type=Path, help="Directory of archived JSONL snapshots")
    parser.add_argument("--live-dir", type=Path, help="Directory of current JSONL sessions")
    parser.add_argument("--api-db", type=Path, help="Optional SQLite messages database")
    parser.add_argument("--out", type=Path, required=True, help="Local output directory")
    parser.add_argument("--tz", default="UTC", help="IANA time zone, e.g. Asia/Shanghai")
    parser.add_argument("--day-start", type=int, default=0, help="Hour when a new archive day begins")
    parser.add_argument("--secrets-file", type=Path, help="Optional one-secret-per-line file; never copied to output")
    args = parser.parse_args(argv)
    if not any((args.archive_dir, args.live_dir, args.api_db)):
        parser.error("Provide --archive-dir, --live-dir, or --api-db")
    index = export(args.archive_dir, args.live_dir, args.out, args.api_db, args.tz, args.day_start, args.secrets_file)
    print(f"Exported {len(index)} days to {args.out}")


if __name__ == "__main__":
    main()
