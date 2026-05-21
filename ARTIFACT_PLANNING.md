# TimSum V3 — Artifact Planning

> วันที่วิเคราะห์: 2026-05-19
> สถานะโปรเจกต์: MVP Development Phase

---

## สถานะปัจจุบัน (Current State Summary)

### Tech Stack
| Layer | Technology |
|-------|-----------|
| Frontend | React 18 + Vite 6 + React Router 7 |
| Backend | FastAPI + Celery + Redis |
| AI/ML | WhisperX (large-v3) + PyAnnote + GPT-4.1 (NTC Gateway) |
| Database | MongoDB |
| Storage | MinIO (S3-compatible) |
| Deploy | Docker Compose (6 containers), GPU Worker |

### สิ่งที่ทำเสร็จแล้ว (Implemented)
- ✅ JWT Authentication (login, role-based: superadmin/admin/user)
- ✅ Admin-only user creation (`POST /api/auth/register`)
- ✅ Public user registration (`POST /api/auth/register-public`) — pending approval
- ✅ Admin approval dashboard (`/admin`) — approve/reject/suspend users
- ✅ User status system (pending/approved/rejected/suspended)
- ✅ Login blocks non-approved users with Thai error messages
- ✅ Quota system พื้นฐาน (4 ค่า per user)
- ✅ Async audio processing (Celery + Redis, job polling)
- ✅ Transcription pipeline (WhisperX + word alignment)
- ✅ Speaker diarization (PyAnnote, auto-detect speaker count)
- ✅ Speaker identification จาก self-introduction (GPT-4.1)
- ✅ Speaker audio clip extraction (~10s per speaker)
- ✅ AI Summarization (11 meeting types + auto-detect)
- ✅ Export to DOCX (transcript + summary)
- ✅ Email integration (SMTP + DOCX attachment)
- ✅ Session history (list + detail)
- ✅ Real-time processing status (5 steps, 0-100%)
- ✅ Rate limiting (slowapi)
- ✅ Text cleaning (noise removal, repetitive phrases)
- ✅ Dark/Light/System theme (infrastructure)
- ✅ Docker deployment with GPU support

---

## MVP Features Gap Analysis

### ตารางเปรียบเทียบ

| # | MVP Feature | สถานะ | ความเร่งด่วน |
|---|-------------|--------|-------------|
| 1 | การลงทะเบียน user แบบออนไลน์ | ✅ เสร็จแล้ว | สูง |
| 2 | Admin approve user ที่ลงทะเบียน | ✅ เสร็จแล้ว | สูง |
| 3 | Admin ตั้งค่า package / repackage | ✅ เสร็จแล้ว | สูง |
| 4 | Package การใช้งาน | ✅ เสร็จแล้ว | สูง |
| 5 | User profile ตั้งค่าตาม package | ✅ เสร็จแล้ว (package + usage bars) | กลาง |
| 6 | Async process file เสียง | ✅ เสร็จแล้ว | - |
| 7 | การแยกเสียง + สรุปประชุม | ✅ เสร็จแล้ว | - |
| 8a | แนบ clip ตัวอย่างเสียง (voice enrollment) | 🔴 ไม่มี | กลาง |
| 8b | ใช้ clip เสียงที่เคยให้ไว้แล้ว (voice library) | 🔴 ไม่มี | กลาง |
| 9a | Auto mode summary [default] | ✅ เสร็จแล้ว | - |
| 9b | ผู้ใช้ prompt เพิ่มเพื่อกำหนดรูปแบบสรุป | ✅ เสร็จแล้ว | กลาง |
| 10 | PDPA — 3 ระดับ user (superadmin/admin/user) | ✅ เสร็จแล้ว | สูง |
| 11 | Encrypt/tokenize ข้อมูลส่วนบุคคลใน DB | 🔴 ไม่มี | สูง |

**สรุป:** 11 features เสร็จแล้ว, 0 features มีบางส่วน, 2 features ต้องทำใหม่

---

## Artifact Plan — แบ่งเป็น Phase

---

### Phase 1: User Registration & Approval System ✅ COMPLETED (2026-05-19)

**เหตุผล:** เป็น core flow ที่ user ต้องใช้ก่อนจะเข้าถึง feature อื่นๆ ได้

#### Artifact 1.1: Public Registration API & Page

**Backend:**
- `POST /api/auth/register-public` — endpoint ใหม่สำหรับ public registration
- เพิ่ม field ใน User model:
  ```
  first_name, last_name, phone, organization, 
  status: "pending" | "approved" | "rejected" | "suspended",
  registered_at, approved_at, approved_by
  ```
