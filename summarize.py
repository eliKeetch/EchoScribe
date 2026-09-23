"""Generate restart-safe local transcript summaries with Ollama."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence


DEFAULT_MODEL = "gemma3:4b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
CHUNK_SIZE = 18_000

SUMMARY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "main_points": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
        },
        "decisions": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "action_items": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
        "open_questions": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 8,
        },
    },
    "required": ["main_points", "decisions", "action_items", "open_questions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class SummaryPaths:
    txt: Path
    json: Path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize completed EchoScribe transcripts with local Ollama."
    )
    parser.add_argument("transcripts", type=Path, help="EchoScribe transcript directory")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Ollama model name")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA_URL)
    parser.add_argument("--force", action="store_true", help="Regenerate summaries")
    return parser.parse_args(argv)


def configure_logging(directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    log_path = directory / "summarization.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    return log_path


def discover_transcripts(directory: Path) -> list[Path]:
    results: list[Path] = []
    for path in directory.rglob("*.json"):
        if path.name.endswith(".summary.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if (
            isinstance(data, dict)
            and data.get("status") == "complete"
            and isinstance(data.get("segments"), list)
            and path.with_suffix(".txt").is_file()
        ):
            results.append(path)
    return sorted(results, key=lambda item: str(item).casefold())


def summary_paths_for(transcript_json: Path) -> SummaryPaths:
    base = transcript_json.with_suffix("")
    return SummaryPaths(
        txt=base.with_name(base.name + ".summary.txt"),
        json=base.with_name(base.name + ".summary.json"),
    )


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def summary_is_complete(paths: SummaryPaths, transcript_hash: str, model: str) -> bool:
    if not paths.txt.is_file() or not paths.json.is_file():
        return False
    try:
        data = json.loads(paths.json.read_text(encoding="utf-8"))
        txt_hash = sha256_bytes(paths.txt.read_bytes())
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    return (
        isinstance(data, dict)
        and data.get("status") == "complete"
        and data.get("source_transcript_sha256") == transcript_hash
        and data.get("summary_model") == model
        and data.get("summary_txt_sha256") == txt_hash
        and all(isinstance(data.get(key), list) for key in SUMMARY_SCHEMA["required"])
    )


def ollama_request(
    base_url: str, endpoint: str, payload: dict[str, Any] | None = None, timeout: int = 900
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}{endpoint}"
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="GET" if payload is None else "POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Ollama returned HTTP {error.code}: {detail}") from error
    except urllib.error.URLError as error:
        raise RuntimeError(
            "Could not connect to Ollama at "
            f"{base_url}. Open the Ollama application and try again. ({error.reason})"
        ) from error


def require_model(base_url: str, model: str) -> None:
    response = ollama_request(base_url, "/api/tags", timeout=15)
    installed = {
        str(item.get("name") or item.get("model"))
        for item in response.get("models", [])
        if isinstance(item, dict)
    }
    normalized = model if ":" in model else f"{model}:latest"
    if model not in installed and normalized not in installed:
        raise RuntimeError(
            f"Ollama model '{model}' is not installed. Run: ollama pull {model}"
        )


def split_transcript(text: str, target_size: int = CHUNK_SIZE) -> list[str]:
    paragraphs = [part.strip() for part in text.splitlines() if part.strip()]
    if not paragraphs:
        return []
    chunks: list[str] = []
    current: list[str] = []
    current_size = 0
    for paragraph in paragraphs:
        if current and current_size + len(paragraph) + 1 > target_size:
            chunks.append("\n".join(current))
            current = []
            current_size = 0
        if len(paragraph) > target_size:
            if current:
                chunks.append("\n".join(current))
                current = []
                current_size = 0
            for start in range(0, len(paragraph), target_size):
                chunks.append(paragraph[start : start + target_size])
            continue
        current.append(paragraph)
        current_size += len(paragraph) + 1
    if current:
        chunks.append("\n".join(current))
    return chunks


def validate_summary(value: Any) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        raise ValueError("Ollama returned a non-object summary")
    cleaned: dict[str, list[str]] = {}
    for key in SUMMARY_SCHEMA["required"]:
        items = value.get(key)
        if not isinstance(items, list):
            raise ValueError(f"Ollama summary is missing the '{key}' list")
        cleaned[key] = [str(item).strip() for item in items if str(item).strip()]
    return cleaned


def request_structured_summary(
    base_url: str, model: str, prompt: str
) -> dict[str, list[str]]:
    last_error: Exception | None = None
    for attempt in range(2):
        retry_instruction = (
            "\nIMPORTANT: Your previous response was malformed or truncated. Return compact, "
            "complete JSON only. Use short bullets and stay within the schema limits."
            if attempt
            else ""
        )
        response = ollama_request(
            base_url,
            "/api/chat",
            {
                "model": model,
                "messages": [{"role": "user", "content": prompt + retry_instruction}],
                "stream": False,
                "format": SUMMARY_SCHEMA,
                "options": {"temperature": 0, "num_predict": 2048},
            },
        )
        message = response.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            last_error = ValueError("Ollama response did not contain summary content")
            continue
        try:
            return validate_summary(json.loads(message["content"]))
        except (json.JSONDecodeError, ValueError) as error:
            last_error = error
            logging.warning("Malformed Ollama structured output; retrying: %s", error)
    assert last_error is not None
    raise last_error


def summarize_text(base_url: str, model: str, text: str, context: str) -> dict[str, list[str]]:
    prompt = f"""You are summarizing a transcript excerpt from {context}.

