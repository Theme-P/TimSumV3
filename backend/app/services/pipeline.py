import torch
import gc
import os
import time
import tempfile
import logging
from typing import Callable, Dict, Any, Optional
import whisperx

from ..core.config import PipelineConfig

logger = logging.getLogger(__name__)
from ..models.meeting import MEETING_TYPES
from ..services.summarizer import (
    detect_speaker_names,
    resolve_meeting_style,
    summarize_with_agendas,
    summarize_with_diarization,
)
from ..services.agenda_detector import detect_agendas
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


def is_cuda_oom(exc: Exception) -> bool:
    message = str(exc).lower()
    return "out of memory" in message and ("cuda" in message or "cublas" in message)

class TranscribeSummaryPipeline:
    """
    Combined pipeline that runs WhisperX transcription and configurable LLM summarization.
    Handles model loading, transcription, speaker diarization, and AI summary.
    """
    
    def __init__(self, config: PipelineConfig = None):
        self.config = config or PipelineConfig()
        self.model = None
        self.active_compute_type = self.config.COMPUTE_TYPE
        self.timing = {}

    def close(self):
        """Release model references and cached CUDA allocations."""
        model = self.model
        self.model = None
        if model is not None:
            del model
        clear_gpu_memory()
    
    def _load_model(self):
        """Load WhisperX model with optimized settings"""
        logger.info("Loading WhisperX model...")
        start = time.time()
        clear_gpu_memory()
        
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
                "initial_prompt": "สวัสดีครับ This is a meeting transcription. 这是一个会议记录。 ถอดเสียงการประชุมภาษาไทย อังกฤษ และจีน",
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
        
        compute_types = [self.config.COMPUTE_TYPE]
        fallback_compute_type = self.config.OOM_FALLBACK_COMPUTE_TYPE
        if fallback_compute_type and fallback_compute_type not in compute_types:
            compute_types.append(fallback_compute_type)

        for index, compute_type in enumerate(compute_types):
            try:
                self.model = whisperx.load_model(
                    self.config.MODEL_NAME,
                    self.config.DEVICE,
                    compute_type=compute_type,
                    **load_kwargs,
                )
                self.active_compute_type = compute_type
                break
            except Exception as exc:
                self.close()
                has_fallback = index + 1 < len(compute_types)
                if not is_cuda_oom(exc) or not has_fallback:
                    raise
                logger.warning(
                    "WhisperX model load ran out of VRAM with compute_type=%s; "
                    "retrying with %s",
                    compute_type,
                    compute_types[index + 1],
                )
        
        self.timing['model_load'] = time.time() - start
        logger.info(
            "Model loaded in %.2fs (compute_type=%s)",
            self.timing['model_load'],
            self.active_compute_type,
        )

    def _transcribe_audio(self, audio):
        batch_size = max(self.config.MIN_BATCH_SIZE, self.config.BATCH_SIZE)
        min_batch_size = min(self.config.MIN_BATCH_SIZE, batch_size)

        while True:
            transcribe_kwargs = {
                "batch_size": batch_size,
                "task": "transcribe",
            }
            if self.config.LANGUAGE:
                transcribe_kwargs["language"] = self.config.LANGUAGE

            try:
                return self.model.transcribe(audio, **transcribe_kwargs), batch_size
            except Exception as exc:
                if not is_cuda_oom(exc) or batch_size <= min_batch_size:
                    raise

                next_batch_size = max(min_batch_size, batch_size // 2)
                logger.warning(
                    "WhisperX transcription ran out of VRAM at batch_size=%s; "
                    "clearing CUDA cache and retrying with batch_size=%s",
                    batch_size,
                    next_batch_size,
                )
                clear_gpu_memory()
                batch_size = next_batch_size
    
    def process(
        self,
        audio_file: str,
        meeting_type_id: int = 0,
        on_progress=None,
        custom_prompt: str = "",
        voice_samples: list = None,
        mongo_service=None,
        cancellation_checker: Optional[Callable[[], None]] = None,
        run_summary: bool = True,
    ) -> Dict[str, Any]:
        """
        Process audio file: transcribe and summarize.

        Args:
            audio_file: Path to audio file
            meeting_type_id: Meeting type ID (0=auto-detect, 1-11=specific type)
            on_progress: Optional callback(step: str, progress: int) for status updates
            custom_prompt: Optional user instruction to append to summary prompt
            run_summary: When False, stop after transcription/diarization/agenda
                preparation and return enough artifacts for a separate summary task.

        Returns structured output with:
        - Full transcript with segments
        - Summary
        - Speaker audio clips (~10s per speaker)
        - Processing times
        """
        def _check_cancelled():
            if cancellation_checker:
                cancellation_checker()

        def _report(step: str, progress: int):
            _check_cancelled()
            if on_progress:
                on_progress(step, progress)
            _check_cancelled()

        total_start = time.time()

        logger.info(f"TranscribeSummaryPipeline starting — audio: {audio_file}")

        # Step 1: Load model
        _report("model_load", 5)
        self._load_model()
        _check_cancelled()
        
        # Step 2: Load audio
        _report("audio_load", 10)
        logger.info("Loading audio...")
        audio_start = time.time()
        audio = whisperx.load_audio(audio_file)
        audio_time = time.time() - audio_start
        logger.info(f"Audio loaded in {audio_time:.2f}s")
        _check_cancelled()
        
        # Step 3: Transcribe
        _report("transcribing", 20)
        logger.info("Transcribing...")
        trans_start = time.time()
        result, effective_batch_size = self._transcribe_audio(audio)
        _check_cancelled()
        
        # Detect language from result (WhisperX returns detected language)
        detected_language = result.get("language", self.config.LANGUAGE or "th")
        logger.info(f"Detected language: {detected_language}")
        trans_time = time.time() - trans_start
        logger.info(
            "Transcription completed in %.2fs (batch_size=%s)",
            trans_time,
            effective_batch_size,
        )
        
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
        _check_cancelled()
        
        # Clear transcription model to free VRAM
        self.close()
        
        # Step 4: Align transcript (word-level timestamps for better speaker assignment)
        alignment_success = False
        if self.config.ENABLE_ALIGNMENT:
            logger.info("Aligning transcript (word-level timestamps)...")
            align_start = time.time()

            # Use detected language for alignment (fallback chain: detected -> en -> skip)
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
                alignment_success = True
                align_time = time.time() - align_start
                logger.info(f"Alignment completed in {align_time:.2f}s (lang={align_language})")

                # Clear alignment model
                del align_model
                clear_gpu_memory()
            except Exception as e:
                # Fallback: try English alignment model (broader phoneme coverage)
                if align_language != "en":
                    logger.warning(f"Alignment failed for '{align_language}', trying English fallback: {e}")
                    try:
                        align_model, align_metadata = whisperx.load_align_model(
                            language_code="en",
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
                        alignment_success = True
                        align_time = time.time() - align_start
                        logger.info(f"Alignment completed with English fallback in {align_time:.2f}s")

                        del align_model
                        clear_gpu_memory()
                    except Exception as e2:
                        logger.warning(f"English fallback alignment also failed (using segment-level timestamps): {e2}")
                else:
                    logger.warning(f"Alignment skipped (will use segment-level timestamps): {e}")
        else:
            logger.info("Alignment disabled by ENABLE_ALIGNMENT=false")

        if not alignment_success:
            align_time = 0
        _check_cancelled()
        
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
        _check_cancelled()
        
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
            # Clean repetitive/hallucinated text at segment level
            # so the web UI and DOCX exports also show clean output
            text = clean_transcription(text)
            segment['text'] = text
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
        
        speaker_labels = list(speakers_time.keys())
        
        # Step 6: Extract audio clips per speaker (~10s each)
        clip_dir = ""
        speaker_clips = {}
        clip_time = 0
        if self.config.ENABLE_SPEAKER_CLIPS:
            logger.info("Extracting speaker audio clips...")
            clip_start = time.time()
            clip_parent_dir = os.path.dirname(audio_file) or None
            clip_dir = tempfile.mkdtemp(prefix="speaker_clips_", dir=clip_parent_dir)
            speaker_clips = extract_speaker_clips(
                audio_file=audio_file,
                segments=segments,
                clip_dir=clip_dir,
                target_duration=10.0
            )
            clip_time = time.time() - clip_start
            logger.info(f"Clip extraction completed in {clip_time:.2f}s")
        else:
            logger.info("Speaker clip extraction disabled by ENABLE_SPEAKER_CLIPS=false")
        _check_cancelled()
        
        # Step 7: Voice enrollment matching (if voice samples provided)
        voice_matches = {}
        if voice_samples and self.config.ENABLE_VOICE_MATCHING:
            logger.info(f"Attempting voice matching with {len(voice_samples)} enrolled samples...")
            voice_match_start = time.time()
            try:
                from ..services.voice_matching import VoiceMatchingService
                matcher = VoiceMatchingService(device=self.config.DEVICE)

                # Extract embeddings for each diarized speaker
                diarized_embeddings = {}
                for speaker_label in speaker_labels:
                    emb = matcher.extract_embedding_from_segments(
                        audio_file=audio_file,
                        segments=segments,
                        speaker_label=speaker_label,
                    )
                    if emb:
                        diarized_embeddings[speaker_label] = emb

                # Match against enrolled voice samples
                if diarized_embeddings:
                    voice_matches = matcher.match_speakers(
                        diarized_embeddings=diarized_embeddings,
                        voice_samples=voice_samples,
                    )
                    logger.info(f"Voice matching found {len(voice_matches)} matches")
                else:
                    logger.info("No diarized embeddings extracted, skipping voice matching")

                del matcher
                clear_gpu_memory()
            except Exception as e:
                logger.warning(f"Voice matching failed (falling back to LLM name detection): {e}")
                voice_matches = {}

            voice_match_time = time.time() - voice_match_start
            logger.info(f"Voice matching completed in {voice_match_time:.2f}s")
        else:
            voice_match_time = 0
            if voice_samples:
                logger.info("Voice matching disabled by ENABLE_VOICE_MATCHING=false")
        _check_cancelled()

        # Step 8: Detect speaker names from self-introductions
        # Only detect for speakers NOT already matched by voice enrollment
        detect_start = time.time()
        unmatched_labels = [s for s in speaker_labels if s not in voice_matches]
        if unmatched_labels and self.config.ENABLE_SPEAKER_NAME_DETECTION:
            logger.info("Detecting speaker names from introductions...")
            suggested_names = detect_speaker_names(
                transcript_with_speakers,
                unmatched_labels,
                mongo_service=mongo_service,
                cancel_check=_check_cancelled,
            )
        else:
            suggested_names = {}
            if unmatched_labels:
                logger.info("Speaker name detection disabled by ENABLE_SPEAKER_NAME_DETECTION=false")
        detect_time = time.time() - detect_start

        # Merge voice matches into suggested_names (voice matches take priority)
        for speaker, match_info in voice_matches.items():
            suggested_names[speaker] = {
                "name": match_info["name"],
                "position": match_info.get("position", ""),
                "confidence": match_info.get("confidence", 0),
                "source": "voice_enrollment",
            }

        if suggested_names:
            for speaker, info in suggested_names.items():
                name_str = info['name']
                if info.get('position'):
                    name_str += f" ({info['position']})"
                logger.info(f"Speaker detected: {speaker} → {name_str}")
        else:
            logger.info("No speaker introductions detected")
        logger.info(f"Name detection completed in {detect_time:.2f}s")
        _check_cancelled()

        transcript_for_style = transcript_with_speakers or combined_text
        effective_meeting_type_id, meeting_style_classification, meeting_style_source = resolve_meeting_style(
            transcript_for_style,
            meeting_type_id,
            mongo_service=mongo_service,
            cancel_check=_check_cancelled,
        )
        resolved_meeting_style = (
            effective_meeting_type_id,
            meeting_style_classification,
            meeting_style_source,
        )
        logger.info(
            "Meeting style resolved: id=%s source=%s key=%s",
            effective_meeting_type_id,
            meeting_style_source,
            meeting_style_classification.get("meeting_type", "general_meeting"),
        )
        _check_cancelled()

        # Step 9: Agenda detection — agenda boundaries come from transcript markers/context only.
        _report("detecting_agendas", 70)
        agenda_result = {"agendas": [], "detection_mode": "single_topic"}
        agenda_time = 0
        if self.config.ENABLE_AGENDA_DETECTION:
            logger.info("Detecting agenda/topic boundaries...")
            agenda_start = time.time()
            agenda_result = detect_agendas(
                segments=segments,
                meeting_type_id=effective_meeting_type_id,
                mongo_service=mongo_service,
                allow_semantic_split=(meeting_type_id == 0),
                cancel_check=_check_cancelled,
            )
            agenda_time = time.time() - agenda_start
        else:
            logger.info("Agenda detection disabled by ENABLE_AGENDA_DETECTION=false")
        detected_agendas = agenda_result.get("agendas", [])
        detection_mode = agenda_result.get("detection_mode", "single_topic")
        logger.info(
            f"Agenda detection completed in {agenda_time:.2f}s — "
            f"mode: {detection_mode}, agendas: {len(detected_agendas)}"
        )
        _check_cancelled()

        # Step 10: Run the incremental summary pipeline, optionally grouped by agenda.
        enriched_agendas = []
        summary_metadata = {}
        summary_text = ""
        summary_time = 0
        if run_summary:
            _report("summarizing", 75)
            meeting_info = MEETING_TYPES.get(effective_meeting_type_id, MEETING_TYPES[0])
            logger.info(f"Running AI Summary ({meeting_info['thai']})...")
            summary_start = time.time()

            if detected_agendas:
                # Multi-agenda path: summarize each agenda + executive summary
                logger.info(f"Using agenda-aware summarization ({len(detected_agendas)} agendas)")
                summary_text, enriched_agendas, summary_metadata = summarize_with_agendas(
                    segments=segments,
                    agendas=detected_agendas,
                    meeting_type_id=effective_meeting_type_id,
                    custom_prompt=custom_prompt,
                    mongo_service=mongo_service,
                    return_metadata=True,
                    resolved_meeting_style=resolved_meeting_style,
                    cancel_check=_check_cancelled,
                )
            else:
                # Standard single-topic path
                summary_text, summary_metadata = summarize_with_diarization(
                    transcript_with_speakers,
                    speaker_summary,
                    meeting_type_id=effective_meeting_type_id,
                    custom_prompt=custom_prompt,
                    mongo_service=mongo_service,
                    segments=segments,
                    return_metadata=True,
                    resolved_meeting_style=resolved_meeting_style,
                    cancel_check=_check_cancelled,
                )
            _check_cancelled()

            summary_metadata = {
                **summary_metadata,
                "meeting_style_id": effective_meeting_type_id,
                "meeting_style_source": meeting_style_source,
                "meeting_style_key": meeting_style_classification.get("meeting_type", "general_meeting"),
                "agenda_detection_mode": detection_mode,
                "agenda_count": len(detected_agendas),
                "agenda_split_reasons": agenda_result.get("split_reasons", []),
            }

            summary_time = time.time() - summary_start
            logger.info(f"Summary API completed in {summary_time:.2f}s")
        else:
            _report("summary_queued", 75)
            enriched_agendas = detected_agendas
            summary_metadata = {
                "version": "async_pending",
                "meeting_style_id": effective_meeting_type_id,
                "meeting_style_source": meeting_style_source,
                "meeting_style_key": meeting_style_classification.get("meeting_type", "general_meeting"),
                "agenda_detection_mode": detection_mode,
                "agenda_count": len(detected_agendas),
                "agenda_split_reasons": agenda_result.get("split_reasons", []),
                "degraded": False,
            }
        
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
                'voice_matching': voice_match_time,
                'agenda_detection': agenda_time,
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
            'summary_metadata': summary_metadata,
            'agendas': enriched_agendas,
            'detection_mode': detection_mode,
            'speaker_clips': speaker_clips,
            'clip_dir': clip_dir,
            'suggested_names': suggested_names,
            'detected_language': detected_language,
            'effective_meeting_type_id': effective_meeting_type_id,
            'meeting_style_source': meeting_style_source,
            'meeting_style_classification': meeting_style_classification,
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
        print("🤖 AI SUMMARY (LLM)")
        print("=" * 60)
        print(output['summary'])
        
        print("\n" + "=" * 60)
        print("✅ Pipeline completed successfully!")
        print("=" * 60)