- สร้าง user ด้วย status = "pending" (ยังล็อกอินไม่ได้)
- Validation: email unique, password strength, required fields
- ส่ง email ยืนยันการลงทะเบียน (optional)

**Frontend:**
- หน้า Register (`/register`) — form: ชื่อ, นามสกุล, อีเมล, เบอร์โทร, องค์กร, รหัสผ่าน
- Link จากหน้า Login ("สมัครสมาชิก" — ปุ่มมีอยู่แล้ว)
- แสดงสถานะ "รอการอนุมัติ" หลังลงทะเบียนสำเร็จ

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/app/models/user.py` — เพิ่ม fields |
| แก้ไข | `backend/app/services/mongo.py` — เพิ่ม register logic |
| แก้ไข | `backend/app/routers/auth.py` — เพิ่ม register-public endpoint |
| แก้ไข | `backend/api.py` — login ต้อง check status = "approved" |
| สร้างใหม่ | `frontend/src/pages/Register.jsx` |
| แก้ไข | `frontend/src/App.jsx` — เพิ่ม route /register |
| แก้ไข | `frontend/src/pages/Login.jsx` — link ไป register |

#### Artifact 1.2: Admin Approval Dashboard

**Backend:**
- `GET /api/admin/users?status=pending` — list users by status (admin only)
- `PUT /api/admin/users/{user_id}/approve` — approve user
- `PUT /api/admin/users/{user_id}/reject` — reject user
- `PUT /api/admin/users/{user_id}/suspend` — suspend user
- Notification email เมื่อ approve/reject

**Frontend:**
- หน้า Admin Dashboard (`/admin`) — protected route (admin/superadmin only)
- Tab "รอการอนุมัติ" — list pending users + approve/reject buttons
- Tab "ผู้ใช้ทั้งหมด" — list all users + status + actions
- Search/filter by status, organization

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/routers/admin.py` — admin endpoints |
| แก้ไข | `backend/api.py` — register admin router |
| สร้างใหม่ | `frontend/src/pages/AdminDashboard.jsx` |
| สร้างใหม่ | `frontend/src/components/admin/UserApprovalList.jsx` |
| สร้างใหม่ | `frontend/src/components/admin/UserManagementTable.jsx` |
| แก้ไข | `frontend/src/App.jsx` — เพิ่ม /admin route |
| แก้ไข | `frontend/src/components/ProtectedRoute.jsx` — เพิ่ม role check |

---

### Phase 2: Package System ✅ COMPLETED (2026-05-19)

**เหตุผล:** Package เป็นหัวใจของ business model — ควบคุมสิทธิ์การใช้งานและ billing

#### Artifact 2.1: Package Model & CRUD

**Backend:**
- สร้าง Package model:
  ```
  package_id, name, description, 
  limits: {
    max_upload_mb: int,
    max_audio_minutes: int,
    max_files_per_month: int,
    max_speakers: int,
    allowed_meeting_types: [int],
    summary_custom_prompt: bool,
    voice_enrollment: bool,
    email_export: bool,
    priority_processing: bool
  },
  price: float,
  billing_cycle: "monthly" | "yearly" | "one-time",
  is_active: bool,
  created_at, updated_at
  ```
- MongoDB collection: `package`
- Admin CRUD APIs:
  - `POST /api/admin/packages` — สร้าง package
  - `GET /api/admin/packages` — list all packages
  - `PUT /api/admin/packages/{id}` — update package
  - `DELETE /api/admin/packages/{id}` — soft delete

#### Artifact 2.2: User-Package Assignment & Usage Tracking

**Backend:**
- สร้าง UserPackage model:
  ```
  user_id, package_id, 
  status: "active" | "expired" | "exceeded",
  started_at, expires_at,
  usage: {
    files_this_month: int,
    minutes_this_month: float,
    total_files: int,
    total_minutes: float
  },
  assigned_by (admin user_id)
  ```
- MongoDB collection: `user_package`
- APIs:
  - `POST /api/admin/users/{user_id}/assign-package` — admin กำหนด package
  - `POST /api/admin/users/{user_id}/repackage` — admin repackage (กรณีใช้เกิน)
  - `GET /api/user/package` — user ดู package ของตัวเอง
  - `GET /api/user/usage` — user ดู usage
  - `POST /api/user/request-repackage` — user ขอ repackage
- Middleware: check package limits ก่อน process audio
  - ตรวจ file size vs `max_upload_mb`
  - ตรวจ files count vs `max_files_per_month`
  - ตรวจ audio duration vs `max_audio_minutes`

