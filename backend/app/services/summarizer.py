"""
Enhanced Summarizer — Merged from ST + V3.

Uses the configured NTC_MODEL via NTC API Gateway for all LLM calls.
Adds: auto-classification, hierarchical chunking for long transcripts,
and keyword-based fallback classification.
"""

import requests
import os
import json
import re
import logging
from typing import Dict, Optional

from pymongo.database import Database

from ..models.meeting import MEETING_TYPES, get_meeting_focus_prompt

logger = logging.getLogger(__name__)


def _clean_env_value(value: Optional[str]) -> Optional[str]:
    """Normalize dotenv/docker env values without exposing secrets in logs."""
    if value is None:
        return None
    return value.strip().strip('"').strip("'")


# NTC AI Gateway API configuration
DEFAULT_NTC_MODEL = "ict-ollama/gemma4:31b-it-q4_K_M"
DEFAULT_FALLBACK_MODELS = ["ict-ollama/qwen2.5:72b-instruct-q4_K_M"]
LEGACY_PRIMARY_MODELS = {"gpt-4.1"}
LEGACY_FALLBACK_MODEL_ALIASES = {
    "qwen2.5:72b-instruct-q4_K_M": "ict-ollama/qwen2.5:72b-instruct-q4_K_M",
}
LEGACY_FALLBACK_MODELS_TO_DROP = {"scb10x/typhoon2.1-gemma3-12b"}

NTC_API_KEY = _clean_env_value(os.getenv("NTC_API_KEY"))
NTC_API_URL = _clean_env_value(os.getenv("NTC_API_URL")) or "https://aigateway.ntictsolution.com/v1/chat/completions"
NTC_MODEL = _clean_env_value(os.getenv("NTC_MODEL")) or DEFAULT_NTC_MODEL

# Threshold: transcripts longer than this use hierarchical approach
HIERARCHICAL_THRESHOLD = 50000  # characters


def _serialize_mongo_doc(doc: Optional[dict]) -> Optional[dict]:
    if not doc:
        return doc
    doc = dict(doc)
    if "_id" in doc:
        doc["_id"] = str(doc["_id"])
    return doc


def _get_pymongo_db(mongo_service=None) -> Optional[Database]:
    if mongo_service is None:
        return None
    if isinstance(mongo_service, Database):
        return mongo_service
    db = getattr(mongo_service, "db", None)
    return db if isinstance(db, Database) else None


def _fetch_llm_config(mongo_service=None, name: str = "default_fallback") -> Optional[dict]:
    if mongo_service is None:
        return None

    if not isinstance(mongo_service, Database):
        getter = getattr(mongo_service, "get_llm_config", None)
        if callable(getter):
            return getter(name)

    db = _get_pymongo_db(mongo_service)
    if db is None:
        return None
    return _serialize_mongo_doc(db.llm_config.find_one({"name": name}))


def _fetch_meeting_template(mongo_service=None, meeting_type_id: int = 0) -> Optional[dict]:
    if mongo_service is None:
        return None

    if not isinstance(mongo_service, Database):
        getter = getattr(mongo_service, "get_meeting_template", None)
        if callable(getter):
            return getter(meeting_type_id)

    db = _get_pymongo_db(mongo_service)
    if db is None:
        return None
    return _serialize_mongo_doc(db.meeting_template.find_one({"meeting_type_id": meeting_type_id}))


def _sanitize_gateway_error(text: str) -> str:
    sanitized = text or ""
    sanitized = re.sub(r"(Received API Key\s*=\s*)[^,\s]+", r"\1[redacted]", sanitized)
    sanitized = re.sub(r"(Key Hash \(Token\)\s*=\s*)[A-Fa-f0-9]+", r"\1[redacted]", sanitized)
    sanitized = re.sub(r"sk-[A-Za-z0-9._-]+", "sk-[redacted]", sanitized)
    return sanitized[:1000]


def _normalize_model_name(value: Optional[str]) -> str:
    return (_clean_env_value(value) or "").strip()


def _resolve_primary_model(config_model: Optional[str]) -> str:
    model = _normalize_model_name(config_model)
    if not model or model in LEGACY_PRIMARY_MODELS:
        return NTC_MODEL
    return model


