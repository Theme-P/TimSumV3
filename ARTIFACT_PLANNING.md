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
| 8a | แนบ clip ตัวอย่างเสียง (voice enrollment) | ✅ เสร็จแล้ว | กลาง |
| 8b | ใช้ clip เสียงที่เคยให้ไว้แล้ว (voice library) | ✅ เสร็จแล้ว | กลาง |
| 9a | Auto mode summary [default] | ✅ เสร็จแล้ว | - |
| 9b | ผู้ใช้ prompt เพิ่มเพื่อกำหนดรูปแบบสรุป | ✅ เสร็จแล้ว | กลาง |
| 10 | PDPA — 3 ระดับ user (superadmin/admin/user) | ✅ เสร็จแล้ว | สูง |
| 11 | Encrypt/tokenize ข้อมูลส่วนบุคคลใน DB | 🔴 ไม่มี | สูง |
| 12 | PDPA Consent UI & Management | ✅ เสร็จแล้ว | สูง |
| 13 | User Activity Log & Audit Trail | ✅ เสร็จแล้ว | สูง |
| 14 | Transcript/Summary รองรับ 3 ภาษา (ไทย/อังกฤษ/จีน) | 🔴 ไม่มี | กลาง |
| 15 | Queue Monitoring (Admin) | ✅ เสร็จแล้ว | กลาง |
| 16 | Server Resource Monitoring + Ollama Management | ✅ เสร็จแล้ว (Phase 10) | กลาง |
| 17 | Scheduled Daily DB Backup | 🔴 ไม่มี | กลาง |
| 18 | Human Speaker Verification (ยืนยันเสียงโดย Human) | 🔴 ไม่มี | กลาง |
| 19 | Sub-agenda Auto-separation & Analysis | 🔴 ไม่มี | ต่ำ |
| 20 | LLM Settings & Rule-based Templates | 🔴 ไม่มี | ต่ำ |
| 21 | VA Pen Test / Security Hardening | 🔴 ไม่มี | สูง |
| 22 | Performance Tuning | ✅ เสร็จแล้ว (Phase 16.2) | กลาง |

**สรุป:** 18 features เสร็จแล้ว, 0 features กำลังดำเนินการ, 4 features ต้องทำใหม่

---

## Implementation Log

### Phase 10 (Monitoring & Administration Update) — Completed 2026-05-28

**Scope:** แยกหน้า Admin Monitoring ออกจากหน้าจัดการผู้ใช้, เพิ่มฟังก์ชันการลบผู้ใช้งาน (Super Admin), ปรับ UI และแก้ปัญหาเรื่อง Timezone

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/services/mongo.py` | เพิ่ม logic Cascade Delete ใน `delete_user()` เพื่อลบ quota, user_package, session, voice_sample และ consent_record ที่เกี่ยวข้อง |
| `backend/app/routers/admin.py` | เพิ่ม endpoint `DELETE /api/admin/users/{user_id}` เพื่อใช้ลบผู้ใช้ |
| `backend/app/core/auth.py` | เพิ่ม `get_current_superadmin` dependency ไว้ดักเช็คสิทธิ์ Super Admin |
| `docker-compose.yml` | เพิ่ม `TZ=Asia/Bangkok` เพื่อแก้ปัญหาเวลาของ DOCX export ที่ต่างกับไทย 7 ชั่วโมง |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/pages/AdminDashboard.jsx` | นำระบบคิวและ System Resource ออกไปแยกเป็นอีกหน้าหนึ่ง, เพิ่มปุ่ม "ลบผู้ใช้" ในตารางผู้ใช้เฉพาะคนที่ Login ด้วยสิทธิ์ Super Admin |
| `frontend/src/pages/AdminMonitoring.jsx` | **New file.** หน้าระบบคิวและการทำงานของเซิร์ฟเวอร์แยกต่างหาก |
| `frontend/src/components/admin/ServerResources.jsx` | ปรับขนาด UI ของ Circular Progress (จาก 96px เป็น 72px) เพื่อให้พอดีกับหน้าจอ และแก้ไขเรื่องการแสดงเวลา |
| `frontend/src/App.jsx` | เพิ่ม Route `/admin/monitoring` |

**System Administration:**
- ทำการ Enable NTP Time Sync บน Host เพื่อแก้ปัญหา System Clock เดินไม่ตรง (drift) ไป 7 นาที
- สร้างสคริปต์ลบ Test Users (`test@timsumv3.local`, `testuser@timsumv3.local`)

---

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
| `frontend/src/components/PackageBadge.jsx` | **New file.** Fetches user's package from API, displays dynamic badge with tier-based colors. |
| `frontend/src/components/SettingsModal.jsx` | Added `UsageBar` component. New "แพ็กเกจ & การใช้งาน" section with progress bars (turns red at >= 80%). |
| `frontend/src/pages/MainApp.jsx` | Replaced hardcoded badge with `<PackageBadge token={token} />`. |
| `frontend/src/pages/AdminDashboard.jsx` | Added package assignment dropdown per approved user. |

