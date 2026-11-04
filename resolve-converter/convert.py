#!/usr/bin/env python3
"""
Resolve-Compatible Video Converter
===================================
Batch-converts video files to DNxHR HQ in a .mov container with PCM audio,
which DaVinci Resolve on Linux can actually read.

Usage:
    python convert.py [INPUT_DIR] [OPTIONS]

Examples:
    python convert.py                         # convert videos in current dir
    python convert.py ~/Videos                # convert videos in ~/Videos
    python convert.py ~/Videos -o ~/Converted # custom output directory
    python convert.py ~/Videos --watch        # keep watching for new files
    python convert.py ~/Videos --profile sq   # use DNxHR SQ (higher quality)
"""

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

# Video extensions to look for
VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts", ".vob",
    ".3gp", ".ogv", ".f4v",
}

# DNxHR quality profiles (pick what suits your workflow)
DNXHR_PROFILES = {
    "lb":  "dnxhr_lb",   # Low Bandwidth — offline/proxy editing
    "sq":  "dnxhr_sq",   # Standard Quality
    "hq":  "dnxhr_hq",   # High Quality (recommended default)
    "hqx": "dnxhr_hqx",  # High Quality 10-bit
    "444": "dnxhr_444",   # 4:4:4 — highest quality, huge files
}

DEFAULT_PROFILE = "hq"


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


def find_videos(input_dir: Path, recursive: bool) -> list[Path]:
    """Find all video files in the input directory."""
    if recursive:
        return sorted(p for p in input_dir.rglob("*") if p.is_file() and is_video_file(p))
    return sorted(p for p in input_dir.iterdir() if p.is_file() and is_video_file(p))


def get_output_path(source: Path, input_dir: Path, output_dir: Path) -> Path:
    """Build the output path, mirroring subdirectory structure and adding a .mov extension."""
    relative = source.relative_to(input_dir)
    out = output_dir / relative.with_suffix(".mov")
    return out


def already_dnxhr(source: Path) -> bool:
    """Check if the file is already DNxHR-encoded (skip re-encoding)."""
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=codec_name",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(source),
            ],
            capture_output=True, text=True, timeout=15,
        )
        codec = result.stdout.strip().lower()
        return codec in ("dnxhd",)  # ffprobe reports DNxHR as "dnxhd"
    except Exception:
        return False


def convert_video(source: Path, dest: Path, profile: str, overwrite: bool) -> bool:
    """Convert a single video file to DNxHR .mov using ffmpeg."""
    if dest.exists() and not overwrite:
        print(f"  ⏭  Skipping (output exists): {dest.name}")
        return True

    if already_dnxhr(source):
        print(f"  ✅ Already DNxHR, skipping: {source.name}")
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)

    dnxhr_profile = DNXHR_PROFILES[profile]

    cmd = [
        "ffmpeg",
        "-y" if overwrite else "-n",
        "-i", str(source),
        "-c:v", "dnxhd",
        "-profile:v", dnxhr_profile,
        "-pix_fmt", "yuv422p",   # standard chroma for DNxHR HQ/SQ/LB
        "-c:a", "pcm_s16le",    # uncompressed audio — Resolve loves this
        str(dest),
    ]

    # For 10-bit profiles, use 10-bit pixel format
    if profile in ("hqx", "444"):
        cmd[cmd.index("yuv422p")] = "yuv422p10le" if profile == "hqx" else "yuv444p10le"

    print(f"  🔄 Converting: {source.name}  →  {dest.name}")
    print(f"     Profile: DNxHR {profile.upper()}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True, text=True, timeout=7200,  # 2-hour timeout per file
        )
        if result.returncode != 0:
            print(f"  ❌ FAILED: {source.name}")
            # Print last few lines of ffmpeg stderr for diagnostics
            err_lines = result.stderr.strip().splitlines()
            for line in err_lines[-5:]:
                print(f"     {line}")
            return False

        src_size = source.stat().st_size / (1024 * 1024)
        dst_size = dest.stat().st_size / (1024 * 1024)
        print(f"  ✅ Done: {dest.name}  ({src_size:.1f} MB → {dst_size:.1f} MB)")
        return True

    except subprocess.TimeoutExpired:
        print(f"  ❌ TIMEOUT after 2 hours: {source.name}")
        return False
    except Exception as e:
        print(f"  ❌ ERROR: {e}")
        return False


