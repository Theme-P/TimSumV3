import torch
import gc
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Fix for PyTorch 2.6+ compatibility with pyannote
# Must patch torch.load BEFORE importing whisperx/pyannote
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

import whisperx 
import time

# ===================== CONFIGURATION =====================
device = "cuda" 
# float16 = ดีกว่า int8 ทั้งความเร็วและคุณภาพบน GPU
compute_type = "float16"
HF_TOKEN = os.environ.get("HF_TOKEN", "")

# Batch size - ยิ่งสูงยิ่งเร็ว แต่ใช้ VRAM มากขึ้น
# A100 40GB: ใช้ได้ถึง 32, RTX 3090: ใช้ได้ 16-24
batch_size = 24

# Language - ระบุภาษาเพื่อข้าม language detection (เร็วขึ้น 10-15%)
language = "th"

# ===================== TRANSCRIBE OPTIONS =====================
# Note: beam_size, best_of ต้องใส่ตอน load_model ผ่าน asr_options
transcribe_options = {
    "batch_size": batch_size,
    "language": language,
    "task": "transcribe",  # สำคัญ! ต้องเป็น "transcribe" ไม่ใช่ "translate" (แปลเป็นอังกฤษ)
}

# ===================== VAD OPTIONS =====================
# Voice Activity Detection - optimized for overlapping speech detection
vad_options = {
    "vad_onset": 0.400,      # Lower = more sensitive to speech start
    "vad_offset": 0.300,     # Lower = faster silence detection  
    "min_duration_on": 0.05, # Catch short speech segments (sec)
    "min_duration_off": 0.05, # Catch short pauses/interruptions (sec)
}

# ===================== SPEAKER DIARIZATION OPTIONS =====================
# Set expected speaker count for better overlapping speech detection
min_speakers = 2    # Minimum expected speakers (None = auto detect)
max_speakers = None # Maximum expected speakers (None = auto detect)

# ===================== MAIN SCRIPT =====================
# รับ path ไฟล์เสียงจาก user
audio_file = input("📁 กรุณาใส่ path ไฟล์เสียง: ").strip().strip('"').strip("'")

# โหลด model พร้อม VAD options ที่ optimize แล้ว
print("🔄 Loading model...")
model_start = time.time()
model = whisperx.load_model(
    "large-v3",  # OpenAI Whisper large-v3 (รองรับหลายภาษารวมถึงไทย) 
    device, 
    compute_type=compute_type,
    language=language,
    asr_options={
        "beam_size": 5,
        "best_of": 5,
        "patience": 1.5,
        # เปิด condition_on_previous_text เพื่อให้ context ต่อเนื่อง ลดการ hallucinate
        "condition_on_previous_text": True,
        # ใช้หลาย temperatures เป็น fallback ถ้า temp ต่ำไม่ได้ผล
        "temperatures": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
        # ลด threshold เพื่อตรวจจับ repetition ได้ดีขึ้น
        "compression_ratio_threshold": 2.2,
        "log_prob_threshold": -0.8,
        "no_speech_threshold": 0.5,
        # initial_prompt ภาษาไทยช่วยให้โมเดลรู้ว่าต้อง transcribe ภาษาไทย
        "initial_prompt": "สวัสดีครับ นี่คือการถอดเสียงภาษาไทย",
        # ป้องกันการซ้ำ
        "repetition_penalty": 1.1,
        "length_penalty": 1.0,
    },
    vad_options=vad_options,
)
model_time = time.time() - model_start
print(f"   ⏱️ โหลด model: {model_time:.2f}s")

st_time = time.time()

# โหลด audio
print("🔄 Loading audio...")
audio_start = time.time()
audio = whisperx.load_audio(audio_file)
audio_time = time.time() - audio_start
print(f"   ⏱️ โหลด audio: {audio_time:.2f}s")