#### Artifact 2.3: Package Management UI

**Frontend:**
- Admin: หน้าจัดการ Package (CRUD table)
- Admin: Assign/Repackage dialog ในหน้า User Management
- User: แสดง package info + usage ใน Settings modal
- User: ปุ่ม "ขอ repackage" เมื่อใช้เกิน quota
- Warning UI เมื่อ usage ใกล้ถึง limit

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/models/package.py` |
| สร้างใหม่ | `backend/app/routers/package.py` |
| แก้ไข | `backend/app/services/mongo.py` — เพิ่ม package/usage collections |
| แก้ไข | `backend/api.py` — register package router, add middleware |
| แก้ไข | `backend/app/tasks/transcription.py` — check limits before process |
| สร้างใหม่ | `frontend/src/components/admin/PackageManager.jsx` |
| สร้างใหม่ | `frontend/src/components/admin/AssignPackageDialog.jsx` |
| แก้ไข | `frontend/src/components/SettingsModal.jsx` — แสดง package + usage |
| สร้างใหม่ | `frontend/src/components/UsageIndicator.jsx` |

---

### Phase 3: PDPA & Data Protection 🔴 (Priority: สูง)

**เหตุผล:** เป็นข้อกำหนดทางกฎหมาย — ต้องทำก่อน production deployment

#### Artifact 3.1: Three-Level Role System (superadmin / admin / user)

**Backend:**
- แก้ User model: `role: "superadmin" | "admin" | "user"` (จากเดิม admin/user)
- สร้าง middleware ใหม่:
  - `get_current_superadmin()` — superadmin only
  - `get_current_admin_or_above()` — admin + superadmin
- Data visibility rules:
  ```
  superadmin → เห็นข้อมูลส่วนบุคคลของทุกคน (full PII)
  admin      → เห็นข้อมูลทุกคน แต่ mask PII (เจษฎา → เจ***)
  user       → เห็นเฉพาะข้อมูลของตัวเอง
  ```
- สร้าง PII masking utility:
  - `mask_name("เจษฎา")` → `"เจ***"`
  - `mask_email("user@email.com")` → `"us***@email.com"`
  - `mask_phone("0891234567")` → `"089***4567"`

**Frontend:**
- Conditional rendering ตาม role
- Admin Dashboard: แสดงข้อมูลแบบ masked
- SuperAdmin: เห็นทุกอย่าง + จัดการ admin ได้

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/app/models/user.py` — เพิ่ม superadmin role |
| แก้ไข | `backend/app/core/auth.py` — เพิ่ม superadmin middleware |
| สร้างใหม่ | `backend/app/utils/pii_masking.py` — masking functions |
| แก้ไข | `backend/app/routers/admin.py` — apply masking per role |
| แก้ไข | `backend/api.py` — apply role-based filtering |
| แก้ไข | `frontend/src/contexts/AuthContext.jsx` — expose role |
| แก้ไข | `frontend/src/pages/AdminDashboard.jsx` — conditional display |
| แก้ไข | `backend/scripts/create_admin.py` — สร้าง superadmin |

#### Artifact 3.2: PII Encryption at Rest (Database-Level)

**Backend:**
- สร้าง encryption service:
  ```python
  # ใช้ AES-256-GCM สำหรับ field-level encryption
  class PIIEncryptor:
      encrypt(plaintext) → ciphertext + nonce
      decrypt(ciphertext, nonce) → plaintext
      tokenize(value) → deterministic_token  # สำหรับ search
  ```
- Fields ที่ต้อง encrypt:
  - `user.first_name`, `user.last_name`
  - `user.phone`
  - `user.email` (เก็บทั้ง encrypted + tokenized สำหรับ lookup)
- Key management:
  - Master encryption key ใน environment variable (`PII_ENCRYPTION_KEY`)
  - Key rotation support (versioned keys)