def _resolve_fallback_models(value) -> list[str]:
    if not value:
        return DEFAULT_FALLBACK_MODELS
    if isinstance(value, str):
        raw_models = [item.strip() for item in value.split(",") if item.strip()]
    else:
        raw_models = [str(item).strip() for item in value if item and str(item).strip()]

    models: list[str] = []
    for model in raw_models:
        mapped = LEGACY_FALLBACK_MODEL_ALIASES.get(model, model)
        if mapped in LEGACY_FALLBACK_MODELS_TO_DROP:
            continue
        if mapped not in models:
            models.append(mapped)
    return models or DEFAULT_FALLBACK_MODELS

def _normalize_llm_config(config: Optional[dict]) -> dict:
    config = config or {}
    return {
        "primary_model": _resolve_primary_model(config.get("primary_model")),
        "fallback_models": _resolve_fallback_models(config.get("fallback_models")),
        "temperature": config.get("temperature", 0.3),
        "max_tokens": config.get("max_tokens", 4000),
    }


def get_llm_config(mongo_service=None) -> dict:
    if mongo_service is not None:
        try:
            config = _fetch_llm_config(mongo_service, "default_fallback")
            if config:
                return _normalize_llm_config(config)
        except Exception as e:
            logger.error(f"Error getting LLM config from DB: {e}")
            
    return _normalize_llm_config({
        "primary_model": NTC_MODEL,
        "fallback_models": DEFAULT_FALLBACK_MODELS,
        "temperature": 0.3,
        "max_tokens": 4000
    })

def _get_template_for_meeting(meeting_type_id: int, mongo_service=None) -> dict:
    """Fetch meeting template from DB or fallback to default."""
    if mongo_service is not None:
        try:
            template = _fetch_meeting_template(mongo_service, meeting_type_id)
            if template:
                return template
        except Exception as e:
            logger.error(f"Error fetching meeting template from DB: {e}")
            
    from ..models.meeting_template import _get_default_system_prompt
    return {
        "system_prompt": _get_default_system_prompt(meeting_type_id),
        "temperature": 0.4,
        "max_tokens": 4000
    }




# ============================================================
# NTC AI Gateway API Helper
# ============================================================

