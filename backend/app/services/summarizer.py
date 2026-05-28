"""
Enhanced Summarizer — Merged from ST + V3.

Uses GPT-4.1 via NTC API Gateway for all LLM calls.
Adds: auto-classification, hierarchical chunking for long transcripts,
and keyword-based fallback classification.
"""

import requests
import os
import json
import re
import logging
from typing import Dict, Tuple, Optional

from ..models.meeting import MEETING_TYPES, get_meeting_focus_prompt

logger = logging.getLogger(__name__)

# NTC AI Gateway API configuration
NTC_API_KEY = os.getenv("NTC_API_KEY")
NTC_API_URL = os.getenv("NTC_API_URL", "https://aigateway.ntictsolution.com/v1/chat/completions")
NTC_MODEL = os.getenv("NTC_MODEL", "gpt-4.1")

# Threshold: transcripts longer than this use hierarchical approach
HIERARCHICAL_THRESHOLD = 50000  # characters

# Default Fallback Models
DEFAULT_FALLBACK_MODELS = ["qwen2.5:72b-instruct-q4_K_M", "scb10x/typhoon2.1-gemma3-12b"]
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")

def get_llm_config(mongo_service=None) -> dict:
    if mongo_service:
        try:
            config = mongo_service.get_llm_config("default_fallback")
            if config:
                return config
        except Exception as e:
            logger.error(f"Error getting LLM config from DB: {e}")
            
    return {
        "primary_model": NTC_MODEL,
        "fallback_models": DEFAULT_FALLBACK_MODELS,
        "temperature": 0.3,
        "max_tokens": 4000
    }



# ============================================================
# GPT-4.1 API Helper
# ============================================================

