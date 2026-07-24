from typing import Optional
from pydantic import BaseModel, Field
from bson import ObjectId
from datetime import datetime
from .meeting import MEETING_TYPES

class PyObjectId(ObjectId):
    @classmethod
    def __get_validators__(cls):
        yield cls.validate

    @classmethod
    def validate(cls, v, *args, **kwargs):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid objectid")
        return ObjectId(v)

    @classmethod
    def __get_pydantic_json_schema__(cls, field_schema):
        field_schema.update(type="string")

class MeetingTemplate(BaseModel):
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    meeting_type_id: int
    name: str
    thai_name: str
    system_prompt: str
    temperature: float = Field(0.4, ge=0.0, le=1.0)
    max_tokens: int = Field(4000, ge=100, le=16000)
    updated_at: Optional[datetime] = None
    updated_by: Optional[str] = None

    class Config:
        populate_by_name = True
        json_encoders = {ObjectId: str}

class MeetingTemplateUpdate(BaseModel):
    system_prompt: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None

def _get_default_system_prompt(meeting_type_id: int) -> str:
    """Generate default system prompt for meeting type based on MEETING_TYPES details."""
    info = MEETING_TYPES.get(meeting_type_id, MEETING_TYPES[11])
    meeting_type_instruction = ""
    if info.get('details'):
        details_text = "\n".join([f"  • {d}" for d in info['details']])
        meeting_type_instruction = f"**🎯 ประเด็นหัวใจหลัก: {info['key_focus']}**\nต้องสรุปให้ละเอียดในหัวข้อนี้:\n{details_text}"

    prompt = f"""คุณคือผู้เชี่ยวชาญวิเคราะห์และสรุปการประชุม

{meeting_type_instruction}

**Output Format:**
**[{info['thai'] if meeting_type_id > 0 else 'ประเภท'}]: [หัวข้อการประชุม]**

**👥 ผู้เข้าร่วมประชุม ({{num_speakers}} คน):**
(วิเคราะห์บทบาทจากเนื้อหาการพูด: ประธาน/ผู้นำเสนอ/ผู้เข้าร่วม)

**📋 สรุปการประชุม:**
(ตามโครงสร้าง: {info['structure']} - เน้นความละเอียดในส่วน {info.get('key_focus', 'ประเด็นหลัก')})

**📌 การสั่งงาน/มอบหมาย:** (ถ้ามี)
- **[ผู้สั่ง]** สั่งให้ **[ผู้รับมอบหมาย]** ทำ: [เนื้อหา]

**❓ คำถามสำคัญ:** (ถ้ามี)
- **[ผู้ถาม]** ถาม: "[คำถาม]" → **[ผู้ตอบ]**: "[คำตอบ]"

**✅ ข้อตกลง/มติ:** (ถ้ามี)

**กฎสำคัญ:**
- ใช้เฉพาะข้อมูลจาก Transcript ห้ามแต่งชื่อ ตัวเลข วันที่ เหตุผล หรือข้อสรุปเพิ่ม
- แยกข้อเสนอ ความเห็น มติที่ยืนยันแล้ว และเรื่องที่ยังไม่ได้ข้อสรุปออกจากกัน
- ถ้ามีข้อมูลขัดแย้งหรือแก้ไขภายหลัง ให้รักษาทั้งข้อมูลเดิมและข้อมูลแก้ไขตามลำดับ
- สรุปเป็นภาษาไทยเสมอ ไม่ว่า transcript จะเป็นภาษาอะไร (ไทย/อังกฤษ/จีน/ผสม)
- คงคำศัพท์เฉพาะทาง ชื่อเฉพาะ และคำย่อภาษาอังกฤษไว้ตามเดิม (เช่น KPI, OKR, ROI)
- ถ้ามีการพูดภาษาจีนหรือภาษาอื่น ให้แปลเป็นภาษาไทยแล้วใส่คำต้นฉบับในวงเล็บ
- ใช้ bullet points
- ต้องระบุชื่อผู้พูดในทุกการสั่งงาน/คำถาม/ข้อตกลง
- ถ้าไม่ทราบผู้รับผิดชอบหรือกำหนดเวลา ให้ระบุว่า "ไม่ระบุ" ห้ามคาดเดา
- เน้นความละเอียดในประเด็นหัวใจหลักของประเภทการประชุมนี้
- สรุปมติท้ายสุด"""
    return prompt.strip()

def get_default_meeting_templates() -> list[dict]:
    configs = []
    for mt_id, info in MEETING_TYPES.items():
        configs.append({
            "meeting_type_id": mt_id,
            "name": info["name"],
            "thai_name": info["thai"],
            "system_prompt": _get_default_system_prompt(mt_id),
            "temperature": 0.4,
            "max_tokens": 4000,
            "updated_at": datetime.utcnow()
        })
    return configs