**New API endpoints:**
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/user/package` | User | Get own package + usage (auto-resets monthly) |
| GET | `/api/packages` | User | List public packages (tier < 10) |
| GET | `/api/admin/packages` | Admin | List all packages including internal |
| PUT | `/api/admin/users/{id}/package` | Admin | Assign package to user |
| GET | `/api/admin/users/{id}/package` | Admin | View user's package details |

### Phase 5 — Completed 2026-05-19

**Scope:** Custom Summary Prompt — collapsible textarea for user-defined instructions appended to GPT-4.1 summary prompt. Gated by package permission (`custom_prompt_enabled`).

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/package.py` | Added `custom_prompt_enabled: bool = False` to `PackageLimits`. Enabled for Pro+. |
| `backend/api.py` | Added `custom_prompt` Form parameter to `POST /api/transcribe-summarize`. Max 500 chars. |
| `backend/app/tasks/transcription.py` | Added `custom_prompt` parameter, forwarded to `pipeline.process()`. |
| `backend/app/services/summarizer.py` | Appends custom_prompt as "**คำสั่งเพิ่มเติมจากผู้ใช้:**" section to system prompt. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/CustomPromptInput.jsx` | **New file.** Collapsible textarea with 500-char limit, character counter. |
| `frontend/src/pages/MainApp.jsx` | Fetches package limits to check `custom_prompt_enabled`. Sends `custom_prompt` in FormData. |

### Phase 6 — Completed

**Scope:** User Profile & Settings

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/ProfileModal.jsx` | **New file.** Extracted from `SettingsModal`. Contains profile + package/usage sections. |
| `frontend/src/components/SettingsModal.jsx` | Removed account/package sections. Now only theme and about. |
| `frontend/src/pages/MainApp.jsx` | Added `showProfile` state and wired "โปรไฟล์" button. |

### Phase 7 — Completed 2026-05-21

**Scope:** Google SSO — Sign in with Google using GIS. Auto-creates approved users. Links to existing accounts by email.

**Backend changes:**
| File | Change |
|------|--------|
| `backend/requirements.txt` | Added `google-auth>=2.29.0`. |
| `backend/app/models/user.py` | Added `google_id: Optional[str]` field. |
| `backend/app/services/mongo.py` | Added `find_or_create_google_user()`. |
| `backend/app/routers/auth.py` | Added `GET /api/auth/google/client-id` and `POST /api/auth/google`. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/index.html` | Added Google Identity Services script tag. |
| `frontend/src/pages/Login.jsx` | Fetches client ID, renders GIS button, sends credential to backend. |

**New API endpoints:**
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/auth/google/client-id` | - | Returns Google Client ID |
| POST | `/api/auth/google` | - | Verify Google credential, create/find user, return JWT |

### Phase 8 — Completed 2026-05-24

**Scope:** User Activity Log — MongoDB collection with 90-day TTL, fire-and-forget logging helper, user and admin log endpoints.

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/activity_log.py` | **New file.** `ActivityLog` model + action constants. |
| `backend/app/services/mongo.py` | Added `activity_log` collection. TTL 90d. `log_activity()`, `get_activity_logs()`, `count_activity_logs()`. |
| `backend/app/routers/activity.py` | **New file.** `GET /api/user/activity-logs`, `GET /api/admin/activity-logs`. |
| `backend/api.py` | Registered `activity_router`. Added `log_activity()` to upload/view endpoints. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/ProfileModal.jsx` | Added "ประวัติการใช้งาน" tab — 10 latest entries with Thai labels + relative timestamps. |
| `frontend/src/pages/AdminDashboard.jsx` | Added "Activity Log" tab. |

### Phase 9 — Completed 2026-05-24

**Scope:** PDPA Consent Management — consent model with versioning, ConsentGate wrapping the entire app, Privacy Policy and Terms pages.

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/consent.py` | **New file.** `ConsentRecord` model. `CONSENT_TYPES` with versioning. |
| `backend/app/services/mongo.py` | Added `consent_record` collection. `save_consent()`, `get_user_consents()`, `has_required_consents()` (version-aware). |
| `backend/app/routers/consent.py` | **New file.** `GET /POST /DELETE /api/consent`, `GET /api/admin/consent-records`. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/ConsentModal.jsx` | **New file.** Full-screen consent overlay (required on first login). |
| `frontend/src/pages/PrivacyPolicy.jsx` | **New file.** 6-section PDPA-aligned policy page. |
| `frontend/src/pages/TermsOfService.jsx` | **New file.** 6-section terms page. |
| `frontend/src/contexts/AuthContext.jsx` | Added `needsConsent`, `consentChecked`, `markConsented`. |
| `frontend/src/App.jsx` | Added `ConsentGate` wrapper + `/privacy-policy` + `/terms` routes. |
| `frontend/src/components/ProfileModal.jsx` | Added "การยินยอม (PDPA)" tab. |

### Phase 4 — Completed 2026-05-22

