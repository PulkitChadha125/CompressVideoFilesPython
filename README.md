## CompressVideoFiles

Simple Python utility to compress videos in a specified folder using FFmpeg (H.264, CRF 23, medium preset). On Windows, it will auto-download a portable FFmpeg build if FFmpeg is not already available.

### Requirements
- Python 3.8+
- Windows (auto-download path targets Windows portable FFmpeg)

### Setup
1. Create the virtual environment and install requirements:
   - PowerShell:
     ```powershell
     python -m venv .venv
     .\.venv\Scripts\Activate.ps1
     python -m pip install --upgrade pip
     pip install -r requirements.txt
     ```

### Usage
By default, the script scans `D:\Compress these videos` (non-recursive), writes compressed outputs into a `Compressed` subfolder, and skips files that already have a compressed version.

Run:
```powershell
python compress_videos.py
```

You can change the source folder, CRF, or preset at the top of `compress_videos.py`.

### Notes
- The auto-downloaded FFmpeg is placed under `ffmpeg/` inside the project. If you have FFmpeg in PATH already, the script will use that instead.