def _call_ntc_gateway(
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.3,
    max_tokens: int = 4000,
    timeout: int = 120,
    model_name: str = None,
) -> str:
    """Call LLM via NTC AI Gateway (OpenAI-compatible). Returns content string or empty."""
    if not NTC_API_KEY:
        logger.error("NTC_API_KEY not set")
        return ""

    headers = {
        "Authorization": f"Bearer {NTC_API_KEY}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": model_name or NTC_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    try:
        resp = requests.post(NTC_API_URL, headers=headers, json=payload, timeout=timeout)
        if resp.status_code >= 400:
            logger.error(
                "NTC AI Gateway API error (%s): %s",
                resp.status_code,
                _sanitize_gateway_error(resp.text) or resp.reason,
            )
            return ""
        try:
            content = resp.json()["choices"][0]["message"]["content"]
            return (content or "").strip()
        except (KeyError, IndexError, TypeError, ValueError) as e:
            logger.error(f"NTC AI Gateway response parse error: {e}")
            return ""
    except requests.exceptions.RequestException as e:
        logger.error(f"NTC AI Gateway request failed: {e}")
        return ""


def _call_llm_with_fallback(
    system_prompt: str,
    user_prompt: str,
    temperature: Optional[float] = None,
    max_tokens: Optional[int] = None,
    timeout: int = 120,
    mongo_service=None,
    override_config: dict = None,
) -> str:
    """Try primary model, fallback to other models via NTC AI Gateway."""
    config = _normalize_llm_config(override_config) if override_config else get_llm_config(mongo_service)
    effective_temperature = temperature if temperature is not None else config["temperature"]
    effective_max_tokens = max_tokens if max_tokens is not None else config["max_tokens"]
    
    # Try primary
    logger.info(f"Attempting summary with primary model: {config['primary_model']}")
    result = _call_ntc_gateway(
        system_prompt,
        user_prompt,
        effective_temperature,
        effective_max_tokens,
        timeout,
        model_name=config["primary_model"],
    )
    
    if result and result.strip():
        return result
        
    logger.warning("Primary model failed, trying fallback models...")
    for fallback_model in config["fallback_models"]:
        logger.info(f"Attempting summary with fallback model: {fallback_model}")
        # All configured models are served via NTC AI Gateway (OpenAI-compatible)
        result = _call_ntc_gateway(
            system_prompt,
            user_prompt,
            effective_temperature,
            effective_max_tokens,
            timeout,
            model_name=fallback_model,
        )
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
    Use the configured LLM to detect self-introductions in the transcript
    and map speaker labels to real names.
    Returns: { "คนพูด 1": { "name": "สมชาย", "position": "ผู้จัดการ" }, ... }
    """
    if not NTC_API_KEY:
        print("   ⚠️ No API key, skipping name detection")
        return {}

    transcript_excerpt = transcript_with_speakers[:5000]
    speakers_list = ", ".join(speakers)

    system = """คุณคือ AI ที่วิเคราะห์บทสนทนาภาษาไทย อังกฤษ และจีน เพื่อหาการแนะนำตัวของผู้พูด

หน้าที่: อ่าน transcript แล้วหาว่าผู้พูดคนไหนแนะนำตัวเอง หรือถูกเรียกชื่อ/แนะนำโดยคนอื่น

ตัวอย่างการแนะนำตัว (ไทย):
- "สวัสดีครับ ผม สมชาย ใจดี ครับ" → name: "สมชาย ใจดี"
- "ดิฉัน สมหญิง รักดี ตำแหน่งผู้จัดการฝ่ายบุคคล" → name: "สมหญิง รักดี", position: "ผู้จัดการฝ่ายบุคคล"

ตัวอย่างการแนะนำตัว (อังกฤษ/ผสม):
- "Hi, I'm John Smith, the project manager" → name: "John Smith", position: "Project Manager"
- "สวัสดีครับ ผม David Lee ครับ เป็น CTO" → name: "David Lee", position: "CTO"

ตัวอย่างการแนะนำตัว (จีน/ผสม):
- "大家好，我是王明，负责市场部" → name: "王明 (หวัง หมิง)", position: "ผู้รับผิดชอบฝ่ายการตลาด"
- "สวัสดีครับ 我叫李华" → name: "李华 (หลี่ หวา)", position: ""

ตอบเป็น JSON เท่านั้น:
{
  "คนพูด 1": {"name": "ชื่อ นามสกุล", "position": "ตำแหน่ง"},
  "คนพูด 2": {"name": "ชื่อ นามสกุล", "position": ""}
}

กฎสำคัญ:
1. ชื่อต้องเว้นวรรคระหว่างชื่อกับนามสกุล
2. ตัดคำนำหน้าทั่วไปออก: นาย, นาง, นางสาว, คุณ (แต่เก็บ ดร., ศ., ผศ. ไว้)
3. position = ตำแหน่ง/บทบาท — แปลเป็นภาษาไทย ถ้าต้นฉบับเป็นภาษาจีนหรือภาษาอื่น
4. ชื่อภาษาจีนให้ใส่คำอ่านภาษาไทยในวงเล็บ เช่น 王明 (หวัง หมิง)
5. ถ้าพบตำแหน่งแต่ไม่พบชื่อ ให้ข้ามไป
6. ถ้าไม่พบการแนะนำตัวเลย ให้ตอบ {}
7. ตอบ JSON เท่านั้น ไม่ต้องมีคำอธิบาย"""

    user = f"ผู้พูดที่ตรวจพบ: {speakers_list}\n\nTranscript:\n{transcript_excerpt}"

    content = _call_ntc_gateway(system, user, temperature=0.1, max_tokens=500, timeout=30)
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
# Meeting Auto-Classification (LLM-assisted)
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
    """Keyword-based classification when the LLM call fails."""
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
    """Classify meeting type using the configured LLM with keyword fallback."""
    sample = transcription[:5000]
    user_msg = f"วิเคราะห์และจำแนกประเภทการประชุมจาก transcript ต่อไปนี้:\n\n{sample}"

    content = _call_ntc_gateway(CLASSIFICATION_SYSTEM, user_msg, temperature=0.1, max_tokens=500, timeout=30)
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
    NTC Gateway models have large context windows, so we use larger chunks.
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

def _summarize_chunk(chunk: str, chunk_idx: int, total_chunks: int, mongo_service=None) -> str:
    """Summarize a single chunk of transcript."""
    system = "คุณคือผู้เชี่ยวชาญในการสรุปการประชุมอย่างละเอียด กรุณาสรุปเนื้อหาการประชุมโดยรักษาข้อมูลสำคัญทั้งหมดไว้ ไม่ให้สูญหาย สรุปเป็นภาษาไทยเสมอ ไม่ว่าเนื้อหาต้นฉบับจะเป็นภาษาอะไร คงคำศัพท์เฉพาะทางภาษาอังกฤษไว้ตามเดิม ถ้ามีภาษาจีนหรือภาษาอื่นให้แปลเป็นไทยและใส่คำต้นฉบับในวงเล็บ"

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

    return _call_llm_with_fallback(
        system,
        user,
        temperature=0.2,
        max_tokens=4000,
        timeout=120,
        mongo_service=mongo_service,
    )


def _consolidate_summaries(
    chunk_summaries: list[str],
    classification: Dict,
    meeting_type_id: int,
    custom_prompt: str = "",
    mongo_service=None,
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

คุณกำลังสร้างสรุปขั้นสุดท้ายจากการประชุมยาว กรุณาให้ความสำคัญกับความครบถ้วนและการไม่สูญหายของข้อมูลสำคัญ
สรุปเป็นภาษาไทยเสมอ ไม่ว่าเนื้อหาต้นฉบับจะเป็นภาษาอะไร คงคำศัพท์เฉพาะทางภาษาอังกฤษไว้ตามเดิม ถ้ามีภาษาจีนหรือภาษาอื่นให้แปลเป็นไทยและใส่คำต้นฉบับในวงเล็บ"""

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

    result = _call_llm_with_fallback(
        system, user, 
        temperature=0.3, 
        max_tokens=2000, 
        timeout=120,
        mongo_service=mongo_service
    )
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
    mongo_service=None,
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
            transcript_with_speakers,
            speaker_summary,
            meeting_type_id,
            classification,
            custom_prompt,
            mongo_service=mongo_service,
        )

    # Standard: single-call approach for shorter transcripts
    return _summarize_standard(
        transcript_with_speakers, speaker_summary, meeting_type_id, classification, custom_prompt, mongo_service=mongo_service
    )


