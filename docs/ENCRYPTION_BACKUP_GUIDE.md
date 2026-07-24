# TimSumV3 — PII Encryption & Daily MongoDB Backup Guide

คู่มือนี้ครอบคลุมการเปิดใช้ PII encryption, migration ข้อมูลเดิม, key rotation,
daily backup เวลา 02:00 น. และการทดสอบ restore

## สิ่งที่ระบบทำให้แล้ว

- เข้ารหัส `user.email`, `username`, `first_name`, `last_name`, `phone` และ
  `organization` ด้วย AES-256-GCM
- ใช้ keyed HMAC blind index (`email_bidx`) สำหรับ login และตรวจ email ซ้ำ
- รองรับ key หลาย version และ migration แบบไม่ต้องหยุดระบบ
- ไม่ใส่ email/username ใน JWT ใหม่
- เก็บ password-reset token เป็น SHA-256 hash แทน plaintext
- แยก backup container ออกจาก GPU worker
- `mongodump --archive --gzip` → เข้ารหัสด้วย `age` → upload ไป off-host S3/MinIO
- เปิด MinIO versioning, GOVERNANCE retention และ lifecycle ตามจำนวนวันที่กำหนด
- ดาวน์โหลดไฟล์ backup กลับมาตรวจ SHA-256 ทุกครั้งก่อนรายงานว่าสำเร็จ

> ขอบเขต PII encryption รอบนี้คือข้อมูลใน `user` collection เท่านั้น
> transcript, summary, filename, IP address และ voice metadata ใน live database
> ยังไม่ได้เข้ารหัสราย field แต่ไฟล์ database backup ทั้งก้อนถูกเข้ารหัสด้วย `age`

## ข้อควรระวังก่อนเริ่ม

1. Commit หรือเก็บ source code ปัจจุบันไว้ก่อน
2. ตั้ง permission ของไฟล์ environment:

   ```bash
   chmod 600 .env
   ```

3. Rotate JWT/Mongo/Redis/MinIO secret ที่เคยเปิดเผย และใช้ one-time CLI สร้างผู้ดูแล
4. ห้ามเก็บ PII key หรือ `age` private identity ใน Git
5. ต้องทำและทดสอบ backup ก่อนรัน PII migration

ลำดับใช้งานที่ปลอดภัยคือ **ตั้ง backup → ทดสอบ restore → เปิด encryption →
migrate → finalize**

## 1. ตั้งค่า Encrypted Daily Backup

### 1.1 สร้าง age identity บนเครื่องผู้ดูแล

ติดตั้ง `age` บนเครื่องที่ใช้กู้คืน แล้วรัน:

```bash
age-keygen -o backup-identity.txt
chmod 600 backup-identity.txt
age-keygen -y backup-identity.txt
```

คำสั่งสุดท้ายจะแสดง public recipient ที่ขึ้นต้นด้วย `age1...` ให้นำไปใส่ `.env`:

```env
BACKUP_AGE_RECIPIENT=age1...
BACKUP_S3_ENDPOINT=https://backup-storage.example.org
BACKUP_BUCKET=db-backups
BACKUP_RETENTION_DAYS=30
BACKUP_SCHEDULE_HOUR=2
BACKUP_SCHEDULE_MINUTE=0
BACKUP_RUN_ON_STARTUP=true
BACKUP_CONFIGURE_BUCKET=true
BACKUP_MAX_AGE_HOURS=26
```

เก็บ `backup-identity.txt` แบบ offline อย่างน้อยสองชุดในสถานที่แยกกัน
backup container ใช้เฉพาะ public recipient และไม่สามารถถอดรหัส backup ได้

### 1.2 สร้าง MongoDB backup user

สร้างรหัสแบบสุ่มและเพิ่มใน `.env`:

```env
MONGO_BACKUP_USER=timsum_backup
MONGO_BACKUP_PASS=<strong-random-password>
MONGO_BACKUP_USE_OPLOG=false
```

สำหรับ database volume เดิม ให้ recreate เฉพาะ Mongo container เพื่อส่ง environment
ใหม่เข้าไป แล้วรัน script แบบ idempotent:

```bash
docker compose up -d --force-recreate mongo
docker compose exec mongo bash -lc \
  'mongosh -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin /docker-entrypoint-initdb.d/create_backup_user.js'
```

ผู้ใช้ที่สร้างจะมี MongoDB built-in role `backup` เท่านั้น สำหรับ installation ใหม่
script นี้จะรันอัตโนมัติระหว่าง initialize database

หากยังไม่กำหนด `MONGO_BACKUP_USER/PASS` ตัว backup จะ fallback ไปใช้ root database
credential เพื่อให้ทดสอบได้ แต่ไม่ควรใช้วิธีนี้ใน production

### 1.3 Build และทดสอบ backup ครั้งแรก

```bash
docker compose --profile backup build backup
docker compose --profile backup run --rm -e BACKUP_ONCE=true backup
```