**Scope:** Voice Enrollment System — Upload voice samples, extract speaker embeddings via PyAnnote, voice-matched diarization.

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/models/voice_sample.py` | **New file.** `VoiceSample` model. |
| `backend/app/services/voice_matching.py` | **New file.** `VoiceMatchingService` — PyAnnote embedding + cosine similarity. |
| `backend/app/routers/voice_samples.py` | **New file.** Upload / list / stream / delete endpoints. |
| `backend/app/services/pipeline.py` | Integrated `VoiceMatchingService` after diarization step. |
| `backend/api.py` | Registered `voice_samples_router`. Added `use_voice_matching` toggle. |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/components/VoiceLibrary.jsx` | **New file.** Upload dropzone, sample list, playback, delete. |
| `frontend/src/components/ProfileModal.jsx` | Added "🎙️ คลังเสียง" tab (package-gated). |
| `frontend/src/pages/MainApp.jsx` | Added `use_voice_matching` toggle. |

### Phase 10 (Queue Monitoring) — Completed 2026-05-25

**Scope:** Queue Monitor tab ใน AdminDashboard — แสดง Celery job stats จาก MongoDB + job list + cancel job

**Backend changes:**
| File | Change |
|------|--------|
| `backend/app/routers/queue.py` | **New file.** `GET /api/admin/queue/stats`, `GET /api/admin/queue/tasks`, `DELETE /api/admin/queue/tasks/{job_id}` |
| `backend/app/services/mongo.py` | Added: `get_job_stats()`, `get_all_jobs()`, `cancel_job()` |
| `backend/api.py` | Registered `queue_router` |

**Frontend changes:**
| File | Change |
|------|--------|
| `frontend/src/pages/AdminDashboard.jsx` | Added "Queue Monitor" tab — 5 stat cards + job list + cancel button. Auto-refresh ทุก 30 วินาที. |

**New API endpoints:**
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/admin/queue/stats` | Admin | Job counts by status + completed_today |
| GET | `/api/admin/queue/tasks` | Admin | Recent 50 jobs (all users), filterable by status |
| DELETE | `/api/admin/queue/tasks/{job_id}` | Admin | Revoke Celery task + mark job as cancelled |

---

## Artifact Plan — แบ่งเป็น Phase

---

### Phase 1: User Registration & Approval System ✅ COMPLETED (2026-05-19)

**เหตุผล:** เป็น core flow ที่ user ต้องใช้ก่อนจะเข้าถึง feature อื่นๆ ได้

#### Artifact 1.1: Public Registration API & Page

**Backend:**
- `POST /api/auth/register-public` — endpoint ใหม่สำหรับ public registration
- เพิ่ม field ใน User model:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
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
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
- MongoDB collection: `package`
- Admin CRUD APIs:
  - `POST /api/admin/packages` — สร้าง package
  - `GET /api/admin/packages` — list all packages
  - `PUT /api/admin/packages/{id}` — update package
  - `DELETE /api/admin/packages/{id}` — soft delete

#### Artifact 2.2: User-Package Assignment & Usage Tracking

**Backend:**
- สร้าง UserPackage model:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
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
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
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
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
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

### Phase 4: Voice Enrollment System ✅ COMPLETED (2026-05-22)

**เหตุผล:** เพิ่มความแม่นยำของ speaker diarization — differentiate จากคู่แข่ง

#### Artifact 4.1: Voice Sample Upload & Storage

**Backend:**
- สร้าง VoiceSample model:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
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

*[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*

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
| ~~8~~ | ~~Phase 4: Voice Enrollment~~ | ~~ใหญ่~~ | ✅ เสร็จแล้ว |
| ~~9~~ | ~~Phase 7: Google SSO~~ | ~~กลาง~~ | ✅ เสร็จแล้ว |
| ~~10~~ | ~~Phase 8: User Activity Log~~ | ~~เล็ก-กลาง~~ | ✅ เสร็จแล้ว |
| ~~11~~ | ~~Phase 9: PDPA Consent Management~~ | ~~กลาง~~ | ✅ เสร็จแล้ว |
| 12 | Phase 10: Queue & Server Monitoring | กลาง | Ops visibility |
| 13 | Phase 11: Scheduled DB Backup | เล็ก | Data safety |
| 14 | Phase 14: Human Speaker Verification | กลาง | Accuracy improvement |
| 15 | Phase 12: Multilingual Support | ใหญ่ | Market expansion |
| 16 | Phase 13: Sub-agenda Auto-separation | ใหญ่ | Premium feature |
| 17 | Phase 15: LLM Settings & Templates | กลาง | Customization |
| 18 | Phase 16: Security & Performance | ใหญ่ | Production readiness |

---

### Phase 8: User Activity Log & Audit Trail ✅ COMPLETED (2026-05-24)

**เหตุผล:** ต้องการสำหรับ PDPA compliance — ต้องรู้ว่าใครเข้าถึงข้อมูลอะไรเมื่อไร + ตรวจสอบ anomaly

#### Artifact 8.1: Activity Logging Backend

**Backend:**
- สร้าง `ActivityLog` model:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
- Actions ที่ต้อง log:
  - `login`, `logout`, `login_failed`
  - `upload_audio`, `view_transcript`, `view_summary`, `download_docx`, `send_email`
  - `view_session`, `delete_session`
  - `update_profile`, `change_password`
  - `admin_approve_user`, `admin_reject_user`, `admin_assign_package`
- MongoDB collection: `activity_log` (TTL index: เก็บ 90 วัน)
- APIs:
  - `GET /api/admin/activity-logs` — superadmin ดู log ทั้งหมด (filter by user, date, action)
  - `GET /api/user/activity-logs` — user ดู log ของตัวเอง
  - Export ออกเป็น CSV

**Frontend:**
- Admin Dashboard: tab "Activity Log" — searchable/filterable table
- User Profile: section "ประวัติการใช้งาน" — แสดง log ย้อนหลัง 30 วัน

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/models/activity_log.py` |
| สร้างใหม่ | `backend/app/services/activity_logger.py` — log() helper function |
| สร้างใหม่ | `backend/app/routers/activity.py` — activity log endpoints |
| แก้ไข | `backend/app/services/mongo.py` — activity_log collection + TTL index |
| แก้ไข | `backend/api.py` — register activity router, add request logging middleware |
| แก้ไข | `frontend/src/pages/AdminDashboard.jsx` — เพิ่ม Activity Log tab |
| แก้ไข | `frontend/src/components/ProfileModal.jsx` — เพิ่ม activity history section |

