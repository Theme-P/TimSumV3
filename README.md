# TimSumV3

> ระบบถอดเสียงการประชุมและสรุปอัตโนมัติ ด้วย WhisperX + GPT-4.1
>
> Full-stack: React frontend, FastAPI backend, Celery GPU worker, MongoDB, Redis, MinIO

## Features

- **Speech-to-Text** — WhisperX (large-v3) พร้อม word-level alignment รองรับภาษาไทย-อังกฤษ
- **Speaker Diarization** — แยกผู้พูดอัตโนมัติด้วย pyannote พร้อมตัดคลิปเสียง ~10 วินาทีต่อคน
- **AI Summary** — สรุปการประชุมด้วย GPT-4.1 ผ่าน NTC AI Gateway รองรับ hierarchical chunking สำหรับประชุมยาว
- **11 Meeting Types** — เลือกหรือตรวจจับอัตโนมัติ พร้อม prompt เฉพาะแต่ละประเภท
- **Speaker Identification** — ฟังเสียงตัวอย่างแล้วกรอกชื่อ-ตำแหน่ง แทนที่ทั้ง transcript และ summary
- **DOCX Export** — ส่งออก Transcript และ Summary เป็นไฟล์ Word
- **Authentication** — JWT-based login, role-based access (admin/user)
- **Async Processing** — Celery + Redis job queue สำหรับ GPU processing แบบ non-blocking
- **Session History** — ดูผลการประชุมย้อนหลังได้

## Architecture

```
┌──────────────────────────────────────────┐
│          Frontend (React + Nginx)        │
│               Port 3000                  │
└─────────────────┬────────────────────────┘
                  │ /api/*
                  ▼
┌──────────────────────────────────────────┐
│          Backend (FastAPI + Uvicorn)     │
│               Port 8000                  │
│   REST API · JWT Auth · Rate Limiting   │
└───┬──────────┬──────────┬────────────────┘
    │          │          │
    ▼          ▼          ▼
┌────────┐ ┌───────┐ ┌────────┐
│MongoDB │ │ Redis │ │ MinIO  │
│  Jobs  │ │Celery │ │ Audio  │
│Sessions│ │Broker │ │ Clips  │
│ Users  │ │       │ │        │
└────────┘ └───┬───┘ └────────┘
               │
               ▼
┌──────────────────────────────────────────┐
│     Celery Worker (GPU · CUDA 12.1)     │
│  WhisperX → Diarize → Clip → Summarize │
└──────────────────────────────────────────┘
```

## Supported Meeting Types

| # | ประเภท | English | โครงสร้าง |
|---|--------|---------|-----------|
| 1 | ประชุมผู้ถือหุ้น | Shareholder Meeting | วาระ → มติ → เงินปันผล |
| 2 | ประชุมคณะกรรมการ | Board Meeting | นโยบาย → การอนุมัติ → มติ |
| 3 | ประชุมวางแผน | Planning Meeting | เป้าหมาย → แผนงาน → ไทม์ไลน์ |
| 4 | รายงานความคืบหน้า | Progress Update | สถานะ → ปัญหา → แนวทางแก้ |
| 5 | ประชุมเชิงกลยุทธ์ | Strategy Meeting | ทิศทาง → กลยุทธ์ → Action Plan |
| 6 | ประชุมแก้ไขปัญหา | Incident Review | ปัญหา → สาเหตุ → การป้องกัน |
| 7 | ประชุมลูกค้า | Client Meeting | ข้อเสนอ → Feedback → Next Steps |
| 8 | เชิงปฏิบัติการ | Workshop | หัวข้อ → บทเรียน → Action Items |
| 9 | ประชุมผู้บริหาร | Executive Meeting | การตัดสินใจ → มติ |
| 10 | ประชุมทีมงาน | Team Meeting | อัพเดต → มอบหมาย → ปัญหา |
| 11 | ประชุมทั่วไป | General Meeting | วาระ → หารือ → มติ |

> เลือก `0` เพื่อให้ระบบตรวจจับประเภทอัตโนมัติ

## Quick Start

### 1. Clone & Configure