# Transcribe with optimized options
print("🎯 Transcribing...")
transcribe_start = time.time()
result = model.transcribe(audio, **transcribe_options)
transcribe_time = time.time() - transcribe_start
print(f"   ⏱️ Transcribe: {transcribe_time:.2f}s")

# ลบ model เพื่อเคลียร์ memory ก่อน diarization
del model
gc.collect()
torch.cuda.empty_cache()

# Speaker diarization
print("👥 Running speaker diarization...")
diarize_start = time.time()
diarize_model = whisperx.diarize.DiarizationPipeline(
    use_auth_token=HF_TOKEN, 
    device=device
)
diarize_segments = diarize_model(
    audio,
    min_speakers=min_speakers,
    max_speakers=max_speakers,
)
diarize_time = time.time() - diarize_start
print(f"   ⏱️ Diarization: {diarize_time:.2f}s")

# Assign speakers to segments
result = whisperx.assign_word_speakers(diarize_segments, result)

# ลบ diarize model
del diarize_model
gc.collect()
torch.cuda.empty_cache()

total_time = time.time() - st_time

# ===================== OUTPUT =====================
print(f"\n⏱️ Total processing time: {total_time:.2f} seconds")
print(f"   - โหลด audio: {audio_time:.2f}s")
print(f"   - Transcribe: {transcribe_time:.2f}s")
print(f"   - Diarization: {diarize_time:.2f}s")

# คำนวณ audio length และ speed
audio_length = len(audio) / 16000  # 16kHz sample rate
speed_factor = audio_length / total_time if total_time > 0 else 0
print(f"   - Audio length: {audio_length:.1f}s")
print(f"   - Speed: {speed_factor:.1f}x realtime")

print("\n" + "="*60)
print("📊 TRANSCRIPTION RESULTS")
print("="*60)
print(f"{'เวลาเริ่ม':<10} {'เวลาจบ':<10} {'คนพูด':<12} {'ข้อความ'}")
print("-"*60)

def format_speaker(speaker):
    if speaker and speaker.startswith('SPEAKER_'):
        num = int(speaker.split('_')[1]) + 1
        return f"คนพูด {num}"
    return speaker or "Unknown"

def format_time(seconds):
    m = int(seconds // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 100)
    return f"{m:02d}:{s:02d}.{ms:02d}"

# เรียงตามเวลา
segments = sorted(result['segments'], key=lambda x: x['start'])

for segment in segments:
    speaker = format_speaker(segment.get('speaker'))
    text = segment['text'].strip()
    start = format_time(segment['start'])
    end = format_time(segment['end'])
    print(f"{start:<10} {end:<10} {speaker:<12} {text}")

# Summary
print("\n" + "="*60)
print("📈 SPEAKER SUMMARY")
print("="*60)
speakers_time = {}
speakers_words = {}
for segment in segments:
    speaker = format_speaker(segment.get('speaker'))
    duration = segment['end'] - segment['start']
    word_count = len(segment['text'].split())
    speakers_time[speaker] = speakers_time.get(speaker, 0) + duration
    speakers_words[speaker] = speakers_words.get(speaker, 0) + word_count

total_speaking_time = sum(speakers_time.values())
for speaker, total in sorted(speakers_time.items()):
    percentage = (total / total_speaking_time * 100) if total_speaking_time > 0 else 0
    words = speakers_words.get(speaker, 0)
    print(f"  {speaker}: {format_time(total)} ({percentage:.1f}%) - {words} words")

# รวม text ทั้งหมด
combined_text = ' '.join(segment['text'].strip() for segment in segments)
print("\n" + "="*60)
print("📝 FULL TEXT:")
print("="*60)
print(combined_text)

# แสดง configuration ที่ใช้
print("\n" + "="*60)
print("⚙️ CONFIGURATION USED:")
print("="*60)
print(f"  Model: large-v3")
print(f"  Compute type: {compute_type}")
print(f"  Batch size: {batch_size}")
print(f"  Beam size: 5")
print(f"  Language: {language}")