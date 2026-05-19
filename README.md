# TimSum V3 — ระบบถอดเสียงและสรุปการประชุมด้วย AI

> ระบบถอดเสียงประชุมและสรุปอัตโนมัติระดับองค์กร รองรับภาษาไทย-อังกฤษ พร้อม Speaker Diarization, JWT Auth, Package System และ Admin Dashboard

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React 18 + Vite 6 + React Router 7 |
| Backend | FastAPI + Celery + Redis |
| AI/ML | WhisperX (large-v3) + PyAnnote + GPT-4.1 (NTC Gateway) |
| Database | MongoDB |
| Storage | MinIO (S3-compatible) |
| Deploy | Docker Compose (6 containers) + NVIDIA GPU Worker |

---

## Features ที่ Implement แล้ว

### 🔐 Authentication & Authorization
- JWT-based login/logout
- **3-level role system:** `superadmin` / `admin` / `user`
- Public user registration (status = `pending` → ต้องรอ Admin approve)
- Admin blocks non-approved users พร้อม Thai error messages
- Token expiry auto-logout

### 👥 User Management (Admin)
- Admin Dashboard (`/admin`) — protected route
- Approve / Reject / Suspend users
- User stats: pending, approved, rejected, suspended
- Package assignment ให้ user แต่ละคน

### 📦 Package System
| Package | ราคา | ถอดเสียง/เดือน | ไฟล์/เดือน |
|---------|------|:--------------:|:---------:|
| TimSumBasic | 200 ฿/เดือน | 180 นาที | 6 ไฟล์ |
| TimSumPro | 400 ฿/เดือน | 900 นาที | 20 ไฟล์ |
| TimSumEnterprise | 1,000 ฿/เดือน | 3,200 นาที | 250 ไฟล์ |
| TimSumEnterprise (yearly) | 6,000 ฿/ปี | 3,200 นาที | 250 ไฟล์ |

- Monthly usage auto-reset
- Atomic limit enforcement (403 เมื่อเกิน quota)
- Dynamic Package Badge ใน navbar
- Usage progress bars ใน Settings (เตือนเมื่อ ≥ 80%)

### 🎙️ Audio Processing Pipeline
- Async processing ด้วย Celery + Redis (polling 5 steps)
- WhisperX large-v3 — word-level alignment
- PyAnnote speaker diarization (auto-detect speaker count)
- Speaker identification จาก self-introduction ด้วย GPT-4.1
- Speaker audio clip extraction (~10s per speaker)
- Text cleaning (noise removal, repetitive phrases)

### 📝 AI Summarization
- 11 ประเภทการประชุม + Auto-detect
- Summarization ด้วย GPT-4.1 (NTC Gateway)
- Export to DOCX (transcript + summary)
- Email delivery พร้อม DOCX attachment (Unicode filename support)
- Session history (list + detail view)

### 🎨 UI/UX
- Dark / Light / System theme
- Real-time processing status (5 steps, 0–100%)
- Speaker identification editable UI
- Responsive layout

---

## Architecture — 6 Docker Containers

```
Browser
  │
  ├─► [timsumv3-frontend]  React + Vite dev server  :5173
  │         │ /api/* proxy
  ├─► [timsumv3-backend]   FastAPI                   :8000
  │         │
  │    ┌────┴────┐
  │    ▼         ▼
  │ [redis]   [mongo]     (internal network only)
  │    │
  │    └──► [timsumv3-worker]  Celery + GPU Worker
  │
  └─► [timsumv3-minio]   Object Storage console      :9001
```

---

## Quick Start

### Prerequisites
- Docker + Docker Compose
- NVIDIA GPU + nvidia-container-toolkit (สำหรับ worker)

### 1. Clone & Setup Environment

```bash
git clone <repo-url>
cd TimSumV3
cp .env.example .env
# แก้ไข .env ตามความต้องการ
```

### 2. Start All Services

```bash
sudo docker compose up -d
```

### 3. เข้าใช้งาน

| Service | URL |
|---------|-----|
| Frontend | http://localhost:5173 |
| Backend API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| MinIO Console | http://localhost:9001 |

---

## Default Admin Account

Admin account จะถูกสร้างอัตโนมัติจาก `.env`:

```
Email:    ADMIN_EMAIL (ค่าใน .env)
Password: ADMIN_PASSWORD (ค่าใน .env)
Role:     superadmin
```

ถ้าต้องการสร้าง admin ด้วย script:
```bash
sudo docker compose exec backend python scripts/create_admin.py
```

---

## Environment Variables (.env)

```env
# MongoDB
MONGO_USER=admin
MONGO_PASS=your_mongo_password

# Redis
REDIS_PASSWORD=your_redis_password

# MinIO
MINIO_USER=minioadmin
MINIO_PASS=your_minio_password

# JWT
JWT_SECRET=your_jwt_secret_key

# Admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=your_admin_password

# AI (NTC Gateway)
OPENAI_API_KEY=your_api_key
OPENAI_BASE_URL=https://api.ntc.ai/v1

# Hugging Face (PyAnnote)
HF_TOKEN=your_hf_token

# Email (SMTP)
SMTP_HOST=smtp.example.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASS=your_smtp_password
SMTP_FROM=noreply@example.com
```

---

## API Endpoints

### Authentication
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| POST | `/api/auth/login` | — | Login → JWT token |
| POST | `/api/auth/register-public` | — | Public registration (pending) |

### User
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/user/package` | User | Package + usage (auto monthly reset) |
| GET | `/api/packages` | User | List public packages |

### Audio Processing
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| POST | `/api/transcribe-summarize` | User | Submit audio job |
| GET | `/api/job/{job_id}` | User | Poll job status |
| GET | `/api/history` | User | Session history |
| GET | `/api/history/{session_id}` | User | Session detail |

### Admin
| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| GET | `/api/admin/users` | Admin | List users (filter by status) |
| GET | `/api/admin/users/stats` | Admin | Count by status |
| PUT | `/api/admin/users/{id}/approve` | Admin | Approve user |
| PUT | `/api/admin/users/{id}/reject` | Admin | Reject user |
| PUT | `/api/admin/users/{id}/suspend` | Admin | Suspend user |
| PUT | `/api/admin/users/{id}/package` | Admin | Assign package |
| GET | `/api/admin/packages` | Admin | List all packages |

---

## Development

### Hot Reload (Dev Mode)
`docker-compose.override.yml` จะถูก merge อัตโนมัติ:
- **Frontend:** Vite dev server พร้อม HMR volume mount
- **Backend:** uvicorn `--reload`
- **Worker:** volume mount (restart manual หลังแก้ code)

```bash
# Restart worker หลังแก้ code
sudo docker compose restart worker
```

### Production Mode (ไม่ใช้ override)
```bash
sudo docker compose -f docker-compose.yml up -d
```

---

## Project Roadmap

| Phase | Feature | Status |
|:-----:|---------|:------:|
| 1 | User Registration & Admin Approval | ✅ Done |
| 2 | Package System & Usage Tracking | ✅ Done |
| 3 | PDPA — PII Encryption at Rest | 🔴 Todo |
| 4 | Voice Enrollment (Speaker Library) | 🔴 Todo |
| 5 | Custom Summary Prompt | 🔴 Todo |
| 6 | User Profile & Password Management | 🔴 Todo |
| 7 | Google SSO | 🟠 Low priority |

ดูรายละเอียดแผนงานเต็มที่ [ARTIFACT_PLANNING.md](./ARTIFACT_PLANNING.md)

---

## License

Internal use only — NTC / TimSum Project