def _call_gpt41(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    timeout: int = 120,
) -> str:
    """Call GPT-4.1 via NTC API Gateway. Returns content string or empty."""
    if not NTC_API_KEY:
        logger.error("NTC_API_KEY not set")
        return ""

    headers = {
        "Authorization": f"Bearer {NTC_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": NTC_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(NTC_API_URL, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        logger.error(f"GPT-4.1 API error: {e}")
        return ""


def _call_ollama(
    model_name: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    timeout: int = 120,
) -> str:
    """Call Ollama API. Returns content string or empty."""
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/chat"
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "options": {
            "temperature": temperature,
        },
        "stream": False
    }

    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()["message"]["content"].strip()
    except Exception as e:
        logger.error(f"Ollama API error ({model_name}): {e}")
        return ""


def _call_llm_with_fallback(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    timeout: int = 120,
    mongo_service=None,
) -> str:
    """Try primary model (NTC GPT-4.1), fallback to Ollama models if it fails."""
    config = get_llm_config(mongo_service)
    
    # Try primary
    logger.info(f"Attempting summary with primary model: {config['primary_model']}")
    result = _call_gpt41(system_prompt, user_prompt, temperature, max_tokens, timeout)
    
    if result and result.strip():
        return result
        
    logger.warning("Primary model failed, trying fallback models...")
    for fallback_model in config["fallback_models"]:
        logger.info(f"Attempting summary with fallback model: {fallback_model}")
        result = _call_ollama(fallback_model, system_prompt, user_prompt, temperature, timeout)
        if result and result.strip():
            logger.info(f"Successfully generated summary with fallback model {fallback_model}")
            return result
            
    logger.error("All models failed.")
    return ""

def _create_fallback_summary(transcription_text: str) -> str:
    """Create a basic fallback summary when all AI models fail."""
    try:
        lines = transcription_text.strip().split('\n')
        # Filter lines with actual content
        content_lines = [line.strip() for line in lines if line.strip() and len(line.strip()) > 10]
        
        if not content_lines:
            return ""
            
        fallback_summary = f"""สรุปการประชุม (สร้างโดยระบบ Fallback)

ข้อมูลการประชุม:
- ความยาวการประชุม: {len(transcription_text)} ตัวอักษร
- จำนวนประโยคที่มีเนื้อหา: {len(content_lines)} ประโยค

เนื้อหาสำคัญบางส่วน:
{chr(10).join(content_lines[:10])}

หมายเหตุ: นี่เป็นสรุปพื้นฐานที่สร้างโดยระบบเนื่องจากการสรุปด้วย AI ประสบปัญหา 
กรุณาตรวจสอบไฟล์ Transcription เพื่อดูรายละเอียดครบถ้วน"""

        return fallback_summary
    except Exception as e:
        logger.error(f"Error creating fallback summary: {e}")
        return "เกิดข้อผิดพลาดในการสรุปผล กรุณาตรวจสอบไฟล์ถอดเสียง (Transcription)"



# ============================================================
# Speaker Name Detection (from ST)
# ============================================================

def detect_speaker_names(transcript_with_speakers: str, speakers: list) -> dict:
    """
    Use GPT-4.1 to detect self-introductions in the transcript
    and map speaker labels to real names.
    Returns: { "คนพูด 1": { "name": "สมชาย", "position": "ผู้จัดการ" }, ... }
    """
    if not NTC_API_KEY:
        print("   ⚠️ No API key, skipping name detection")
        return {}

    transcript_excerpt = transcript_with_speakers[:5000]
    speakers_list = ", ".join(speakers)

    system = """คุณคือ AI ที่วิเคราะห์บทสนทนาภาษาไทยและอังกฤษเพื่อหาการแนะนำตัวของผู้พูด

หน้าที่: อ่าน transcript แล้วหาว่าผู้พูดคนไหนแนะนำตัวเอง หรือถูกเรียกชื่อ/แนะนำโดยคนอื่น

ตัวอย่างการแนะนำตัว (ไทย):
- "สวัสดีครับ ผม สมชาย ใจดี ครับ" → name: "สมชาย ใจดี"
- "ดิฉัน สมหญิง รักดี ตำแหน่งผู้จัดการฝ่ายบุคคล" → name: "สมหญิง รักดี", position: "ผู้จัดการฝ่ายบุคคล"

ตัวอย่างการแนะนำตัว (อังกฤษ/ผสม):
- "Hi, I'm John Smith, the project manager" → name: "John Smith", position: "Project Manager"
- "สวัสดีครับ ผม David Lee ครับ เป็น CTO" → name: "David Lee", position: "CTO"

ตอบเป็น JSON เท่านั้น:
{
  "คนพูด 1": {"name": "ชื่อ นามสกุล", "position": "ตำแหน่ง"},
  "คนพูด 2": {"name": "ชื่อ นามสกุล", "position": ""}
}

กฎสำคัญ:
1. ชื่อต้องเว้นวรรคระหว่างชื่อกับนามสกุล
2. ตัดคำนำหน้าทั่วไปออก: นาย, นาง, นางสาว, คุณ (แต่เก็บ ดร., ศ., ผศ. ไว้)
3. position = ตำแหน่ง/บทบาท — คงภาษาเดิมที่พูด
4. ถ้าพบตำแหน่งแต่ไม่พบชื่อ ให้ข้ามไป
5. ถ้าไม่พบการแนะนำตัวเลย ให้ตอบ {}
6. ตอบ JSON เท่านั้น ไม่ต้องมีคำอธิบาย"""

    user = f"ผู้พูดที่ตรวจพบ: {speakers_list}\n\nTranscript:\n{transcript_excerpt}"

    content = _call_gpt41(system, user, temperature=0.1, max_tokens=500, timeout=30)
    if not content:
        return {}

    try:
        # Clean markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()

        detected = json.loads(content)

        # Validate — only keep entries for known speakers
        validated = {}
        for speaker in speakers:
            if speaker in detected and isinstance(detected[speaker], dict):
                name = detected[speaker].get("name", "").strip()
                position = detected[speaker].get("position", "").strip()
                if name:
                    validated[speaker] = {"name": name, "position": position}
        return validated
    except Exception as e:
        print(f"   ⚠️ Name detection failed: {e}")
        return {}


# ============================================================
# Meeting Auto-Classification (from V3, adapted for GPT-4.1)
# ============================================================

CLASSIFICATION_SYSTEM = """คุณคือผู้เชี่ยวชาญในการวิเคราะห์ประเภทการประชุม จากเนื้อหาการประชุมที่ได้รับ กรุณาจำแนกประเภทการประชุม:

MEETING_TYPES:
- shareholder_meeting: ประชุมผู้ถือหุ้น (มีวาระ การลงมติ เงินปันผล)
- board_meeting: ประชุมคณะกรรมการ (การตัดสินใจระดับบริษัท)
- planning_meeting: ประชุมวางแผน (กลยุทธ์ แผนการทำงาน)
- progress_update: รายงานความคืบหน้า (สถานะงาน ปัญหา)
- strategy_meeting: ประชุมเชิงกลยุทธ์ (ทิศทางธุรกิจ)
- incident_review: แก้ไขปัญหา (วิเคราะห์ปัญหา หาแนวทาง)
- client_meeting: ประชุมลูกค้า (นำเสนองาน ตอบข้อซักถาม)
- workshop: เชิงปฏิบัติการ (ฝึกอบรม แลกเปลี่ยนความรู้)
- executive_meeting: ผู้บริหารระดับสูง (การตัดสินใจสำคัญ)
- team_meeting: ทีมงาน (ประสานงาน มอบหมายงาน)
- general_meeting: ทั่วไป

ตอบด้วย JSON:
{
  "meeting_type": "ประเภท",
  "confidence": 0.95,
  "key_indicators": ["คำสำคัญ"],
  "participants_level": "executive/management/team",
  "meeting_tone": "formal/semi-formal/informal"
}"""

# Keyword fallback mapping
KEYWORD_PATTERNS = {
    "shareholder_meeting": ["ผู้ถือหุ้น", "วาระ", "ลงมติ", "เงินปันผล", "กรรมการ", "องค์ประชุม"],
    "board_meeting": ["คณะกรรมการ", "นโยบาย", "อนุมัติ", "ผู้บริหาร"],
    "planning_meeting": ["แผน", "วางแผน", "กลยุทธ์", "เป้าหมาย", "ไทม์ไลน์"],
    "progress_update": ["ความคืบหน้า", "สถานะ", "รายงาน", "ปัญหา", "อุปสรรค"],
    "client_meeting": ["ลูกค้า", "นำเสนอ", "ข้อเสนอ", "ราคา", "สัญญา"],
    "workshop": ["ฝึกอบรม", "workshop", "เรียนรู้", "ทักษะ", "ความรู้"],
}

# Meeting type ID (int) ↔ classification key (str) mapping
_MEETING_ID_TO_KEY = {
    1: "shareholder_meeting", 2: "board_meeting", 3: "planning_meeting",
    4: "progress_update", 5: "strategy_meeting", 6: "incident_review",
    7: "client_meeting", 8: "workshop", 9: "executive_meeting",
    10: "team_meeting", 11: "general_meeting",
}
_MEETING_KEY_TO_ID = {v: k for k, v in _MEETING_ID_TO_KEY.items()}


def _fallback_classification(transcription: str) -> Dict:
    """Keyword-based classification when GPT-4.1 call fails."""
    text_lower = transcription.lower()
    max_score = 0
    detected_type = "general_meeting"

    for mtype, keywords in KEYWORD_PATTERNS.items():
        score = sum(1 for kw in keywords if kw in text_lower)
        if score > max_score:
            max_score = score
            detected_type = mtype

    confidence = min(0.8, max_score / 3.0)
    return {
        "meeting_type": detected_type,
        "confidence": confidence,
        "key_indicators": [kw for kw in KEYWORD_PATTERNS.get(detected_type, []) if kw in text_lower],
        "participants_level": "team",
        "meeting_tone": "semi-formal",
    }


def classify_meeting_type(transcription: str) -> Dict:
    """Classify meeting type using GPT-4.1 with keyword fallback."""
    sample = transcription[:5000]
    user_msg = f"วิเคราะห์และจำแนกประเภทการประชุมจาก transcript ต่อไปนี้:\n\n{sample}"

    content = _call_gpt41(CLASSIFICATION_SYSTEM, user_msg, temperature=0.1, max_tokens=500, timeout=30)
    if not content:
        return _fallback_classification(transcription)

    try:
        json_start = content.find('{')
        json_end = content.rfind('}') + 1
        if json_start != -1 and json_end > json_start:
            return json.loads(content[json_start:json_end])
        raise ValueError("No JSON found")
    except Exception:
        return _fallback_classification(transcription)


# ============================================================
# Text Chunking (from V3, smart boundary)
# ============================================================

def split_text_into_chunks(text: str, max_tokens: int = 30000) -> list[str]:
    """
    Split text into chunks with smart boundary detection.
    GPT-4.1 has 1M context window so we use larger chunks than Ollama.
    """
    words = text.split()
    max_words = int(max_tokens * 0.75)  # 1 token ≈ 0.75 Thai words
    chunks: list[str] = []
    current_chunk: list[str] = []

    sentence_markers = ['ค่ะ', 'ครับ', 'นะครับ', 'นะคะ', 'วาระที่', 'ประชุม', 'หัวข้อ']

    for word in words:
        current_chunk.append(word)
        if len(current_chunk) >= max_words:
            # Try to cut at a sentence boundary
            for i in range(len(current_chunk) - 1, max(0, len(current_chunk) - 500), -1):
                if any(marker in current_chunk[i] for marker in sentence_markers):
                    chunks.append(' '.join(current_chunk[:i + 1]))
                    current_chunk = current_chunk[i + 1:]
                    break
            else:
                chunks.append(' '.join(current_chunk))
                current_chunk = []

    if current_chunk:
        chunks.append(' '.join(current_chunk))

    logger.info(f"Split text into {len(chunks)} chunks")
    return chunks


# ============================================================
# Chunk-level Summary
# ============================================================

def _summarize_chunk(chunk: str, chunk_idx: int, total_chunks: int) -> str:
    """Summarize a single chunk of transcript."""
    system = "คุณคือผู้เชี่ยวชาญในการสรุปการประชุมอย่างละเอียด กรุณาสรุปเนื้อหาการประชุมโดยรักษาข้อมูลสำคัญทั้งหมดไว้ ไม่ให้สูญหาย"

    user = f"""กรุณาสรุปส่วนการประชุมนี้อย่างละเอียด โดยรักษาข้อมูลสำคัญทั้งหมดไว้:

ส่วนที่ {chunk_idx + 1} จากทั้งหมด {total_chunks} ส่วน

{chunk}

โปรดสรุปให้ครอบคลุม:
- ประเด็นหลักและย่อยที่กล่าวถึงในส่วนนี้
- ตัวเลข วันที่ และข้อมูลเฉพาะเจาะจง
- ชื่อบุคคล ตำแหน่ง และผู้ที่มีส่วนเกี่ยวข้อง
- การตัดสินใจหรือข้อสรุปในส่วนนี้
- การมอบหมายงานหรือ action items

หมายเหตุ: นี่คือเพียงส่วนหนึ่งของการประชุม กรุณาสรุปเฉพาะเนื้อหาในส่วนนี้อย่างครบถ้วน"""

    return _call_llm_with_fallback(system, user, temperature=0.2, max_tokens=4000, timeout=120)


def _consolidate_summaries(
    chunk_summaries: list[str],
    classification: Dict,
    meeting_type_id: int,
    custom_prompt: str = "",
) -> str:
    """Consolidate multiple chunk summaries into one final summary."""
    meeting_type = classification.get("meeting_type", "general_meeting")
    confidence = classification.get("confidence", 0.5)
    key_indicators = classification.get("key_indicators", [])

    # Map classification key to Thai name
    type_id = _MEETING_KEY_TO_ID.get(meeting_type, meeting_type_id or 11)
    info = MEETING_TYPES.get(type_id, MEETING_TYPES[11])
    focus_prompt = get_meeting_focus_prompt(type_id)

    combined = "\n\n---\n\n".join(
        f"=== สรุปส่วนที่ {i+1} ===\n{s}" for i, s in enumerate(chunk_summaries)
    )

    system = f"""คุณคือผู้เชี่ยวชาญวิเคราะห์และสรุปการประชุม

**ประเภทการประชุม:** {info['thai']} ({info['name']})
**โครงสร้างการสรุป:** {info['structure']}

{focus_prompt}

คุณกำลังสร้างสรุปขั้นสุดท้ายจากการประชุมยาว กรุณาให้ความสำคัญกับความครบถ้วนและการไม่สูญหายของข้อมูลสำคัญ"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""กรุณาสร้างสรุปการประชุมฉบับสมบูรณ์จากสรุปส่วนต่างๆ ต่อไปนี้:

ข้อมูลบริบท:
- ประเภท: {info['thai']} (ความเชื่อมั่น: {confidence:.0%})
- คำสำคัญ: {', '.join(key_indicators)}

{combined}

กรุณาสร้างสรุปที่:
1. เริ่มต้นด้วยหัวข้อ "สรุป{info['thai']}"
2. ครอบคลุมเนื้อหาจากทุกส่วน ไม่ให้สูญหาย
3. จัดเรียงตามลำดับเหมาะสม ไม่ซ้ำซ้อน
4. ยาวและละเอียด ประมาณ 3-5 หน้า A4
5. ใช้ bullet points และหัวข้อย่อย"""

    result = _call_llm_with_fallback(system, user, temperature=0.1, max_tokens=8000, timeout=180)
    if not result:
        # Fallback: join summaries
        header = f"สรุป{info['thai']}\n{'=' * 50}\n\n"
        return header + "\n\n".join(chunk_summaries)
    return result


# ============================================================
# Main Summarization Entry Points
# ============================================================

def get_meeting_type_prompt(meeting_type_id: int) -> str:
    """Get the prompt instruction for a specific meeting type."""
    if meeting_type_id == 0:
        types_table = "\n".join([
            f"| {info['name']} | {info['structure']} |"
            for num, info in MEETING_TYPES.items() if num > 0
        ])
        return f"""**ขั้นตอน:**
1. วิเคราะห์ข้อมูลผู้พูดเพื่อระบุบทบาท (ประธาน/ผู้นำเสนอ/ผู้เข้าร่วม)
2. วิเคราะห์เนื้อหาเพื่อระบุประเภทการประชุม
3. สรุปตามโครงสร้างที่เหมาะสม

**ประเภทการประชุม:**
| ประเภท | โครงสร้าง |
|--------|----------|
{types_table}"""
    else:
        info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES[11])
        focus = get_meeting_focus_prompt(meeting_type_id)
        return f"""**ประเภทการประชุม:** {info['thai']} ({info['name']})
**โครงสร้างการสรุป:** {info['structure']}

{focus}

สรุปเนื้อหาตามโครงสร้างข้างต้น โดยเน้นความละเอียดในประเด็นหัวใจหลัก"""