- MongoDB: เก็บเป็น `{ encrypted: "...", nonce: "...", version: 1 }`
- Migration script: encrypt ข้อมูลเดิมที่เป็น plaintext

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/services/encryption.py` — PIIEncryptor class |
| แก้ไข | `backend/app/services/mongo.py` — encrypt/decrypt on read/write |
| แก้ไข | `backend/app/models/user.py` — encrypted field definitions |
| สร้างใหม่ | `backend/scripts/migrate_encrypt_pii.py` — migration script |
| แก้ไข | `.env.example` — เพิ่ม PII_ENCRYPTION_KEY |
| แก้ไข | `docker-compose.yml` — pass encryption key |

---

### Phase 4: Voice Enrollment System 🔴 (Priority: กลาง)

**เหตุผล:** เพิ่มความแม่นยำของ speaker diarization — differentiate จากคู่แข่ง

#### Artifact 4.1: Voice Sample Upload & Storage

**Backend:**
- สร้าง VoiceSample model:
  ```
  sample_id, user_id (owner), 
  speaker_name, speaker_position,
  audio_path (MinIO), 
  embedding: [float] (speaker embedding vector),
  duration_seconds,
  created_at
  ```
- MongoDB collection: `voice_sample`
- MinIO bucket: `voice-samples`
- APIs:
  - `POST /api/voice-samples` — upload voice clip + metadata
  - `GET /api/voice-samples` — list user's voice samples
  - `DELETE /api/voice-samples/{id}` — delete sample
  - `GET /api/voice-samples/{id}/play` — stream audio
- Extract speaker embedding จาก audio clip (PyAnnote embedding model)

#### Artifact 4.2: Voice-Matched Diarization

**Backend:**
- แก้ pipeline.py:
  1. หลัง diarization ปกติ → ได้ speaker segments
  2. Extract embedding ของแต่ละ speaker
  3. Compare กับ voice samples ของ user (cosine similarity)
  4. ถ้า similarity > threshold → auto-assign ชื่อ
  5. Fallback: ใช้ GPT-4.1 detect names เหมือนเดิม

**Frontend:**
- Component "คลังเสียง" (Voice Library) ใน Settings หรือ sidebar
- Upload voice clip + ระบุชื่อ/ตำแหน่ง
- แสดง list ของ voice samples ที่มี + play button
- ใน upload page: checkbox "ใช้คลังเสียงจับคู่อัตโนมัติ"

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/models/voice_sample.py` |
| สร้างใหม่ | `backend/app/routers/voice_samples.py` |
| สร้างใหม่ | `backend/app/services/voice_matching.py` — embedding + matching |
| แก้ไข | `backend/app/services/pipeline.py` — integrate voice matching |
| แก้ไข | `backend/app/services/mongo.py` — voice_sample collection |
| แก้ไข | `backend/app/tasks/transcription.py` — pass voice samples |
| สร้างใหม่ | `frontend/src/components/VoiceLibrary.jsx` |
| แก้ไข | `frontend/src/pages/MainApp.jsx` — voice matching toggle |
| แก้ไข | `frontend/src/components/SettingsModal.jsx` — voice library tab |

---

### Phase 5: Custom Summary Prompt ✅ COMPLETED (2026-05-19)

**เหตุผล:** ให้ user ปรับแต่ง output ได้ — เพิ่ม flexibility

#### Artifact 5.1: Custom Prompt Input

**Backend:**
- เพิ่ม field `custom_prompt` ใน transcribe-summarize request
- แก้ summarizer.py:
  - ถ้ามี custom_prompt → append เข้า system prompt ของ GPT-4.1
  - ถ้าไม่มี → ใช้ auto mode เหมือนเดิม
- Validation: max 500 characters, sanitize input

**Frontend:**
- เพิ่ม textarea "คำสั่งเพิ่มเติม (optional)" ใต้ meeting type selector
- Placeholder: "เช่น สรุปเป็น bullet points, เน้น action items, สรุปเป็นภาษาอังกฤษ"
- Collapsible (ซ่อนเป็น default, กด expand)
- Check package permission: `summary_custom_prompt == true`

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/app/services/summarizer.py` — รับ custom_prompt |
| แก้ไข | `backend/api.py` — เพิ่ม custom_prompt param |
| แก้ไข | `backend/app/tasks/transcription.py` — pass custom_prompt |
| สร้างใหม่ | `frontend/src/components/CustomPromptInput.jsx` |
| แก้ไข | `frontend/src/pages/MainApp.jsx` — integrate component |

---

### Phase 6: User Profile & Settings ✅ (Priority: กลาง)

#### Artifact 6.1: User Profile Management

**Backend:**
- `GET /api/user/profile` — ดูข้อมูลตัวเอง
- `PUT /api/user/profile` — แก้ไขข้อมูล (ชื่อ, นามสกุล, เบอร์โทร, organization)
- `PUT /api/user/change-password` — เปลี่ยนรหัสผ่าน
- `POST /api/auth/forgot-password` — ส่ง reset link ทาง email
- `POST /api/auth/reset-password` — reset ด้วย token

**Frontend:**
- ปรับ SettingsModal ให้ functional:
  - Tab "โปรไฟล์" — แก้ไขข้อมูลส่วนตัว
  - Tab "Package & Usage" — แสดง package + usage chart
  - Tab "ความปลอดภัย" — เปลี่ยนรหัสผ่าน
  - Tab "ธีม" — light/dark/system (มี infrastructure แล้ว)
- หน้า Forgot Password (`/forgot-password`)
- หน้า Reset Password (`/reset-password?token=xxx`)

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/routers/user.py` — user profile endpoints |
| แก้ไข | `backend/app/routers/auth.py` — forgot/reset password |
| แก้ไข | `backend/app/services/mongo.py` — update profile, reset token |
| แก้ไข | `frontend/src/components/SettingsModal.jsx` — full implementation |
| สร้างใหม่ | `frontend/src/pages/ForgotPassword.jsx` |
| สร้างใหม่ | `frontend/src/pages/ResetPassword.jsx` |
| แก้ไข | `frontend/src/App.jsx` — เพิ่ม routes |