ปลายทางต้องอยู่นอกเครื่องและนอก volume ของ TimSumV3; production script จะปฏิเสธ
localhost และ object store `minio:9000` ของแอป ต้องเห็นข้อความ
`Backup completed and verified` จึงถือว่าสำเร็จ ครั้งแรก service
จะสร้าง bucket `db-backups`, เปิด versioning, ตั้ง WORM GOVERNANCE retention และ
lifecycle cleanup ให้อัตโนมัติ

เริ่ม scheduler:

```bash
docker compose --profile backup up -d backup
docker compose --profile backup logs -f backup
```

ตรวจรายการไฟล์ผ่าน console ของ off-host storage หรือ `mc ls --recursive` ไฟล์จะอยู่ใต้:

```text
db-backups/daily/YYYY/MM/timsumv3_YYYYMMDDTHHMMSS+0700.archive.gz.age
```

### 1.4 Limited object-store service account สำหรับ production

Provision bucket/retention ด้วยผู้ดูแลปลายทางก่อน จากนั้นสร้าง service account ที่
เข้าถึงเฉพาะ `db-backups` แล้วตั้ง:

```env
BACKUP_MINIO_ACCESS_KEY=<limited-access-key>
BACKUP_MINIO_SECRET_KEY=<limited-secret-key>
BACKUP_CONFIGURE_BUCKET=false
```

Production บังคับ dedicated credential นี้และไม่ fallback ไปใช้ MinIO root credential
สิทธิ์ขั้นต่ำหลัง provision คือ list bucket, put/get/stat object และอ่าน checksum
ส่วน lifecycle/retention ให้ผู้ดูแล MinIO เป็นผู้จัดการ

## 2. เปิดใช้ PII Encryption

### 2.1 สร้าง keys

สร้าง key สองครั้ง ห้ามใช้ค่าเดียวกัน:

```bash
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
python3 -c "import base64,secrets; print(base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())"
```

นำ key แรกเป็น encryption key และ key ที่สองเป็น blind-index key:

```env
PII_ENCRYPTION_ENABLED=true
PII_ACTIVE_KEY_VERSION=1
PII_ENCRYPTION_KEYS={"1":"<encryption-key>"}
PII_BLIND_INDEX_KEY=<different-blind-index-key>
PII_ALLOW_LEGACY_PLAINTEXT=true
```

`PII_BLIND_INDEX_KEY` ไม่ควร rotate พร้อม encryption key เพราะจะทำให้ email lookup
ทั้งหมดต้องสร้าง index ใหม่ หากจำเป็นต้อง rotate ให้ใช้ maintenance window และวางแผน
dual blind index แยกต่างหาก

### 2.2 Deploy โค้ดแบบอ่านข้อมูลเก่าได้

```bash
./deploy.sh
```

หากพบ `ModuleNotFoundError: No module named 'cryptography'` แปลว่า container
ยังใช้ image เก่า ให้ rebuild และ recreate เฉพาะ backend โดยไม่แตะ service อื่น:

```bash
sudo docker compose up -d --no-deps --build --force-recreate backend
sudo docker compose logs --tail=100 backend
sudo docker compose exec -T backend python -c "import cryptography; print(cryptography.__version__)"
```

ควรเห็น backend เริ่มทำงานสำเร็จ, health check ตอบ `200 OK` และคำสั่งสุดท้าย
แสดงเวอร์ชันของ `cryptography`

เมื่อ `PII_ALLOW_LEGACY_PLAINTEXT=true` ระบบจะ:

- เขียน user ใหม่เป็น ciphertext
- อ่านได้ทั้ง user เก่าแบบ plaintext และ user ใหม่แบบ encrypted
- ค้น email ผ่านทั้ง blind index และ legacy email ชั่วคราว

### 2.3 Dry run และ migrate

ก่อน apply ให้สร้าง backup manual อีกครั้ง:

```bash
docker compose --profile backup run --rm -e BACKUP_ONCE=true backup
```

ตรวจ duplicate email/key configuration โดยไม่เขียนข้อมูล:

```bash
docker compose exec backend python scripts/migrate_encrypt_pii.py
```

ถ้า `Preflight OK` ให้ apply:

```bash
docker compose exec backend python scripts/migrate_encrypt_pii.py --apply
```

Migration เป็นแบบ idempotent จึงรันซ้ำได้ และจะ re-encrypt เฉพาะ record ที่ยังเป็น
plaintext หรือใช้ key version เก่า

### 2.4 Verify และ finalize

```bash
docker compose exec backend python scripts/migrate_encrypt_pii.py --finalize
```

คำสั่งนี้จะไม่ finalize หากยังมี plaintext/stale record และจะลบ unique index
`email` แบบเก่าหลังตรวจครบแล้ว จากนั้นตั้ง:

```env
PII_ALLOW_LEGACY_PLAINTEXT=false
```

แล้ว restart backend:

```bash
docker compose up -d --force-recreate backend
```

ทดสอบอย่างน้อย:

- login ด้วย email ตัวพิมพ์เล็ก/ใหญ่ต่างกัน
- register email ซ้ำต้องถูกปฏิเสธ
- forgot/reset password
- profile และหน้า admin user list แสดงข้อมูลได้

ตรวจ DB เพิ่มเติมได้ด้วย:

```javascript
db.user.countDocuments({email: {$type: "string"}}) // ต้องเป็น 0
db.user.countDocuments({email_bidx: {$type: "string"}}) // ต้องเท่าจำนวน user ที่มี email
```

## 3. Key Rotation

1. เก็บ key version 1 ไว้ และเพิ่ม version 2
2. เปลี่ยน active version เป็น 2:

   ```env
   PII_ACTIVE_KEY_VERSION=2
   PII_ENCRYPTION_KEYS={"1":"<old-key>","2":"<new-key>"}
   ```

3. Restart backend แล้วรัน:

   ```bash
   docker compose exec backend python scripts/migrate_encrypt_pii.py
   docker compose exec backend python scripts/migrate_encrypt_pii.py --apply
   docker compose exec backend python scripts/migrate_encrypt_pii.py --finalize
   ```

4. ทดสอบระบบและเก็บ backup ใหม่ ก่อนนำ key version 1 ออกจาก runtime
5. เก็บ old key ตาม retention ของ backup เก่า มิฉะนั้น restore backup เก่าแล้วจะอ่าน
   PII ไม่ได้

## 4. Restore Drill

ควรทำ restore drill อย่างน้อยเดือนละครั้ง และบันทึกเวลาที่ใช้จริง (RTO)

### 4.1 Download และถอดรหัส

ดาวน์โหลดไฟล์ `.age` และ `.sha256` จาก MinIO แล้วตรวจ:

```bash
sha256sum --check timsumv3_....archive.gz.age.sha256
age --decrypt -i backup-identity.txt \
  -o timsumv3_restore.archive.gz timsumv3_....archive.gz.age
```

### 4.2 Restore ลง temporary MongoDB ก่อนเสมอ

```bash
docker run -d --name timsum-restore-check <MONGO_TOOLS_IMAGE ที่ pin digest แล้ว>
docker cp timsumv3_restore.archive.gz timsum-restore-check:/tmp/restore.archive.gz
docker exec timsum-restore-check mongorestore \
  --archive=/tmp/restore.archive.gz --gzip --drop
docker exec timsum-restore-check mongosh timsumv3 \
  --eval 'printjson(db.getCollectionNames()); print(db.user.countDocuments({}))'
docker stop timsum-restore-check
docker rm timsum-restore-check
```

หาก backup สร้างด้วย `MONGO_BACKUP_USE_OPLOG=true` ให้เพิ่ม `--oplogReplay` ตอน restore

ก่อน restore production ต้องหยุด backend/worker, เก็บ backup ของสถานะปัจจุบัน และยืนยัน
ชื่อ database/namespace ให้ถูกต้อง การใช้ `--drop` จะลบ collection ปัจจุบัน

## 5. Operational Checklist

ตรวจทุกวัน:

- backup container ยัง healthy
- persisted `last_attempt_at` ไม่ fail และ `last_success_at` ไม่เก่ากว่า 26 ชั่วโมง
- log มี `Backup completed and verified`
- มีไฟล์ใหม่ใน `db-backups/daily/...`
- ขนาด backup ไม่ลดผิดปกติ

ตรวจทุกเดือน:

- restore ลง temporary MongoDB ผ่าน
- document counts/indexes สำคัญครบ
- login ด้วย restored data ได้ใน isolated environment
- age private identity และ PII keys ยังเปิดอ่านได้จากสำเนา offline

## ข้อจำกัดที่ต้องวางแผนต่อ

- MongoDB ปัจจุบันเป็น standalone ดังนั้น dump ระหว่างมี write ไม่รับประกัน snapshot
  consistency ข้าม collection ควรรันช่วง traffic ต่ำ หรือเปลี่ยนเป็น replica set แล้วตั้ง
  `MONGO_BACKUP_USE_OPLOG=true`
- Production backup ต้องชี้ `BACKUP_S3_ENDPOINT` ไป off-host storage; local MinIO ใช้
  เป็น application storage เท่านั้นและไม่นับเป็น backup
- การทำ MinIO backup ไม่รวมอยู่ใน database dump หากต้องเก็บ voice samples และ speaker
  clips ต้องเปิด MinIO bucket replication เพิ่ม
- การสูญเสีย PII encryption key หรือ age private identity จะทำให้ข้อมูลกู้คืนไม่ได้

เอกสารอ้างอิง:

- [MongoDB mongodump](https://www.mongodb.com/docs/database-tools/mongodump/)
- [MongoDB built-in backup role](https://www.mongodb.com/docs/manual/reference/built-in-roles/)
- [MinIO Object Lock](https://docs.min.io/aistor/administration/object-locking-and-immutability/)
- [MinIO Lifecycle Management](https://docs.min.io/aistor/administration/object-lifecycle-management/)
