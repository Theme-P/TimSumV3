"""
Agenda Detector — Hybrid Rule-based + LLM Context Analysis.

Detects topic/agenda boundaries in meeting transcripts using a two-pass approach:
1. Rule-based: Regex patterns to find explicit agenda/topic markers (e.g., "วาระที่ 1")
2. LLM Context: the configured gateway model analyzes semantic shifts and validates/extends rule-based anchors

Supports 3 detection modes:
- formal_agenda: Formal meetings with numbered agendas
- topic_segments: Semi-formal meetings with topic shifts
- single_topic: No segmentation needed (single continuous topic)
"""

import re
import json
import logging
from typing import Optional

from .summarizer import _call_llm_with_fallback

logger = logging.getLogger(__name__)

# Thai agenda keyword patterns — ordered by formality level
AGENDA_PATTERNS: list[dict] = [
    # Formal: วาระที่ 1, วาระที่ ๑, agenda 1
    {"pattern": r"วาระ\s*ที่\s*[\d๑-๙]+", "type": "formal_agenda", "weight": 3},
    {"pattern": r"เรื่อง\s*ที่\s*[\d๑-๙]+", "type": "formal_agenda", "weight": 3},
    {"pattern": r"(?i)agenda\s*(?:item\s*)?\d+", "type": "formal_agenda", "weight": 3},
    # Semi-formal: เข้าสู่วาระ, เรื่องถัดไป
    {"pattern": r"เข้า\s*สู่\s*วาระ", "type": "formal_agenda", "weight": 2},
    {"pattern": r"เรื่อง\s*ถัดไป", "type": "topic_segments", "weight": 2},
    {"pattern": r"หัวข้อ\s*ถัดไป", "type": "topic_segments", "weight": 2},
    {"pattern": r"ประเด็น\s*ถัดไป", "type": "topic_segments", "weight": 2},
    {"pattern": r"(?:ต่อไป|ถัดไป)\s*(?:เป็น|คือ)?\s*เรื่อง", "type": "topic_segments", "weight": 2},
    # Informal: ขอจบ, เปลี่ยนเรื่อง
    {"pattern": r"ขอ\s*จบ\s*(?:วาระ|เรื่อง|หัวข้อ)", "type": "topic_segments", "weight": 1},
    {"pattern": r"เปลี่ยน\s*เรื่อง", "type": "topic_segments", "weight": 1},
]


def _extract_rule_based_anchors(
    segments: list[dict],
) -> list[dict]:
    """
    Scan transcript segments for keyword-based agenda markers.

    Returns list of anchor points with segment indices and matched patterns.
    Each anchor: {"segment_idx": int, "text": str, "pattern": str, "type": str, "weight": int}
    """
    anchors: list[dict] = []

    for idx, segment in enumerate(segments):
        text = segment.get("text", "").strip()
        if not text:
            continue

        for rule in AGENDA_PATTERNS:
            match = re.search(rule["pattern"], text)
            if match:
                anchors.append({
                    "segment_idx": idx,
                    "matched_text": match.group(),
                    "full_text": text[:200],  # Truncate for context
                    "start_time": segment.get("start", 0.0),
                    "type": rule["type"],
                    "weight": rule["weight"],
                })
                break  # Only one match per segment to avoid duplicates

    logger.info(f"Rule-based anchors found: {len(anchors)}")
    return anchors


