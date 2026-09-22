# EchoScribe

EchoScribe is a local, restart-safe bulk video transcription application for Windows. Its desktop interface lets you select folders and monitor progress without using PowerShell. It recursively finds videos, transcribes them with `faster-whisper`, and writes TXT, SRT, and JSON output while preserving the source directory structure.

Local-first is the recommended setup for a folder already on your Windows PC. The videos do not need to be uploaded, and EchoScribe can use a local NVIDIA GPU. A server/web interface can be added later without replacing the transcription core.

## Requirements

- Windows 10 or 11
- Python 3.12 (64-bit)
- Enough free disk space for the Whisper model and transcripts
- Optional: an NVIDIA GPU with compatible CUDA 12/cuDNN 9 runtime libraries

`faster-whisper` uses PyAV, which bundles FFmpeg libraries, so a separate system FFmpeg installation is normally not required.

## 1. Install Python 3.12

Download Python 3.12 from [python.org](https://www.python.org/downloads/windows/). Choose the 64-bit installer and enable **Add Python to PATH** during installation.

Confirm the Python launcher can find it:

```powershell
py -3.12 --version
```

## 2. Create a virtual environment

From the EchoScribe project directory:

```powershell
py -3.12 -m venv .venv
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

The `Set-ExecutionPolicy` command only affects the current PowerShell process and is useful if Windows blocks the activation script.

## 3. Install dependencies

```powershell
pip install -r requirements.txt
```

The selected Whisper model is downloaded the first time it is used. `large-v3` requires several gigabytes of download/cache space.

## 4. Run the desktop application

Double-click:

```text
EchoScribe.bat
```

The application provides folder pickers, model and device controls, live progress and logs, safe stopping, and a shortcut to the transcript folder. Settings are remembered for the next launch.

Stopping the app during a transcription is safe. Start it again with the same folder, and completed videos will be skipped automatically.

You can also start the desktop interface from PowerShell:

```powershell
python gui.py
```

## 5. Run the command-line version

```powershell
python transcribe.py "D:\path\to\videos"
```

Or use the batch launcher from any directory:

```powershell
run.bat "D:\path\to\videos"
```

`run.bat` uses `.venv` when it exists next to the script; otherwise it uses the Windows Python launcher for Python 3.12.

By default, results and the log are stored under:

```text
D:\path\to\videos\_transcripts\
```

For example:

```text
videos\
├── customer-a\
│   ├── call1.mp4
│   └── call2.mp4
├── customer-b\
│   └── call3.mp4
└── _transcripts\
    ├── transcription.log
    ├── customer-a\
    │   ├── call1.txt
    │   ├── call1.srt
    │   ├── call1.json
    │   ├── call2.txt
    │   ├── call2.srt
    │   └── call2.json
    └── customer-b\
        ├── call3.txt
        ├── call3.srt
        └── call3.json
```

## Command-line options

Show built-in help:

```powershell
python transcribe.py --help
```

Use the default `large-v3` model and automatic device selection:

```powershell
python transcribe.py "D:\videos"
```

Use a different model:

```powershell
python transcribe.py "D:\videos" --model medium
```

Automatically prefer CUDA and fall back to CPU (the default):

```powershell
python transcribe.py "D:\videos" --device auto
```

Request CUDA explicitly. EchoScribe still falls back to CPU if CUDA libraries fail to initialize:

```powershell
python transcribe.py "D:\videos" --device cuda
```

Force CPU with int8 computation:

```powershell
python transcribe.py "D:\videos" --device cpu
```

Put output somewhere else:

```powershell
python transcribe.py "D:\videos" --output "E:\transcripts"
```

Retranscribe all discovered videos and replace their completed outputs:

```powershell
python transcribe.py "D:\videos" --force
```

Options can be combined:

```powershell
run.bat "D:\videos" --model medium --device cpu --output "E:\transcripts" --force
```

## CPU usage

CPU mode uses `compute_type="int8"` to reduce memory use and improve CPU throughput. `large-v3` is accurate but can be slow on a CPU. If speed matters more than maximum accuracy, try `--model medium` or `--model small`.

```powershell
python transcribe.py "D:\videos" --device cpu --model medium
```

## NVIDIA GPU / CUDA usage

Automatic mode asks CTranslate2 whether an NVIDIA CUDA device is visible. When one is available, EchoScribe uses:

```text
device="cuda"
compute_type="float16"
```

Current CTranslate2 releases used by `faster-whisper` require the CUDA 12 cuBLAS and cuDNN 9 runtime libraries. Your NVIDIA driver must also support the installed CUDA runtime. Keep the relevant DLL directory on the Windows `PATH`, then open a new terminal before running EchoScribe.

Check that Windows can see the driver and GPU:

```powershell
nvidia-smi
```

## CUDA/cuDNN troubleshooting

Typical GPU setup errors mention `cublas`, `cudnn`, `nvcuda`, a missing DLL, or failure to load a library. If that happens:

1. Update the NVIDIA display/compute driver.
2. Install compatible CUDA 12 cuBLAS and cuDNN 9 runtime libraries.
3. Ensure their `bin` directories are included in the Windows `PATH`.
4. Close and reopen PowerShell so it receives the updated `PATH`.
5. Run `nvidia-smi`, then retry with `--device cuda`.

EchoScribe prints a warning and retries on CPU when CUDA initialization fails, so a missing GPU library does not have to stop a long batch. The official `faster-whisper` README has the latest compatibility notes: [GPU requirements](https://github.com/SYSTRAN/faster-whisper#gpu).

To bypass CUDA entirely while troubleshooting:

```powershell
python transcribe.py "D:\videos" --device cpu
```

## Safe resume behavior

For each video, EchoScribe writes TXT, SRT, and JSON to temporary files in the destination directory, flushes them to disk, and atomically replaces the final files. The JSON completion record is published last.

A video is skipped only when all three final files exist and its JSON completion record is valid for that source-relative path. The completion record includes hashes of the final TXT and SRT files, preventing a partially replaced or edited set from being mistaken for a completed transaction. If the process is stopped during a video, the next run treats partial output as incomplete and transcribes that video again. Completed videos are not overwritten unless `--force` is supplied.

One corrupt video does not stop the rest of the batch. Failures and progress are appended to `_transcripts\transcription.log`, and the process returns a nonzero exit code if any videos fail.

## Supported video formats

- `.mp4`
- `.mov`
- `.mkv`
- `.avi`
- `.m4v`
- `.webm`

Discovery is recursive and ignores directories named `_transcripts`.

## Local app or Coolify web app?

Start with this local CLI when the videos are already on the Windows machine. It has direct filesystem access, no upload step, a smaller security surface, and straightforward GPU access.

A Coolify-hosted web app is useful when multiple people need uploads, job status, centralized results, or remote access. That version also needs authentication, upload limits, a persistent input/output volume, a durable job queue, a database, cleanup/retention rules, and backups. GPU acceleration additionally requires an NVIDIA-equipped Linux server, the NVIDIA Container Toolkit, and GPU access configured for the container. A typical inexpensive CPU-only VPS will make `large-v3` transcription slow.

A practical future architecture is to retain `transcribe.py` as the worker and add a thin web/API and job queue around it. Do not expose a transcription upload service publicly without authentication and file-size controls.