def summarize_with_diarization(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int = 0,
    language: str = "Thai",
    custom_prompt: str = "",
) -> str:
    """
    Summarize transcription with speaker diarization data.
    Routes to hierarchical approach for long transcripts.
    """
    if not NTC_API_KEY:
        return "Error: NTC_API_KEY not found in environment variables"

    # Auto-classify if meeting_type_id == 0
    classification = None
    if meeting_type_id == 0:
        classification = classify_meeting_type(transcript_with_speakers)
        detected_id = _MEETING_KEY_TO_ID.get(
            classification.get("meeting_type", "general_meeting"), 11
        )
        logger.info(f"Auto-classified as: {classification.get('meeting_type')} → ID {detected_id}")
    else:
        classification = {
            "meeting_type": _MEETING_ID_TO_KEY.get(meeting_type_id, "general_meeting"),
            "confidence": 1.0,
            "key_indicators": [],
        }

    if custom_prompt:
        logger.info(f"Custom prompt provided ({len(custom_prompt)} chars)")

    # Route: hierarchical for long transcripts
    if len(transcript_with_speakers) > HIERARCHICAL_THRESHOLD:
        logger.info(f"Using HIERARCHICAL approach ({len(transcript_with_speakers)} chars)")
        return _summarize_hierarchical(
            transcript_with_speakers, speaker_summary, meeting_type_id, classification, custom_prompt
        )

    # Standard: single-call approach for shorter transcripts
    return _summarize_standard(
        transcript_with_speakers, speaker_summary, meeting_type_id, classification, custom_prompt
    )


