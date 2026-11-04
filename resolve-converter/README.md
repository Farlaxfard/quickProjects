# Resolve-Compatible Video Converter

Batch-converts video files to **DNxHR** (`.mov` container, PCM audio) so DaVinci Resolve on Linux can actually import them.

## Why?

DaVinci Resolve (free) on Linux doesn't include H.264/H.265 decode support. This tool re-encodes your clips to DNxHR, which Resolve handles natively.

## Requirements

- Python 3.10+
- `ffmpeg` and `ffprobe` installed and on your `PATH`

## Quick Start

```bash
# Convert all videos in a folder
python convert.py ~/Videos

# Custom output directory
python convert.py ~/Videos -o ~/ResolveReady

# Watch a folder for new files and auto-convert
python convert.py ~/Videos --watch

# Recursively search subdirectories
python convert.py ~/Videos -r

# Use a different quality profile
python convert.py ~/Videos --profile sq
```

## Quality Profiles

| Flag   | Profile     | Use Case                        |
|--------|-------------|----------------------------------|
| `lb`   | DNxHR LB    | Proxy/offline editing            |
| `sq`   | DNxHR SQ    | Standard quality                 |
| `hq`   | DNxHR HQ    | **Recommended** — high quality   |
| `hqx`  | DNxHR HQX   | High quality, 10-bit             |
| `444`  | DNxHR 444   | Maximum quality, huge files      |

## Notes

- Output files go into `<input_dir>/converted/` by default
- Already-converted DNxHR files are automatically skipped
- Existing outputs are skipped unless you pass `--overwrite`
- The `--watch` mode polls every 5 seconds (customizable with `--watch-interval`)
