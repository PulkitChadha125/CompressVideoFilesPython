import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path


# ---------- Configuration ----------
# Change this to your folder containing videos to compress
SOURCE_DIR = r"D:\\Compress these videos"

# Compression settings
CRF = "23"          # lower = better quality (larger file), higher = smaller file
PRESET = "medium"    # slower presets compress more efficiently (e.g., "slow")

# Output directory name inside SOURCE_DIR
OUTPUT_SUBDIR = "Compressed"

# Supported input extensions (lowercase)
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}


def is_windows() -> bool:
    return os.name == "nt"


def _run_version_check(exe: str) -> bool:
    try:
        subprocess.run([exe, "-version"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        return True
    except Exception:
        return False


def find_local_ffmpeg(project_root: Path) -> Path | None:
    # Expected portable layout after extraction: project_root/ffmpeg/bin/ffmpeg.exe
    exe_name = "ffmpeg.exe" if is_windows() else "ffmpeg"
    candidate = project_root / "ffmpeg" / "bin" / exe_name
    return candidate if candidate.exists() else None


def download_and_prepare_ffmpeg(project_root: Path) -> Path:
    """
    Downloads a portable FFmpeg (Windows) zip and extracts to project_root/ffmpeg.
    Tries to be resilient to changing inner folder names by detecting the single extracted root.
    """
    if not is_windows():
        raise RuntimeError("Auto-download is implemented for Windows only. Install ffmpeg via your package manager.")

    ffmpeg_dir = project_root / "ffmpeg"
    ffmpeg_dir.mkdir(parents=True, exist_ok=True)

    # Download a rolling 'essentials' release zip from gyan.dev (commonly used Windows builds)
    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
    tmp_dir = Path(tempfile.mkdtemp(prefix="ffmpeg_dl_"))
    zip_path = tmp_dir / "ffmpeg.zip"

    try:
        print("Downloading FFmpeg... (this may take a minute)")
        with urllib.request.urlopen(url) as resp, open(zip_path, "wb") as f:
            shutil.copyfileobj(resp, f)

        print("Extracting FFmpeg...")
        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(tmp_dir)

        # Find the single extracted root folder
        extracted_roots = [p for p in tmp_dir.iterdir() if p.is_dir() and p.name.lower().startswith("ffmpeg")]
        if not extracted_roots:
            raise RuntimeError("Failed to locate extracted FFmpeg folder.")
        extracted_root = max(extracted_roots, key=lambda p: len(p.name))

        # Move/merge into project_root/ffmpeg
        # If ffmpeg_dir already contains something stale, clear it first
        for child in ffmpeg_dir.iterdir():
            if child.is_file():
                child.unlink(missing_ok=True)
            else:
                shutil.rmtree(child, ignore_errors=True)

        # Move the extracted content into ffmpeg_dir
        for item in extracted_root.iterdir():
            dest = ffmpeg_dir / item.name
            if item.is_dir():
                shutil.move(str(item), str(dest))
            else:
                shutil.move(str(item), str(dest))

        exe_path = find_local_ffmpeg(project_root)
        if not exe_path:
            raise RuntimeError("FFmpeg executable not found after extraction.")
        return exe_path
    finally:
        # Cleanup temp folder
        shutil.rmtree(tmp_dir, ignore_errors=True)


def ensure_ffmpeg(project_root: Path) -> str:
    # 1) Prefer system ffmpeg if available
    if _run_version_check("ffmpeg"):
        return "ffmpeg"

    # 2) Try local portable copy under project_root/ffmpeg
    local = find_local_ffmpeg(project_root)
    if local and _run_version_check(str(local)):
        return str(local)

    # 3) Download portable FFmpeg for Windows and use it
    exe = download_and_prepare_ffmpeg(project_root)
    if not _run_version_check(str(exe)):
        raise RuntimeError("FFmpeg was downloaded but failed the version check.")
    return str(exe)


def should_compress(file_path: Path) -> bool:
    return file_path.suffix.lower() in VIDEO_EXTS


def build_output_path(input_path: Path, output_dir: Path) -> Path:
    # Keep same name; write to output_dir
    return output_dir / input_path.with_suffix(".mp4").name


def compress_file(ffmpeg_exe: str, input_path: Path, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Avoid overwriting existing compressed files
    if output_path.exists():
        print(f"Skipping (exists): {output_path}")
        return 0

    cmd = [
        ffmpeg_exe,
        "-i", str(input_path),
        "-vcodec", "libx264",
        "-crf", CRF,
        "-preset", PRESET,
        # Improve Windows Media Player compatibility
        "-profile:v", "high",
        "-level", "4.1",
        "-pix_fmt", "yuv420p",
        # Audio: AAC LC stereo 48 kHz with explicit bitrate
        "-acodec", "aac",
        "-b:a", "160k",
        "-ac", "2",
        "-ar", "48000",
        "-movflags", "+faststart",
        str(output_path),
    ]
    print(f"Compressing: {input_path.name} -> {output_path.name}")
    result = subprocess.run(cmd)
    return result.returncode


def compress_directory(source_dir: Path, ffmpeg_exe: str) -> None:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    output_dir = source_dir / OUTPUT_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs = [p for p in source_dir.iterdir() if p.is_file() and should_compress(p)]
    if not inputs:
        print("No supported video files found.")
        return

    failures = 0
    for src in inputs:
        dst = build_output_path(src, output_dir)
        rc = compress_file(ffmpeg_exe, src, dst)
        if rc != 0:
            print(f"Failed: {src}")
            failures += 1

    print()
    print(f"Done. {len(inputs) - failures} succeeded, {failures} failed.")


def main() -> int:
    project_root = Path(__file__).resolve().parent
    try:
        ffmpeg_exe = ensure_ffmpeg(project_root)
    except Exception as e:
        print(f"Error ensuring FFmpeg: {e}")
        return 1

    source_dir = Path(SOURCE_DIR)
    try:
        compress_directory(source_dir, ffmpeg_exe)
    except Exception as e:
        print(f"Error: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())