---

### Phase 9: PDPA Consent Management ✅ COMPLETED (2026-05-24)

**เหตุผล:** กฎหมาย PDPA กำหนดให้ต้องเก็บ consent อย่างชัดเจน พร้อม audit trail ของ consent

#### Artifact 9.1: Consent Collection & Management

**Backend:**
- สร้าง `ConsentRecord` model:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
- Consent types:
  - `privacy_policy` — นโยบายความเป็นส่วนตัว
  - `data_processing` — การประมวลผลข้อมูลเสียง/transcript
  - `marketing` — การรับข้อมูลข่าวสาร (optional)
- APIs:
  - `GET /api/consent` — ดู consent status ของ user ปัจจุบัน
  - `POST /api/consent` — บันทึก consent (ระบุ type + version)
  - `DELETE /api/consent/{type}` — ถอน consent
  - `GET /api/admin/consent-records` — superadmin ดู consent records ทั้งหมด
- Policy versioning: เมื่อ privacy policy อัปเดต → ต้อง re-consent

**Frontend:**
- Modal "ยินยอมการใช้งาน" แสดงในครั้งแรกที่ login (ถ้ายังไม่ consent)
- หน้า Privacy Policy (`/privacy-policy`) และ Terms of Service (`/terms`)
- User Profile: section "การยินยอม" — ดู/ถอน consent แต่ละประเภท
- Admin: รายงาน consent statistics

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/models/consent.py` |
| สร้างใหม่ | `backend/app/routers/consent.py` |
| แก้ไข | `backend/app/services/mongo.py` — consent collection |
| แก้ไข | `backend/api.py` — register consent router |
| สร้างใหม่ | `frontend/src/components/ConsentModal.jsx` |
| สร้างใหม่ | `frontend/src/pages/PrivacyPolicy.jsx` |
| สร้างใหม่ | `frontend/src/pages/TermsOfService.jsx` |
| แก้ไข | `frontend/src/App.jsx` — เพิ่ม routes + consent gate |
| แก้ไข | `frontend/src/contexts/AuthContext.jsx` — check consent on login |

---

### Phase 10: Queue Monitoring & Server Administration ✅ COMPLETED (2026-05-28)

**เหตุผล:** Admin ต้องการ visibility ของ Celery queue และ server resources เพื่อ manage workload

**Artifact 10.1: Queue Monitoring Dashboard**

**Backend:**
- APIs:
  - `GET /api/admin/queue/stats` — Celery queue depth, active/reserved/scheduled tasks
  - `GET /api/admin/queue/tasks` — list tasks (pending/active/completed/failed) with details
  - `DELETE /api/admin/queue/tasks/{task_id}` — revoke/cancel task (superadmin)
  - `GET /api/admin/system/resources` — CPU%, RAM%, GPU%, Disk usage (Docker stats)
  - `POST /api/admin/system/ollama/restart` — restart Ollama container (superadmin)
- ใช้ Celery Inspect API สำหรับ queue data
- ใช้ `psutil` หรือ Docker API สำหรับ resource metrics

**Frontend:**
- Admin Monitoring (แยกหน้าใหม่จาก Admin Dashboard):
  - Real-time queue stats (jobs waiting, processing, completed today)
  - Server resource gauges (CPU, RAM, GPU, Disk)
  - Task list with status + user + filename
  - Buttons: Cancel task, Restart Ollama (superadmin only)
- Auto-refresh ทุก 30 วินาที

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/routers/system_admin.py` — queue + system endpoints |
| แก้ไข | `backend/api.py` — register system_admin router |
| สร้างใหม่ | `frontend/src/pages/AdminMonitoring.jsx` — แยกระบบ System Monitor |
| สร้างใหม่ | `frontend/src/components/admin/QueueMonitor.jsx` |
| สร้างใหม่ | `frontend/src/components/admin/ServerResources.jsx` |

