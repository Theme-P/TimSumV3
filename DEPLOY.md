# 🚀 Deployment Guide — TimSumV3

> คู่มือ Deploy สำหรับคนที่ Fork ไป — ครอบคลุมทุกสิ่งที่ต้องแก้ไขก่อนรันได้

---

## สถาปัตยกรรม (6 Docker Containers)

```
Browser
  │
  ├─► [timsumv3-frontend]   React + Nginx         :3000 (prod) / :5173 (dev)
  │         │ /api/* proxy → backend
  ├─► [timsumv3-backend]    FastAPI (API + Auth)   :8000
  │         │
  │    ┌────┴────┐
  │    ▼         ▼
  │ [redis]   [mongo]       (internal network only, ไม่เปิด port)
  │    │
  │    └──► [timsumv3-worker]  Celery + GPU (WhisperX)
  │
  └─► [timsumv3-minio]      Object Storage console  :9001
```

---

## ⚠️ สิ่งที่ต้องแก้ไขก่อนรัน (Checklist สำหรับคน Fork)

### 1. สร้างไฟล์ `.env` จาก Template

```bash
cp .env.example .env
```

### 2. แก้ไขค่าใน `.env` (จำเป็นต้องแก้ทุกรายการที่มี ❗)

| ตัวแปร | คำอธิบาย | ต้องแก้? |
|--------|---------|:--------:|
| `HF_TOKEN` | Hugging Face Token สำหรับ pyannote speaker diarization | ❗ **ต้องแก้** |
| `NTC_API_KEY` | API Key ของ NTC AI Gateway (GPT-4.1) สำหรับสรุป | ❗ **ต้องแก้** |
| `NTC_API_URL` | URL endpoint ของ NTC AI Gateway | ✅ ใช้ค่า default ได้ |
| `NTC_MODEL` | ชื่อ Model ที่ใช้ | ✅ ใช้ค่า default ได้ |
| `MONGO_USER` | MongoDB admin username | 🔒 แนะนำให้แก้ |
| `MONGO_PASS` | MongoDB admin password | ❗ **ต้องแก้** |
| `JWT_SECRET_KEY` | Secret key สำหรับ sign JWT token | ❗ **ต้องแก้** |
| `REDIS_PASSWORD` | Redis password | ❗ **ต้องแก้** |
| `MINIO_USER` | MinIO admin username | 🔒 แนะนำให้แก้ |
| `MINIO_PASS` | MinIO admin password (อย่างน้อย 8 ตัวอักษร) | ❗ **ต้องแก้** |
| `SUPERADMIN_PASS` | รหัสผ่าน superadmin | 🔒 แนะนำให้แก้ |
| `ADMIN_PASS` | รหัสผ่าน admin | 🔒 แนะนำให้แก้ |
| `ALLOWED_ORIGINS` | CORS origins (ถ้า deploy ขึ้น server จริง ต้องเปลี่ยน) | ❗ **ต้องแก้ ถ้า deploy จริง** |
| `GOOGLE_CLIENT_ID` | Google OAuth Client ID (Optional — ถ้าไม่ใช้ SSO ไม่ต้องใส่) | ✅ Optional |
| `SMTP_SERVER` | SMTP server สำหรับส่งอีเมล (Optional) | ✅ Optional |

#### 📌 วิธี Generate ค่าสำคัญ

```bash
# สร้าง JWT_SECRET_KEY
python3 -c "import secrets; print(secrets.token_hex(32))"

# สร้าง password แบบ random
python3 -c "import secrets; print(secrets.token_urlsafe(16))"
```

#### 📌 วิธีขอ HF_TOKEN

1. ไปที่ https://huggingface.co/settings/tokens
2. สร้าง Access Token ใหม่ (Read permission)
3. ต้อง **Accept License Agreement** ที่:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0

#### 📌 วิธีขอ NTC_API_KEY

- ติดต่อ NTC ICT Solution เพื่อขอ API Key สำหรับ GPT-4.1
- หรือเปลี่ยนไปใช้ OpenAI API โดยแก้ `NTC_API_URL` เป็น `https://api.openai.com/v1/chat/completions` และใส่ OpenAI API Key ใน `NTC_API_KEY`

