from typing import Optional

def format_speaker(speaker: Optional[str]) -> str:
    """Format speaker label to Thai"""
    if speaker and speaker.startswith('SPEAKER_'):
        num = int(speaker.split('_')[1]) + 1
        return f"คนพูด {num}"
    return speaker or "Unknown"

def format_time(seconds: float) -> str:
    """Format seconds to MM:SS.ms"""
    m = int(seconds // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 100)
    return f"{m:02d}:{s:02d}.{ms:02d}"