def _summarize_standard(
    transcript_with_speakers: str,
    speaker_summary: dict,
    meeting_type_id: int,
    classification: Dict,
    custom_prompt: str = "",
    mongo_service=None,
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
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=mongo_service)
    system = template_data["system_prompt"]
    
    # Replace placeholder if present, otherwise append
    if "{custom_prompt}" in system:
        if custom_prompt:
            system = system.replace("{custom_prompt}", f"**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}")
        else:
            system = system.replace("{custom_prompt}", "")
    elif custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"
        
    system = system.replace("{num_speakers}", str(num_speakers))

    user = f"""**ข้อมูลผู้พูด:**
{speaker_info}

**เนื้อหาการประชุม:**
{transcript_with_speakers}"""

    result = _call_llm_with_fallback(
        system, 
        user, 
        temperature=template_data.get("temperature", 0.4), 
        max_tokens=template_data.get("max_tokens", 4000), 
        timeout=120,
        mongo_service=mongo_service
    )
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
    mongo_service=None,
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
        summary = _summarize_chunk(chunk, i, len(chunks), mongo_service=mongo_service)
        if summary:
            chunk_summaries.append(summary)
            logger.info(f"Chunk {i+1} done ({len(summary)} chars)")
        else:
            logger.warning(f"Chunk {i+1} returned empty")

    if not chunk_summaries:
        return "Error: No chunk summaries generated"

    # Step 3: Consolidate into final summary
    logger.info(f"Consolidating {len(chunk_summaries)} chunk summaries")
    final = _consolidate_summaries(
        chunk_summaries,
        classification,
        meeting_type_id,
        custom_prompt,
        mongo_service=mongo_service,
    )

    logger.info(f"Hierarchical summary complete ({len(final)} chars)")
    return final