---

### Phase 11: Scheduled Database Backup 🔴 (Priority: กลาง)

**เหตุผล:** ป้องกันข้อมูลสูญหาย — backup อัตโนมัติทุกวัน + เก็บไว้ 30 วัน

#### Artifact 11.1: Automated MongoDB Backup

**Backend:**
- Celery Beat task: `backup_mongodb()` — รันทุกวันตี 2
  - `mongodump` → compress → เก็บใน MinIO bucket `db-backups`
  - ลบ backups ที่เก่ากว่า 30 วัน (cleanup)
  - ส่ง email แจ้ง superadmin เมื่อ backup สำเร็จ/ล้มเหลว
- APIs:
  - `GET /api/admin/backups` — list backup files + size + date
  - `POST /api/admin/backups/trigger` — manual trigger backup (superadmin)
  - `GET /api/admin/backups/{id}/download` — download backup file

**Infrastructure:**
- MinIO bucket: `db-backups`
- Celery Beat scheduler เพิ่ม schedule ใน `celery_config.py`
- `mongodump` ต้องมีใน GPU worker container

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/tasks/backup.py` — backup Celery task |
| แก้ไข | `backend/app/services/storage.py` — เพิ่ม db-backups bucket |
| แก้ไข | `backend/celery_config.py` — เพิ่ม beat schedule |
| สร้างใหม่ | `backend/app/routers/backup.py` — backup management endpoints |
| แก้ไข | `backend/api.py` — register backup router |
| แก้ไข | `docker-compose.yml` — เพิ่ม mongodump ใน worker |
| แก้ไข | `frontend/src/components/admin/ServerResources.jsx` — แสดง backup status |

---

### Phase 12: Multilingual Support (ไทย / อังกฤษ / จีน) 🔴 (Priority: กลาง)

**เหตุผล:** รองรับการประชุมแบบ code-switching หรือการประชุมที่มีผู้เข้าร่วมหลายชาติ

#### Artifact 12.1: Multi-language Transcription & Summary

**Backend:**
- WhisperX รองรับ multilingual อยู่แล้ว — ปัจจุบัน force `language="th"`
- เพิ่ม `language` parameter ใน transcribe-summarize request:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
- Summary prompt: ปรับ system prompt ตาม language mode
  - `th` → สรุปเป็นภาษาไทย
  - `en` → Summarize in English
  - `zh` → 用中文总结
  - `mixed` → สรุปเป็นภาษาไทย แต่ระบุ original language ของแต่ละ speaker
- เพิ่ม `detected_language` field ใน session result

**Frontend:**
- เพิ่ม "ภาษาการประชุม" dropdown ใน upload form (ใต้ meeting type):
  - 🇹🇭 ภาษาไทย (default)
  - 🇬🇧 ภาษาอังกฤษ
  - 🇨🇳 ภาษาจีน
  - 🌐 ตรวจสอบอัตโนมัติ
- แสดง detected language badge ใน session history

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/api.py` — เพิ่ม `language` Form parameter |
| แก้ไข | `backend/app/tasks/transcription.py` — pass language |
| แก้ไข | `backend/app/services/pipeline.py` — ส่ง language ไป WhisperX |
| แก้ไข | `backend/app/services/summarizer.py` — ปรับ system prompt ตาม language |
| แก้ไข | `frontend/src/pages/MainApp.jsx` — เพิ่ม language selector |
| แก้ไข | `frontend/src/styles/index.css` — style สำหรับ language selector |

---

### Phase 13: Sub-agenda Auto-separation & Meeting Agenda Analysis 🔴 (Priority: ต่ำ)

**เหตุผล:** ช่วย user ดู transcript/summary แบ่งตาม วาระการประชุม — มีคุณค่ามากสำหรับ formal meetings

**แนวคิดหลัก:** ใช้ GPT-4.1 ตรวจจับจุดแบ่งวาระจาก transcript โดยแบ่ง 3 ระดับตามประเภทประชุม:

| ประเภทประชุม | ผลลัพธ์ | ตัวอย่าง |
|-------------|---------|---------|
| Formal | แบ่งเป็น **"วาระ"** (Agenda) | ประชุมคณะกรรมการ, สามัญ/วิสามัญ |
| Semi-formal | แบ่งเป็น **"หัวข้อ"** (Topic) | ประชุมทีม, ติดตามงาน |
| ไม่มีโครงสร้าง | **ไม่แบ่ง** — แสดง summary เดิม | คุยเรื่องเดียวตลอด |

ใช้ meeting_type (1-11) เป็น hint + ให้ GPT ตัดสินใจขั้นสุดท้ายว่าควรแบ่งหรือไม่

**Pipeline Flow:**
```
[เดิม] Audio → WhisperX → Diarization → Speaker ID
                                            ↓
[เพิ่ม]                              GPT-4.1 Agenda Detection
                                      (ได้จุดแบ่ง + ชื่อวาระ)
                                            ↓
[เดิม แต่ปรับ]                       Summarize แยกแต่ละวาระ
                                      + สรุปรวมภาพใหญ่
```

