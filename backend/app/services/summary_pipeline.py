"""Token-budgeted, context-preserving incremental meeting summarization."""

from __future__ import annotations

import json
import logging
import math
import os
import re
from typing import Any, Callable, Optional

from ..models.meeting import MEETING_TYPES, get_meeting_focus_prompt

logger = logging.getLogger(__name__)

LLMCall = Callable[..., str]


# Product fast-summary profile. Token/chunk sizes are env-tunable so production
# can adjust Gemma budgets without changing code.
def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    logger.warning("Invalid boolean env %s=%r; using default %s", name, raw, default)
    return default


def _env_int(
    name: str,
    default: int,
    minimum: int = 1,
    maximum: Optional[int] = None,
) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid integer env %s=%r; using default %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning("Integer env %s=%r below minimum %s; using %s", name, raw, minimum, minimum)
        return minimum
    if maximum is not None and value > maximum:
        logger.warning("Integer env %s=%r above maximum %s; using %s", name, raw, maximum, maximum)
        return maximum
    return value


def _env_timeout_seconds(name: str, default: Optional[int]) -> Optional[int]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw.strip())
    except ValueError:
        logger.warning("Invalid timeout env %s=%r; using default %s", name, raw, default)
        return default
    if value <= 0:
        return None
    return value


CHUNK_INPUT_TOKENS = _env_int("SUMMARY_CHUNK_INPUT_TOKENS", 8000, minimum=1000, maximum=30000)
CHUNK_OVERLAP_TOKENS = _env_int("SUMMARY_CHUNK_OVERLAP_TOKENS", 0, minimum=0, maximum=5000)
REDUCE_INPUT_TOKENS = _env_int("SUMMARY_REDUCE_INPUT_TOKENS", 12000, minimum=2000, maximum=60000)
CHUNK_OUTPUT_TOKENS = _env_int("SUMMARY_CHUNK_OUTPUT_TOKENS", 1500, minimum=100, maximum=4000)
REDUCE_OUTPUT_TOKENS = _env_int("SUMMARY_REDUCE_OUTPUT_TOKENS", 900, minimum=100, maximum=4000)
FINAL_MAX_TOKENS = _env_int("SUMMARY_FINAL_MAX_TOKENS", 1200, minimum=300, maximum=6000)
SUMMARY_LLM_TIMEOUT_SECONDS = _env_timeout_seconds("SUMMARY_LLM_TIMEOUT_SECONDS", 300)
SUMMARY_FAST_DEGRADE_ON_TIMEOUT = _env_bool("SUMMARY_FAST_DEGRADE_ON_TIMEOUT", True)
SUMMARY_FAST_TIMEOUT_SECONDS = _env_timeout_seconds("SUMMARY_FAST_TIMEOUT_SECONDS", 300)
ADAPTIVE_MIN_INPUT_TOKENS = _env_int("SUMMARY_ADAPTIVE_MIN_INPUT_TOKENS", 1000, minimum=500, maximum=10000)
ADAPTIVE_SPLIT_MAX_DEPTH = _env_int("SUMMARY_ADAPTIVE_SPLIT_MAX_DEPTH", 0, minimum=0, maximum=4)

SUMMARY_USER_WARNING = (
    "ขออภัย ระบบไม่สามารถสรุปเนื้อหาได้ครบทุกส่วน "
    "กรุณาลองประมวลผลใหม่อีกครั้ง หรือติดต่อทีม Support"
)
SUMMARY_GEMMA_PARTIAL_WARNING = (
    "ขออภัย Gemma ใช้เวลาสรุปเกินกำหนดหรือไม่สามารถประมวลผลบางช่วงได้ "
    "ระบบจึงส่งสรุปเท่าที่ Gemma สรุปสำเร็จแล้ว กรุณาตรวจ Transcript เพิ่มเติม "
    "หรือติดต่อทีม Support"
)
SUMMARY_GEMMA_EMPTY_WARNING = (
    "ขออภัย Gemma ยังสรุปไม่สำเร็จภายในเวลาที่กำหนด "
    "ระบบจึงแนบ Transcript เพื่อไม่ให้ข้อมูลสูญหาย กรุณาลองใหม่อีกครั้ง "
    "หรือติดต่อทีม Support"
)

GROUNDING_RULES = """
กฎความถูกต้องที่ต้องปฏิบัติ:
- ใช้เฉพาะข้อมูลที่ปรากฏใน Transcript หรือ Context ที่ให้มา ห้ามแต่งชื่อ ตัวเลข วันที่ เหตุผล หรือข้อสรุปเพิ่ม
- แยกให้ชัดระหว่างข้อเสนอ ความเห็น การตัดสินใจที่ยืนยันแล้ว และเรื่องที่ยังไม่ได้ข้อสรุป
- เก็บตัวเลข หน่วยเงิน วันที่ กำหนดส่ง ชื่อบุคคล ชื่อโครงการ และคำปฏิเสธให้ครบถ้วน
- ถ้ามีข้อมูลขัดแย้งหรือมีการแก้ไขภายหลัง ให้เก็บทั้งข้อมูลเดิมและข้อมูลแก้ไขพร้อมลำดับเหตุการณ์
- ถ้าไม่ทราบผู้รับผิดชอบหรือกำหนดเวลา ให้ระบุว่า "ไม่ระบุ" ห้ามคาดเดา
- สรุปเป็นภาษาไทย คงชื่อเฉพาะ คำย่อ และศัพท์เทคนิคภาษาอังกฤษตามต้นฉบับ
""".strip()

RECORD_LIST_FIELDS = (
    "participants",
    "topics",
    "decisions",
    "action_items",
    "key_facts",
    "questions",
    "risks",
    "open_issues",
    "corrections",
    "carry_forward",
)

CRITICAL_FIELDS = (
    "decisions",
    "action_items",
    "key_facts",
    "questions",
    "risks",
    "open_issues",
    "corrections",
)