# ============================================================
# Agenda-aware Summarization (Feature 19)
# ============================================================

def _summarize_single_agenda(
    agenda_transcript: str,
    agenda_title: str,
    agenda_number: int,
    total_agendas: int,
    meeting_type_id: int,
    custom_prompt: str = "",
    mongo_service=None,
) -> dict:
    """
    Summarize a single agenda section.

    Returns:
        {"summary": str, "decisions": list[str], "action_items": list[str]}
    """
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=mongo_service)
    thai_name = template_data.get('thai_name', 'ทั่วไป')

    system = f"""คุณคือผู้เชี่ยวชาญในการสรุปการประชุม กรุณาสรุปเนื้อหาเฉพาะวาระนี้อย่างละเอียด

**ประเภทการประชุม:** {thai_name}
**วาระที่กำลังสรุป:** {agenda_title} (วาระที่ {agenda_number}/{total_agendas})

ตอบเป็น JSON เท่านั้น:
```json
{{
  "summary": "สรุปเนื้อหาวาระนี้อย่างละเอียด ใช้ bullet points",
  "decisions": ["มติหรือข้อตกลง (ถ้ามี)"],
  "action_items": ["การมอบหมายงาน: [ผู้รับมอบหมาย] — [งาน] (ถ้ามี)"]
}}
```

**กฎ:**
- สรุปเป็นภาษาไทยเสมอ ไม่ว่า transcript จะเป็นภาษาอะไร (ไทย/อังกฤษ/จีน/ผสม)
- คงคำศัพท์เฉพาะทาง ชื่อเฉพาะ และคำย่อภาษาอังกฤษไว้ตามเดิม
- ถ้ามีการพูดภาษาจีนหรือภาษาอื่น ให้แปลเป็นภาษาไทยแล้วใส่คำต้นฉบับในวงเล็บ
- ใช้ bullet points ใน summary
- ระบุชื่อผู้พูดเมื่อกล่าวถึงการสั่งงาน/ความเห็น
- ถ้าไม่มีมติหรือ action items ให้ตอบ list ว่าง []
- ตอบ JSON เท่านั้น ไม่ต้องมีข้อความอื่น"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""**วาระ:** {agenda_title}

**เนื้อหา:**
{agenda_transcript}"""

    content = _call_llm_with_fallback(
        system, user, 
        temperature=template_data.get("temperature", 0.4) if template_data else 0.2, 
        max_tokens=3000, 
        timeout=90,
        mongo_service=mongo_service
    )

    if not content:
        return {"summary": "", "decisions": [], "action_items": []}

    try:
        # Strip markdown code fences
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        result = json.loads(cleaned)
        return {
            "summary": result.get("summary", ""),
            "decisions": result.get("decisions", []) if isinstance(result.get("decisions"), list) else [],
            "action_items": result.get("action_items", []) if isinstance(result.get("action_items"), list) else [],
        }
    except (json.JSONDecodeError, TypeError) as exc:
        logger.warning(f"Failed to parse agenda summary JSON, using raw text: {exc}")
        return {"summary": content, "decisions": [], "action_items": []}


