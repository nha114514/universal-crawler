"""
FFmpeg Merger & FFprobe Media Inspector
Losslessly merges DASH video stream and audio stream into MP4 and performs strict verification using ffprobe
"""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional


class MergerError(Exception):
    def __init__(self, code: str, message: str, remediation: str = ""):
        super().__init__(f"[{code}] {message}")
        self.code = code
        self.message = message
        self.remediation = remediation


class MediaMerger:
    def verify_mp4(self, file_path, expected_duration=0, max_bytes=None):
        path = Path(file_path)
        if max_bytes is not None and path.stat().st_size > max_bytes:
            raise MergerError('SIZE_LIMIT', 'Merged file exceeds size limit')
        probe = self.probe(path)
        if not probe['has_video'] or not probe['has_audio']:
            raise MergerError('MEDIA_TYPE_MISMATCH', 'File does not contain complete video and audio tracks')
        if 'mp4' not in probe.get('format_name', '').split(','):
            raise MergerError('MEDIA_TYPE_MISMATCH', 'Media container is not MP4; cannot be saved by simply renaming extension')
        expected = float(expected_duration or 0)
        actual = float(probe.get('duration') or 0)
        if expected > 0 and (actual <= 0 or abs(actual - expected) > max(2.0, expected * 0.02)):
            raise MergerError('DURATION_MISMATCH', 'File duration does not match selected part; partial download may have occurred')
        return probe

    def __init__(self, ffmpeg_path: Optional[str] = None, ffprobe_path: Optional[str] = None):
        self.ffmpeg = ffmpeg_path or shutil.which("ffmpeg") or str(Path.home() / ".local/bin/ffmpeg.cmd")
        self.ffprobe = ffprobe_path or shutil.which("ffprobe") or str(Path.home() / ".local/bin/ffprobe.cmd")

        if not shutil.which(self.ffmpeg) and not Path(self.ffmpeg).exists():
            raise MergerError("FFMPEG_NOT_FOUND", f"FFmpeg executable not found: {self.ffmpeg}", "Please install FFmpeg and add to PATH")
        if not shutil.which(self.ffprobe) and not Path(self.ffprobe).exists():
            raise MergerError("FFPROBE_NOT_FOUND", f"FFprobe executable not found: {self.ffprobe}", "Please install FFprobe and add to PATH")

    def merge_dash(self, video_file: Path, audio_file: Path, output_file: Path) -> Path:
        """
        Lossless stream copy merge of DASH video and audio streams.
        """
        video_file = Path(video_file).resolve()
        audio_file = Path(audio_file).resolve()
        output_file = Path(output_file).resolve()
        output_file.parent.mkdir(parents=True, exist_ok=True)

        if not video_file.exists():
            raise MergerError("FILE_NOT_FOUND", f"Video track file does not exist: {video_file}")
        if not audio_file.exists():
            raise MergerError("FILE_NOT_FOUND", f"Audio track file does not exist: {audio_file}")

        # Fast stream merge with ffmpeg -c:v copy -c:a copy
        cmd = [
            self.ffmpeg,
            "-y",
            "-i", str(video_file),
            "-i", str(audio_file),
            "-c:v", "copy",
            "-c:a", "copy",
            "-movflags", "+faststart",
            str(output_file),
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=60)
        except Exception as exc:
            raise MergerError("FFMPEG_EXECUTION_FAILED", f"Failed to execute FFmpeg command: {exc}")

        if res.returncode != 0:
            raise MergerError(
                "MERGE_FAILED",
                f"FFmpeg merge failed (exit code {res.returncode}):\n{res.stderr[-500:]}",
                "Check input media track format or stream encoding",
            )

        if not output_file.exists() or output_file.stat().st_size == 0:
            raise MergerError("EMPTY_OUTPUT", "Merged output MP4 file does not exist or is empty")

        return output_file

    def probe(self, file_path: Path) -> Dict[str, Any]:
        """
        Probe media file using ffprobe, checking video track, audio track, resolution, and duration.
        """
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise MergerError("FILE_NOT_FOUND", f"Probe target does not exist: {file_path}")

        cmd = [
            self.ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]

        try:
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
        except Exception as exc:
            raise MergerError("FFPROBE_EXECUTION_FAILED", f"Failed to execute FFprobe: {exc}")

        if res.returncode != 0:
            raise MergerError("PROBE_FAILED", f"FFprobe probe failed (exit code {res.returncode}): {res.stderr}")

        try:
            info = json.loads(res.stdout)
        except Exception as exc:
            raise MergerError("PROBE_PARSE_ERROR", f"Failed to parse FFprobe JSON output: {exc}")

        streams = info.get("streams", [])
        fmt = info.get("format", {})

        video_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

        duration = None
        if "duration" in fmt:
            try:
                duration = float(fmt["duration"])
            except ValueError:
                pass
        if duration is None and video_stream and "duration" in video_stream:
            try:
                duration = float(video_stream["duration"])
            except ValueError:
                pass

        return {
            "format_name": fmt.get("format_name", ""),
            "duration": duration,
            "size": int(fmt.get("size", 0)),
            "bit_rate": int(fmt.get("bit_rate", 0)) if fmt.get("bit_rate") else None,
            "has_video": video_stream is not None,
            "video": {
                "codec": video_stream.get("codec_name"),
                "profile": video_stream.get("profile"),
                "width": video_stream.get("width"),
                "height": video_stream.get("height"),
                "r_frame_rate": video_stream.get("r_frame_rate"),
            } if video_stream else None,
            "has_audio": audio_stream is not None,
            "audio": {
                "codec": audio_stream.get("codec_name"),
                "channels": audio_stream.get("channels"),
                "sample_rate": audio_stream.get("sample_rate"),
            } if audio_stream else None,
        }
