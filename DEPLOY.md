# Deploy TimSumV3

คู่มือนี้เป็น production runbook สำหรับสถาปัตยกรรม Caddy + FastAPI + React +
Celery 3 worker roles + beat + MongoDB + Redis + MinIO ห้ามข้าม backup/restore และ
workflow migration gate

## 1. เตรียมค่า Production

```bash
cp .env.example .env
chmod 600 .env
```

ค่าหลักที่ต้องกำหนด:

```env
APP_ENV=production
PUBLIC_FRONTEND_URL=https://timsum.example.org
PUBLIC_HOST=timsum.example.org
TLS_MODE=acme                 # หรือ internal สำหรับ LAN
ALLOWED_ORIGINS=https://timsum.example.org
ALLOWED_ORIGIN_REGEX=
TRUSTED_PROXY_CIDRS=<CIDR ของ Compose/Caddy>

JWT_SECRET_KEY=<random อย่างน้อย 32 ตัว>
CONSENT_AUDIT_KEY=<stable random secret แยกจาก JWT>
MONGO_PASS=<rotated secret>
REDIS_PASSWORD=<rotated secret>
MINIO_PASS=<rotated secret>
RATE_LIMIT_REDIS_URL=redis://:<redis-password>@redis:6379/1

SUMMARY_PIPELINE_MODE=async
UPLOADS_ENABLED=true
```

`deploy.sh` บังคับ base images ทุกตัวเป็น `tag@sha256:<64 hex>` และบังคับ
`WHISPERX_COMMIT` เป็น commit 40 hex จาก known-good staging build ดูรายชื่อตัวแปร
ทั้งหมดใน `.env.example` ห้ามใช้ `latest`, branch หรือ Git HEAD ใน production

การเปลี่ยน Mongo/Redis/MinIO credential ของ installation เดิมต้องเปลี่ยน credential
ใน service ก่อนแล้วจึงเปลี่ยน `.env` แบบประสานกัน การแก้เฉพาะ `.env` จะทำให้ระบบต่อ
ข้อมูลเดิมไม่ได้ ส่วน JWT rotation ทำให้ session เดิมหมดอายุทันที

ตรวจ preflight:

```bash
test "$(stat -c %a .env)" = 600
docker compose -f docker-compose.yml config --quiet
python3 -m compileall -q backend
git diff --check
```

## 2. HTTPS edge

Production เปิด host port แค่ `80/443`; backend/frontend เป็น internal และ MinIO
console bind `127.0.0.1:9001`

### Domain/ACME

ตั้ง `TLS_MODE=acme`, ชี้ DNS ของ `PUBLIC_HOST` มาที่ server และเปิด inbound 80/443
Caddy จะขอ/ต่ออายุ certificate อัตโนมัติ

### LAN/internal CA

ตั้ง `TLS_MODE=internal` และ `PUBLIC_HOST` เป็นชื่อ DNS/hosts ที่ client ใช้จริง เมื่อ
Caddy เริ่มแล้วให้ export root CA:

```bash
docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt ./timsum-caddy-root.crt
```

ติดตั้ง CA นี้ใน trust store ของ client ทุกเครื่องก่อนเข้า
`https://<PUBLIC_HOST>` ห้ามเรียก port `9443` ว่า HTTPS: port นี้เป็น Vite HTTP สำหรับ
development เท่านั้น (`http://<server>:9443`)

MinIO console ใช้ SSH tunnel:

```bash
ssh -L 9001:127.0.0.1:9001 <server>
# เปิด http://127.0.0.1:9001 บนเครื่องผู้ดูแล
```

## 3. Backup/restore gate

Production backup ต้องใช้ Mongo user สำหรับ backup โดยเฉพาะ, limited S3 credential,
`age` public recipient และ `BACKUP_S3_ENDPOINT=https://...` ที่อยู่นอกเครื่อง/volume
ของ TimSumV3 ตัว script จะปฏิเสธ localhost หรือ `minio:9000`

```bash
docker compose --profile backup build backup
docker compose --profile backup run --rm -e BACKUP_ONCE=true backup
docker compose --profile backup up -d backup
docker compose --profile backup ps
```

Health ของ backup อ่าน persisted `last_attempt_at/last_success_at`; จะ unhealthy เมื่อ
ครั้งล่าสุด fail หรือ success เก่ากว่า `BACKUP_MAX_AGE_HOURS` (default 26 ชั่วโมง)

ดาวน์โหลด `.age` + `.sha256`, ตรวจ checksum, ถอดรหัสด้วย private identity ที่เก็บ
offline และ restore ลง MongoDB ชั่วคราวเท่านั้นตาม
[docs/ENCRYPTION_BACKUP_GUIDE.md](./docs/ENCRYPTION_BACKUP_GUIDE.md) ต้องตรวจ collection,
indexes และ login ใน isolated environment หาก restore ไม่ผ่าน ให้หยุด deployment

