from pathlib import Path
from compress_videos import ensure_ffmpeg

if __name__ == "__main__":
    exe = ensure_ffmpeg(Path(__file__).resolve().parent)
    print(exe)

