# 🚀 Deployment Guide - Summary-Transcribe

## สถาปัตยกรรม

```
┌──────────────────────────────────────────────────────┐
│                   GPU Server (SSH)                    │
├─────────────────────┬────────────────────────────────┤
│  Frontend (Nginx)   │    Backend (FastAPI + GPU)     │
│  Port: 3000         │    Port: 8000                  │
│  - React build      │    - WhisperX                  │
│  - Proxy to API     │    - CUDA/cuDNN                │
└─────────────────────┴────────────────────────────────┘
```

---

## วิธี Deploy

### 1. อัพโหลดไฟล์ไปยัง Server

```bash
# บน Windows (PowerShell)
scp -r . user@your-server:/path/to/Summary-Transcribe
```

หรือใช้ Git:
```bash
# บน Server
git clone <your-repo-url>
cd Summary-Transcribe
```

### 2. เตรียม Environment

```bash
# สร้างไฟล์ .env
cp .env.example .env
nano .env  # แก้ไข HF_TOKEN, OPENAI_API_KEY
```

### 3. Deploy

```bash
# ให้สิทธิ์ execute
chmod +x deploy.sh

# รัน deploy
./deploy.sh
```

หรือรันด้วย docker-compose โดยตรง:
```bash
docker-compose up -d --build
```

---

## เข้าถึง

| Service | URL |
|---------|-----|
| Frontend | `http://your-server:3000` |
| Backend API | `http://your-server:8000/docs` |

---

## คำสั่งที่มีประโยชน์

```bash
# ดู status
docker-compose ps

# ดู logs
docker-compose logs -f
docker-compose logs -f backend
docker-compose logs -f frontend

# Restart
docker-compose restart

# Stop
docker-compose down

# Rebuild (หลังแก้ไขโค้ด)
docker-compose up -d --build
```

---

## Troubleshooting

### GPU ไม่ทำงาน
```bash
# ตรวจสอบ NVIDIA runtime
nvidia-smi
docker run --rm --gpus all nvidia/cuda:12.1.1-base-ubuntu22.04 nvidia-smi
```

### Port ถูกใช้งานอยู่
```bash
# ตรวจสอบ port
sudo lsof -i :3000
sudo lsof -i :8000
```