## 4. Workflow v2 migration gate

ห้ามมี old/new writers พร้อมกัน:

1. ตั้ง `UPLOADS_ENABLED=false` แล้ว recreate เฉพาะ backend
2. รอ queue transcription/summary จบ หรือยกเลิกผ่าน API
3. รัน check-only; conflict ใด ๆ ต้อง reconcile ด้วยมือ ห้ามลบอัตโนมัติ
4. apply migration และสร้าง indexes
5. deploy API + transcription + summary + maintenance + beat พร้อมกัน
6. รัน reconciler แล้วเปิด upload

```bash
docker compose -f docker-compose.yml up -d --no-deps --force-recreate backend
docker compose exec backend python scripts/migrate_workflow_v2.py --check
docker compose exec backend python scripts/migrate_workflow_v2.py --apply

./deploy.sh

docker compose exec maintenance-worker \
  celery -A app.celery_app:celery_app call maintenance.reconcile

# เปลี่ยน UPLOADS_ENABLED=true แล้ว recreate backend เมื่อ smoke ผ่าน
docker compose -f docker-compose.yml up -d --no-deps --force-recreate backend
```

Migration จะ backfill `session.job_id`, workflow/checkpoint fields, quota ledger,
auth-version forced logout marker และ TTL/unique indexes หากพบ duplicate หรือ owner
mismatch จะ exit non-zero

## 5. Deploy และ rollback

`deploy.sh` ไม่ pull source, ไม่หยุด stack ก่อน build และไม่ใช้ no-cache:

```bash
RELEASE_TAG=2026-07-22.1 ./deploy.sh
```

Script ทำ security/config preflight, build app images ก่อน cutover, เรียก
`docker compose up -d --wait` และเก็บ tag ใน `.deploy-state/current-release`; หาก
cutover fail จะลองใช้ app image tag ก่อนหน้าโดยไม่ rollback database migration

ตรวจหลัง deploy:

```bash
docker compose -f docker-compose.yml ps
curl --fail https://<PUBLIC_HOST>/api/health
curl --fail https://<PUBLIC_HOST>/api/health/ready
docker compose logs --tail=200 backend worker summary-worker maintenance-worker celery-beat
```

Backend และ MinIO data port ต้องเข้าโดยตรงจาก LAN/public ไม่ได้ ตรวจ worker health
แยก node `transcription@...`, `summary@...`, `maintenance@...`

## 6. Bootstrap account

ไม่มี account default และ startup ไม่สร้าง account:

```bash
docker compose exec backend python scripts/create_admin.py \
  --role superadmin --username "Operations" --email ops@example.com
```

ใช้ TTY หรือ `--password-file` เท่านั้น password อย่างน้อย 12 ตัว

## 7. PII cutover

ลำดับบังคับ:

1. off-host backup + restore drill ผ่าน
2. deploy code ที่อ่าน plaintext/ciphertext ได้
3. `PII_ENCRYPTION_ENABLED=true`, `PII_ALLOW_LEGACY_PLAINTEXT=true`
4. dry run → apply → verify → finalize
5. `PII_ALLOW_LEGACY_PLAINTEXT=false`, `PII_CUTOVER_COMPLETE=true`

```bash
docker compose exec backend python scripts/migrate_encrypt_pii.py
docker compose exec backend python scripts/migrate_encrypt_pii.py --apply
docker compose exec backend python scripts/migrate_encrypt_pii.py --finalize
```

หลัง cutover startup จะ fail หากปิด encryption/เปิด legacy หรือขาด off-host backup
config เก็บ encryption key รุ่นเก่าตลอด retention ของ backup; rollback ได้เฉพาะ image
ที่รองรับ ciphertext

## 8. Release smoke gate

ก่อนเปิด traffic:

```bash
PYTHONPATH=backend pytest -q backend/tests
cd frontend && npm ci && npm run lint && npm run test:run && npm run build
docker compose -f docker-compose.yml config --quiet
```

บน staging ต้องทดสอบ Host/Origin poisoning, reset token reuse, old JWT revocation,
consent/entitlement/RBAC 403, rate limit 429 + Retry-After, duplicate task, cancellation,
account deletion retry, backup health failure และ short real-audio async flow อย่างน้อย
หนึ่งรอบ จากนั้นเฝ้า stuck jobs, duplicate sessions, cleanup backlog และ quota mismatch
24–48 ชั่วโมงก่อน dead-code removal/release ถัดไป
