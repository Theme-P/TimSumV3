"""
Audio clip extraction utility.
Extracts ~10-second audio clips per speaker from diarized segments using ffmpeg.
"""
import os
import subprocess
import tempfile
import base64
from typing import Dict, Any, List


def find_best_segment_for_speaker(segments: List[dict], speaker_label: str, target_duration: float = 10.0) -> dict:
    """
    Find the best audio segment for a speaker — picks the longest continuous
    speaking segment (or merges consecutive ones) up to target_duration seconds.
    
    Returns: {"start": float, "end": float, "duration": float}
    """
    # Get all segments for this speaker
    speaker_segments = [
        s for s in segments 
        if s.get('speaker') == speaker_label
    ]
    
    if not speaker_segments:
        return None
    
    # Sort by start time
    speaker_segments.sort(key=lambda x: x['start'])
    
    # Strategy 1: Find the single longest segment
    longest = max(speaker_segments, key=lambda s: s['end'] - s['start'])
    longest_duration = longest['end'] - longest['start']
    
    # If single segment is >= target, use it (capped at target_duration)
    if longest_duration >= target_duration:
        return {
            "start": longest['start'],
            "end": longest['start'] + target_duration,
            "duration": target_duration
        }
    
    # Strategy 2: Merge consecutive segments to reach target_duration
    best_start = speaker_segments[0]['start']
    best_end = speaker_segments[0]['end']
    best_duration = best_end - best_start
    
    for i in range(len(speaker_segments)):
        current_start = speaker_segments[i]['start']
        current_end = speaker_segments[i]['end']
        merged_duration = current_end - current_start
        
        # Try extending with next segments (allow up to 3s gap between segments)
        for j in range(i + 1, len(speaker_segments)):
            gap = speaker_segments[j]['start'] - current_end
            if gap > 3.0:  # Too much gap, stop merging
                break
            current_end = speaker_segments[j]['end']
            merged_duration = current_end - current_start
            
            if merged_duration >= target_duration:
                break
        
        if merged_duration > best_duration:
            best_start = current_start
            best_end = current_end
            best_duration = merged_duration
        
        if best_duration >= target_duration:
            break
    
    # Cap at target_duration
    actual_duration = min(best_duration, target_duration)
    
    return {
        "start": best_start,
        "end": best_start + actual_duration,
        "duration": actual_duration
    }


def extract_clip_ffmpeg(audio_file: str, start: float, duration: float, output_path: str) -> bool:
    """
    Extract an audio clip using ffmpeg.
    Outputs as MP3 for browser compatibility and small size.
    """
    try:
        cmd = [
            'ffmpeg', '-y',
            '-i', audio_file,
            '-ss', str(start),
            '-t', str(duration),
            '-ac', '1',           # Mono
            '-ar', '16000',       # 16kHz sample rate
            '-b:a', '64k',        # 64kbps bitrate (small file)
            '-f', 'mp3',
            output_path
        ]
        
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30
        )
        
        return result.returncode == 0 and os.path.exists(output_path)
        
    except (subprocess.TimeoutExpired, Exception) as e:
        print(f"   ⚠️ ffmpeg error: {e}")
        return False


def extract_speaker_clips(
    audio_file: str,
    segments: List[dict],
    clip_dir: str,
    target_duration: float = 10.0
) -> Dict[str, Any]:
    """
    Extract audio clips for each unique speaker from diarized segments.
    
    Args:
        audio_file: Path to the original audio file
        segments: List of diarized segments with 'speaker', 'start', 'end' keys
        clip_dir: Directory to save the clips
        target_duration: Target clip duration in seconds (default: 10)
    
    Returns:
        Dict mapping speaker labels to clip info:
        {
            "คนพูด 1": {
                "clip_file": "/path/to/clip.mp3",
                "clip_filename": "speaker_0.mp3",
                "start": 12.5,
                "end": 22.5,
                "duration": 10.0
            },
            ...
        }
    """
    # Get unique speakers
    speakers = set()
    for seg in segments:
        speaker = seg.get('speaker')
        if speaker:
            speakers.add(speaker)
    
    speakers = sorted(speakers)
    
    os.makedirs(clip_dir, exist_ok=True)
    
    clips = {}
    
    for idx, speaker in enumerate(speakers):
        print(f"   🔊 Extracting clip for {speaker}...")
        
        # Find best segment
        best = find_best_segment_for_speaker(segments, speaker, target_duration)
        
        if not best:
            print(f"   ⚠️ No segments found for {speaker}")
            continue
        
        # Extract clip
        clip_filename = f"speaker_{idx}.mp3"
        clip_path = os.path.join(clip_dir, clip_filename)
        
        success = extract_clip_ffmpeg(
            audio_file,
            start=best['start'],
            duration=best['duration'],
            output_path=clip_path
        )
        
        if success:
            clips[speaker] = {
                "clip_file": clip_path,
                "clip_filename": clip_filename,
                "start": best['start'],
                "end": best['end'],
                "duration": best['duration']
            }
            print(f"   ✅ {speaker}: {best['duration']:.1f}s clip ({best['start']:.1f}s - {best['end']:.1f}s)")
        else:
            print(f"   ❌ Failed to extract clip for {speaker}")
    
    return clips