---

### Phase 7: Google SSO ✅ COMPLETED (2026-05-21)

**หมายเหตุ:** ปุ่ม Google SSO มีอยู่แล้วใน Login page แต่ยังไม่ทำงาน

#### Artifact 7.1: Google OAuth Integration

**Backend:**
- `GET /api/auth/google` — redirect to Google OAuth
- `GET /api/auth/google/callback` — handle callback, create/login user
- Auto-create user with status "approved" (Google-verified)
- Link Google account กับ existing account ถ้า email ตรงกัน

**Frontend:**
- ปุ่ม Google ใน Login page → redirect ไป backend OAuth URL
- Handle callback redirect กลับมา frontend

---

## Implementation Priority & Dependency Map

```
Phase 1 (Registration & Approval) ──────┐
                                         ├─→ Phase 2 (Package System)
Phase 3 (PDPA & Encryption) ─────────────┘         │
                                                     ├─→ Phase 6 (Profile & Settings)
Phase 4 (Voice Enrollment) ──── standalone           │
Phase 5 (Custom Prompt) ─────── standalone           │
Phase 7 (Google SSO) ────────── standalone (low priority)
```

### แนะนำลำดับการทำ

| ลำดับ | Phase | ประมาณ Effort | เหตุผล |
|:-----:|-------|:------------:|--------|
| ~~1~~ | ~~Phase 3.1: Three-Level Roles~~ | ~~เล็ก~~ | ✅ ทำพร้อม Phase 1 แล้ว |
| ~~2~~ | ~~Phase 1.1: Registration~~ | ~~กลาง~~ | ✅ เสร็จแล้ว |
| ~~3~~ | ~~Phase 1.2: Admin Approval~~ | ~~กลาง~~ | ✅ เสร็จแล้ว |
| 4 | Phase 3.2: PII Encryption | กลาง-ใหญ่ | PDPA compliance |
| ~~5~~ | ~~Phase 2.1-2.3: Package System~~ | ~~ใหญ่~~ | ✅ เสร็จแล้ว |
| ~~6~~ | ~~Phase 5: Custom Prompt~~ | ~~เล็ก~~ | ✅ เสร็จแล้ว |
| 7 | Phase 6: Profile & Settings | กลาง | Better UX |
| 8 | Phase 4: Voice Enrollment | ใหญ่ | Advanced feature |
| ~~9~~ | ~~Phase 7: Google SSO~~ | ~~กลาง~~ | ✅ เสร็จแล้ว |

---

## สรุป New Collections ที่ต้องเพิ่มใน MongoDB

| Collection | จาก Phase | หน้าที่ |
|-----------|-----------|---------|
| `package` | Phase 2 | นิยาม packages + limits |
| `user_package` | Phase 2 | mapping user ↔ package + usage tracking |
| `voice_sample` | Phase 4 | voice clips + embeddings |
| `password_reset` | Phase 6 | reset tokens (TTL) |

## สรุป New MinIO Buckets

| Bucket | จาก Phase | หน้าที่ |
|--------|-----------|---------|
| `voice-samples` | Phase 4 | เก็บ voice enrollment clips |

## สรุป New Environment Variables

| Variable | จาก Phase | หน้าที่ |
|----------|-----------|---------|
| `PII_ENCRYPTION_KEY` | Phase 3 | AES-256 master key |
| `PII_KEY_VERSION` | Phase 3 | Key rotation version |
| `GOOGLE_CLIENT_ID` | Phase 7 | Google OAuth |
| `GOOGLE_CLIENT_SECRET` | Phase 7 | Google OAuth |