```bash
git clone https://github.com/Theme-P/TimSumV3.git
cd TimSumV3

cp .env.example .env
# แก้ไข .env ใส่ API keys และ passwords ที่ต้องการ
```

### 2. Run with Docker Compose

```bash
docker compose up -d --build

# Frontend:  http://localhost:3000
# Backend:   http://localhost:8000
# MinIO:     http://localhost:9001
```

### Prerequisites

- Docker + Docker Compose
- NVIDIA GPU + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/)
- Hugging Face token (สำหรับ pyannote speaker diarization)
- NTC AI Gateway API key (สำหรับ GPT-4.1 summarization)

## Environment Variables

สร้างไฟล์ `.env` จาก `.env.example`:

| Variable | Description |
|----------|-------------|
| `HF_TOKEN` | Hugging Face token สำหรับ speaker diarization |
| `NTC_API_KEY` | NTC AI Gateway API key สำหรับ GPT-4.1 |
| `NTC_API_URL` | NTC API endpoint |
| `NTC_MODEL` | Model name (default: `gpt-4.1`) |
| `MONGO_USER` / `MONGO_PASS` | MongoDB credentials |
| `REDIS_PASSWORD` | Redis password |
| `MINIO_USER` / `MINIO_PASS` | MinIO credentials (min 8 chars) |
| `JWT_SECRET_KEY` | JWT signing secret (generate: `python -c "import secrets; print(secrets.token_hex(32))"`) |
| `ALLOWED_ORIGINS` | CORS origins (comma-separated) |
| `MAX_UPLOAD_MB` | Max upload size in MB (default: 500) |

## API Endpoints

| Method | Endpoint | Auth | Description |
|--------|----------|:----:|-------------|
| `GET` | `/api/health` | - | Health check |
| `GET` | `/api/meeting-types` | - | รายการประเภทการประชุม |
| `POST` | `/api/auth/login` | - | Login → JWT token |
| `POST` | `/api/auth/register` | Admin | สร้างผู้ใช้ใหม่ |
| `GET` | `/api/quota` | Yes | ดึงข้อมูล quota |
| `POST` | `/api/transcribe-summarize` | Yes | อัพโหลดเสียง → async job |
| `GET` | `/api/jobs/{job_id}` | Yes | ดูสถานะ job (polling) |
| `GET` | `/api/jobs/{job_id}/result` | Yes | ดึงผลลัพธ์เมื่อเสร็จ |
| `POST` | `/api/export/transcript` | Yes | Export transcript → DOCX |
| `POST` | `/api/export/summary` | Yes | Export summary → DOCX |
| `GET` | `/api/speaker-clip/{session_id}/{filename}` | Yes | Stream audio clip |
| `DELETE` | `/api/session/{session_id}` | Yes | ลบ clips ของ session |
| `POST` | `/api/email-results` | Yes | ส่ง DOCX ทางอีเมล |
| `GET` | `/api/history` | Yes | ประวัติการประชุม |
| `GET` | `/api/history/{session_id}` | Yes | รายละเอียด session |

## Pipeline Flow

```
User uploads audio (Frontend)
    ↓
POST /api/transcribe-summarize → Upload to MinIO → Create Job → Celery task
    ↓
[Celery Worker - GPU]
├─ Load WhisperX model (large-v3, float16)
├─ Transcribe audio (auto-detect language)
├─ Word-level alignment (better speaker boundaries)
├─ Speaker diarization (pyannote)
├─ Extract ~10s audio clip per speaker (ffmpeg)
├─ Detect speaker names from self-introductions (GPT-4.1)
├─ Summarize with meeting type context (GPT-4.1)
├─ Upload clips to MinIO
└─ Save session to MongoDB
    ↓
Frontend polls job status every 3s
    ↓
Display: Transcript + Summary + Speaker Clips
    ↓
User identifies speakers (listen & name)
    ↓
Export DOCX
```

## Project Structure

