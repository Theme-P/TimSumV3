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

import json
import logging
import os
import re
from typing import Callable, Optional

from .summarizer import _call_llm_with_fallback
from .summary_pipeline import CHUNK_INPUT_TOKENS, CHUNK_OVERLAP_TOKENS, chunk_segments

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.environ.get(name, str(default))))
    except (TypeError, ValueError):
        return default


def _env_float(name: str, default: float, minimum: float, maximum: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


AGENDA_SEMANTIC_SPLIT_CONFIDENCE = _env_float("AGENDA_SEMANTIC_SPLIT_CONFIDENCE", 0.80, 0.0, 1.0)
AGENDA_MIN_SEGMENTS = _env_int("AGENDA_MIN_SEGMENTS", 5)
AGENDA_MIN_DURATION_SECONDS = _env_int("AGENDA_MIN_DURATION_SECONDS", 90)

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
                    "explicit_marker": True,
                })
                break  # Only one match per segment to avoid duplicates

    logger.info(f"Rule-based anchors found: {len(anchors)}")
    return anchors


def _build_llm_detection_prompt(
    transcript_lines: list[str],
    anchors: list[dict],
    meeting_type_id: int,
    previous_context: str = "ไม่มี นี่คือ window แรก",
    window_start: int = 0,
    window_end: int = 0,
) -> tuple[str, str]:
    """Build system and user prompts for LLM agenda detection."""

    transcript_text = "\n".join(transcript_lines)

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

    system_prompt = f"""คุณคือ AI ผู้เชี่ยวชาญในการวิเคราะห์โครงสร้างการประชุม
คุณต้องหาจุดแบ่งหัวข้อ/วาระใน window ของบันทึกการประชุมยาว โดยใช้ global segment index
Meeting Type ID เป็นเพียง style/template สำหรับรูปแบบรายงาน ห้ามใช้ Meeting Type ID เป็นเหตุผลในการแบ่งวาระ

**วิธีการตรวจจับ:**
1. ถ้ามี Anchor Points (keyword ที่พบแล้ว) ให้ใช้เป็นจุดอ้างอิงหลัก
2. ตรวจสอบจุดเปลี่ยนหัวข้อเพิ่มเติมที่ไม่มี keyword เฉพาะกรณีเป็นวาระ/หัวข้อหลักใหม่จริงเท่านั้น โดยต้องมีหลักฐานชัด:
   - บริบทหลักเปลี่ยนต่อเนื่อง ไม่ใช่แค่ feature/product/task ย่อยสั้น ๆ
   - มีวลีเชื่อมชัด เช่น "ต่อไป", "อีกเรื่องหนึ่ง", "มาดูเรื่อง..." ร่วมกับเนื้อหาใหม่
   - confidence ต้องไม่น้อยกว่า {AGENDA_SEMANTIC_SPLIT_CONFIDENCE:.2f}
3. กำหนดชื่อวาระ/หัวข้อที่สื่อความหมายสั้นๆ ชัดเจน

**กฎสำคัญ:**
- items ต้องครอบคลุมทุก segment ใน window ตั้งแต่ตัวแรกถึงตัวสุดท้ายโดยไม่มีช่องว่าง
- แม้ window นี้มีหัวข้อเดียว ให้คืน item 1 รายการที่ครอบคลุมทั้ง window และใช้ detection_mode = "single_topic"
- ถ้ามีการคุยย้อนกลับมาเรื่องเดิมอีกรอบ ให้แยกเป็น item ใหม่ เช่น "งบประมาณ (ต่อ)"
- Segment index ต้องเรียงจากน้อยไปมาก ไม่ซ้อนทับกัน
- Context จาก window ก่อนหน้ามีไว้เชื่อมหัวข้อเท่านั้น ห้ามสร้าง segment ที่ไม่อยู่ใน window ปัจจุบัน
- ห้ามเดาหัวข้อจากความรู้ภายนอก ใช้เฉพาะ Transcript และ Anchor Points
- ห้ามแตกหัวข้อจาก 12 รูปแบบการประชุมหรือ template การสรุป
- ถ้าไม่แน่ใจว่าเป็นวาระใหม่ ให้รวมไว้ในหัวข้อเดิม
- ตอบเป็น JSON เท่านั้น ห้ามมีข้อความอื่น

**JSON Format:**
```json
{{
  "detection_mode": "formal_agenda" | "topic_segments" | "single_topic",
  "items": [
    {{
      "title": "ชื่อวาระ/หัวข้อ",
      "start_segment_idx": 0,
      "end_segment_idx": 45,
      "confidence": 0.95,
      "split_reason": "explicit_marker" | "semantic_shift" | "single_topic",
      "evidence": "วลีหรือประโยคสั้นๆ จาก transcript ที่ใช้เป็นหลักฐาน"
    }}
  ]
}}
```"""

    user_prompt = f"""**ข้อมูล Meeting Type ID:** {meeting_type_id} (style/template เท่านั้น ไม่ใช่ตัวแบ่งวาระ)
**ขอบเขต window:** Segment {window_start} ถึง {window_end}

**Context จาก window ก่อนหน้า:**
{previous_context}

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
            split_reason = str(item.get("split_reason") or "").strip() or (
                "single_topic" if detection_mode == "single_topic" else "semantic_shift"
            )
            evidence = str(item.get("evidence") or "").strip()

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
                "split_reason": split_reason,
                "evidence": evidence,
            })

        # Keep candidate boundaries; cross-window reconciliation below resolves
        # overlaps without silently discarding a differently named topic.
        validated_items.sort(key=lambda x: x["start_segment_idx"])
        return {
            "detection_mode": detection_mode,
            "items": validated_items,
        }

    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        logger.error(f"Failed to parse LLM agenda response: {exc}")
        return None


def _normalized_title(title: str) -> str:
    normalized = re.sub(r"\s*\((?:ต่อ|ต่อเนื่อง)\)\s*$", "", title.strip().lower())
    return re.sub(r"[^\w\u0E00-\u0E7F]+", "", normalized)


def _segment_duration(segments: list[dict], start_idx: int, end_idx: int) -> float:
    if not segments or start_idx < 0 or end_idx >= len(segments) or start_idx > end_idx:
        return 0.0
    try:
        start_time = float(segments[start_idx].get("start") or 0)
        end_time = float(segments[end_idx].get("end") or start_time)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, end_time - start_time)


def _anchor_title(anchor: dict, fallback_number: int) -> str:
    text = str(anchor.get("full_text") or anchor.get("matched_text") or "").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return f"วาระที่ {fallback_number}"
    return text[:120]


def _items_from_explicit_anchors(segments: list[dict], anchors: list[dict]) -> list[dict]:
    if not anchors:
        return []

    total_segments = len(segments)
    ordered = sorted(anchors, key=lambda anchor: anchor["segment_idx"])
    items: list[dict] = []
    first_anchor_idx = ordered[0]["segment_idx"]
    if first_anchor_idx > 0:
        items.append({
            "title": "ช่วงนำก่อนเข้าสู่วาระ",
            "start_segment_idx": 0,
            "end_segment_idx": first_anchor_idx - 1,
            "confidence": 0.7,
            "split_reason": "intro_before_explicit_marker",
            "evidence": "",
            "explicit_marker": False,
        })

    for index, anchor in enumerate(ordered):
        start_idx = anchor["segment_idx"]
        next_start = ordered[index + 1]["segment_idx"] if index + 1 < len(ordered) else total_segments
        end_idx = max(start_idx, next_start - 1)
        items.append({
            "title": _anchor_title(anchor, index + 1),
            "start_segment_idx": start_idx,
            "end_segment_idx": end_idx,
            "confidence": min(0.98, 0.65 + (anchor.get("weight", 1) * 0.1)),
            "split_reason": "explicit_marker",
            "evidence": anchor.get("matched_text", ""),
            "explicit_marker": True,
            "anchor_type": anchor.get("type", "topic_segments"),
        })
    return _merge_short_items(items, segments)


def _passes_semantic_threshold(item: dict, segments: list[dict]) -> bool:
    if item.get("split_reason") == "single_topic":
        return True
    if item.get("explicit_marker") or item.get("split_reason") == "explicit_marker":
        return True
    confidence = float(item.get("confidence", 0.0) or 0.0)
    segment_count = item["end_segment_idx"] - item["start_segment_idx"] + 1
    duration = _segment_duration(segments, item["start_segment_idx"], item["end_segment_idx"])
    return (
        confidence >= AGENDA_SEMANTIC_SPLIT_CONFIDENCE
        and segment_count >= AGENDA_MIN_SEGMENTS
        and duration >= AGENDA_MIN_DURATION_SECONDS
        and bool(str(item.get("evidence") or "").strip())
    )


def _merge_short_items(items: list[dict], segments: list[dict]) -> list[dict]:
    if len(items) <= 1:
        return items

    items = [dict(item) for item in items]
    first = items[0]
    first_count = first["end_segment_idx"] - first["start_segment_idx"] + 1
    first_duration = _segment_duration(segments, first["start_segment_idx"], first["end_segment_idx"])
    if (
        len(items) > 1
        and not first.get("explicit_marker")
        and (first_count < AGENDA_MIN_SEGMENTS or first_duration < AGENDA_MIN_DURATION_SECONDS)
    ):
        items[1]["start_segment_idx"] = first["start_segment_idx"]
        if first.get("evidence"):
            items[1]["evidence"] = f"{first['evidence']}; {items[1].get('evidence', '')}".strip("; ")
        items = items[1:]

    merged: list[dict] = []
    for item in items:
        segment_count = item["end_segment_idx"] - item["start_segment_idx"] + 1
        duration = _segment_duration(segments, item["start_segment_idx"], item["end_segment_idx"])
        is_short = segment_count < AGENDA_MIN_SEGMENTS or duration < AGENDA_MIN_DURATION_SECONDS
        if (
            merged
            and is_short
            and not item.get("explicit_marker")
            and item.get("split_reason") != "explicit_marker"
        ):
            previous = merged[-1]
            previous["end_segment_idx"] = max(previous["end_segment_idx"], item["end_segment_idx"])
            previous["confidence"] = min(previous.get("confidence", 0.5), item.get("confidence", 0.5))
            if item.get("evidence"):
                previous["evidence"] = f"{previous.get('evidence', '')}; {item['evidence']}".strip("; ")
            continue
        merged.append(item)
    return merged


def _filter_semantic_items(items: list[dict], segments: list[dict]) -> list[dict]:
    filtered = [item for item in items if _passes_semantic_threshold(item, segments)]
    if len(filtered) <= 1:
        return []
    return _merge_short_items(filtered, segments)


def _build_agenda_payload(items: list[dict], segments: list[dict], detection_mode: str) -> list[dict]:
    agendas: list[dict] = []
    for number, item in enumerate(items, start=1):
        start_idx = item["start_segment_idx"]
        end_idx = item["end_segment_idx"]

        start_time = segments[start_idx].get("start", 0.0)
        end_time = segments[end_idx].get("end", 0.0)

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
            "split_reason": item.get("split_reason", "explicit_marker" if detection_mode == "formal_agenda" else "semantic_shift"),
            "evidence": item.get("evidence", ""),
        })
    return agendas


def _merge_window_items(items: list[dict], total_segments: int) -> list[dict]:
    """Reconcile overlapping window results and enforce full transcript coverage."""
    if not items or total_segments <= 0:
        return []

    ordered = sorted(items, key=lambda item: (item["start_segment_idx"], item["end_segment_idx"]))
    merged: list[dict] = []
    for raw in ordered:
        item = dict(raw)
        if not merged:
            merged.append(item)
            continue

        previous = merged[-1]
        same_title = _normalized_title(previous["title"]) == _normalized_title(item["title"])
        overlaps = item["start_segment_idx"] <= previous["end_segment_idx"]
        touches = item["start_segment_idx"] <= previous["end_segment_idx"] + 1

        if same_title and touches:
            previous["end_segment_idx"] = max(previous["end_segment_idx"], item["end_segment_idx"])
            previous["confidence"] = max(previous.get("confidence", 0.5), item.get("confidence", 0.5))
            if item.get("evidence"):
                previous["evidence"] = f"{previous.get('evidence', '')}; {item['evidence']}".strip("; ")
            continue

        if overlaps:
            boundary = (item["start_segment_idx"] + previous["end_segment_idx"]) // 2
            boundary = max(previous["start_segment_idx"], boundary)
            previous["end_segment_idx"] = boundary
            item["start_segment_idx"] = boundary + 1

        if item["start_segment_idx"] > previous["end_segment_idx"] + 1:
            previous["end_segment_idx"] = item["start_segment_idx"] - 1

        if item["start_segment_idx"] <= item["end_segment_idx"]:
            merged.append(item)

    merged[0]["start_segment_idx"] = 0
    merged[-1]["end_segment_idx"] = total_segments - 1
    for index in range(1, len(merged)):
        merged[index]["start_segment_idx"] = merged[index - 1]["end_segment_idx"] + 1

    return [item for item in merged if item["start_segment_idx"] <= item["end_segment_idx"]]


def detect_agendas(
    segments: list[dict],
    meeting_type_id: int = 0,
    mongo_service=None,
    allow_semantic_split: Optional[bool] = None,
    cancel_check: Optional[Callable[[], None]] = None,
) -> dict:
    """
    Main entry point — detect agenda/topic boundaries using hybrid approach.

    Args:
        segments: Transcript segments from WhisperX (must have 'text', 'start', 'end', 'speaker')
        meeting_type_id: Meeting style hint (0=auto, 1-11=specific)
        allow_semantic_split: True only when automatic mode should infer major
            agenda shifts without explicit transcript markers.

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
    if cancel_check:
        cancel_check()

    total_segments = len(segments)
    logger.info(f"Starting agenda detection on {total_segments} segments")
    if allow_semantic_split is None:
        allow_semantic_split = meeting_type_id == 0

    # Pass 1: Rule-based anchor extraction
    anchors = _extract_rule_based_anchors(segments)
    if anchors:
        explicit_items = _items_from_explicit_anchors(segments, anchors)
        if len(explicit_items) > 1:
            detection_mode = "formal_agenda" if any(
                anchor.get("type") == "formal_agenda" for anchor in anchors
            ) else "topic_segments"
            agendas = _build_agenda_payload(explicit_items, segments, detection_mode)
            logger.info(
                "Detected %s agendas from explicit transcript markers (mode: %s)",
                len(agendas),
                detection_mode,
            )
            return {
                "detection_mode": detection_mode,
                "agendas": agendas,
                "split_reasons": [
                    {
                        "agenda_number": agenda["agenda_number"],
                        "reason": agenda.get("split_reason", "explicit_marker"),
                        "evidence": agenda.get("evidence", ""),
                        "confidence": agenda.get("confidence", 0.0),
                    }
                    for agenda in agendas
                ],
            }

    if not allow_semantic_split:
        logger.info("No explicit agenda markers found; semantic splitting disabled for selected meeting style")
        return {
            "detection_mode": "single_topic",
            "agendas": [],
            "split_reasons": [],
        }

    # Pass 2: analyze overlapping windows so meetings longer than one context window
    # are covered from the first through the final segment.
    indexed_segments = [
        {**segment, "_source_index": index}
        for index, segment in enumerate(segments)
    ]
    windows = chunk_segments(
        indexed_segments,
        max_tokens=CHUNK_INPUT_TOKENS,
        overlap_tokens=CHUNK_OVERLAP_TOKENS,
    )
    logger.info("Agenda detection split transcript into %s windows", len(windows))

    all_items: list[dict] = []
    detection_modes: list[str] = []
    for window in windows:
        if cancel_check:
            cancel_check()
        window_start = window["start_segment_idx"]
        window_end = window["end_segment_idx"]
        window_anchors = [
            anchor for anchor in anchors
            if window_start <= anchor["segment_idx"] <= window_end
        ]
        previous_context = "ไม่มี นี่คือ window แรก"
        if all_items:
            previous_context = "\n".join(
                f"- {item['title']} (Segment {item['start_segment_idx']}-{item['end_segment_idx']})"
                for item in all_items[-3:]
            )

        system_prompt, user_prompt = _build_llm_detection_prompt(
            window["text"].splitlines(),
            window_anchors,
            meeting_type_id,
            previous_context=previous_context,
            window_start=window_start,
            window_end=window_end,
        )
        llm_response = _call_llm_with_fallback(
            system_prompt,
            user_prompt,
            temperature=0.1,
            max_tokens=2000,
            timeout=120,
            mongo_service=mongo_service,
            cancel_check=cancel_check,
        )
        if cancel_check:
            cancel_check()
        parsed = _parse_llm_response(llm_response, total_segments)
        if parsed and parsed["items"]:
            item_count_before = len(all_items)
            for item in parsed["items"]:
                item = dict(item)
                item["start_segment_idx"] = max(window_start, item["start_segment_idx"])
                item["end_segment_idx"] = min(window_end, item["end_segment_idx"])
                if item.get("split_reason") != "single_topic":
                    item["split_reason"] = item.get("split_reason") or "semantic_shift"
                if item["start_segment_idx"] <= item["end_segment_idx"]:
                    all_items.append(item)
            if len(all_items) > item_count_before:
                detection_modes.append(parsed["detection_mode"])
                continue

        fallback_title = "หัวข้อต่อเนื่อง"
        if window_anchors:
            fallback_title = window_anchors[0].get("full_text", fallback_title)[:120]
        all_items.append({
            "title": fallback_title,
            "start_segment_idx": window_start,
            "end_segment_idx": window_end,
            "confidence": 0.3,
            "split_reason": "single_topic",
            "evidence": "",
        })
        detection_modes.append("single_topic")

    merged_items = _filter_semantic_items(
        _merge_window_items(all_items, total_segments),
        segments,
    )
    if len(merged_items) <= 1:
        logger.info("No agenda segmentation detected (single topic)")
        return {"detection_mode": "single_topic", "agendas": [], "split_reasons": []}

    detection_mode = "topic_segments"
    if any(mode == "formal_agenda" for mode in detection_modes) or any(
        anchor.get("type") == "formal_agenda" for anchor in anchors
    ):
        detection_mode = "formal_agenda"

    agendas = _build_agenda_payload(merged_items, segments, detection_mode)

    logger.info(
        f"Detected {len(agendas)} agendas "
        f"(mode: {detection_mode})"
    )

    return {
        "detection_mode": detection_mode,
        "agendas": agendas,
        "split_reasons": [
            {
                "agenda_number": agenda["agenda_number"],
                "reason": agenda.get("split_reason", "semantic_shift"),
                "evidence": agenda.get("evidence", ""),
                "confidence": agenda.get("confidence", 0.0),
            }
            for agenda in agendas
        ],
    }