---

## Risk & Considerations

1. **PII Encryption Performance** — Field-level encryption จะเพิ่ม latency ในการ read/write user data. ควร cache decrypted data ใน memory (short-lived) สำหรับ frequent access patterns.

2. **Voice Embedding Storage** — PyAnnote embedding vectors มีขนาดใหญ่ (~512 floats per sample). ถ้ามี user จำนวนมาก ต้อง consider vector database (Qdrant/Milvus) แทน MongoDB.

3. **Package Enforcement** — ต้อง atomic check-and-increment usage เพื่อป้องกัน race condition (ใช้ MongoDB `findOneAndUpdate` with conditions).

4. **Migration** — ข้อมูล user เดิมที่เป็น plaintext ต้อง migrate เป็น encrypted. ต้องมี downtime หรือทำ rolling migration.

5. **Key Management** — PII encryption key ต้องไม่อยู่ใน git repo. ควรใช้ vault service (HashiCorp Vault, AWS KMS) สำหรับ production.

6. **Google SSO + Approval Flow** — ถ้า Google SSO user ต้องรอ approve จะเป็น UX ที่แย่. ควร auto-approve Google SSO users หรือมี separate flow.

---

## Implementation Log

### Phase 1 — Completed 2026-05-19

**Scope:** Public Registration + Admin Approval + 3-Level Role Foundation

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/user.py` | Added: `first_name`, `last_name`, `phone`, `organization`, `status`, `registered_at`, `approved_at`, `approved_by` fields. Added role/status constants. |
| `backend/app/services/mongo.py` | Added: `get_user_status()`, `register_public_user()`, `get_users_by_status()`, `update_user_status()`, `get_user_count_by_status()`. Login now blocks non-approved users. |
| `backend/app/routers/auth.py` | Added: `POST /api/auth/register-public` (public registration with validation). Login returns Thai error messages for pending/rejected/suspended. |
| `backend/app/routers/admin.py` | **New file.** Admin endpoints: `GET /api/admin/users`, `GET /api/admin/users/stats`, `PUT /api/admin/users/{id}/approve\|reject\|suspend\|status`. |
| `backend/app/core/auth.py` | `get_current_admin()` now allows both `admin` and `superadmin` roles. |
| `backend/api.py` | Registered `admin_router`. Added `PUT` to CORS allowed methods. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/pages/Register.jsx` | **New file.** Public registration form with: first name, last name, email, phone, organization, password + confirm. Shows success state with "รอการอนุมัติ" message. |
| `frontend/src/pages/AdminDashboard.jsx` | **New file.** Admin dashboard with: stat cards (pending/approved/rejected/suspended), tab filter, user list with approve/reject/suspend actions. |
| `frontend/src/App.jsx` | Added `/register` and `/admin` routes. |
| `frontend/src/components/ProtectedRoute.jsx` | Added `requiredRole` prop — supports role-based route protection. |
| `frontend/src/contexts/AuthContext.jsx` | Exposes `userRole` from JWT payload. |
| `frontend/src/pages/Login.jsx` | "สมัครสมาชิก" link now routes to `/register`. |
| `frontend/src/pages/MainApp.jsx` | Navbar shows "จัดการผู้ใช้" link for admin/superadmin roles. |

**New API endpoints:**
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| POST | `/api/auth/register-public` | - | Public user registration (status=pending) |
| GET | `/api/admin/users` | Admin | List users with optional status filter |
| GET | `/api/admin/users/stats` | Admin | User count by status |
| PUT | `/api/admin/users/{id}/approve` | Admin | Approve pending user |
| PUT | `/api/admin/users/{id}/reject` | Admin | Reject user |
| PUT | `/api/admin/users/{id}/suspend` | Admin | Suspend user |
| PUT | `/api/admin/users/{id}/status` | Admin | Set any valid status |

### Phase 2 — Completed 2026-05-19

**Scope:** Package System — 4 public packages + 2 internal (Admin/SuperAdmin), usage tracking with monthly auto-reset, package limit enforcement, dynamic nav badge, and admin package assignment UI.

