"""EchoScribe: restart-safe, recursive local video transcription."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Sequence

from faster_whisper import WhisperModel


APP_NAME = "EchoScribe"
DEFAULT_MODEL = "large-v3"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".m4v", ".webm"}
TRANSCRIPTS_DIRECTORY = "_transcripts"
CUDA_ERROR_MARKERS = (
    "cuda",
    "cudnn",
    "cublas",
    "nvcuda",
    "cudart",
    "cannot load library",
    "library not found",
    "dll",
    "driver version",
)


@dataclass(frozen=True)
class OutputPaths:
    txt: Path
    srt: Path
    json: Path


@dataclass
class ModelRuntime:
    model: WhisperModel
    model_name: str
    device: str
    compute_type: str


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog=APP_NAME,
        description="Recursively transcribe a local video directory with faster-whisper.",
    )
    parser.add_argument("source", type=Path, help="Directory containing video files")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Whisper model name or local model path (default: {DEFAULT_MODEL})",
    )
    parser.add_argument(
        "--device",
        choices=("auto", "cuda", "cpu"),
        default="auto",
        help="Processing device (default: auto)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retranscribe videos even when complete output already exists",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output directory (default: <source>\\_transcripts)",
    )
    return parser.parse_args(argv)


def configure_logging(output_directory: Path) -> Path:
    output_directory.mkdir(parents=True, exist_ok=True)
    log_path = output_directory / "transcription.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.FileHandler(log_path, encoding="utf-8")],
        force=True,
    )
    return log_path


def discover_videos(source: Path, output_directory: Path) -> list[Path]:
    videos: list[Path] = []
    output_resolved = output_directory.resolve()

    for root, directory_names, file_names in os.walk(source, followlinks=False):
        root_path = Path(root)
        kept_directories: list[str] = []
        for name in directory_names:
            candidate = root_path / name
            if name.casefold() == TRANSCRIPTS_DIRECTORY.casefold():
                continue
            if candidate.resolve() == output_resolved:
                continue
            kept_directories.append(name)
        directory_names[:] = kept_directories

        for name in file_names:
            path = root_path / name
            if path.suffix.casefold() in VIDEO_EXTENSIONS:
                videos.append(path.resolve())

    return sorted(videos, key=lambda path: str(path).casefold())


def output_paths_for(video: Path, source: Path, output_directory: Path) -> OutputPaths:
    relative_parent = video.relative_to(source).parent
    destination = output_directory / relative_parent
    base = destination / video.stem
    return OutputPaths(
        txt=base.with_suffix(".txt"),
        srt=base.with_suffix(".srt"),
        json=base.with_suffix(".json"),
    )


def validate_unique_outputs(
    videos: Iterable[Path], source: Path, output_directory: Path
) -> None:
    owners: dict[Path, Path] = {}
    collisions: list[tuple[Path, Path, Path]] = []
    for video in videos:
        destination = output_paths_for(video, source, output_directory).json
        key = Path(str(destination).casefold())
        previous = owners.get(key)
        if previous is not None:
            collisions.append((destination, previous, video))
        else:
            owners[key] = video

    if collisions:
        details = "\n".join(
            f"  {first}\n  {second}\n  -> {destination}"
            for destination, first, second in collisions
        )
        raise ValueError(
            "Two videos would write the same transcript name. Rename one of these "
            f"same-stem files:\n{details}"
        )


def outputs_are_complete(paths: OutputPaths, relative_video: Path) -> bool:
    expected = (paths.txt, paths.srt, paths.json)
    if not all(path.is_file() for path in expected):
        return False
    try:
        data = json.loads(paths.json.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    hashes = data.get("output_sha256")
    if not isinstance(hashes, dict):
        return False
    try:
        txt_hash = hashlib.sha256(paths.txt.read_bytes()).hexdigest()
        srt_hash = hashlib.sha256(paths.srt.read_bytes()).hexdigest()
    except OSError:
        return False
    return (
        data.get("status") == "complete"
        and data.get("original_relative_path") == str(relative_video)
        and isinstance(data.get("segments"), list)
        and hashes.get("txt") == txt_hash
        and hashes.get("srt") == srt_hash
    )


def cuda_device_count() -> int:
    try:
        import ctranslate2

        return int(ctranslate2.get_cuda_device_count())
    except Exception as error:  # Device probing must never prevent CPU use.
        logging.warning("CUDA probe failed; selecting CPU: %s", error)
        return 0


def requested_runtime(device_option: str) -> tuple[str, str]:
    if device_option == "cpu":
        return "cpu", "int8"
    if device_option == "cuda":
        return "cuda", "float16"
    if cuda_device_count() > 0:
        return "cuda", "float16"
    return "cpu", "int8"


def is_cuda_error(error: BaseException) -> bool:
    message = f"{type(error).__name__}: {error}".casefold()
    return any(marker in message for marker in CUDA_ERROR_MARKERS)


def load_model(model_name: str, device: str, compute_type: str) -> WhisperModel:
    logging.info(
        "Loading model=%s device=%s compute_type=%s",
        model_name,
        device,
        compute_type,
    )
    return WhisperModel(model_name, device=device, compute_type=compute_type)


def initialize_model(model_name: str, device_option: str) -> ModelRuntime:
    device, compute_type = requested_runtime(device_option)
    try:
        model = load_model(model_name, device, compute_type)
        return ModelRuntime(model, model_name, device, compute_type)
    except Exception as error:
        if device == "cuda" and is_cuda_error(error):
            warning = (
                "WARNING: CUDA could not be initialized. Required NVIDIA CUDA/cuDNN "
                f"libraries may be missing or incompatible. Falling back to CPU. ({error})"
            )
            print(warning, file=sys.stderr)
            logging.warning(warning)
            model = load_model(model_name, "cpu", "int8")
            return ModelRuntime(model, model_name, "cpu", "int8")
        raise


def switch_to_cpu(runtime: ModelRuntime, error: BaseException) -> None:
    warning = (
        "WARNING: CUDA failed while starting transcription. Falling back to CPU and "
        f"retrying this video. ({error})"
    )
    print(warning, file=sys.stderr)
    logging.warning(warning)
    runtime.model = load_model(runtime.model_name, "cpu", "int8")
    runtime.device = "cpu"
    runtime.compute_type = "int8"


def transcribe_video(
    runtime: ModelRuntime, video: Path
) -> tuple[list[TranscriptSegment], str | None, float | None, float | None]:
    raw_segments, info = runtime.model.transcribe(
        str(video),
        beam_size=5,
        vad_filter=True,
    )
    segments = [
        TranscriptSegment(
            start=float(segment.start),
            end=float(segment.end),
            text=segment.text.strip(),
        )
        for segment in raw_segments
    ]
    language = getattr(info, "language", None)
    language_probability = getattr(info, "language_probability", None)
    duration = getattr(info, "duration", None)
    return segments, language, language_probability, duration


def format_timestamp(seconds: float) -> str:
    total_milliseconds = round(max(0.0, seconds) * 1000)
    total_seconds, milliseconds = divmod(total_milliseconds, 1000)
    total_minutes, seconds_part = divmod(total_seconds, 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds_part:02d},{milliseconds:03d}"


def format_duration(seconds: float | None) -> str:
    if seconds is None:
        return "unknown"
    total_seconds = max(0, round(seconds))
    minutes, seconds_part = divmod(total_seconds, 60)
    hours, minutes_part = divmod(minutes, 60)
    return f"{hours:02d}:{minutes_part:02d}:{seconds_part:02d}"


def render_txt(segments: Iterable[TranscriptSegment]) -> str:
    lines = [segment.text for segment in segments if segment.text]
    return "\n".join(lines) + ("\n" if lines else "")


def render_srt(segments: Iterable[TranscriptSegment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        text = segment.text.replace("\r\n", "\n").replace("\r", "\n")
        blocks.append(
            f"{index}\n{format_timestamp(segment.start)} --> "
            f"{format_timestamp(segment.end)}\n{text}"
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def json_document(
    video: Path,
    source: Path,
    runtime: ModelRuntime,
    segments: Iterable[TranscriptSegment],
    language: str | None,
    language_probability: float | None,
    txt_content: str,
    srt_content: str,
) -> dict[str, Any]:
    relative_video = video.relative_to(source)
    return {
        "status": "complete",
        "original_video_filename": video.name,
        "original_relative_path": str(relative_video),
        "detected_language": language,
        "language_probability": language_probability,
        "transcription_model": runtime.model_name,
        "transcription_device": runtime.device,
        "output_sha256": {
            "txt": hashlib.sha256(txt_content.encode("utf-8")).hexdigest(),
            "srt": hashlib.sha256(srt_content.encode("utf-8")).hexdigest(),
        },
        "segments": [
            {"start": item.start, "end": item.end, "text": item.text}
            for item in segments
        ],
    }


def write_temporary_text(destination: Path, content: str) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise
    return temporary_path


def write_outputs_atomically(
    paths: OutputPaths, txt_content: str, srt_content: str, json_content: str
) -> None:
    temporary_paths: list[Path] = []
    try:
        # Append one at a time so already-created files are tracked for cleanup
        # if a later temporary write fails.
        temporary_paths.append(write_temporary_text(paths.txt, txt_content))
        temporary_paths.append(write_temporary_text(paths.srt, srt_content))
        temporary_paths.append(write_temporary_text(paths.json, json_content))
        # JSON is the completion record, so publish it last.
        os.replace(temporary_paths[0], paths.txt)
        os.replace(temporary_paths[1], paths.srt)
        os.replace(temporary_paths[2], paths.json)
    finally:
        for temporary_path in temporary_paths:
            temporary_path.unlink(missing_ok=True)


def print_device(runtime: ModelRuntime) -> None:
    label = "NVIDIA GPU / CUDA" if runtime.device == "cuda" else "CPU"
    print(f"Device: {label} ({runtime.compute_type})")
    print(f"Model: {runtime.model_name}\n")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    source = args.source.expanduser().resolve()
    if not source.is_dir():
        print(f"ERROR: Source directory does not exist or is not a directory: {source}", file=sys.stderr)
        return 2

    output_directory = (
        args.output.expanduser().resolve()
        if args.output
        else source / TRANSCRIPTS_DIRECTORY
    )
    if output_directory == source:
        print("ERROR: The output directory cannot be the source directory itself.", file=sys.stderr)
        return 2

    log_path = configure_logging(output_directory)
    batch_started = time.monotonic()
    logging.info("========== %s started at %s ==========", APP_NAME, datetime.now().astimezone().isoformat())
    logging.info("Source=%s output=%s model=%s requested_device=%s force=%s", source, output_directory, args.model, args.device, args.force)

    videos = discover_videos(source, output_directory)
    print(f"Found {len(videos)} videos.\n")
    logging.info("Videos discovered: %d", len(videos))
    try:
        validate_unique_outputs(videos, source, output_directory)
    except ValueError as error:
        print(f"ERROR: {error}", file=sys.stderr)
        logging.error("%s", error)
        return 2

    complete_before_start = [
        video
        for video in videos
        if not args.force
        and outputs_are_complete(
            output_paths_for(video, source, output_directory), video.relative_to(source)
        )
    ]
    pending_count = len(videos) - len(complete_before_start)
    runtime: ModelRuntime | None = None
    if pending_count:
        try:
            runtime = initialize_model(args.model, args.device)
        except Exception as error:
            print(f"ERROR: Could not load model '{args.model}': {error}", file=sys.stderr)
            logging.exception("Could not load model")
            return 2
        print_device(runtime)
        logging.info("Detected device=%s compute_type=%s model=%s", runtime.device, runtime.compute_type, runtime.model_name)
    else:
        print("All discovered videos already have complete transcripts.\n")
        logging.info("No model loaded because every video is already complete")

    succeeded = 0
    skipped = 0
    failed = 0
    cuda_runtime_fallback_used = False

    for index, video in enumerate(videos, start=1):
        relative_video = video.relative_to(source)
        paths = output_paths_for(video, source, output_directory)
        if not args.force and outputs_are_complete(paths, relative_video):
            skipped += 1
            print(f"[{index}/{len(videos)}] SKIPPED (already complete):\n{video}\n")
            logging.info("SKIPPED already complete: %s", video)
            continue

        assert runtime is not None
        print(f"[{index}/{len(videos)}] Transcribing:\n{video}\n")
        logging.info("Video started: %s", video)
        video_started = time.monotonic()
        try:
            try:
                segments, language, probability, duration = transcribe_video(runtime, video)
            except Exception as error:
                if (
                    runtime.device == "cuda"
                    and not cuda_runtime_fallback_used
                    and is_cuda_error(error)
                ):
                    cuda_runtime_fallback_used = True
                    switch_to_cpu(runtime, error)
                    print_device(runtime)
                    segments, language, probability, duration = transcribe_video(runtime, video)
                else:
                    raise

            txt_content = render_txt(segments)
            srt_content = render_srt(segments)
            payload = json_document(
                video,
                source,
                runtime,
                segments,
                language,
                float(probability) if probability is not None else None,
                txt_content,
                srt_content,
            )
            write_outputs_atomically(
                paths,
                txt_content,
                srt_content,
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            )
            elapsed = time.monotonic() - video_started
            probability_text = (
                f" ({float(probability):.1%})" if probability is not None else ""
            )
            print(f"Language: {language or 'unknown'}{probability_text}")
            print(f"Duration: {format_duration(float(duration) if duration is not None else None)}")
            print(f"Completed in: {format_duration(elapsed)}\n")
            succeeded += 1
            logging.info(
                "Video completed: %s | elapsed=%s | language=%s | probability=%s",
                video,
                format_duration(elapsed),
                language,
                probability,
            )
        except Exception as error:
            failed += 1
            elapsed = time.monotonic() - video_started
            print(f"ERROR: Failed to transcribe: {video}", file=sys.stderr)
            print(f"       {type(error).__name__}: {error}\n", file=sys.stderr)
            logging.exception("Video failed: %s | elapsed=%s", video, format_duration(elapsed))

    total_runtime = time.monotonic() - batch_started
    summary = (
        "========== COMPLETE ==========\n\n"
        f"Videos discovered: {len(videos)}\n"
        f"Successfully transcribed: {succeeded}\n"
        f"Already completed/skipped: {skipped}\n"
        f"Failed: {failed}\n"
        f"Total runtime: {format_duration(total_runtime)}\n\n"
        f"Transcripts:\n{output_directory}"
    )
    print(summary)
    logging.info(
        "Final summary | discovered=%d succeeded=%d skipped=%d failed=%d runtime=%s transcripts=%s log=%s",
        len(videos),
        succeeded,
        skipped,
        failed,
        format_duration(total_runtime),
        output_directory,
        log_path,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
