import torch
import gc
import os
import time
import tempfile
import logging
from typing import Dict, Any, Optional
import whisperx

from ..core.config import PipelineConfig

logger = logging.getLogger(__name__)
from ..models.meeting import MEETING_TYPES
from ..services.summarizer import summarize_with_diarization, detect_speaker_names
from ..services.text_cleaner import clean_transcription
from ..utils.formatting import format_speaker, format_time
from ..utils.audio_clip import extract_speaker_clips

# Fix for PyTorch 2.6+ compatibility with pyannote
_original_torch_load = torch.load
def _patched_torch_load(*args, **kwargs):
    kwargs['weights_only'] = False
    return _original_torch_load(*args, **kwargs)
torch.load = _patched_torch_load

def clear_gpu_memory():
    """Clear GPU memory"""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

class TranscribeSummaryPipeline:
    """
    Combined pipeline that runs WhisperX transcription and GPT-4.1 summarization.
    Handles model loading, transcription, speaker diarization, and AI summary.
    """
    
    def __init__(self, config: PipelineConfig = None):
        self.config = config or PipelineConfig()
        self.model = None
        self.timing = {}
    
    def _load_model(self):
        """Load WhisperX model with optimized settings"""
        logger.info("Loading WhisperX model...")
        start = time.time()
        
        load_kwargs = {
            "asr_options": {
                "beam_size": self.config.BEAM_SIZE,
                "best_of": self.config.BEST_OF,
                "patience": self.config.PATIENCE,
                "condition_on_previous_text": True,
                "temperatures": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                "compression_ratio_threshold": 2.2,
                "log_prob_threshold": -0.8,
                "no_speech_threshold": 0.5,
                "initial_prompt": "สวัสดีครับ This is a meeting transcription. ถอดเสียงการประชุมภาษาไทยและอังกฤษ",
                "repetition_penalty": 1.1,
                "length_penalty": 1.0,
            },
            "vad_options": {
                "vad_onset": self.config.VAD_ONSET,
                "vad_offset": self.config.VAD_OFFSET,
                "min_duration_on": self.config.MIN_DURATION_ON,
                "min_duration_off": self.config.MIN_DURATION_OFF,
            },
        }
        
        # Only pass language if explicitly set (None = auto-detect)
        if self.config.LANGUAGE:
            load_kwargs["language"] = self.config.LANGUAGE
        
        self.model = whisperx.load_model(
            self.config.MODEL_NAME,
            self.config.DEVICE,
            compute_type=self.config.COMPUTE_TYPE,
            **load_kwargs,
        )
        
        self.timing['model_load'] = time.time() - start
        logger.info(f"Model loaded in {self.timing['model_load']:.2f}s")
    
    def process(self, audio_file: str, meeting_type_id: int = 0, on_progress=None) -> Dict[str, Any]:
        """
        Process audio file: transcribe and summarize.

        Args:
            audio_file: Path to audio file
            meeting_type_id: Meeting type ID (0=auto-detect, 1-11=specific type)
            on_progress: Optional callback(step: str, progress: int) for status updates

        Returns structured output with:
        - Full transcript with segments
        - Summary
        - Speaker audio clips (~10s per speaker)
        - Processing times
        """
        def _report(step: str, progress: int):
            if on_progress:
                on_progress(step, progress)

        total_start = time.time()

        logger.info(f"TranscribeSummaryPipeline starting — audio: {audio_file}")

        # Step 1: Load model
        _report("model_load", 5)
        self._load_model()
        
        # Step 2: Load audio
        _report("audio_load", 10)
        logger.info("Loading audio...")
        audio_start = time.time()
        audio = whisperx.load_audio(audio_file)
        audio_time = time.time() - audio_start
        logger.info(f"Audio loaded in {audio_time:.2f}s")
        
        # Step 3: Transcribe
        _report("transcribing", 20)
        logger.info("Transcribing...")
        trans_start = time.time()
        # Build transcribe kwargs
        transcribe_kwargs = {
            "batch_size": self.config.BATCH_SIZE,
            "task": "transcribe",
        }
        if self.config.LANGUAGE:
            transcribe_kwargs["language"] = self.config.LANGUAGE
        
        result = self.model.transcribe(audio, **transcribe_kwargs)
        
        # Detect language from result (WhisperX returns detected language)
        detected_language = result.get("language", self.config.LANGUAGE or "th")
        logger.info(f"Detected language: {detected_language}")
        trans_time = time.time() - trans_start
        logger.info(f"Transcription completed in {trans_time:.2f}s")
        
        # Extract text for summary
        combined_text = ' '.join(
            seg.get('text', '').strip() 
            for seg in result.get('segments', [])
        )
        
        # Clean transcription text (remove noise, repetitions) — from V3
        logger.info("Cleaning transcription text...")
        clean_start = time.time()
        combined_text = clean_transcription(combined_text)
        clean_time = time.time() - clean_start
        logger.info(f"Text cleaning completed in {clean_time:.2f}s")
        
        # Clear transcription model to free VRAM
        del self.model
        self.model = None
        clear_gpu_memory()
        
        # Step 4: Align transcript (word-level timestamps for better speaker assignment)
        logger.info("Aligning transcript (word-level timestamps)...")
        align_start = time.time()
        
        # Use detected language for alignment (fallback to Thai)
        align_language = detected_language or "th"
        logger.info(f"Aligning with language: {align_language}")
        
        try:
            align_model, align_metadata = whisperx.load_align_model(
                language_code=align_language,
                device=self.config.DEVICE
            )
            result = whisperx.align(
                result["segments"],
                align_model,
                align_metadata,
                audio,
                self.config.DEVICE,
                return_char_alignments=False,
            )
            align_time = time.time() - align_start
            logger.info(f"Alignment completed in {align_time:.2f}s")
            
            # Clear alignment model
            del align_model
            clear_gpu_memory()
        except Exception as e:
            align_time = 0
            logger.warning(f"Alignment skipped (will use segment-level timestamps): {e}")
        
        # Step 5: Run speaker diarization
        _report("diarizing", 50)
        logger.info("Running speaker diarization...")
        diarize_start = time.time()
        try:
            diarize_model = whisperx.diarize.DiarizationPipeline(
                use_auth_token=self.config.HF_TOKEN,
                device=self.config.DEVICE
            )
        except TypeError:
            # Newer pyannote versions use 'token' instead of 'use_auth_token'
            diarize_model = whisperx.diarize.DiarizationPipeline(
                token=self.config.HF_TOKEN,
                device=self.config.DEVICE
            )
        diarize_segments = diarize_model(
            audio,
            min_speakers=self.config.MIN_SPEAKERS,
            max_speakers=self.config.MAX_SPEAKERS,
        )
        diarize_time = time.time() - diarize_start
        logger.info(f"Diarization completed in {diarize_time:.2f}s")
        
        # Assign speakers to segments (with word-level alignment = much better accuracy)
        result = whisperx.assign_word_speakers(diarize_segments, result)
        
        # Clear diarization model
        del diarize_model
        clear_gpu_memory()
        
        # Build speaker summary and transcript with generic speaker labels
        segments = sorted(result.get('segments', []), key=lambda x: x['start'])
        speakers_time = {}
        speakers_words = {}
        transcript_lines = []
        
        for segment in segments:
            speaker = format_speaker(segment.get('speaker'))
            # Keep generic labels (คนพูด 1, คนพูด 2, ...)
            segment['speaker'] = speaker
            
            duration = segment['end'] - segment['start']
            text = segment.get('text', '').strip()
            word_count = len(text.split())
            speakers_time[speaker] = speakers_time.get(speaker, 0) + duration
            speakers_words[speaker] = speakers_words.get(speaker, 0) + word_count
            # Build transcript with speaker labels
            transcript_lines.append(f"[{speaker}]: {text}")
        
        transcript_with_speakers = "\n".join(transcript_lines)
        
        # Clean speaker transcript as well
        transcript_with_speakers = clean_transcription(transcript_with_speakers)
        speaker_summary = {
            'speaking_time': speakers_time,
            'word_count': speakers_words,
        }
        
        # Step 4: Extract audio clips per speaker (~10s each)
        logger.info("Extracting speaker audio clips...")
        clip_start = time.time()
        clip_dir = tempfile.mkdtemp(prefix="speaker_clips_")
        speaker_clips = extract_speaker_clips(
            audio_file=audio_file,
            segments=segments,
            clip_dir=clip_dir,
            target_duration=10.0
        )
        clip_time = time.time() - clip_start
        logger.info(f"Clip extraction completed in {clip_time:.2f}s")
        
        # Step: Detect speaker names from self-introductions
        logger.info("Detecting speaker names from introductions...")
        detect_start = time.time()
        speaker_labels = list(speakers_time.keys())
        suggested_names = detect_speaker_names(transcript_with_speakers, speaker_labels)
        detect_time = time.time() - detect_start
        if suggested_names:
            for speaker, info in suggested_names.items():
                name_str = info['name']
                if info.get('position'):
                    name_str += f" ({info['position']})"
                logger.info(f"Speaker detected: {speaker} → {name_str}")
        else:
            logger.info("No speaker introductions detected")
        logger.info(f"Name detection completed in {detect_time:.2f}s")
        
        # Step 3: Run summary with diarization data
        _report("summarizing", 75)
        meeting_info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES[0])
        logger.info(f"Running AI Summary ({meeting_info['thai']})...")
        summary_start = time.time()
        summary_text = summarize_with_diarization(
            transcript_with_speakers, 
            speaker_summary,
            meeting_type_id=meeting_type_id
        )
        summary_time = time.time() - summary_start
        logger.info(f"Summary API completed in {summary_time:.2f}s")
        
        total_time = time.time() - total_start
        
        # Calculate audio length and speed
        audio_length = len(audio) / 16000
        speed_factor = audio_length / total_time if total_time > 0 else 0
        
        # Build output
        output = {
            'audio_file': audio_file,
            'processing_time': {
                'model_load': self.timing.get('model_load', 0),
                'audio_load': audio_time,
                'transcription': trans_time,
                'alignment': align_time,
                'diarization': diarize_time,
                'summarization': summary_time,
                'clip_extraction': clip_time,
                'total': total_time,
            },
            'audio_length_seconds': audio_length,
            'speed_factor': speed_factor,
            'full_transcript': {
                'segments': segments,
                'combined_text': combined_text,
                'transcript_with_speakers': transcript_with_speakers,
                'speaker_summary': speaker_summary,
            },
            'summary': summary_text,
            'speaker_clips': speaker_clips,
            'clip_dir': clip_dir,
            'suggested_names': suggested_names,
            'detected_language': detected_language,
        }
        
        return output
    
    def print_results(self, output: Dict[str, Any]):
        """Pretty print the results"""
        print("\n" + "=" * 60)
        print("📊 PROCESSING SUMMARY")
        print("=" * 60)
        
        pt = output['processing_time']
        print(f"⏱️ Total processing time: {pt['total']:.2f}s")
        print(f"   - Model load: {pt['model_load']:.2f}s")
        print(f"   - Audio load: {pt['audio_load']:.2f}s")
        print(f"   - Transcription: {pt['transcription']:.2f}s")
        print(f"   - Alignment: {pt.get('alignment', 0):.2f}s")
        print(f"   - Diarization: {pt['diarization']:.2f}s")
        print(f"   - Summarization: {pt['summarization']:.2f}s")
        print(f"   - Audio length: {output['audio_length_seconds']:.1f}s")
        print(f"   - Speed: {output['speed_factor']:.1f}x realtime")
        
        # Transcription results
        print("\n" + "=" * 60)
        print("📝 FULL TRANSCRIPT")
        print("=" * 60)
        print(f"{'เวลาเริ่ม':<10} {'เวลาจบ':<10} {'คนพูด':<12} {'ข้อความ'}")
        print("-" * 60)
        
        for segment in output['full_transcript']['segments']:
            speaker = format_speaker(segment.get('speaker'))
            text = segment.get('text', '').strip()
            start = format_time(segment['start'])
            end = format_time(segment['end'])
            print(f"{start:<10} {end:<10} {speaker:<12} {text}")
        
        # Speaker summary
        print("\n" + "=" * 60)
        print("📈 SPEAKER SUMMARY")
        print("=" * 60)
        
        speakers_time = output['full_transcript']['speaker_summary']['speaking_time']
        speakers_words = output['full_transcript']['speaker_summary']['word_count']
        total_time = sum(speakers_time.values())
        
        for speaker, speaking_time in sorted(speakers_time.items()):
            pct = (speaking_time / total_time * 100) if total_time > 0 else 0
            words = speakers_words.get(speaker, 0)
            print(f"  {speaker}: {format_time(speaking_time)} ({pct:.1f}%) - {words} words")
        
        # Combined text
        print("\n" + "=" * 60)
        print("📋 COMBINED TEXT")
        print("=" * 60)
        print(output['full_transcript']['combined_text'])
        
        # Summary
        print("\n" + "=" * 60)
        print("🤖 AI SUMMARY (GPT-4.1)")
        print("=" * 60)
        print(output['summary'])
        
        print("\n" + "=" * 60)
        print("✅ Pipeline completed successfully!")
        print("=" * 60)