**Packages implemented (from pricing table):**
| Package | Price | Tier | Transcription | Max/File | Files/Mo | AI Sum/Mo |
|---------|-------|:----:|:-------------:|:--------:|:--------:|:---------:|
| TimSumBasic | 200 ฿/mo | 0 | 180 min | 30 min | 6 | 6 |
| TimSumPro | 400 ฿/mo | 1 | 900 min | 90 min | 20 | 20 |
| TimSumEnterprise | 1,000 ฿/mo | 2 | 3,200 min | 300 min | 250 | 250 |
| TimSumEnterprise (yearly) | 6,000 ฿/yr | 2 | 3,200 min | 300 min | 250 | 250 |
| TimSumAdmin (internal) | — | 10 | 99,999 min | 99,999 min | 99,999 | 99,999 |
| TimSumSuperAdmin (internal) | — | 99 | 99,999 min | 99,999 min | 99,999 | 99,999 |

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/package.py` | **New file.** `Package`, `PackageLimits`, `UserPackage`, `UserPackageUsage` models. `DEFAULT_PACKAGES` list, `ADMIN_PACKAGE`, `SUPERADMIN_PACKAGE` constants. |
| `backend/app/services/mongo.py` | Added collections: `package`, `user_package`. Methods: `upsert_package()`, `get_all_packages()`, `get_package_by_id()`, `get_package_by_name()`, `assign_user_package()`, `get_user_package()` (with auto monthly usage reset), `increment_usage()`, `check_package_limits()`. |
| `backend/app/routers/package.py` | **New file.** Package endpoints (see table below). |
| `backend/api.py` | Registered `package_router`. Added `_seed_packages()` startup — seeds all default packages + assigns Admin/SuperAdmin packages to default users. Added package limit check (`check_package_limits()`) before transcribe-summarize. Added `increment_usage(files=1, ai_summaries=1)` after job creation. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/PackageBadge.jsx` | **New file.** Fetches user's package from API, displays dynamic badge with tier-based colors (Basic=brown, Pro=gold, Enterprise=blue, Admin=gray, SuperAdmin=purple). |
| `frontend/src/components/SettingsModal.jsx` | Added `UsageBar` component. New "แพ็กเกจ & การใช้งาน" section showing: package name, price, billing cycle, usage reset month, and progress bars for files/AI summaries/transcription minutes (turns red at >= 80%). |
| `frontend/src/pages/MainApp.jsx` | Replaced hardcoded `<button className="nav-pro-badge">TimSum Pro</button>` with `<PackageBadge token={token} />`. Passes `token` prop to `<SettingsModal>`. |
| `frontend/src/pages/AdminDashboard.jsx` | Added package assignment: fetches packages list, shows `<select>` dropdown per approved user to assign packages via admin API. |

**New API endpoints:**
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/user/package` | User | Get own package + usage (auto-resets monthly) |
| GET | `/api/packages` | User | List public packages (tier < 10) |
| GET | `/api/admin/packages` | Admin | List all packages including internal |
| PUT | `/api/admin/users/{id}/package` | Admin | Assign package to user |
| GET | `/api/admin/users/{id}/package` | Admin | View user's package details |

**Key design decisions:**
- **Tier-based hierarchy** — public packages (tier 0-2) vs internal (tier 10, 99) enables clean filtering without exposing admin packages.
- **Monthly auto-reset** — `usage_reset_month` (YYYY-MM format) in `user_package` document; counters reset on first access of new month.
- **Atomic limit enforcement** — `check_package_limits()` runs before upload, returns 403 with Thai error messages when limits exceeded.
- **Startup seeding** — `_seed_packages()` uses `upsert_package()` to be idempotent; safe to run on every restart.

### Phase 6 — Completed

**Scope:** User Profile & Settings

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/ProfileModal.jsx` | **New file.** Extracted from `SettingsModal`. Contains "บัญชีผู้ใช้" and "แพ็คเกจ & การใช้งาน" sections. |
| `frontend/src/components/SettingsModal.jsx` | Removed account section, package/usage section, and `UsageBar`. Now only contains theme and about sections. |
| `frontend/src/pages/MainApp.jsx` | Imported and rendered `ProfileModal`. Added `showProfile` state and wired "โปรไฟล์" button. |

### Phase 5 — Completed 2026-05-19