def estimate_tokens(text: str) -> int:
    """Conservative multilingual estimate when the gateway tokenizer is unavailable."""
    if not text:
        return 0

    thai_chars = len(re.findall(r"[\u0E00-\u0E7F]", text))
    cjk_chars = len(re.findall(r"[\u3400-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]", text))
    remaining = re.sub(
        r"[\u0E00-\u0E7F\u3400-\u9FFF\u3040-\u30FF\uAC00-\uD7AF]",
        "",
        text,
    )
    latin_and_digits = len(re.findall(r"[A-Za-z0-9]", remaining))
    punctuation = len(re.findall(r"[^\w\s]", remaining, flags=re.UNICODE))
    line_overhead = text.count("\n") * 2

    estimate = (
        math.ceil(thai_chars / 2)
        + cjk_chars
        + math.ceil(latin_and_digits / 4)
        + math.ceil(punctuation / 2)
        + line_overhead
    )
    word_floor = math.ceil(len(text.split()) * 1.25)
    return max(1, estimate, word_floor)


def sample_text_windows(text: str, max_chars: int = 12000, windows: int = 3) -> str:
    """Sample beginning, middle, and end instead of silently using only the beginning."""
    if len(text) <= max_chars or windows <= 1:
        return text

    window_size = max(500, max_chars // windows)
    max_start = max(0, len(text) - window_size)
    starts = [round(i * max_start / (windows - 1)) for i in range(windows)]
    labels = ["ช่วงต้น", "ช่วงกลาง", "ช่วงท้าย"] if windows == 3 else []
    parts: list[str] = []
    for index, start in enumerate(starts):
        end = min(len(text), start + window_size)
        label = labels[index] if labels else f"ช่วงตัวอย่าง {index + 1}"
        parts.append(f"=== {label} ({start}:{end}) ===\n{text[start:end]}")
    return "\n\n".join(parts)


def _format_timestamp(seconds: Any) -> str:
    try:
        value = max(0, int(float(seconds)))
    except (TypeError, ValueError):
        value = 0
    hours, remainder = divmod(value, 3600)
    minutes, secs = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def _format_segment(segment: dict, source_index: int) -> str:
    speaker = str(segment.get("speaker") or "ไม่ทราบผู้พูด").strip()
    text = str(segment.get("text") or "").strip()
    timestamp = _format_timestamp(segment.get("start", 0))
    return f"[S{source_index}][{timestamp}][{speaker}] {text}"


def segments_from_text(text: str) -> list[dict]:
    """Create stable pseudo-segments for callers that only have rendered transcript text."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    return [
        {"text": line, "speaker": "Transcript", "start": index, "end": index + 1}
        for index, line in enumerate(lines)
    ]


def chunk_segments(
    segments: list[dict],
    max_tokens: int = CHUNK_INPUT_TOKENS,
    overlap_tokens: int = CHUNK_OVERLAP_TOKENS,
    source_offset: int = 0,
) -> list[dict]:
    """Split on segment boundaries and retain a small overlap for continuity."""
    prepared: list[tuple[int, str, int, dict]] = []
    for local_index, segment in enumerate(segments):
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        source_index = int(segment.get("_source_index", source_offset + local_index))
        line = _format_segment(segment, source_index)
        prepared.append((
            source_index,
            line,
            estimate_tokens(line),
            {**segment, "_source_index": source_index},
        ))

    if not prepared:
        return []

    chunks: list[dict] = []
    start = 0
    previous_end = 0
    while start < len(prepared):
        end = start
        token_count = 0
        while end < len(prepared):
            next_tokens = prepared[end][2]
            if end > start and token_count + next_tokens > max_tokens:
                break
            token_count += next_tokens
            end += 1
            if token_count >= max_tokens:
                break

        source_ids = [item[0] for item in prepared[start:end]]
        chunks.append({
            "chunk_number": len(chunks) + 1,
            "text": "\n".join(item[1] for item in prepared[start:end]),
            "segment_ids": source_ids,
            "start_segment_idx": source_ids[0],
            "end_segment_idx": source_ids[-1],
            "new_start_segment_idx": prepared[previous_end][0] if previous_end < end else source_ids[0],
            "estimated_tokens": token_count,
            "_segments": [item[3] for item in prepared[start:end]],
        })

        if end >= len(prepared):
            break

        overlap_start = end
        overlap_count = 0
        while overlap_start > start and overlap_count < overlap_tokens:
            overlap_start -= 1
            overlap_count += prepared[overlap_start][2]
        if overlap_start <= start:
            overlap_start = end
        previous_end = end
        start = overlap_start

    total = len(chunks)
    for chunk in chunks:
        chunk["total_chunks"] = total
    return chunks


def _extract_json_object(content: str) -> Optional[dict]:
    if not content:
        return None
    cleaned = content.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 3:
            cleaned = parts[1]
            if cleaned.lstrip().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
    start = cleaned.find("{")
    end = cleaned.rfind("}") + 1
    if start < 0 or end <= start:
        return None
    try:
        value = json.loads(cleaned[start:end])
    except (json.JSONDecodeError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def _json_string_field_fragment(content: str, field_name: str) -> str:
    match = re.search(rf'"{re.escape(field_name)}"\s*:\s*"', content)
    if not match:
        return ""

    chars: list[str] = []
    index = match.end()
    escaped = False
    while index < len(content):
        char = content[index]
        if escaped:
            if char == "n":
                chars.append("\n")
            elif char == "r":
                chars.append("\r")
            elif char == "t":
                chars.append("\t")
            elif char == "u" and index + 4 < len(content):
                hex_value = content[index + 1:index + 5]
                try:
                    chars.append(chr(int(hex_value, 16)))
                    index += 4
                except ValueError:
                    chars.append(char)
            else:
                chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
        else:
            chars.append(char)
        index += 1
    return "".join(chars).strip()


def _partial_summary_from_content(content: str) -> str:
    cleaned = str(content or "").strip()
    if not cleaned:
        return ""
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        if len(parts) >= 2:
            cleaned = parts[1]
            if cleaned.lstrip().startswith("json"):
                cleaned = cleaned.lstrip()[4:]
            cleaned = cleaned.strip()

    summary = _json_string_field_fragment(cleaned, "summary")
    if summary:
        return summary
    if cleaned.startswith("{") or cleaned.startswith("["):
        return ""
    return cleaned


def _dedupe_list(values: list[Any]) -> list[Any]:
    result: list[Any] = []
    seen: set[str] = set()
    for value in values:
        if value in (None, "", [], {}):
            continue
        marker = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (dict, list)) else str(value)
        marker = re.sub(r"\s+", " ", marker).strip().lower()
        if marker and marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def normalize_record(value: Optional[dict], fallback_summary: str = "") -> dict:
    value = value if isinstance(value, dict) else {}
    record = {
        "summary": str(value.get("summary") or fallback_summary or "").strip(),
        "coverage": value.get("coverage") if isinstance(value.get("coverage"), list) else [],
    }
    for field in RECORD_LIST_FIELDS:
        items = value.get(field, [])
        if not isinstance(items, list):
            items = [items] if items else []
        record[field] = _dedupe_list(items)
    return record


def _record_has_content(record: dict) -> bool:
    return bool(
        str(record.get("summary") or "").strip()
        or any(record.get(field) for field in RECORD_LIST_FIELDS)
    )


def _context_capsule(record: Optional[dict]) -> str:
    if not record:
        return "ไม่มี Context ก่อนหน้า นี่คือช่วงแรกของการประชุม"
    capsule = {
        "previous_summary": str(record.get("summary", ""))[-1600:],
        "participants": record.get("participants", [])[-20:],
        "topics": record.get("topics", [])[-12:],
        "open_issues": record.get("open_issues", [])[-12:],
        "corrections": record.get("corrections", [])[-8:],
        "carry_forward": record.get("carry_forward", [])[-12:],
    }
    return json.dumps(capsule, ensure_ascii=False)


def _template_instruction_block(template_prompt: str) -> str:
    template_prompt = str(template_prompt or "").strip()
    if not template_prompt:
        return "ไม่มี Meeting Template เพิ่มเติม ให้ใช้โครงสร้างสรุปมาตรฐานของระบบ"
    return template_prompt


def _chunk_system_prompt(scope_label: str, template_prompt: str = "", custom_prompt: str = "") -> str:
    prompt = f"""คุณคือผู้บันทึกการประชุมที่กำลังประมวลผลการประชุมยาวแบบต่อเนื่อง
หน้าที่ของคุณคือสกัดข้อเท็จจริงจาก Transcript {scope_label} โดยรักษาความเชื่อมโยงกับ Context ก่อนหน้า

Meeting Template หลักที่ต้องใช้กำหนดรูปแบบ น้ำหนักประเด็น และมุมมองการสรุป:
{_template_instruction_block(template_prompt)}

ในขั้นตอนนี้ยังต้องตอบเป็น JSON object ตาม schema ด้านล่างเท่านั้น
ให้นำ Meeting Template ไปใช้เพื่อเลือกความสำคัญและเรียงเนื้อหาใน field "summary"
ห้ามละทิ้งข้อเท็จจริงสำคัญจาก Transcript แม้ template จะไม่ได้ระบุหัวข้อนั้นโดยตรง

{GROUNDING_RULES}

ตอบเป็น JSON object เท่านั้นตามโครงสร้างนี้:
{{
  "summary": "สรุปช่วงนี้ตามลำดับเหตุการณ์อย่างละเอียด",
  "participants": ["ชื่อ/รหัสผู้พูดและบทบาทที่พบ"],
  "topics": ["ประเด็นที่หารือ"],
  "decisions": [{{"text": "มติหรือข้อตกลง", "status": "confirmed|proposed|rejected", "speaker": "", "evidence": ["S12"]}}],
  "action_items": [{{"task": "งาน", "owner": "ผู้รับผิดชอบหรือไม่ระบุ", "deadline": "กำหนดเวลาหรือไม่ระบุ", "status": "", "evidence": ["S12"]}}],
  "key_facts": [{{"text": "ตัวเลข วันที่ ชื่อเฉพาะ หรือข้อเท็จจริงสำคัญ", "evidence": ["S12"]}}],
  "questions": [{{"question": "คำถาม", "answer": "คำตอบหรือยังไม่มีคำตอบ", "evidence": ["S12"]}}],
  "risks": [{{"text": "ปัญหา ความเสี่ยง หรือข้อจำกัด", "evidence": ["S12"]}}],
  "open_issues": [{{"text": "เรื่องที่ยังไม่จบ", "evidence": ["S12"]}}],
  "corrections": [{{"old": "ข้อมูลเดิม", "new": "ข้อมูลแก้ไข", "evidence": ["S12"]}}],
  "carry_forward": ["บริบทที่ช่วงถัดไปจำเป็นต้องรู้"]
}}

Context ก่อนหน้าใช้เพื่อแก้คำอ้างอิง เช่น "เรื่องนั้น" หรือ "ตามที่กล่าวไว้" เท่านั้น
ห้ามนำข้อเท็จจริงจาก Context ก่อนหน้ามานับซ้ำว่าเกิดในช่วงปัจจุบัน
Segment ก่อน new_start_segment เป็น overlap สำหรับเชื่อมประโยค ให้หลีกเลี่ยงการบันทึกซ้ำ
evidence ต้องอ้างเฉพาะรหัส S ที่มีอยู่ใน Transcript ปัจจุบัน
ห้ามใส่รหัส S ใน summary หรือข้อความสำหรับผู้ใช้ ให้เก็บรหัสไว้ใน evidence field เท่านั้น"""
    if custom_prompt:
        prompt += f"\n\nคำสั่งเพิ่มเติมจากผู้ใช้ (ต้องไม่ขัดกับกฎความถูกต้อง):\n{custom_prompt}"
    return prompt


def extract_chunk_record(
    chunk: dict,
    previous_record: Optional[dict],
    llm_call: LLMCall,
    scope_label: str = "ช่วงหนึ่ง",
    custom_prompt: str = "",
    template_prompt: str = "",
    primary_only: bool = False,
) -> dict:
    chunk_label = chunk.get("_chunk_label", chunk["chunk_number"])
    root_chunk_number = int(chunk.get("_root_chunk_number", chunk["chunk_number"]))
    user_prompt = f"""ลำดับ: chunk {chunk_label} จาก {chunk['total_chunks']}
ขอบเขตหลักฐาน: S{chunk['start_segment_idx']} ถึง S{chunk['end_segment_idx']}
new_start_segment: S{chunk['new_start_segment_idx']}

Context ก่อนหน้า:
{_context_capsule(previous_record)}

Transcript ปัจจุบัน:
{chunk['text']}

สกัดข้อมูลให้ครบและตอบ JSON เท่านั้น"""
    content = llm_call(
        _chunk_system_prompt(scope_label, template_prompt, custom_prompt),
        user_prompt,
        temperature=0.1,
        max_tokens=CHUNK_OUTPUT_TOKENS,
        timeout=summary_llm_timeout(),
        primary_only=primary_only,
    )
    parsed = _extract_json_object(content)
    partial_summary = _partial_summary_from_content(content) if content and not parsed else ""
    record = normalize_record(parsed, fallback_summary=partial_summary)
    has_structured_content = bool(
        parsed
        and (
            record.get("summary")
            or any(record.get(field) for field in RECORD_LIST_FIELDS)
        )
    )
    has_partial_content = bool(partial_summary and not has_structured_content)
    if content and not has_structured_content:
        if has_partial_content:
            logger.warning("Chunk %s retained partial Gemma output without complete JSON", chunk_label)
        else:
            logger.warning("Chunk %s returned invalid or empty structured output", chunk_label)
    record["coverage"] = list(chunk["segment_ids"]) if has_structured_content else []
    record["source_chunks"] = [root_chunk_number]
    record["failed_chunks"] = [] if has_structured_content else [root_chunk_number]
    record["partial_chunks"] = [root_chunk_number] if has_partial_content else []
    return record


def _fragment_segment(segment: dict, target_tokens: int) -> list[dict]:
    text = str(segment.get("text") or "").strip()
    if not text or estimate_tokens(text) <= target_tokens:
        return [segment]

    piece_count = max(2, math.ceil(estimate_tokens(text) / target_tokens))
    char_size = max(1, math.ceil(len(text) / piece_count))
    try:
        start_time = float(segment.get("start") or 0)
    except (TypeError, ValueError):
        start_time = 0.0
    try:
        end_time = float(segment.get("end") or start_time)
    except (TypeError, ValueError):
        end_time = start_time
    duration = max(0.0, end_time - start_time)
    fragments: list[dict] = []
    for index, start in enumerate(range(0, len(text), char_size)):
        fragment_text = text[start:start + char_size].strip()
        if not fragment_text:
            continue
        ratio_start = start / len(text)
        ratio_end = min(1.0, (start + char_size) / len(text))
        fragments.append({
            **segment,
            "text": fragment_text,
            "start": start_time + duration * ratio_start,
            "end": start_time + duration * ratio_end,
        })
    return fragments or [segment]


def _split_failed_chunk(chunk: dict, target_tokens: int) -> list[dict]:
    source_segments = list(chunk.get("_segments") or [])
    if len(source_segments) == 1:
        source_segments = _fragment_segment(source_segments[0], target_tokens)
    children = chunk_segments(source_segments, max_tokens=target_tokens, overlap_tokens=0)
    if len(children) <= 1:
        return children

    root_chunk_number = int(chunk.get("_root_chunk_number", chunk["chunk_number"]))
    parent_label = str(chunk.get("_chunk_label", root_chunk_number))
    for index, child in enumerate(children, start=1):
        child["_root_chunk_number"] = root_chunk_number
        child["_chunk_label"] = f"{parent_label}.{index}"
        child["chunk_number"] = root_chunk_number
        child["total_chunks"] = chunk["total_chunks"]
    return children


def extract_chunk_records_adaptively(
    chunk: dict,
    previous_record: Optional[dict],
    llm_call: LLMCall,
    scope_label: str,
    custom_prompt: str,
    template_prompt: str = "",
    depth: int = 0,
) -> list[dict]:
    can_split = (
        depth < ADAPTIVE_SPLIT_MAX_DEPTH
        and int(chunk.get("estimated_tokens") or 0) > ADAPTIVE_MIN_INPUT_TOKENS
        and bool(chunk.get("_segments"))
    )
    record = extract_chunk_record(
        chunk,
        previous_record,
        llm_call,
        scope_label,
        custom_prompt,
        template_prompt=template_prompt,
        primary_only=can_split or SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
    )
    if record.get("failed_chunks") and SUMMARY_FAST_DEGRADE_ON_TIMEOUT:
        logger.warning(
            "Fast summary degraded chunk %s without adaptive split",
            chunk.get("_chunk_label", chunk["chunk_number"]),
        )
        return [record]
    if not record.get("failed_chunks") or not can_split:
        return [record]

    target_tokens = max(
        ADAPTIVE_MIN_INPUT_TOKENS,
        int(chunk.get("estimated_tokens") or CHUNK_INPUT_TOKENS) // 2,
    )
    children = _split_failed_chunk(chunk, target_tokens)
    if len(children) <= 1:
        # Splitting cannot make this input smaller; make one final attempt with fallbacks enabled.
        return [extract_chunk_record(
            chunk,
            previous_record,
            llm_call,
            scope_label,
            custom_prompt,
            template_prompt=template_prompt,
            primary_only=SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
        )]

    logger.warning(
        "Adaptive summary split chunk %s into %s parts at target_tokens=%s depth=%s",
        chunk.get("_chunk_label", chunk["chunk_number"]),
        len(children),
        target_tokens,
        depth + 1,
    )
    records: list[dict] = []
    context = previous_record
    for child in children:
        child_records = extract_chunk_records_adaptively(
            child,
            context,
            llm_call,
            scope_label,
            custom_prompt,
            template_prompt=template_prompt,
            depth=depth + 1,
        )
        records.extend(child_records)
        successful = [item for item in child_records if not item.get("failed_chunks")]
        if successful:
            context = deterministic_merge(([context] if context else []) + successful)
    return records


def deterministic_merge(records: list[dict]) -> dict:
    merged = normalize_record({})
    summaries: list[str] = []
    source_chunks: list[int] = []
    failed_chunks: list[int] = []
    partial_chunks: list[int] = []
    reduce_degraded = False
    reduce_skipped_for_speed = False
    for record in records:
        summary = str(record.get("summary") or "").strip()
        if summary:
            summaries.append(summary)
        reduce_degraded = reduce_degraded or bool(record.get("reduce_degraded"))
        reduce_skipped_for_speed = reduce_skipped_for_speed or bool(record.get("reduce_skipped_for_speed"))
        merged["coverage"].extend(record.get("coverage", []))
        source_chunks.extend(record.get("source_chunks", []))
        failed_chunks.extend(record.get("failed_chunks", []))
        partial_chunks.extend(record.get("partial_chunks", []))
        for field in RECORD_LIST_FIELDS:
            merged[field].extend(record.get(field, []))
    merged["summary"] = "\n\n".join(summaries)
    merged["coverage"] = sorted(set(merged["coverage"]))
    merged["source_chunks"] = sorted(set(source_chunks))
    merged["failed_chunks"] = sorted(set(failed_chunks))
    merged["partial_chunks"] = sorted(set(partial_chunks))
    merged["reduce_degraded"] = reduce_degraded
    merged["reduce_skipped_for_speed"] = reduce_skipped_for_speed
    for field in RECORD_LIST_FIELDS:
        merged[field] = _dedupe_list(merged[field])
    return merged


def _reduce_system_prompt(scope_label: str) -> str:
    return f"""คุณคือบรรณาธิการบันทึกการประชุมยาว กำลังรวมข้อมูลหลายช่วงของ {scope_label}

{GROUNDING_RULES}

รวมข้อมูลโดย:
1. รักษาลำดับเหตุการณ์และความต่อเนื่องระหว่างช่วง
2. ตัดเฉพาะข้อความที่ซ้ำกันจริง ห้ามตัดมติ งานมอบหมาย ตัวเลข คำถาม ความเห็นต่าง หรือเรื่องค้าง
3. ถ้าข้อมูลช่วงหลังแก้ไขช่วงก่อน ให้บันทึกการแก้ไข ไม่ลบประวัติเดิม
4. รวม evidence ของข้อเท็จจริงที่ซ้ำกัน
5. ห้ามแสดงข้อสรุปที่ไม่มีในข้อมูลต้นทาง

ตอบ JSON object เท่านั้น และใช้ field เดิมทั้งหมด:
summary, participants, topics, decisions, action_items, key_facts, questions,
risks, open_issues, corrections, carry_forward"""


def _merge_record_group(records: list[dict], llm_call: LLMCall, scope_label: str) -> dict:
    deterministic = deterministic_merge(records)
    payload = [{key: record.get(key) for key in ("summary", *RECORD_LIST_FIELDS, "coverage")} for record in records]
    payload_tokens = estimate_tokens(json.dumps(payload, ensure_ascii=False))
    if SUMMARY_FAST_DEGRADE_ON_TIMEOUT:
        logger.info(
            "Skipping LLM reduce in fast summary mode: payload_tokens=%s threshold=%s",
            payload_tokens,
            int(REDUCE_INPUT_TOKENS * 0.8),
        )
        deterministic["reduce_skipped_for_speed"] = True
        return deterministic

    content = llm_call(
        _reduce_system_prompt(scope_label),
        "รวมข้อมูลต่อไปนี้โดยไม่ทำข้อเท็จจริงสูญหาย:\n" + json.dumps(payload, ensure_ascii=False),
        temperature=0.1,
        max_tokens=REDUCE_OUTPUT_TOKENS,
        timeout=summary_llm_timeout(),
        primary_only=SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
    )
    parsed = _extract_json_object(content)
    if not parsed:
        logger.warning("Structured reduce failed; retaining deterministic merge")
        deterministic["reduce_degraded"] = True
        return deterministic

    merged = normalize_record(parsed, fallback_summary=content)
    merged["coverage"] = deterministic["coverage"]
    merged["source_chunks"] = deterministic["source_chunks"]
    merged["failed_chunks"] = deterministic["failed_chunks"]
    merged["reduce_degraded"] = False
    for field in CRITICAL_FIELDS:
        # Never let a compression pass erase previously extracted evidence.
        merged[field] = _dedupe_list(deterministic[field] + merged[field])
    return merged


def _group_records(records: list[dict], token_budget: int) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current: list[dict] = []
    current_tokens = 0
    for record in records:
        serialized = json.dumps(record, ensure_ascii=False)
        record_tokens = estimate_tokens(serialized)
        if current and current_tokens + record_tokens > token_budget:
            groups.append(current)
            current = []
            current_tokens = 0
        current.append(record)
        current_tokens += record_tokens
    if current:
        groups.append(current)
    return groups


def reduce_records(records: list[dict], llm_call: LLMCall, scope_label: str) -> dict:
    if not records:
        return normalize_record({})
    level = list(records)
    round_number = 0
    while len(level) > 1:
        round_number += 1
        groups = _group_records(level, REDUCE_INPUT_TOKENS)
        # A group of one does not reduce the tree; pair adjacent groups in that case.
        if len(groups) == len(level):
            groups = [level[index:index + 2] for index in range(0, len(level), 2)]
        logger.info(
            "Incremental summary reduce round %s: %s records -> %s groups",
            round_number,
            len(level),
            len(groups),
        )
        level = [
            _merge_record_group(group, llm_call, scope_label) if len(group) > 1 else group[0]
            for group in groups
        ]
    return level[0]


def adaptive_final_max_tokens(input_tokens: int) -> int:
    if input_tokens <= 20000:
        return min(4000, FINAL_MAX_TOKENS)
    if input_tokens <= 60000:
        return min(6000, FINAL_MAX_TOKENS)
    return FINAL_MAX_TOKENS


def _min_configured_timeout(*timeouts: Optional[int]) -> Optional[int]:
    configured = [timeout for timeout in timeouts if timeout is not None]
    if not configured:
        return None
    return min(configured)


def summary_llm_timeout() -> Optional[int]:
    if SUMMARY_FAST_DEGRADE_ON_TIMEOUT:
        return _min_configured_timeout(SUMMARY_LLM_TIMEOUT_SECONDS, SUMMARY_FAST_TIMEOUT_SECONDS)
    return SUMMARY_LLM_TIMEOUT_SECONDS


def _render_system_prompt(
    meeting_type_id: int,
    template_prompt: str,
    custom_prompt: str,
    scope_label: str,
) -> str:
    info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES[11])
    focus = get_meeting_focus_prompt(meeting_type_id)
    prompt = f"""คุณคือผู้จัดทำรายงานสรุปการประชุมฉบับสมบูรณ์สำหรับ {scope_label}

ประเภทการประชุม: {info['thai']} ({info['name']})
โครงสร้าง: {info['structure']}
{focus}

คำแนะนำรูปแบบจากระบบ:
{template_prompt}

{GROUNDING_RULES}

ข้อมูลที่ได้รับผ่านการสกัดและตรวจลำดับแล้ว ให้จัดรูปแบบเท่านั้น ห้ามเพิ่มข้อเท็จจริงใหม่
ต้องครอบคลุมทุกหัวข้อ มติ งานมอบหมาย ตัวเลข คำถาม ความเสี่ยง และเรื่องที่ยังไม่จบ
ห้ามแสดงรหัสหลักฐาน S หรือ field ภายในในรายงานสำหรับผู้ใช้
จัดหัวข้อและ bullet points ให้อ่านง่าย โดยความยาวต้องสัมพันธ์กับเนื้อหาจริง ไม่ย่อช่วงท้ายมากกว่าช่วงต้น"""
    if custom_prompt:
        prompt += f"\n\nคำสั่งเพิ่มเติมจากผู้ใช้ (ต้องไม่ขัดกับกฎความถูกต้อง):\n{custom_prompt}"
    return prompt


def render_record(
    record: dict,
    llm_call: LLMCall,
    meeting_type_id: int,
    template_prompt: str,
    custom_prompt: str,
    scope_label: str,
    input_tokens: int,
) -> tuple[str, int, bool]:
    max_tokens = adaptive_final_max_tokens(input_tokens)
    content = llm_call(
        _render_system_prompt(meeting_type_id, template_prompt, custom_prompt, scope_label),
        "จัดทำรายงานจาก Meeting Memory ต่อไปนี้:\n" + json.dumps(record, ensure_ascii=False),
        temperature=0.2,
        max_tokens=max_tokens,
        timeout=summary_llm_timeout(),
        primary_only=SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
    )
    render_degraded = False
    if content:
        result = content.strip()
    else:
        result = record_to_text(record, heading=f"สรุป{scope_label}")
        render_degraded = True
    failed_chunks = sorted(set(record.get("failed_chunks", [])))
    if failed_chunks or render_degraded or record.get("reduce_degraded"):
        failed_text = ", ".join(str(number) for number in failed_chunks)
        warning = SUMMARY_GEMMA_PARTIAL_WARNING
        if failed_text:
            warning = f"{SUMMARY_GEMMA_PARTIAL_WARNING} (ส่วนที่ต้องตรวจสอบเพิ่มเติม: {failed_text})"
        result = append_user_warning(result, warning)
    return result.strip(), max_tokens, render_degraded


def _item_text(item: Any, preferred_keys: tuple[str, ...] = ("text",)) -> str:
    if isinstance(item, str):
        return item.strip()
    if not isinstance(item, dict):
        return str(item).strip()
    for key in preferred_keys:
        value = item.get(key)
        if value:
            return str(value).strip()
    return "; ".join(f"{key}: {value}" for key, value in item.items() if key != "evidence" and value)


def record_to_text(record: dict, heading: str = "สรุปการประชุม") -> str:
    parts = [heading, "", str(record.get("summary") or "").strip()]
    sections = (
        ("มติ/ข้อตกลง", "decisions", ("text",)),
        ("งานมอบหมาย", "action_items", ("task", "text")),
        ("ข้อเท็จจริงสำคัญ", "key_facts", ("text",)),
        ("คำถามสำคัญ", "questions", ("question", "text")),
        ("ความเสี่ยง/ปัญหา", "risks", ("text",)),
        ("เรื่องที่ยังไม่จบ", "open_issues", ("text",)),
    )
    for title, field, keys in sections:
        values = record.get(field, [])
        if values:
            parts.extend(["", title])
            parts.extend(f"- {_item_text(item, keys)}" for item in values)
    return "\n".join(part for part in parts if part is not None).strip()


def transcript_fallback_text(segments: list[dict], heading: str = "สรุปการประชุม") -> str:
    lines = [
        heading,
        "",
        "ระบบ LLM ไม่พร้อมใช้งาน จึงแสดงข้อมูลจาก Transcript ทุกช่วงตามลำดับแทนการตัดทิ้ง",
    ]
    for index, segment in enumerate(segments):
        text = re.sub(r"\s+", " ", str(segment.get("text") or "")).strip()
        if not text:
            continue
        source_index = int(segment.get("_source_index", index))
        speaker = str(segment.get("speaker") or "ไม่ทราบผู้พูด").strip()
        timestamp = _format_timestamp(segment.get("start", 0))
        lines.append(f"- [S{source_index}][{timestamp}][{speaker}] {text}")
    return "\n".join(lines).strip()


def append_user_warning(summary: str, warning: str = SUMMARY_USER_WARNING) -> str:
    result = str(summary or "").strip()
    if warning and warning not in result:
        result = f"{result}\n\nคำเตือน: {warning}".strip()
    return result


def build_token_check(input_tokens: int, chunk_count: int) -> dict:
    return {
        "estimated_tokens": input_tokens,
        "max_tokens_per_chunk": CHUNK_INPUT_TOKENS,
        "exceeds_max_tokens": input_tokens > CHUNK_INPUT_TOKENS,
        "chunking_applied": chunk_count > 1,
    }


def emergency_truncated_summary(
    segments: list[dict],
    llm_call: LLMCall,
    scope_label: str = "การประชุม",
) -> tuple[str, dict]:
    chunks = chunk_segments(segments, max_tokens=CHUNK_INPUT_TOKENS, overlap_tokens=0)
    if not chunks:
        return "", {
            "recovery_attempted": False,
            "recovery_succeeded": False,
            "truncated_input_tokens": 0,
        }

    recovery_chunk = chunks[0]
    if (
        recovery_chunk["estimated_tokens"] > CHUNK_INPUT_TOKENS
        and len(recovery_chunk.get("_segments") or []) == 1
    ):
        fragments = _fragment_segment(
            recovery_chunk["_segments"][0],
            max(500, CHUNK_INPUT_TOKENS - 200),
        )
        fragment_chunks = chunk_segments(
            fragments,
            max_tokens=CHUNK_INPUT_TOKENS,
            overlap_tokens=0,
        )
        if fragment_chunks:
            recovery_chunk = fragment_chunks[0]
    content = llm_call(
        f"""คุณกำลังทำสรุปฉุกเฉินสำหรับ{scope_label}
สรุปเฉพาะ Transcript ที่ได้รับอย่างตรงไปตรงมา ห้ามแต่งข้อมูล และห้ามกล่าวว่าเนื้อหาครบทั้งหมด
ตอบเป็นภาษาไทยพร้อมหัวข้อ ประเด็นสำคัญ มติ และงานมอบหมายเท่าที่พบ""",
        "Transcript ที่ตัดตามเพดาน token:\n" + recovery_chunk["text"],
        temperature=0.1,
        max_tokens=CHUNK_OUTPUT_TOKENS,
        timeout=summary_llm_timeout(),
        primary_only=SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
    )
    return str(content or "").strip(), {
        "recovery_attempted": True,
        "recovery_succeeded": bool(content and str(content).strip()),
        "truncated_input_tokens": recovery_chunk["estimated_tokens"],
        "truncated_segment_ids": recovery_chunk["segment_ids"],
        "source_segment_count": len({
            int(segment.get("_source_index", index))
            for index, segment in enumerate(segments)
            if str(segment.get("text") or "").strip()
        }),
    }


def summarize_transcript_incrementally(
    transcript: str,
    segments: Optional[list[dict]],
    meeting_type_id: int,
    template_prompt: str,
    custom_prompt: str,
    llm_call: LLMCall,
) -> tuple[str, dict]:
    source_segments = segments or segments_from_text(transcript)
    chunks = chunk_segments(source_segments)
    if not chunks:
        return "", {"version": "incremental", "chunk_count": 0, "coverage_complete": False}

    input_tokens = estimate_tokens(transcript)
    token_check = build_token_check(input_tokens, len(chunks))
    logger.info(
        "WhisperX transcript token check: estimated_tokens=%s max_tokens_per_chunk=%s "
        "exceeds_max=%s chunks=%s",
        input_tokens,
        CHUNK_INPUT_TOKENS,
        token_check["exceeds_max_tokens"],
        len(chunks),
    )
    records: list[dict] = []
    previous: Optional[dict] = None
    for chunk in chunks:
        logger.info(
            "Incremental summary chunk %s/%s: S%s-S%s estimated_tokens=%s",
            chunk["chunk_number"],
            chunk["total_chunks"],
            chunk["start_segment_idx"],
            chunk["end_segment_idx"],
            chunk["estimated_tokens"],
        )
        chunk_records = extract_chunk_records_adaptively(
            chunk,
            previous,
            llm_call,
            scope_label="การประชุม",
            custom_prompt=custom_prompt,
            template_prompt=template_prompt,
        )
        records.extend(chunk_records)
        successful = [record for record in chunk_records if not record.get("failed_chunks")]
        if successful:
            previous = deterministic_merge(([previous] if previous else []) + successful)

    if all(record.get("failed_chunks") for record in records) and not any(
        _record_has_content(record) for record in records
    ):
        fallback = transcript_fallback_text(source_segments)
        return append_user_warning(fallback, SUMMARY_GEMMA_EMPTY_WARNING), {
            "version": "incremental",
            "chunk_count": len(chunks),
            "token_check": token_check,
            "coverage_complete": False,
            "covered_segments": 0,
            "total_segments": sum(
                1 for segment in source_segments if str(segment.get("text") or "").strip()
            ),
            "failed_chunks": [chunk["chunk_number"] for chunk in chunks],
            "partial_chunks": [],
            "extraction_complete": False,
            "degraded": True,
            "user_warning": SUMMARY_GEMMA_EMPTY_WARNING,
            "fallback_strategy": "transcript_fallback_no_gemma_chunks_completed",
            "recovery_attempted": False,
            "recovery_succeeded": False,
            "recovery_skipped": "fast_summary_no_extra_llm_call",
        }

    merged = reduce_records(records, llm_call, "การประชุม")
    summary, final_max_tokens, render_degraded = render_record(
        merged,
        llm_call,
        meeting_type_id,
        template_prompt,
        custom_prompt,
        "การประชุม",
        input_tokens,
    )
    expected_ids = {
        int(segment.get("_source_index", index))
        for index, segment in enumerate(source_segments)
        if str(segment.get("text") or "").strip()
    }
    covered_ids = set(merged.get("coverage", []))
    metadata = {
        "version": "incremental",
        "estimated_input_tokens": input_tokens,
        "chunk_count": len(chunks),
        "token_check": token_check,
        "extraction_call_count": len(records),
        "final_max_tokens": final_max_tokens,
        "fast_degrade_enabled": SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
        "render_degraded": render_degraded,
        "reduce_degraded": bool(merged.get("reduce_degraded")),
        "reduce_skipped_for_speed": bool(merged.get("reduce_skipped_for_speed")),
        "coverage_complete": expected_ids.issubset(covered_ids),
        "covered_segments": len(expected_ids & covered_ids),
        "total_segments": len(expected_ids),
        "evidence_counts": {field: len(merged.get(field, [])) for field in CRITICAL_FIELDS},
        "evidence_index": _evidence_index(merged),
        "failed_chunks": merged.get("failed_chunks", []),
        "partial_chunks": merged.get("partial_chunks", []),
        "extraction_complete": not merged.get("failed_chunks"),
        "degraded": bool(
            merged.get("failed_chunks")
            or merged.get("reduce_degraded")
            or render_degraded
        ),
    }
    if metadata["degraded"]:
        metadata["user_warning"] = SUMMARY_GEMMA_PARTIAL_WARNING
        metadata["fallback_strategy"] = "gemma_partial_summary"
    return summary, metadata


def summarize_agenda_segments(
    segments: list[dict],
    agenda_title: str,
    agenda_number: int,
    total_agendas: int,
    custom_prompt: str,
    llm_call: LLMCall,
    template_prompt: str = "",
) -> tuple[dict, dict, dict]:
    chunks = chunk_segments(segments)
    input_text = "\n".join(
        _format_segment(segment, int(segment.get("_source_index", index)))
        for index, segment in enumerate(segments)
        if str(segment.get("text") or "").strip()
    )
    input_tokens = estimate_tokens(input_text)
    token_check = build_token_check(input_tokens, len(chunks))
    records: list[dict] = []
    previous: Optional[dict] = None
    scope = f"วาระที่ {agenda_number}/{total_agendas}: {agenda_title}"
    for chunk in chunks:
        chunk_records = extract_chunk_records_adaptively(
            chunk,
            previous,
            llm_call,
            scope,
            custom_prompt,
            template_prompt=template_prompt,
        )
        records.extend(chunk_records)
        successful = [record for record in chunk_records if not record.get("failed_chunks")]
        if successful:
            previous = deterministic_merge(([previous] if previous else []) + successful)
    merged = reduce_records(records, llm_call, scope) if records else normalize_record({})
    result = {
        "summary": merged.get("summary", ""),
        "decisions": [_item_text(item, ("text",)) for item in merged.get("decisions", [])],
        "action_items": [_format_action_item(item) for item in merged.get("action_items", [])],
    }
    metadata = {
        "agenda_number": agenda_number,
        "estimated_input_tokens": input_tokens,
        "chunk_count": len(chunks),
        "token_check": token_check,
        "extraction_call_count": len(records),
        "coverage": merged.get("coverage", []),
        "evidence_counts": {field: len(merged.get(field, [])) for field in CRITICAL_FIELDS},
        "evidence_index": _evidence_index(merged),
        "failed_chunks": merged.get("failed_chunks", []),
        "partial_chunks": merged.get("partial_chunks", []),
        "extraction_complete": not merged.get("failed_chunks"),
        "degraded": bool(merged.get("failed_chunks")),
    }
    if metadata["degraded"]:
        metadata["user_warning"] = SUMMARY_GEMMA_PARTIAL_WARNING
        metadata["fallback_strategy"] = "gemma_partial_summary"
    return result, merged, metadata


def _format_action_item(item: Any) -> str:
    if not isinstance(item, dict):
        return str(item).strip()
    task = _item_text(item, ("task", "text"))
    owner = str(item.get("owner") or "ไม่ระบุ")
    deadline = str(item.get("deadline") or "ไม่ระบุ")
    return f"{task} | ผู้รับผิดชอบ: {owner} | กำหนด: {deadline}"


def _evidence_index(record: dict) -> dict:
    index: dict[str, list[dict]] = {}
    for field in CRITICAL_FIELDS:
        entries: list[dict] = []
        for item in record.get(field, []):
            if not isinstance(item, dict):
                continue
            evidence = item.get("evidence", [])
            if not isinstance(evidence, list) or not evidence:
                continue
            entries.append({
                "text": _item_text(item, ("text", "task", "question", "old")),
                "segments": evidence,
            })
        if entries:
            index[field] = entries
    return index


def summarize_agenda_collection(
    records: list[dict],
    meeting_type_id: int,
    template_prompt: str,
    custom_prompt: str,
    llm_call: LLMCall,
    input_tokens: int,
) -> tuple[str, dict]:
    merged = reduce_records(records, llm_call, "ทุกวาระของการประชุม")
    summary, final_max_tokens, render_degraded = render_record(
        merged,
        llm_call,
        meeting_type_id,
        template_prompt,
        custom_prompt,
        "ภาพรวมการประชุม",
        input_tokens,
    )
    metadata = {
        "version": "incremental-agenda",
        "estimated_input_tokens": input_tokens,
        "agenda_count": len(records),
        "final_max_tokens": final_max_tokens,
        "fast_degrade_enabled": SUMMARY_FAST_DEGRADE_ON_TIMEOUT,
        "render_degraded": render_degraded,
        "reduce_degraded": bool(merged.get("reduce_degraded")),
        "reduce_skipped_for_speed": bool(merged.get("reduce_skipped_for_speed")),
        "evidence_counts": {field: len(merged.get(field, [])) for field in CRITICAL_FIELDS},
        "evidence_index": _evidence_index(merged),
        "failed_chunks": merged.get("failed_chunks", []),
        "partial_chunks": merged.get("partial_chunks", []),
        "extraction_complete": not merged.get("failed_chunks"),
        "degraded": bool(
            merged.get("failed_chunks")
            or merged.get("reduce_degraded")
            or render_degraded
        ),
    }
    if metadata["degraded"]:
        metadata["user_warning"] = SUMMARY_GEMMA_PARTIAL_WARNING
        metadata["fallback_strategy"] = "gemma_partial_summary"
        summary = append_user_warning(summary, metadata["user_warning"])
    return summary, metadata