```
TimSumV3/
├── backend/
│   ├── app/
│   │   ├── core/
│   │   │   ├── auth.py              # JWT validation
│   │   │   └── config.py            # Pipeline & email config
│   │   ├── models/
│   │   │   ├── meeting.py           # 11 meeting type definitions + prompts
│   │   │   └── user.py              # User, UserData, Quota models
│   │   ├── routers/
│   │   │   ├── auth.py              # /api/auth/login, /api/auth/register
│   │   │   └── quota.py             # /api/quota
│   │   ├── services/
│   │   │   ├── db.py                # Worker MongoDB singleton
│   │   │   ├── email_service.py     # SMTP email with DOCX attachments
│   │   │   ├── mongo.py             # MongoService (users, jobs, sessions)
│   │   │   ├── pipeline.py          # TranscribeSummaryPipeline (WhisperX)
│   │   │   ├── storage.py           # MinIO storage service
│   │   │   ├── summarizer.py        # GPT-4.1 summary + classification
│   │   │   └── text_cleaner.py      # ASR noise/repetition cleanup
│   │   ├── tasks/
│   │   │   └── transcription.py     # Celery async task
│   │   ├── utils/
│   │   │   ├── audio_clip.py        # Speaker clip extraction (ffmpeg)
│   │   │   ├── export.py            # DOCX generation
│   │   │   └── formatting.py        # Thai speaker labels, time format
│   │   └── celery_app.py            # Celery configuration
│   ├── scripts/
│   │   ├── create_admin.py          # Admin user creation
│   │   └── init_mongo.js            # MongoDB indexes + TTL
│   ├── api.py                       # FastAPI application
│   ├── main.py                      # CLI entry point
│   ├── Dockerfile                   # CUDA 12.1 base image
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── FileUploader.jsx     # Drag & drop upload
│   │   │   ├── HistoryView.jsx      # Session history
│   │   │   ├── MeetingTypeSelect.jsx
│   │   │   ├── ProcessingStatus.jsx # Progress bar + steps
│   │   │   ├── ProtectedRoute.jsx   # JWT route guard
│   │   │   ├── ResultsTabs.jsx      # Transcript/Summary/Stats tabs
│   │   │   └── SpeakerIdentification.jsx
│   │   ├── contexts/
│   │   │   └── AuthContext.jsx      # Token management
│   │   ├── pages/
│   │   │   ├── Login.jsx
│   │   │   └── MainApp.jsx
│   │   ├── styles/
│   │   │   ├── index.css            # Main styles (cream theme)
│   │   │   └── Login.css
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── Dockerfile                   # Node build + Nginx
│   ├── nginx.conf                   # SPA routing + API proxy
│   └── package.json
├── audio/                           # Audio files (gitignored)
├── docker-compose.yml               # All 6 services
├── .env.example                     # Template environment variables
├── deploy.sh                        # Deployment script
└── DEPLOY.md                        # Deployment guide
```

## Docker Services

| Service | Image | Port | Description |
|---------|-------|------|-------------|
| `mongo` | mongo:latest | 27017 (internal) | Database (users, jobs, sessions) |
| `redis` | redis:7-alpine | 6379 (internal) | Celery broker + result backend |
| `minio` | minio/minio:latest | 9001 (console) | Object storage (audio, clips) |
| `backend` | CUDA 12.1 + Python | 8000 | FastAPI API server |
| `worker` | CUDA 12.1 + Python | - | Celery GPU worker (concurrency=1) |
| `frontend` | Node 20 + Nginx | 3000 | React SPA |

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| Model | `large-v3` | WhisperX model |
| Compute Type | `float16` | GPU precision |
| Batch Size | `24` | Transcription batch size |
| Beam Size | `5` | Beam search width |
| Language | `auto-detect` | Supports Thai, English, mixed |
| VAD Onset | `0.500` | Speech start threshold |
| VAD Offset | `0.363` | Speech end threshold |
| Max Upload | `500 MB` | File size limit |
| JWT Expiry | `8 hours` | Token lifetime |
| Job TTL | `30 days` | Auto-cleanup old jobs |

## Tech Stack

**Backend:** Python 3.10, FastAPI, Celery, WhisperX, PyAnnote, PyMongo, MinIO SDK, PyJWT

**Frontend:** React 18, React Router 7, Vite 6

**Infrastructure:** Docker, NVIDIA CUDA 12.1, MongoDB, Redis, MinIO, Nginx

## License

MIT License