def _generate_executive_summary(
    agenda_summaries: list[dict],
    meeting_type_id: int,
    custom_prompt: str = "",
    mongo_service=None,
) -> str:
    """
    Generate an executive summary from per-agenda summaries.

    Args:
        agenda_summaries: List of {"agenda_number", "title", "summary", "decisions", "action_items"}
    """
    info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES.get(11, MEETING_TYPES[0]))
    template_data = _get_template_for_meeting(meeting_type_id, mongo_service=mongo_service)
    thai_name = template_data.get("thai_name") or info.get("thai", "ทั่วไป")

    # Build combined input
    combined_parts: list[str] = []
    for agenda in agenda_summaries:
        part = f"=== วาระที่ {agenda['agenda_number']}: {agenda['title']} ===\n"
        part += agenda.get("summary", "")
        if agenda.get("decisions"):
            part += "\nมติ: " + "; ".join(agenda["decisions"])
        if agenda.get("action_items"):
            part += "\nงานมอบหมาย: " + "; ".join(agenda["action_items"])
        combined_parts.append(part)

    combined = "\n\n".join(combined_parts)

    system = f"""คุณคือผู้เชี่ยวชาญในการสรุปการประชุม
กรุณาสร้าง **สรุปภาพรวมการประชุม (Executive Summary)** จากสรุปแต่ละวาระด้านล่าง

**ประเภทการประชุม:** {thai_name}

**รูปแบบ:**
1. เริ่มด้วย "สรุปภาพรวมการประชุม"
2. สรุปใจความสำคัญจากทุกวาระอย่างกระชับ
3. รวบรวมมติที่ประชุมทั้งหมด
4. รวบรวมงานมอบหมายทั้งหมด
5. ใช้ bullet points
6. สรุปเป็นภาษาไทยเสมอ ไม่ว่าเนื้อหาต้นฉบับจะเป็นภาษาอะไร คงคำศัพท์เฉพาะทางภาษาอังกฤษไว้ตามเดิม ถ้ามีภาษาจีนหรือภาษาอื่นให้แปลเป็นไทยและใส่คำต้นฉบับในวงเล็บ"""

    if custom_prompt:
        system += f"\n\n**คำสั่งเพิ่มเติมจากผู้ใช้:**\n{custom_prompt}"

    user = f"""จำนวนวาระทั้งหมด: {len(agenda_summaries)} วาระ

{combined}

กรุณาสร้างสรุปภาพรวม"""

    result = _call_llm_with_fallback(
        system, user, 
        temperature=template_data.get("temperature", 0.2),
        max_tokens=template_data.get("max_tokens", 4000), 
        timeout=120,
        mongo_service=mongo_service
    )
    if not result:
        # Fallback: combine agenda summaries as plain text
        header = f"สรุปภาพรวม{thai_name}\n{'=' * 50}\n\n"
        return header + combined

    return result


def summarize_with_agendas(
    segments: list[dict],
    agendas: list[dict],
    meeting_type_id: int = 0,
    custom_prompt: str = "",
    mongo_service=None,
) -> tuple[str, list[dict]]:
    """
    Summarize meeting with agenda-aware approach.

    Produces per-agenda summaries + an executive summary.

    Args:
        segments: Transcript segments (with 'text', 'speaker', 'start', 'end')
        agendas: List of agenda dicts from detect_agendas()
        meeting_type_id: Meeting type (0=auto, 1-11=specific)
        custom_prompt: Optional user instruction

    Returns:
        (executive_summary: str, enriched_agendas: list[dict])
        Each enriched agenda has added: "summary", "decisions", "action_items"
    """
    total = len(agendas)
    logger.info(f"Starting agenda-aware summarization for {total} agendas")

    enriched: list[dict] = []

    for agenda in agendas:
        start_idx = agenda["start_segment_idx"]
        end_idx = agenda["end_segment_idx"]
        title = agenda["title"]
        number = agenda["agenda_number"]

        # Slice transcript for this agenda
        agenda_segments = segments[start_idx : end_idx + 1]
        agenda_lines = []
        for seg in agenda_segments:
            speaker = seg.get("speaker", "?")
            text = seg.get("text", "").strip()
            if text:
                agenda_lines.append(f"[{speaker}]: {text}")

        agenda_transcript = "\n".join(agenda_lines)

        logger.info(
            f"Summarizing agenda {number}/{total}: '{title}' "
            f"(segments {start_idx}-{end_idx}, {len(agenda_transcript)} chars)"
        )

        result = _summarize_single_agenda(
            agenda_transcript=agenda_transcript,
            agenda_title=title,
            agenda_number=number,
            total_agendas=total,
            meeting_type_id=meeting_type_id,
            custom_prompt=custom_prompt,
            mongo_service=mongo_service,
        )

        enriched_agenda = {
            **agenda,
            "summary": result["summary"],
            "decisions": result["decisions"],
            "action_items": result["action_items"],
        }
        enriched.append(enriched_agenda)

    # Generate executive summary from all per-agenda summaries
    logger.info("Generating executive summary from per-agenda summaries")
    executive_summary = _generate_executive_summary(
        enriched, meeting_type_id, custom_prompt, mongo_service=mongo_service
    )

    logger.info(f"Agenda-aware summarization complete: {len(enriched)} agendas")
    return executive_summary, enriched