def _build_llm_detection_prompt(
    transcript_lines: list[str],
    anchors: list[dict],
    meeting_type_id: int,
) -> tuple[str, str]:
    """Build system and user prompts for LLM agenda detection."""

    # Truncate transcript to fit context window — use first 80k chars max
    transcript_text = "\n".join(transcript_lines)
    max_chars = 80000
    if len(transcript_text) > max_chars:
        transcript_text = transcript_text[:max_chars] + "\n[... ตัดทอนส่วนที่เหลือ ...]"

    anchor_summary = "ไม่พบ keyword วาระในเนื้อหา"
    if anchors:
        anchor_lines = []
        for anchor in anchors:
            anchor_lines.append(
                f"  - Segment {anchor['segment_idx']} "
                f"(เวลา {anchor['start_time']:.1f}s): \"{anchor['matched_text']}\" "
                f"→ {anchor['full_text'][:100]}"
            )
        anchor_summary = "\n".join(anchor_lines)

    system_prompt = """คุณคือ AI ผู้เชี่ยวชาญในการวิเคราะห์โครงสร้างการประชุม
คุณต้องหาจุดแบ่งหัวข้อ/วาระในบันทึกการประชุม (transcript) ที่ให้มา

**วิธีการตรวจจับ:**
1. ถ้ามี Anchor Points (keyword ที่พบแล้ว) ให้ใช้เป็นจุดอ้างอิงหลัก
2. ตรวจสอบว่ามีจุดเปลี่ยนหัวข้อเพิ่มเติมที่ไม่มี keyword หรือไม่ โดยดูจาก:
   - การเปลี่ยนบริบทของเนื้อหา (Semantic Shift)
   - การเปลี่ยนผู้นำเสนอ/ผู้พูดหลัก
   - วลีเชื่อมเช่น "ต่อไป", "อีกเรื่องหนึ่ง", "มาดูเรื่อง..."
3. กำหนดชื่อวาระ/หัวข้อที่สื่อความหมายสั้นๆ ชัดเจน

**กฎสำคัญ:**
- ถ้าการประชุมคุยเรื่องเดียวตลอด ให้ตอบ detection_mode = "single_topic" และ items = []
- ถ้ามีการคุยย้อนกลับมาเรื่องเดิมอีกรอบ ให้แยกเป็น item ใหม่ เช่น "งบประมาณ (ต่อ)"
- Segment index ต้องเรียงจากน้อยไปมาก ไม่ซ้อนทับกัน
- ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น

**JSON Format:**
```json
{
  "detection_mode": "formal_agenda" | "topic_segments" | "single_topic",
  "items": [
    {
      "title": "ชื่อวาระ/หัวข้อ",
      "start_segment_idx": 0,
      "end_segment_idx": 45,
      "confidence": 0.95
    }
  ]
}
```"""

    user_prompt = f"""**ข้อมูล Meeting Type ID:** {meeting_type_id}

**Anchor Points ที่ Rule-based ตรวจพบ:**
{anchor_summary}

**Transcript (พร้อม segment index):**
{transcript_text}

กรุณาวิเคราะห์และตอบเป็น JSON"""

    return system_prompt, user_prompt


def _parse_llm_response(content: str, total_segments: int) -> Optional[dict]:
    """Parse and validate LLM JSON response for agenda detection."""
    if not content:
        return None

    try:
        # Strip markdown code fences if present
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.split("```")[1]
            if cleaned.startswith("json"):
                cleaned = cleaned[4:]
            cleaned = cleaned.strip()

        result = json.loads(cleaned)

        # Validate required fields
        detection_mode = result.get("detection_mode", "single_topic")
        if detection_mode not in ("formal_agenda", "topic_segments", "single_topic"):
            logger.warning(f"Invalid detection_mode: {detection_mode}, defaulting to single_topic")
            detection_mode = "single_topic"

        items = result.get("items", [])
        if not isinstance(items, list):
            items = []

        # Validate and sanitize each item
        validated_items: list[dict] = []
        for item in items:
            start_idx = item.get("start_segment_idx", 0)
            end_idx = item.get("end_segment_idx", 0)
            title = item.get("title", "").strip()
            confidence = item.get("confidence", 0.5)

            # Bounds check
            if not isinstance(start_idx, int) or not isinstance(end_idx, int):
                continue
            start_idx = max(0, min(start_idx, total_segments - 1))
            end_idx = max(start_idx, min(end_idx, total_segments - 1))

            if not title:
                title = f"หัวข้อที่ {len(validated_items) + 1}"

            # Clamp confidence to 0-1 range
            confidence = max(0.0, min(1.0, float(confidence)))

            validated_items.append({
                "title": title,
                "start_segment_idx": start_idx,
                "end_segment_idx": end_idx,
                "confidence": confidence,
            })

        # Sort by start_segment_idx and remove overlaps
        validated_items.sort(key=lambda x: x["start_segment_idx"])
        non_overlapping: list[dict] = []
        for item in validated_items:
            if non_overlapping and item["start_segment_idx"] <= non_overlapping[-1]["end_segment_idx"]:
                # Overlap detected — extend the previous item instead
                non_overlapping[-1]["end_segment_idx"] = max(
                    non_overlapping[-1]["end_segment_idx"],
                    item["end_segment_idx"],
                )
                logger.warning(
                    f"Merged overlapping agenda items: "
                    f"'{non_overlapping[-1]['title']}' absorbed '{item['title']}'"
                )
            else:
                non_overlapping.append(item)

        return {
            "detection_mode": detection_mode,
            "items": non_overlapping,
        }

    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error(f"Failed to parse LLM agenda response: {exc}")
        return None