def _summarize_standard(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int,
    classification: Dict,
    custom_prompt: str = "",
) -> str:
    """Standard single-call summary for shorter transcripts."""
    speakers_time = speaker_summary.get('speaking_time', {})
    speakers_words = speaker_summary.get('word_count', {})
    total_time = sum(speakers_time.values()) if speakers_time else 1

    speaker_info_lines = []
    for speaker, time_sec in sorted(speakers_time.items(), key=lambda x: -x[1]):
        pct = (time_sec / total_time * 100) if total_time > 0 else 0
        words = speakers_words.get(speaker, 0)
        mins = int(time_sec // 60)
        secs = int(time_sec % 60)
        speaker_info_lines.append(f"- {speaker}: {mins}:{secs:02d} ({pct:.1f}%), {words} คำ")

    speaker_info = "\n".join(speaker_info_lines)
    num_speakers = len(speakers_time)
    meeting_type_instruction = get_meeting_type_prompt(meeting_type_id)
    info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES[0])

    system = f"""คุณคือผู้เชี่ยวชาญวิเคราะห์และสรุปการประชุม

{meeting_type_instruction}

**Output Format:**
**[{info['thai'] if meeting_type_id > 0 else 'ประเภท'}]: [หัวข้อการประชุม]**

**👥 ผู้เข้าร่วมประชุม ({num_speakers} คน):**
(วิเคราะห์บทบาทจากเนื้อหาการพูด: ประธาน/ผู้นำเสนอ/ผู้เข้าร่วม)

**📋 สรุปการประชุม:**
(ตามโครงสร้าง: {info['structure']} - เน้นความละเอียดในส่วน {info.get('key_focus', 'ประเด็นหลัก')})

**📌 การสั่งงาน/มอบหมาย:** (ถ้ามี)
- **[ผู้สั่ง]** สั่งให้ **[ผู้รับมอบหมาย]** ทำ: [เนื้อหา]

**❓ คำถามสำคัญ:** (ถ้ามี)
- **[ผู้ถาม]** ถาม: "[คำถาม]" → **[ผู้ตอบ]**: "[คำตอบ]"

**✅ ข้อตกลง/มติ:** (ถ้ามี)

**กฎสำคัญ:**
- สรุปเป็นภาษาไทยเป็นหลัก แต่คงคำศัพท์เฉพาะทางภาษาอังกฤษไว้ตามเดิม
- ใช้ bullet points
- ต้องระบุชื่อผู้พูดในทุกการสั่งงาน/คำถาม/ข้อตกลง
- เน้นความละเอียดในประเด็นหัวใจหลักของประเภทการประชุมนี้
- สรุปมติท้ายสุด"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""**ข้อมูลผู้พูด:**
{speaker_info}

**เนื้อหาการประชุม:**
{transcript_with_speakers}"""

    result = _call_llm_with_fallback(system, user, temperature=0.4, max_tokens=4000, timeout=120)
    if not result:
        logger.warning("All models failed, using basic fallback summary.")
        return _create_fallback_summary(transcript_with_speakers)
    return result


def _summarize_hierarchical(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int,
    classification: Dict,
    custom_prompt: str = "",
) -> str:
    """Hierarchical multi-stage summary for long transcripts."""
    logger.info("Starting hierarchical summarization")

    # Step 1: Split into chunks
    chunks = split_text_into_chunks(transcript_with_speakers, max_tokens=30000)
    logger.info(f"Split into {len(chunks)} chunks")

    # Step 2: Summarize each chunk
    chunk_summaries = []
    for i, chunk in enumerate(chunks):
        logger.info(f"Summarizing chunk {i+1}/{len(chunks)} ({len(chunk)} chars)")
        summary = _summarize_chunk(chunk, i, len(chunks))
        if summary:
            chunk_summaries.append(summary)
            logger.info(f"Chunk {i+1} done ({len(summary)} chars)")
        else:
            logger.warning(f"Chunk {i+1} returned empty")

    if not chunk_summaries:
        return "Error: No chunk summaries generated"

    # Step 3: Consolidate into final summary
    logger.info(f"Consolidating {len(chunk_summaries)} chunk summaries")
    final = _consolidate_summaries(chunk_summaries, classification, meeting_type_id, custom_prompt)

    logger.info(f"Hierarchical summary complete ({len(final)} chars)")
    return final
