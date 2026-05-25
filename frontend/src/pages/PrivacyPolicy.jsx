import React from 'react';
import { useNavigate } from 'react-router-dom';

export default function PrivacyPolicy() {
    const navigate = useNavigate();
    return (
        <div className="policy-page">
            <div className="policy-container">
                <button className="policy-back-btn" onClick={() => navigate(-1)}>← กลับ</button>
                <h1>นโยบายความเป็นส่วนตัว</h1>
                <p className="policy-version">เวอร์ชัน 1.0 | มีผลบังคับใช้: 2026-05-24</p>

                <section>
                    <h2>1. ข้อมูลที่เราเก็บรวบรวม</h2>
                    <p>TimSum V3 เก็บรวบรวมข้อมูลดังต่อไปนี้เพื่อการให้บริการ:</p>
                    <ul>
                        <li><strong>ข้อมูลบัญชี:</strong> ชื่อ, นามสกุล, อีเมล, เบอร์โทรศัพท์, องค์กร</li>
                        <li><strong>ข้อมูลการใช้งาน:</strong> ไฟล์เสียงที่อัปโหลด, Transcript, สรุปการประชุม</li>
                        <li><strong>ข้อมูลทางเทคนิค:</strong> IP address, ประวัติการเข้าใช้งาน</li>
                        <li><strong>ตัวอย่างเสียง:</strong> Voice samples สำหรับระบุตัวตนผู้พูด (Voice Enrollment)</li>
                    </ul>
                </section>

                <section>
                    <h2>2. วัตถุประสงค์ในการใช้ข้อมูล</h2>
                    <ul>
                        <li>ให้บริการถอดเสียงและสรุปการประชุม</li>
                        <li>ยืนยันตัวตนและจัดการบัญชีผู้ใช้</li>
                        <li>ติดต่อสื่อสารเกี่ยวกับการใช้งาน</li>
                        <li>ปรับปรุงคุณภาพบริการ</li>
                    </ul>
                </section>

                <section>
                    <h2>3. การเก็บรักษาข้อมูล</h2>
                    <p>
                        ข้อมูลบัญชีเก็บตลอดอายุการใช้งาน ไฟล์เสียงลบทิ้งหลังประมวลผลเสร็จ
                        ประวัติ Transcript และสรุปเก็บสูงสุด 1 ปี Log การใช้งานเก็บ 90 วัน
                    </p>
                </section>

                <section>
                    <h2>4. การเปิดเผยข้อมูล</h2>
                    <p>
                        ข้อมูลของคุณจะไม่ถูกขายหรือเปิดเผยให้บุคคลที่สาม ยกเว้นกรณีที่กฎหมายกำหนด
                        หรือได้รับความยินยอมจากคุณอย่างชัดเจน
                    </p>
                </section>

                <section>
                    <h2>5. สิทธิของเจ้าของข้อมูล (PDPA)</h2>
                    <ul>
                        <li>สิทธิในการเข้าถึงข้อมูล</li>
                        <li>สิทธิในการแก้ไขข้อมูล</li>
                        <li>สิทธิในการลบข้อมูล ("สิทธิที่จะถูกลืม")</li>
                        <li>สิทธิในการถอนความยินยอม</li>
                        <li>สิทธิในการคัดค้านการประมวลผล</li>
                    </ul>
                    <p>ติดต่อใช้สิทธิ์ได้ที่ผู้ดูแลระบบ</p>
                </section>

                <section>
                    <h2>6. ติดต่อเรา</h2>
                    <p>หากมีคำถามเกี่ยวกับนโยบายนี้ กรุณาติดต่อผู้ดูแลระบบของคุณ</p>
                </section>
            </div>
        </div>
    );
}