### 3. ⚠️ ข้อควรระวัง: `.env` มี 2 ค่าที่ **ไม่ต้องแก้**

ค่าต่อไปนี้ใน `.env.example` จะถูก **docker-compose.yml override** อัตโนมัติ — ไม่ต้องแก้ไข:

| ตัวแปร | เหตุผลที่ไม่ต้องแก้ |
|--------|-------------------|
| `MONGO_CONNECTION_STRING` | docker-compose สร้าง connection string จาก `MONGO_USER` + `MONGO_PASS` ให้อัตโนมัติ |
| `REDIS_URL` | docker-compose สร้าง Redis URL จาก `REDIS_PASSWORD` ให้อัตโนมัติ |

> ⚡ ดังนั้นแค่แก้ `MONGO_PASS` และ `REDIS_PASSWORD` ก็พอ ไม่ต้องไปยุ่งกับ connection string

### 4. (Optional) เพิ่ม `GOOGLE_CLIENT_ID` ใน `.env`

ถ้าต้องการ Google SSO ให้เพิ่มบรรทัดนี้ใน `.env`:

```env
GOOGLE_CLIENT_ID=your_google_client_id_here
```

ถ้าไม่ต้องการ ระบบจะ disable Google SSO อัตโนมัติ (ปลอดภัย — แค่มี warning ตอน docker-compose ที่ไม่ส่งผลต่อการทำงาน)

---

## 📋 Prerequisites (ความต้องการของ Server)

| ความต้องการ | รายละเอียด |
|------------|-----------|
| **Docker** | Docker Engine 20+ |
| **Docker Compose** | v2.x (มาพร้อมกับ Docker Desktop) |
| **NVIDIA GPU** | จำเป็นสำหรับ Worker container (WhisperX) |
| **NVIDIA Container Toolkit** | `nvidia-container-toolkit` ต้องติดตั้งแล้ว |
| **RAM** | แนะนำ ≥ 16 GB |
| **Disk** | ≥ 20 GB สำหรับ Docker images + model cache |

### ตรวจสอบ GPU

```bash
# ตรวจสอบว่า GPU ใช้ได้
nvidia-smi

# ตรวจสอบว่า Docker เห็น GPU
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

---

## วิธี Deploy

### 1. Clone โปรเจกต์

```bash
git clone <your-repo-url>
cd TimSumV3
```

### 2. เตรียม Environment

```bash
# สร้างไฟล์ .env
cp .env.example .env

# แก้ไขค่าตาม Checklist ด้านบน
nano .env
```

### 3. Deploy — Production Mode

```bash
# ให้สิทธิ์ execute
chmod +x deploy.sh

# รัน deploy (ใช้เฉพาะ docker-compose.yml — ไม่รวม dev override)
./deploy.sh
```

หรือรันด้วย docker-compose โดยตรง:
```bash
sudo docker compose -f docker-compose.yml up -d --build
```

### 4. Deploy — Development Mode (Hot Reload)

```bash
# ใช้ทั้ง docker-compose.yml + docker-compose.override.yml
sudo docker compose up -d --build
```

> **ความแตกต่าง Dev vs Prod:**
>
> | | Production | Development |
> |--|-----------|-------------|
> | Frontend | Nginx serve static build `:3000` | Vite dev server + HMR `:5173` |
> | Backend | uvicorn (2 workers) | uvicorn `--reload` |
> | Worker | ไม่ mount volume | mount `./backend:/app` |

---

## 🌐 เข้าถึงระบบ

### Production Mode

| Service | URL |
|---------|-----|
| Frontend | `http://your-server:3000` |
| Backend API Docs | `http://your-server:8000/docs` |
| MinIO Console | `http://your-server:9001` |

### Development Mode

| Service | URL |
|---------|-----|
| Frontend (Vite) | `http://your-server:5173` |
| Backend API Docs | `http://your-server:8000/docs` |
| MinIO Console | `http://your-server:9001` |

---

## 👤 Default Accounts

ระบบจะสร้าง user อัตโนมัติจาก `.env` ตอน startup ครั้งแรก:

