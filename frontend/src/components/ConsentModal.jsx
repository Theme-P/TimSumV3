import React, { useState } from 'react';

const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

export default function ConsentModal({ token, onConsented }) {
    const [marketing, setMarketing] = useState(false);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState('');

    const handleSubmit = async () => {
        setLoading(true);
        setError('');
        try {
            const res = await fetch(`${API_BASE}/api/consent`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
                body: JSON.stringify({
                    consents: {
                        privacy_policy: true,
                        data_processing: true,
                        marketing,
                    },
                }),
            });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || 'เกิดข้อผิดพลาด');
            }
            onConsented();
        } catch (e) {
            setError(e.message);
        } finally {
            setLoading(false);
        }
    };

    return (
        <div className="consent-overlay">
            <div className="consent-modal">
                <div className="consent-header">
                    <h2>ยินยอมการใช้งาน</h2>
                    <p className="consent-subtitle">กรุณาอ่านและยินยอมเงื่อนไขด้านล่างเพื่อเริ่มใช้งาน TimSum V3</p>
                </div>

                <div className="consent-body">
                    <div className="consent-item required">
                        <div className="consent-check">
                            <input type="checkbox" id="privacy" checked readOnly />
                            <label htmlFor="privacy">
                                <strong>นโยบายความเป็นส่วนตัว</strong>
                                <span className="required-badge">จำเป็น</span>
                            </label>
                        </div>
                        <p className="consent-desc">
                            ข้าพเจ้ายินยอมให้ TimSum เก็บรวบรวมและใช้ข้อมูลส่วนบุคคล (ชื่อ, อีเมล, เบอร์โทร, องค์กร)
                            เพื่อการให้บริการตามที่ระบุใน{' '}
                            <a href="/privacy-policy" target="_blank" rel="noopener noreferrer">นโยบายความเป็นส่วนตัว</a>
                        </p>
                    </div>

                    <div className="consent-item required">
                        <div className="consent-check">
                            <input type="checkbox" id="data_processing" checked readOnly />
                            <label htmlFor="data_processing">
                                <strong>การประมวลผลข้อมูลเสียงและ Transcript</strong>
                                <span className="required-badge">จำเป็น</span>
                            </label>
                        </div>
                        <p className="consent-desc">
                            ข้าพเจ้ายินยอมให้ระบบประมวลผลไฟล์เสียงและสร้าง Transcript / สรุปการประชุม
                            โดยข้อมูลเสียงจะถูกเก็บไว้ชั่วคราวระหว่างการประมวลผลเท่านั้น
                            ดูรายละเอียดเพิ่มเติมใน{' '}
                            <a href="/terms" target="_blank" rel="noopener noreferrer">เงื่อนไขการใช้งาน</a>
                        </p>
                    </div>

                    <div className="consent-item optional">
                        <div className="consent-check">
                            <input
                                type="checkbox"
                                id="marketing"
                                checked={marketing}
                                onChange={e => setMarketing(e.target.checked)}
                            />
                            <label htmlFor="marketing">
                                <strong>รับข้อมูลข่าวสารและโปรโมชั่น</strong>
                                <span className="optional-badge">ไม่บังคับ</span>
                            </label>
                        </div>
                        <p className="consent-desc">
                            ข้าพเจ้ายินยอมรับอีเมลข้อมูลข่าวสาร อัปเดตฟีเจอร์ใหม่ และโปรโมชั่นจาก TimSum
                        </p>
                    </div>

                    {error && <p className="consent-error">{error}</p>}
                </div>

                <div className="consent-footer">
                    <p className="consent-pdpa-note">
                        การยินยอมนี้เป็นไปตาม พ.ร.บ. คุ้มครองข้อมูลส่วนบุคคล (PDPA) พ.ศ. 2562
                    </p>
                    <button
                        className="consent-btn-accept"
                        onClick={handleSubmit}
                        disabled={loading}
                    >
                        {loading ? 'กำลังบันทึก...' : 'ยินยอมและเริ่มใช้งาน'}
                    </button>
                </div>
            </div>
        </div>
    );
}