def run_batch(input_dir: Path, output_dir: Path, profile: str,
              recursive: bool, overwrite: bool) -> tuple[int, int]:
    """Convert all videos found in input_dir. Returns (success_count, fail_count)."""
    videos = find_videos(input_dir, recursive)
    if not videos:
        print("No video files found.")
        return 0, 0

    print(f"\nFound {len(videos)} video(s) in {input_dir}\n")

    ok, fail = 0, 0
    for i, video in enumerate(videos, 1):
        print(f"[{i}/{len(videos)}]")
        dest = get_output_path(video, input_dir, output_dir)
        if convert_video(video, dest, profile, overwrite):
            ok += 1
        else:
            fail += 1
        print()

    return ok, fail


def watch_mode(input_dir: Path, output_dir: Path, profile: str,
               recursive: bool, overwrite: bool, interval: int = 5):
    """Continuously watch for new video files and convert them."""
    seen: set[Path] = set()
    print(f"👀 Watching {input_dir} for new videos (Ctrl+C to stop)...\n")

    try:
        while True:
            videos = find_videos(input_dir, recursive)
            new = [v for v in videos if v not in seen]

            for video in new:
                dest = get_output_path(video, input_dir, output_dir)
                convert_video(video, dest, profile, overwrite)
                seen.add(video)
                print()

            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n\nStopped watching.")


def main():
    parser = argparse.ArgumentParser(
        description="Convert videos to DaVinci Resolve-compatible DNxHR .mov files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "input_dir", nargs="?", default=".",
        help="Directory containing video files (default: current directory)",
    )
    parser.add_argument(
        "-o", "--output", default=None,
        help="Output directory (default: <input_dir>/converted)",
    )
    parser.add_argument(
        "-p", "--profile", choices=DNXHR_PROFILES.keys(), default=DEFAULT_PROFILE,
        help=f"DNxHR quality profile (default: {DEFAULT_PROFILE})",
    )
    parser.add_argument(
        "-r", "--recursive", action="store_true",
        help="Search subdirectories for videos",
    )
    parser.add_argument(
        "--overwrite", action="store_true",
        help="Overwrite existing output files",
    )
    parser.add_argument(
        "--watch", action="store_true",
        help="Keep running and watch for new files",
    )
    parser.add_argument(
        "--watch-interval", type=int, default=5,
        help="Seconds between scans in watch mode (default: 5)",
    )

    args = parser.parse_args()

    input_dir = Path(args.input_dir).resolve()
    if not input_dir.is_dir():
        print(f"Error: '{input_dir}' is not a directory.", file=sys.stderr)
        sys.exit(1)

    output_dir = Path(args.output).resolve() if args.output else input_dir / "converted"

    # Sanity check: is ffmpeg available?
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
    except FileNotFoundError:
        print("Error: ffmpeg not found. Install it first.", file=sys.stderr)
        sys.exit(1)

    print("╔══════════════════════════════════════════╗")
    print("║   Resolve-Compatible Video Converter     ║")
    print("╚══════════════════════════════════════════╝")
    print(f"  Input:   {input_dir}")
    print(f"  Output:  {output_dir}")
    print(f"  Profile: DNxHR {args.profile.upper()}")
    print(f"  Mode:    {'watch' if args.watch else 'batch'}")

    if args.watch:
        watch_mode(input_dir, output_dir, args.profile,
                   args.recursive, args.overwrite, args.watch_interval)
    else:
        ok, fail = run_batch(input_dir, output_dir, args.profile,
                             args.recursive, args.overwrite)
        print("─" * 40)
        print(f"  ✅ Converted: {ok}")
        if fail:
            print(f"  ❌ Failed:    {fail}")
        print("─" * 40)
        sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