**MongoDB Storage:** ฝังใน session document เดิม ไม่สร้าง collection ใหม่
```json
{
  "agendas": [
    {
      "number": 1,
      "title": "รับรองรายงานการประชุมครั้งที่ 3/2568",
      "type": "agenda",
      "start_time": 0.0,
      "end_time": 542.3,
      "start_segment_idx": 0,
      "end_segment_idx": 45,
      "speakers": ["คนพูด 1", "คนพูด 3"],
      "summary": "...",
      "decisions": ["..."],
      "action_items": ["..."],
      "confidence": 0.95
    }
  ],
  "detection_mode": "formal_agenda"
}
```

**หมายเหตุ:** Phase นี้เป็น read-only (auto-detect เท่านั้น) — ไม่รวม manual editing UI เพื่อลดความซับซ้อน สามารถต่อยอดเพิ่ม manual editing ในอนาคตได้

#### Artifact 13.1: Agenda Detection Service

**Backend:**
- สร้าง `AgendaDetector` class ที่ส่ง transcript ให้ GPT-4.1 พร้อม meeting_type เป็น hint
- GPT prompt ให้ตอบ structured JSON: `detection_mode` + `items[]` พร้อม segment boundaries
- รองรับ 3 modes: `formal_agenda` / `topic_segments` / `single_topic`
- เป็น optional feature — เปิดใช้ผ่าน `detect_agenda` toggle ใน upload form

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/services/agenda_detector.py` — GPT-4.1 agenda detection + parsing |

#### Artifact 13.2: Pipeline & Summarizer Integration

**Backend:**
- แก้ pipeline ให้เรียก AgendaDetector หลัง diarization เสร็จ (ก่อน summarize)
- แก้ summarizer ให้สรุปแยกแต่ละวาระ + สรุปรวมภาพใหญ่
- เก็บ `agendas` array ใน session document

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/app/services/pipeline.py` — เพิ่ม agenda detection step |
| แก้ไข | `backend/app/services/summarizer.py` — สรุปแยกวาระ |
| แก้ไข | `backend/app/tasks/transcription.py` — pass detect_agenda flag |
| แก้ไข | `backend/api.py` — เพิ่ม `detect_agenda` Form parameter |

#### Artifact 13.3: Agenda View UI (Read-only)

**Frontend:**
- ใน session detail: tab "วาระการประชุม" แสดงสรุปแยกวาระ (read-only)
- แสดง timeline bar + clickable วาระ พร้อม timestamp
- แสดง detection_mode badge (วาระ/หัวข้อ)
- Upload form: checkbox "ตรวจจับวาระอัตโนมัติ"

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `frontend/src/components/AgendaView.jsx` — agenda timeline + per-agenda summary |
| แก้ไข | `frontend/src/pages/MainApp.jsx` — agenda tab + detect_agenda toggle |

#### Artifact 13.4: Agenda-aware DOCX Export

**Backend:**
- ถ้ามี agendas → export DOCX แบ่ง section ตามวาระ พร้อม heading + summary + decisions
- ถ้าไม่มี → export เหมือนเดิม

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/app/services/export.py` — agenda-aware DOCX structure |

---

### Phase 14: Human Speaker Verification (ยืนยันเสียงโดย Human) 🔴 (Priority: กลาง)

**เหตุผล:** Voice matching อาจผิดพลาด — ให้ user ตรวจสอบและแก้ไข speaker assignment ก่อน export

#### Artifact 14.1: Speaker Review & Manual Override UI

**Backend:**
- เพิ่ม `speaker_assignments` field ใน session result:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
- APIs:
  - `PUT /api/sessions/{id}/speakers` — update speaker name assignments
  - `POST /api/sessions/{id}/speakers/add` — เพิ่ม speaker ที่ระบบไม่ detect
  - `POST /api/sessions/{id}/speakers/merge` — merge 2 speakers เป็น 1 คน
- Re-generate transcript/summary หลัง speaker correction (optional)

**Frontend:**
- ใน session detail: panel "ยืนยันผู้พูด"
  - List speakers ที่ระบบ detect + confidence score
  - Input field สำหรับแก้ชื่อแต่ละ speaker
  - ปุ่ม "เพิ่มผู้พูด" (manual add) และ "รวมผู้พูด" (merge)
  - Highlight segments ใน transcript ตาม speaker ที่เลือก
  - ปุ่ม "ยืนยันและ Export" — lock assignments + generate DOCX

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| แก้ไข | `backend/app/models/session.py` — เพิ่ม speaker_assignments field |
| แก้ไข | `backend/app/routers/sessions.py` — speaker update endpoints |
| แก้ไข | `backend/app/services/mongo.py` — update speaker assignments |
| แก้ไข | `backend/app/services/export.py` — use confirmed assignments |
| สร้างใหม่ | `frontend/src/components/SpeakerVerification.jsx` |
| แก้ไข | `frontend/src/pages/MainApp.jsx` — integrate speaker verification |

---

### Phase 15: LLM Settings & Rule-based Templates 🔴 (Priority: ต่ำ)

**เหตุผล:** Admin ต้องการปรับแต่ง prompt templates และ LLM parameters โดยไม่ต้องแก้ code

#### Artifact 15.1: Admin LLM Configuration

**Backend:**
- สร้าง `LLMConfig` model:
  *[รายละเอียดโค้ดถูกตัดออกเพื่อความกระชับ]*
- MongoDB collection: `llm_config`
- APIs:
  - `GET /api/admin/llm-configs` — list all configs
  - `PUT /api/admin/llm-configs/{name}` — update config
  - `POST /api/admin/llm-configs/test` — test prompt with sample text
- Pipeline: load config จาก DB แทน hardcoded prompts

**Rule-based Templates:**
- สร้าง template variables: `{meeting_type}`, `{speaker_list}`, `{language}`, `{custom_prompt}`
- Admin สร้าง template ใหม่สำหรับ meeting type เฉพาะองค์กร

**Frontend:**
- Admin Dashboard: tab "LLM Settings"
  - List meeting types + edit prompt template
  - Temperature/max_tokens sliders
  - Test interface: ป้อน sample transcript → ดู output

**Files ที่ต้องแก้/สร้าง:**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/models/llm_config.py` |
| สร้างใหม่ | `backend/app/routers/llm_config.py` |
| แก้ไข | `backend/app/services/mongo.py` — llm_config collection |
| แก้ไข | `backend/app/services/summarizer.py` — load prompts from DB |
| แก้ไข | `frontend/src/pages/AdminDashboard.jsx` — LLM Settings tab |
| สร้างใหม่ | `frontend/src/components/admin/LLMConfigEditor.jsx` |