Return only facts supported by the transcript. Be concise and specific. Do not invent
names, decisions, deadlines, responsibilities, or questions. Put general takeaways in
main_points. Put explicit decisions in decisions. Put tasks with an owner or requested
next step in action_items. Put unresolved issues in open_questions. Empty categories
must be empty arrays.

TRANSCRIPT:
{text}
"""
    return request_structured_summary(base_url, model, prompt)


def summarize_transcript(base_url: str, model: str, text: str) -> dict[str, list[str]]:
    chunks = split_transcript(text)
    if not chunks:
        return {key: [] for key in SUMMARY_SCHEMA["required"]}
    partials = [
        summarize_text(base_url, model, chunk, f"part {index} of {len(chunks)}")
        for index, chunk in enumerate(chunks, start=1)
    ]
    if len(partials) == 1:
        return partials[0]
    combined = json.dumps(partials, ensure_ascii=False, indent=2)
    prompt = f"""Consolidate these partial transcript summaries into one final summary.
Remove duplicates, preserve concrete details, and do not add information. Return the
same JSON schema with main_points, decisions, action_items, and open_questions arrays.

PARTIAL SUMMARIES:
{combined}
"""
    return request_structured_summary(base_url, model, prompt)


def render_summary(summary: dict[str, list[str]]) -> str:
    headings = (
        ("MAIN POINTS", "main_points"),
        ("DECISIONS", "decisions"),
        ("ACTION ITEMS", "action_items"),
        ("OPEN QUESTIONS", "open_questions"),
    )
    sections: list[str] = []
    for heading, key in headings:
        items = summary[key]
        bullets = "\n".join(f"- {item}" for item in items) if items else "- None identified."
        sections.append(f"{heading}\n\n{bullets}")
    return "\n\n".join(sections) + "\n"


def write_temporary_text(destination: Path, content: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    path = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return path


def write_summary_atomically(paths: SummaryPaths, txt: str, data: dict[str, Any]) -> None:
    temporary: list[Path] = []
    try:
        temporary.append(write_temporary_text(paths.txt, txt))
        temporary.append(
            write_temporary_text(
                paths.json, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            )
        )
        os.replace(temporary[0], paths.txt)
        os.replace(temporary[1], paths.json)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def format_duration(seconds: float) -> str:
    total = max(0, round(seconds))
    minutes, second = divmod(total, 60)
    hours, minute = divmod(minutes, 60)
    return f"{hours:02d}:{minute:02d}:{second:02d}"


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    directory = args.transcripts.expanduser().resolve()
    if not directory.is_dir():
        print(f"ERROR: Transcript directory does not exist: {directory}")
        return 2
    configure_logging(directory)
    started = time.monotonic()
    logging.info("Summary batch started at %s | directory=%s model=%s", datetime.now().astimezone().isoformat(), directory, args.model)

    transcripts = discover_transcripts(directory)
    print(f"Found {len(transcripts)} completed transcripts.\n")
    try:
        require_model(args.ollama_url, args.model)
    except Exception as error:
        print(f"ERROR: {error}")
        logging.exception("Ollama readiness check failed")
        return 2

    generated = skipped = failed = 0
    for index, transcript_json in enumerate(transcripts, start=1):
        txt_path = transcript_json.with_suffix(".txt")
        paths = summary_paths_for(transcript_json)
        try:
            transcript_bytes = txt_path.read_bytes()
            transcript_hash = sha256_bytes(transcript_bytes)
            if not args.force and summary_is_complete(paths, transcript_hash, args.model):
                skipped += 1
                print(f"[{index}/{len(transcripts)}] SKIPPED (summary complete):\n{txt_path}\n")
                logging.info("Summary skipped: %s", txt_path)
                continue

            print(f"[{index}/{len(transcripts)}] Summarizing:\n{txt_path}\n")
            logging.info("Summary started: %s", txt_path)
            item_started = time.monotonic()
            transcript_text = transcript_bytes.decode("utf-8")
            source_data = json.loads(transcript_json.read_text(encoding="utf-8"))
            summary = summarize_transcript(args.ollama_url, args.model, transcript_text)
            summary_txt = render_summary(summary)
            payload: dict[str, Any] = {
                "status": "complete",
                "source_transcript": str(transcript_json.relative_to(directory)),
                "source_video_relative_path": source_data.get("original_relative_path"),
                "source_transcript_sha256": transcript_hash,
                "summary_txt_sha256": sha256_bytes(summary_txt.encode("utf-8")),
                "summary_model": args.model,
                **summary,
            }
            write_summary_atomically(paths, summary_txt, payload)
            elapsed = time.monotonic() - item_started
            generated += 1
            print(f"Summary completed in: {format_duration(elapsed)}\n")
            logging.info("Summary completed: %s | elapsed=%s", txt_path, format_duration(elapsed))
        except Exception as error:
            failed += 1
            print(f"ERROR: Failed to summarize: {txt_path}")
            print(f"       {type(error).__name__}: {error}\n")
            logging.exception("Summary failed: %s", txt_path)

    runtime = time.monotonic() - started
    print("========== SUMMARIES COMPLETE ==========\n")
    print(f"Transcripts discovered: {len(transcripts)}")
    print(f"Summaries generated: {generated}")
    print(f"Summaries already complete/skipped: {skipped}")
    print(f"Summaries failed: {failed}")
    print(f"Summary runtime: {format_duration(runtime)}")
    logging.info("Summary final | discovered=%d generated=%d skipped=%d failed=%d runtime=%s", len(transcripts), generated, skipped, failed, format_duration(runtime))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
