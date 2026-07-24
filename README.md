# TimSum V3

ระบบถอดเสียงและสรุปการประชุมด้วย FastAPI, React, Celery, WhisperX, MongoDB,
Redis และ MinIO รองรับ speaker diarization, async summary, History, DOCX,
email, package quota, voice enrollment, consent และ RBAC 3 ระดับ

## อัพเดทล่าสุด (Recent Updates)

จากการพัฒนาล่าสุด ระบบได้เพิ่มฟีเจอร์และปรับปรุงประสิทธิภาพในหลายส่วน:

- **Pipeline & ประสิทธิภาพ:**
  - ใช้ Threaded pool สำหรับ Summary Worker
  - เพิ่มระบบสรุปผลแยกตาม Segment แบบ Asynchronous
  - ปรับปรุงการแสดงผลสรุปแบบ Partial streaming และ Chunk coverage ให้ดียิ่งขึ้น
  - เพิ่มระบบ Hybrid sub-agenda auto-separation และการสรุปแยกตามแต่ละ Agenda อัตโนมัติ
- **การจัดการและ Admin:**
  - เพิ่มการตั้งค่า Admin LLM และ LLM Rule-based Templates
  - เพิ่มระบบฟิลเตอร์แยกตามผู้ใช้และเวลาในหน้า Activity log และระบบ Queue monitoring
- **การรองรับภาษา:**
  - เพิ่มการรองรับ Multilingual support พร้อมโหมดซ่อน Mixed mode และบังคับสรุปเป็นภาษาไทย
- **ความปลอดภัยและ UX/UI:**
  - ปรับปรุง Backend flow และขัดเกลา Frontend UI / Admin UX
  - เพิ่มความปลอดภัยในการจัดการข้อมูล, ระบบ Auth และ Transcription queue
  - นำตัวเลือก Marketing consent ออกเพื่อความกระชับ

## สถาปัตยกรรมปัจจุบัน

Production เปิดต่อสาธารณะเฉพาะ Caddy ที่พอร์ต `80/443` เท่านั้น:

```text
Browser -- HTTPS --> Caddy
                       |-- /api/* --> FastAPI (internal :8000)
                       `-- /*      --> React/Nginx (internal :80)

FastAPI --> MongoDB + Redis + MinIO (internal)
Celery  --> transcription worker (GPU, concurrency 1)
        --> summary worker (CPU/API)
        --> maintenance worker + Celery beat
```

บริการหลักมี 10 service: `caddy`, `frontend`, `backend`, `worker`,
`summary-worker`, `maintenance-worker`, `celery-beat`, `mongo`, `redis` และ
`minio`; `backup` เป็น profile เพิ่มเติมสำหรับ encrypted off-host backup

## เริ่ม Development

ข้อกำหนด: Docker Compose v2, NVIDIA Container Toolkit และ GPU สำหรับ worker

```bash
cp .env.example .env
chmod 600 .env
# แก้ HF_TOKEN, NTC_API_KEY และ secret ทุกค่า
docker compose config --quiet
docker compose up -d --build
```

Development override เปิด Vite ที่ `http://localhost:9443` โดยตั้งใจให้เป็น
HTTP ไม่ใช่ TLS ส่วน backend, Mongo, Redis และ MinIO data port ไม่เปิดตรงสู่ host
MinIO console เปิดเฉพาะ `http://127.0.0.1:9001`

## Production

อ่าน [DEPLOY.md](./DEPLOY.md) ก่อน deploy โดยเฉพาะ backup/restore gate,
workflow migration และ immutable image/WhisperX pins

- `TLS_MODE=internal`: เหมาะกับ LAN ต้องติดตั้ง Caddy root CA จาก volume
  `caddy_data` บน client ทุกเครื่อง
- `TLS_MODE=acme`: ใช้ domain จริงที่ DNS ชี้มาหา server และเปิด 80/443
- `PUBLIC_FRONTEND_URL` ต้องเป็น `https://...` และเป็นฐานเดียวสำหรับ login/reset URL
- `deploy.sh` ไม่ทำ `git pull`, ไม่ `down` และไม่ใช้ `--no-cache`; script build ก่อน
  cutover, ใช้ `up -d --wait` และเก็บ release tag ก่อนหน้าสำหรับ rollback

## สร้างผู้ดูแลครั้งแรก

ระบบไม่สร้าง Admin/Superadmin อัตโนมัติและไม่มีรหัสผ่าน default:

```bash
docker compose exec backend python scripts/create_admin.py \
  --role superadmin --username "Operations" --email ops@example.com
```

รหัสผ่านต้องยาวอย่างน้อย 12 ตัวและอ่านจาก TTY เท่านั้น สำหรับ automation ให้
mount secret แล้วใช้ `--password-file /run/secrets/admin-password` ห้ามส่ง password
ผ่าน command line หรือ environment

## Processing flow

1. API ตรวจ JWT version, required consent, package entitlement, rate limit และ quota
2. สร้าง job ID ก่อน reserve quota แล้ว upload raw audio ไป MinIO
3. GPU worker สร้าง canonical cleaned segments และ transcript artifact
4. Summary state/checkpoint ถูกบันทึกก่อน publish summary task และลบ raw audio
5. Summary finalizer upsert History ด้วย `session.job_id`, CAS job terminal แล้วสร้าง
   email outbox/cleanup work แบบ retry ได้
6. Maintenance worker reconcile stale lease, cancellation, retention, outbox และ
   asynchronous account deletion

Production default คือ `SUMMARY_PIPELINE_MODE=async`; inline path เก็บไว้เป็น fallback
หนึ่ง release ห้ามเพิ่ม GPU worker concurrency บน GPU เดียวโดยไม่มี benchmark จริง

## API สำคัญ

| Method | Endpoint | ความหมาย |
|---|---|---|
| `POST` | `/api/auth/login` | login และรับ versioned JWT |
| `POST` | `/api/transcribe-summarize` | ส่ง async job; ต้องมี consent/entitlement |
| `GET` | `/api/jobs/{job_id}` | สถานะงาน |
| `GET` | `/api/jobs/{job_id}/result` | ผลงานที่พร้อม/partial |
| `GET` | `/api/history` | History ของผู้ใช้ |
| `DELETE` | `/api/history/{session_id}` | ลบ History และ speaker clips ของเจ้าของ |
| `DELETE` | `/api/admin/users/{id}` | เริ่ม account deletion และตอบ `202` |
| `GET` | `/api/admin/deletions/{id}` | ติดตาม deletion manifest |
| `GET` | `/api/health` | liveness |
| `GET` | `/api/health/ready` | Mongo/Redis/MinIO readiness |

`/api/quota` เป็น legacy endpoint ที่ mark deprecated และเก็บ telemetry หนึ่ง release

## Retention เริ่มต้น

- raw audio: ลบทันทีเมื่อ downstream checkpoint durable; failsafe 48 ชั่วโมง
- transcript artifact: ลบหลัง terminal; failsafe 7 วัน
- speaker clips: 30 วัน
- Transcript/Summary History: 365 วัน
- job/summary diagnostics: 30 วัน
- activity log: 90 วัน
- password reset: TTL ตาม `expires_at`
- voice enrollment: จนผู้ใช้ลบเองหรือลบบัญชี
- pseudonymous consent audit: 365 วัน

ดูรายละเอียด PII, key rotation, off-host backup และ restore drill ที่
[docs/ENCRYPTION_BACKUP_GUIDE.md](./docs/ENCRYPTION_BACKUP_GUIDE.md)

## Validation

```bash
python3 -m compileall -q backend
PYTHONPATH=backend pytest -q backend/tests
cd frontend && npm ci && npm run lint && npm run test:run && npm run build
docker compose -f docker-compose.yml config --quiet
git diff --check
```

Live LLM check ถูกแยกจาก automated tests:

```bash
PYTHONPATH=backend python backend/scripts/smoke_llm_gateway.py
```

## CLI

`backend/main.py` เป็น documented interactive local-audio CLI สำหรับเครื่องที่มี
WhisperX/GPU dependencies ครบ ไม่ใช่ API entrypoint

## License

Internal use only — NTC / TimSum Project