---

### Phase 16: Security Hardening & Performance Tuning 🔴 (Priority: สูง, ทำก่อน Production)

**เหตุผล:** ต้องผ่าน VA Pen Test และรับ production load ได้ก่อน go-live

#### Artifact 16.1: Security Hardening

**Backend:**
- **Input validation:** sanitize all user inputs, validate file types (magic bytes, not just extension)
- **Rate limiting:** เข้มขึ้น — login: 5 req/min, upload: 10 req/hour per user
- **JWT hardening:** short-lived access tokens (15 min) + refresh token rotation
- **Security headers:** X-Content-Type-Options, X-Frame-Options, CSP, HSTS (via Nginx)
- **Dependency audit:** `pip audit` / `npm audit` — fix critical CVEs
- **Secrets scanning:** ensure no secrets in git history
- **File upload security:**
  - Scan uploaded audio files (validate audio format, reject executables)
  - Store files with random UUIDs, never original filenames
  - Pre-signed URLs สำหรับ MinIO download (ไม่ expose direct URLs)

**VA Pen Test Checklist:**
- [ ] SQL/NoSQL Injection
- [ ] XSS (Cross-site scripting)
- [ ] IDOR (Insecure Direct Object Reference) — ตรวจสอบว่า user A ไม่เข้าถึงไฟล์ user B ได้
- [ ] Authentication bypass
- [ ] File upload vulnerabilities
- [ ] Exposed sensitive endpoints

#### Artifact 16.2: Performance Tuning ✅ COMPLETED (2026-05-28)

**สิ่งที่ทำ:**

**1. MongoDB Indexes** — เพิ่ม 8 indexes สำหรับ frequent queries:
| Collection | Index | Type |
|-----------|-------|------|
| `user` | `email` | unique |
| `quota` | `user_id` | single |
| `user_package` | `user_id` | unique |
| `package` | `name` | unique |
| `session` | `user_id` | single |
| `job` | `(user_id, status)` | compound |
| `job` | `status` | single |
| `voice_sample` | `user_id` | single |

**2. Redis Caching Layer** — `CacheService` ใช้ Redis DB 1 (แยกจาก Celery DB 0):
- Cache `get_user_package()` — ลด DB queries ทุก request ที่ต้อง check limits (TTL: 5 min)
- Cache `get_all_packages()` — package list ไม่ค่อยเปลี่ยน (TTL: 5 min)
- Auto-invalidate เมื่อ `assign_user_package()`, `increment_usage()`, `upsert_package()`
- Graceful degradation — ถ้า Redis ไม่พร้อมจะ fallback ไป query DB ตรงๆ

**3. Voice Samples Pagination** — เพิ่ม limit parameter:
- `get_voice_samples_by_user()` — default limit 100
- `get_voice_samples_with_embeddings()` — default limit 50

**4. WhisperX Batch Size** — configurable ผ่าน `WHISPERX_BATCH_SIZE` env var (default: 24)

**หมายเหตุ:** Celery concurrency คงไว้ที่ 1 (GPU constraint), async file I/O ไม่จำเป็นเพราะ heavy I/O อยู่ใน Celery worker