def detect_agendas(
    segments: list[dict],
    meeting_type_id: int = 0,
) -> dict:
    """
    Main entry point — detect agenda/topic boundaries using hybrid approach.

    Args:
        segments: Transcript segments from WhisperX (must have 'text', 'start', 'end', 'speaker')
        meeting_type_id: Meeting type hint (0=auto, 1-11=specific)

    Returns:
        {
            "detection_mode": "formal_agenda" | "topic_segments" | "single_topic",
            "agendas": [
                {
                    "agenda_number": 1,
                    "title": "...",
                    "start_segment_idx": 0,
                    "end_segment_idx": 45,
                    "start_time": 0.0,
                    "end_time": 542.3,
                    "speakers": ["คนพูด 1", "คนพูด 3"],
                    "confidence": 0.95
                }
            ]
        }
    """
    if not segments:
        return {"detection_mode": "single_topic", "agendas": []}

    total_segments = len(segments)
    logger.info(f"Starting agenda detection on {total_segments} segments")

    # Pass 1: Rule-based anchor extraction
    anchors = _extract_rule_based_anchors(segments)

    # Build indexed transcript lines for LLM
    transcript_lines: list[str] = []
    for idx, seg in enumerate(segments):
        speaker = seg.get("speaker", "?")
        text = seg.get("text", "").strip()
        start_time = seg.get("start", 0.0)
        transcript_lines.append(f"[Segment {idx}] [{speaker}] (t={start_time:.1f}s): {text}")

    # Pass 2: LLM context detection
    system_prompt, user_prompt = _build_llm_detection_prompt(
        transcript_lines, anchors, meeting_type_id
    )

    llm_response = _call_llm_with_fallback(
        system_prompt, user_prompt,
        temperature=0.1,
        max_tokens=2000,
        timeout=60,
    )

    parsed = _parse_llm_response(llm_response, total_segments)

    if not parsed or parsed["detection_mode"] == "single_topic" or not parsed["items"]:
        logger.info("No agenda segmentation detected (single topic)")
        return {"detection_mode": "single_topic", "agendas": []}

    # Enrich items with timing and speaker data from segments
    agendas: list[dict] = []
    for number, item in enumerate(parsed["items"], start=1):
        start_idx = item["start_segment_idx"]
        end_idx = item["end_segment_idx"]

        # Collect timing from actual segments
        start_time = segments[start_idx].get("start", 0.0)
        end_time = segments[end_idx].get("end", 0.0)

        # Collect unique speakers in this range
        speakers_in_range: set[str] = set()
        for seg in segments[start_idx : end_idx + 1]:
            speaker = seg.get("speaker", "")
            if speaker:
                speakers_in_range.add(speaker)

        agendas.append({
            "agenda_number": number,
            "title": item["title"],
            "start_segment_idx": start_idx,
            "end_segment_idx": end_idx,
            "start_time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "speakers": sorted(speakers_in_range),
            "confidence": item["confidence"],
        })

    logger.info(
        f"Detected {len(agendas)} agendas "
        f"(mode: {parsed['detection_mode']})"
    )

    return {
        "detection_mode": parsed["detection_mode"],
        "agendas": agendas,
    }