| Role | Email | Password (default) |
|------|-------|-------------------|
| **superadmin** | `superadmin@timsumv3.local` | `TimSum@SuperAdmin2026` |
| **admin** | `admin@timsumv3.local` | `TimSum@Admin2026` |

> ⚠️ **ต้องเปลี่ยนรหัสผ่าน** ถ้า deploy ขึ้น production!

---

## ⚠️ CORS — กรณี Deploy ขึ้น Server จริง

ถ้า frontend ถูก access ผ่าน domain/IP อื่นที่ไม่ใช่ `localhost:3000` ต้องแก้ `ALLOWED_ORIGINS` ใน `.env`:

```env
# ตัวอย่าง: อนุญาตหลาย origins (คั่นด้วย comma)
ALLOWED_ORIGINS=http://your-server:3000,https://timsum.yourcompany.com
```

> หมายเหตุ: ใน Production mode (Nginx) frontend จะ proxy `/api/*` ไปที่ backend container ภายใน Docker network โดยตรง ดังนั้น CORS อาจไม่จำเป็น ถ้า frontend กับ backend อยู่บน origin เดียวกัน

---

## 📂 โครงสร้างไฟล์สำคัญ

```
TimSumV3/
├── .env                        # ⚠️ ไม่ push ขึ้น git (อยู่ใน .gitignore)
├── .env.example                # Template สำหรับสร้าง .env
├── docker-compose.yml          # Production config (6 containers)
├── docker-compose.override.yml # Dev overrides (auto-merge เมื่อ `docker compose up`)
├── deploy.sh                   # Production deploy script
│
├── backend/
│   ├── Dockerfile              # CUDA 12.1 + Python 3.10 + WhisperX
│   ├── requirements.txt        # Python dependencies
│   ├── api.py                  # FastAPI app entry point
│   ├── app/
│   │   ├── celery_app.py       # Celery configuration (Redis broker)
│   │   ├── core/               # Auth middleware, config
│   │   ├── models/             # User, Package, Meeting type models
│   │   ├── routers/            # Auth, Admin, Quota, Package, User routes
│   │   ├── services/           # MongoDB, MinIO, Pipeline, Summarizer
│   │   ├── tasks/              # Celery tasks (transcription)
│   │   └── utils/              # Export DOCX, audio clip, formatting
│   └── scripts/
│       ├── init_mongo.js       # MongoDB indexes (runs on first start)
│       └── create_admin.py     # Manual admin creation script
│
└── frontend/
    ├── Dockerfile              # Multi-stage: Node build → Nginx serve
    ├── Dockerfile.dev          # Dev: Vite dev server
    ├── nginx.conf              # Nginx config with /api proxy
    ├── package.json            # React 18 + Vite 6
    └── src/
        ├── App.jsx             # Routes: Login, Register, MainApp, Admin
        ├── pages/              # Login, Register, MainApp, AdminDashboard
        ├── components/         # UI components
        ├── contexts/           # AuthContext, ThemeContext
        └── styles/             # CSS
```

---

## คำสั่งที่มีประโยชน์

```bash
# ดู status ของ containers
sudo docker compose -f docker-compose.yml ps

# ดู logs ทั้งหมด
sudo docker compose -f docker-compose.yml logs -f

# ดู logs เฉพาะ service
sudo docker compose -f docker-compose.yml logs -f backend
sudo docker compose -f docker-compose.yml logs -f worker
sudo docker compose -f docker-compose.yml logs -f frontend

# Restart ทั้งหมด
sudo docker compose -f docker-compose.yml restart

# Restart เฉพาะ worker (หลังแก้ code)
sudo docker compose restart worker

# Stop ทั้งหมด
sudo docker compose -f docker-compose.yml down

# Rebuild + restart (หลังแก้ไขโค้ด)
sudo docker compose -f docker-compose.yml up -d --build

# เข้าไปใน container
sudo docker compose exec backend bash
sudo docker compose exec worker bash

# สร้าง admin ด้วย script
sudo docker compose exec backend python scripts/create_admin.py
```

---

## Troubleshooting

### ❌ GPU ไม่ทำงาน / Worker crash

```bash
# ตรวจสอบ NVIDIA runtime
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi

# ตรวจสอบ nvidia-container-toolkit
dpkg -l | grep nvidia-container-toolkit
```