**Files ที่แก้/สร้าง:**
| Action | File | Change |
|--------|------|--------|
| สร้างใหม่ | `backend/app/services/cache.py` | Redis caching layer with JSON serialization |
| แก้ไข | `backend/app/services/mongo.py` | เพิ่ม 8 indexes + integrate cache ใน package/user_package queries |
| แก้ไข | `backend/app/core/config.py` | BATCH_SIZE อ่านจาก env var `WHISPERX_BATCH_SIZE` |
| แก้ไข | `backend/api.py` | สร้าง CacheService แล้ว inject เข้า MongoService |

**Files สำหรับ 16.1 (Security — ยังไม่ทำ):**
| Action | File |
|--------|------|
| สร้างใหม่ | `backend/app/middleware/security_headers.py` |
| แก้ไข | `backend/app/core/auth.py` — JWT refresh token support |
| แก้ไข | `backend/api.py` — เพิ่ม security middleware, ปรับ rate limits |
| สร้างใหม่ | `backend/scripts/security_audit.sh` — run dependency audit |
| แก้ไข | `nginx.conf` — security headers, HSTS |
| แก้ไข | `docker-compose.yml` — production security settings |

---

## สรุป New Collections ที่ต้องเพิ่มใน MongoDB

| Collection | จาก Phase | หน้าที่ |
|-----------|-----------|---------|
| `package` | Phase 2 | นิยาม packages + limits |
| `user_package` | Phase 2 | mapping user ↔ package + usage tracking |
| `voice_sample` | Phase 4 | voice clips + embeddings |
| `password_reset` | Phase 6 | reset tokens (TTL) |
| `activity_log` | Phase 8 | user action audit trail (TTL: 90 วัน) |
| `consent_record` | Phase 9 | PDPA consent history per user |
| `llm_config` | Phase 15 | admin-configurable LLM prompt templates |

## สรุป New MinIO Buckets

| Bucket | จาก Phase | หน้าที่ |
|--------|-----------|---------|
| `voice-samples` | Phase 4 | เก็บ voice enrollment clips |
| `db-backups` | Phase 11 | เก็บ MongoDB backup archives |

## สรุป New Environment Variables

| Variable | จาก Phase | หน้าที่ |
|----------|-----------|---------|
| `PII_ENCRYPTION_KEY` | Phase 3 | AES-256 master key |
| `PII_KEY_VERSION` | Phase 3 | Key rotation version |
| `GOOGLE_CLIENT_ID` | Phase 7 | Google OAuth |
| `GOOGLE_CLIENT_SECRET` | Phase 7 | Google OAuth |
| `BACKUP_RETENTION_DAYS` | Phase 11 | จำนวนวันเก็บ backup (default: 30) |
| `BACKUP_NOTIFY_EMAIL` | Phase 11 | Email แจ้งผล backup |
| `JWT_REFRESH_SECRET` | Phase 16 | Secret สำหรับ refresh tokens |

---

## Risk & Considerations

1. **PII Encryption Performance** — Field-level encryption จะเพิ่ม latency ในการ read/write user data. ควร cache decrypted data ใน memory (short-lived) สำหรับ frequent access patterns.

2. **Voice Embedding Storage** — PyAnnote embedding vectors มีขนาดใหญ่ (~512 floats per sample). ถ้ามี user จำนวนมาก ต้อง consider vector database (Qdrant/Milvus) แทน MongoDB.

3. **Package Enforcement** — ต้อง atomic check-and-increment usage เพื่อป้องกัน race condition (ใช้ MongoDB `findOneAndUpdate` with conditions).

4. **Migration** — ข้อมูล user เดิมที่เป็น plaintext ต้อง migrate เป็น encrypted. ต้องมี downtime หรือทำ rolling migration.

5. **Key Management** — PII encryption key ต้องไม่อยู่ใน git repo. ควรใช้ vault service (HashiCorp Vault, AWS KMS) สำหรับ production.

6. **Google SSO + Approval Flow** — ถ้า Google SSO user ต้องรอ approve จะเป็น UX ที่แย่. ควร auto-approve Google SSO users หรือมี separate flow.

7. **Activity Log Volume** — ถ้า user มีจำนวนมาก activity_log จะโตเร็ว. ต้องมี TTL index (90 วัน) และ archive strategy สำหรับ compliance records ที่ต้องเก็บนานกว่า.

8. **Multilingual WhisperX** — การ auto-detect language มี accuracy ต่ำกว่าการระบุ language ตรงๆ โดยเฉพาะ Thai-English code-switching. ควร default เป็น "th" และให้ user เลือกเอง.

9. **Sub-agenda Detection Accuracy** — GPT-4.1 detect agenda boundaries อาจไม่แม่นยำสำหรับการประชุมที่ไม่เป็นทางการ. ควรเป็น optional feature และให้ user ปรับแก้ได้.

10. **Backup Restore Process** — ต้องมี runbook สำหรับ restore จาก backup — ทดสอบ restore process ก่อน production go-live.

11. **VA Pen Test Scope** — ควรทำ pen test กับ environment ที่ใกล้เคียง production มากที่สุด รวมถึง Docker network, MinIO access controls, และ MongoDB authentication.

--