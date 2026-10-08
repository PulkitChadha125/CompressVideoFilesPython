import json
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
PRESET = "fast"      # use "fast" or "veryfast" for speed; "medium" is much slower

# Downscale videos larger than this to speed up encoding and reduce memory use.
# Set to 0 to disable downscaling.
MAX_WIDTH = 1920
MAX_HEIGHT = 1080

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


def ffprobe_exe(ffmpeg_exe: str) -> str:
    ffmpeg_path = Path(ffmpeg_exe)
    if ffmpeg_path.name.startswith("ffmpeg"):
        probe_name = ffmpeg_path.name.replace("ffmpeg", "ffprobe", 1)
        candidate = ffmpeg_path.with_name(probe_name)
        if candidate.exists():
            return str(candidate)
    if _run_version_check("ffprobe"):
        return "ffprobe"
    raise RuntimeError("ffprobe not found alongside ffmpeg.")


def probe_video(ffprobe: str, input_path: Path) -> dict:
    cmd = [
        ffprobe,
        "-v", "quiet",
        "-print_format", "json",
        "-show_streams",
        "-show_format",
        str(input_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def get_video_stream(probe_data: dict) -> dict | None:
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "video":
            return stream
    return None


def get_audio_stream(probe_data: dict) -> dict | None:
    for stream in probe_data.get("streams", []):
        if stream.get("codec_type") == "audio":
            return stream
    return None


def format_duration(seconds: float | None) -> str:
    if not seconds:
        return "unknown"
    seconds = int(seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    return f"{minutes}m {secs}s"


def needs_downscale(width: int, height: int) -> bool:
    if MAX_WIDTH <= 0 or MAX_HEIGHT <= 0:
        return False
    return width > MAX_WIDTH or height > MAX_HEIGHT


def build_scale_filter(width: int, height: int) -> str | None:
    if not needs_downscale(width, height):
        return None
    return (
        f"scale="
        f"'min({MAX_WIDTH},iw)':'min({MAX_HEIGHT},ih)'"
        f":force_original_aspect_ratio=decrease"
    )


def detect_hw_encoder(ffmpeg_exe: str) -> str | None:
    result = subprocess.run(
        [ffmpeg_exe, "-hide_banner", "-encoders"],
        capture_output=True,
        text=True,
        check=True,
    )
    listed = {
        encoder
        for encoder in ("h264_nvenc", "h264_qsv", "h264_amf")
        if encoder in result.stdout
    }
    for encoder in ("h264_nvenc", "h264_qsv", "h264_amf"):
        if encoder in listed and _test_hw_encoder(ffmpeg_exe, encoder):
            return encoder
    return None


def _test_hw_encoder(ffmpeg_exe: str, encoder: str) -> bool:
    encoder_args = {
        "h264_nvenc": ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0"],
        "h264_qsv": ["-c:v", "h264_qsv", "-global_quality", "23"],
        "h264_amf": ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "23", "-qp_p", "23"],
    }[encoder]
    cmd = [
        ffmpeg_exe,
        "-hide_banner",
        "-loglevel", "error",
        "-f", "lavfi",
        "-i", "color=black:s=64x64:d=0.1",
        *encoder_args,
        "-pix_fmt", "yuv420p",
        "-f", "null",
        "-",
    ]
    return subprocess.run(cmd, capture_output=True).returncode == 0


def build_video_encode_args(ffmpeg_exe: str, use_hw: bool) -> list[str]:
    if use_hw:
        hw_encoder = detect_hw_encoder(ffmpeg_exe)
        if hw_encoder == "h264_nvenc":
            return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", CRF, "-b:v", "0"]
        if hw_encoder == "h264_qsv":
            return ["-c:v", "h264_qsv", "-global_quality", CRF]
        if hw_encoder == "h264_amf":
            return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", CRF, "-qp_p", CRF]
    return [
        "-c:v", "libx264",
        "-crf", CRF,
        "-preset", PRESET,
        "-profile:v", "high",
        "-level", "4.1",
    ]


def build_audio_args(audio_stream: dict | None) -> list[str]:
    if audio_stream and audio_stream.get("codec_name") == "aac":
        return ["-c:a", "copy"]
    return ["-c:a", "aac", "-b:a", "160k", "-ac", "2", "-ar", "48000"]


def should_compress(file_path: Path) -> bool:
    return file_path.suffix.lower() in VIDEO_EXTS


def build_output_path(input_path: Path, output_dir: Path) -> Path:
    # Keep same name; write to output_dir
    return output_dir / input_path.with_suffix(".mp4").name


def is_valid_output(ffprobe: str, output_path: Path) -> bool:
    try:
        probe_data = probe_video(ffprobe, output_path)
        duration = float(probe_data.get("format", {}).get("duration") or 0)
        return duration > 0 and get_video_stream(probe_data) is not None
    except Exception:
        return False


def compress_file(ffmpeg_exe: str, ffprobe: str, input_path: Path, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        if is_valid_output(ffprobe, output_path):
            print(f"Skipping (exists): {output_path}")
            return 0
        print(f"Removing incomplete output: {output_path.name}")
        output_path.unlink()

    probe_data = probe_video(ffprobe, input_path)
    video_stream = get_video_stream(probe_data)
    if not video_stream:
        print(f"Skipping (no video stream): {input_path.name}")
        return 1

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = float(probe_data.get("format", {}).get("duration") or 0)
    fps_text = video_stream.get("r_frame_rate", "?")
    audio_stream = get_audio_stream(probe_data)

    scale_filter = build_scale_filter(width, height)
    video_args = build_video_encode_args(ffmpeg_exe, use_hw=True)
    audio_args = build_audio_args(audio_stream)

    encoder_name = video_args[1]
    scale_note = f" -> {MAX_WIDTH}x{MAX_HEIGHT} max" if scale_filter else ""
    print(
        f"Input: {input_path.name} | {width}x{height} @ {fps_text} | "
        f"duration {format_duration(duration)} | encoder {encoder_name}{scale_note}"
    )

    temp_output = output_path.with_name(f"{output_path.stem}.tmp{output_path.suffix}")
    if temp_output.exists():
        temp_output.unlink()

    def run_encode(video_args: list[str]) -> int:
        cmd = [
            ffmpeg_exe,
            "-hide_banner",
            "-loglevel", "error",
            "-stats",
            "-y",
            "-i", str(input_path),
            *video_args,
            "-pix_fmt", "yuv420p",
            *audio_args,
            "-movflags", "+faststart",
        ]
        if scale_filter:
            cmd.extend(["-vf", scale_filter])
        cmd.append(str(temp_output))
        return subprocess.run(cmd).returncode

    print(f"Compressing: {input_path.name} -> {output_path.name}")
    try:
        return_code = run_encode(video_args)
        if return_code != 0 and video_args[1] != "libx264":
            print("Hardware encoder failed, retrying with libx264...")
            temp_output.unlink(missing_ok=True)
            video_args = build_video_encode_args(ffmpeg_exe, use_hw=False)
            return_code = run_encode(video_args)

        if return_code != 0:
            temp_output.unlink(missing_ok=True)
            return return_code
        temp_output.replace(output_path)
        return 0
    except Exception:
        temp_output.unlink(missing_ok=True)
        raise


def compress_directory(source_dir: Path, ffmpeg_exe: str) -> None:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    ffprobe = ffprobe_exe(ffmpeg_exe)
    output_dir = source_dir / OUTPUT_SUBDIR
    output_dir.mkdir(parents=True, exist_ok=True)

    inputs = [p for p in source_dir.iterdir() if p.is_file() and should_compress(p)]
    if not inputs:
        print("No supported video files found.")
        return

    failures = 0
    for src in inputs:
        dst = build_output_path(src, output_dir)
        rc = compress_file(ffmpeg_exe, ffprobe, src, dst)
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