**Scope:** Custom Summary Prompt — collapsible textarea for user-defined instructions appended to GPT-4.1 summary prompt. Gated by package permission (`custom_prompt_enabled`).

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/package.py` | Added `custom_prompt_enabled: bool = False` to `PackageLimits`. Enabled for TimSumPro, TimSumEnterprise, TimSumAdmin, and TimSumSuperAdmin packages. |
| `backend/api.py` | Added `custom_prompt` Form parameter to `POST /api/transcribe-summarize`. Validation: max 500 chars. Passed to Celery task. |
| `backend/app/tasks/transcription.py` | Added `custom_prompt` parameter to `process_audio()`, forwarded to `pipeline.process()`. |
| `backend/app/services/pipeline.py` | Added `custom_prompt` parameter to `TranscribeSummaryPipeline.process()`, forwarded to `summarize_with_diarization()`. |
| `backend/app/services/summarizer.py` | Added `custom_prompt` parameter to `summarize_with_diarization()`, `_summarize_standard()`, `_summarize_hierarchical()`, and `_consolidate_summaries()`. Appends as "**คำสั่งเพิ่มเติมจากผู้ใช้:**" section to system prompt when provided. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/CustomPromptInput.jsx` | **New file.** Collapsible textarea with 500-char limit, character counter, hint text. Hidden by default, expands on click. |
| `frontend/src/pages/MainApp.jsx` | Added `customPrompt` and `customPromptEnabled` state. Fetches package limits on mount to check `custom_prompt_enabled` permission. Renders `CustomPromptInput` inside meeting type card (only when package allows). Sends `custom_prompt` in FormData on submit. |
| `frontend/src/styles/index.css` | Added `.custom-prompt-*` styles: toggle button, textarea, footer with hint and character count. |

**Key design decisions:**
- **Package-gated** — `custom_prompt_enabled` is `false` for Basic tier, `true` for Pro+. Frontend hides the input entirely for Basic users.
- **Appended to system prompt** — Custom prompt is injected as a clearly labeled section at the end of the system prompt, so it influences but doesn't override the meeting-type-specific structure.
- **Both summarization paths** — Custom prompt applies to both standard (single-call) and hierarchical (multi-chunk consolidation) paths.
- **Server-side validation** — 500-char limit enforced both client-side and server-side (API returns 400 if exceeded).

### Phase 7 — Completed 2026-05-21

**Scope:** Google SSO — Sign in with Google using Google Identity Services (GIS). Auto-creates approved users on first login. Links to existing accounts by email.

**Backend changes:**
| File | Change |
|------|--------|
| `backend/requirements.txt` | Added `google-auth>=2.29.0` for Google ID token verification. |
| `backend/app/models/user.py` | Added `google_id: Optional[str]` field to `User` model for storing Google subject ID. |
| `backend/app/services/mongo.py` | Added `find_or_create_google_user()` — finds user by email or creates new approved user with random password, quota, and TimSumBasic package assignment. Links `google_id` to existing accounts. |
| `backend/app/routers/auth.py` | Added `GET /api/auth/google/client-id` (returns client ID for frontend GIS init) and `POST /api/auth/google` (verifies Google ID token via `google.oauth2.id_token`, creates/finds user, returns JWT). |
| `docker-compose.yml` | Added `GOOGLE_CLIENT_ID` env var to backend service. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/index.html` | Added Google Identity Services script tag (`accounts.google.com/gsi/client`). |
| `frontend/src/pages/Login.jsx` | Fetches `GOOGLE_CLIENT_ID` from backend on mount. Initializes GIS with `google.accounts.id.initialize()` and renders official Google button via `renderButton()`. On success, sends credential to `POST /api/auth/google` and stores JWT. Falls back to disabled styled button when SSO not configured. |
| `frontend/src/styles/Login.css` | Added `.login-google-gsi` container style and disabled state for fallback button. |

**New API endpoints:**
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/auth/google/client-id` | - | Returns Google Client ID for frontend (or `enabled: false` if not configured) |
| POST | `/api/auth/google` | - | Verify Google credential, create/find user, return JWT |

**New environment variables:**
| Variable | Description |
|----------|-------------|
| `GOOGLE_CLIENT_ID` | Google OAuth 2.0 Client ID (from Google Cloud Console) |

**Key design decisions:**
- **Frontend-initiated flow** — Uses Google Identity Services (GIS) library with `renderButton()` for the official Google-branded sign-in button. No redirect-based OAuth needed.
- **Backend-fetched client ID** — Login page fetches `GOOGLE_CLIENT_ID` from `GET /api/auth/google/client-id` instead of baking it into the frontend build. Allows changing the ID without rebuilding.
- **Auto-approved** — Google SSO users are created with `status=approved` (Google-verified identity). No admin approval needed.
- **Email linking** — If a user with the same email already exists (registered manually), the Google `sub` ID is linked to their account. They can then use either login method.
- **Graceful degradation** — When `GOOGLE_CLIENT_ID` is not set, the Google button is disabled and the `POST /api/auth/google` endpoint returns 501. No errors or crashes.
- **Default package** — New Google users are automatically assigned the TimSumBasic package.