ถ้า Worker ใช้ GPU ไม่ได้ ตรวจสอบว่า:
1. ติดตั้ง `nvidia-container-toolkit` แล้ว
2. Docker daemon ถูก config ให้ใช้ nvidia runtime
3. `docker-compose.yml` มี `runtime: nvidia` ใน worker service

### ❌ Port ถูกใช้งานอยู่

```bash
sudo lsof -i :3000
sudo lsof -i :8000
sudo lsof -i :9001
```

### ❌ Backend ไม่สามารถเชื่อมต่อ MongoDB

```bash
# ดู mongo logs
sudo docker compose -f docker-compose.yml logs mongo

# ตรวจสอบว่า MONGO_PASS ใน .env ตรงกัน
# (docker-compose.yml จะ override MONGO_CONNECTION_STRING อัตโนมัติ)
```

### ❌ Warning: "GOOGLE_CLIENT_ID variable is not set"

เป็นแค่ warning ไม่ส่งผลต่อการทำงาน — Google SSO จะถูก disable อัตโนมัติ
ถ้าต้องการปิด warning ให้เพิ่มใน `.env`:

```env
GOOGLE_CLIENT_ID=
```

### ❌ WhisperX model download ช้า / ล้มเหลว

ครั้งแรกที่รัน Worker จะ download model ~3GB จาก Hugging Face
- ตรวจสอบ `HF_TOKEN` ว่าถูกต้อง
- Model จะถูก cache ใน Docker volume `whisperx_cache` (ไม่ต้อง download ซ้ำ)
- ถ้าต้องการ clear cache: `sudo docker volume rm timsumv3_whisperx_cache`

### ❌ ลืมรหัสผ่าน Admin

```bash
# สร้าง admin ใหม่
sudo docker compose exec backend python scripts/create_admin.py
```

---

## 🔒 Security Notes สำหรับ Production

1. **เปลี่ยนรหัสผ่านทั้งหมด** — อย่าใช้ค่า default จาก `.env.example`
2. **Generate JWT_SECRET_KEY ใหม่** — ใช้ `python -c "import secrets; print(secrets.token_hex(32))"`
3. **ปิด port ที่ไม่จำเป็น** — MinIO console (9001) ไม่ควรเปิดให้ภายนอกเข้าถึง
4. **ตั้งค่า ALLOWED_ORIGINS** ให้ตรงกับ domain ที่ใช้จริง
5. **ใช้ HTTPS** — ติดตั้ง reverse proxy (Nginx/Caddy) ข้างหน้าพร้อม SSL certificate
6. **Backup MongoDB** — ตั้ง cron job สำหรับ `mongodump`

---

## 📊 ผลการทดสอบ (Testing Results)

> ทดสอบล่าสุด: 2026-05-21

| ✅ ทดสอบ | ผลลัพธ์ |
|---------|--------|
| Docker Compose build (production mode) | ✅ ผ่าน — 6 containers build + start สำเร็จ |
| Container health checks | ✅ ผ่าน — ทุก container สถานะ healthy |
| Backend API `/api/health` | ✅ ผ่าน — `{"status":"healthy"}` |
| Frontend (Nginx) `:3000` | ✅ ผ่าน — HTTP 200, serve SPA สำเร็จ |
| Frontend → Backend proxy `/api/*` | ✅ ผ่าน — Nginx proxy ไปยัง backend ได้ |
| Login API (superadmin) | ✅ ผ่าน — ได้ JWT token |
| Meeting Types API | ✅ ผ่าน — 12 ประเภท (0-11) |
| User Package API | ✅ ผ่าน — TimSumSuperAdmin package |
| Admin Stats API | ✅ ผ่าน — user count by status |
| Packages API | ✅ ผ่าน — 4 public packages |
| History API | ✅ ผ่าน — empty sessions (ยังไม่มีข้อมูล) |
| Google SSO (disabled) | ✅ ผ่าน — `{"enabled":false}` |
| Celery Worker | ✅ ผ่าน — connected to Redis, ready |
| MinIO Storage | ✅ ผ่าน — console accessible `:9001` |
