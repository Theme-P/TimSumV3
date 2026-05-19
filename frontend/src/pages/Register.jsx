import { useState } from 'react';
import { useNavigate, Link } from 'react-router-dom';
import '../styles/Login.css';

const API_BASE = '/api';

const Register = () => {
    const [form, setForm] = useState({
        first_name: '',
        last_name: '',
        email: '',
        phone: '',
        organization: '',
        password: '',
        confirmPassword: '',
    });
    const [error, setError] = useState('');
    const [success, setSuccess] = useState(false);
    const [isLoading, setIsLoading] = useState(false);

    const navigate = useNavigate();

    const handleChange = (e) => {
        setForm({ ...form, [e.target.name]: e.target.value });
    };

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');

        if (form.password !== form.confirmPassword) {
            setError('รหัสผ่านไม่ตรงกัน');
            return;
        }
        if (form.password.length < 8) {
            setError('รหัสผ่านต้องมีอย่างน้อย 8 ตัวอักษร');
            return;
        }

        setIsLoading(true);
        try {
            const response = await fetch(`${API_BASE}/auth/register-public`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    first_name: form.first_name,
                    last_name: form.last_name,
                    email: form.email,
                    phone: form.phone || null,
                    organization: form.organization || null,
                    password: form.password,
                }),
            });

            const data = await response.json();
            if (!response.ok) {
                throw new Error(data.detail || 'เกิดข้อผิดพลาดในการลงทะเบียน');
            }

            setSuccess(true);
        } catch (err) {
            setError(err.message);
        } finally {
            setIsLoading(false);
        }
    };

    // Success screen
    if (success) {
        return (
            <div className="login-split-container">
                <div className="login-left-panel">
                    <div className="login-grid-bg" />
                    <div className="login-left-content">
                        <div className="login-brand">
                            <div className="login-brand-logo">
                                <span className="brand-tim">Tim</span><span className="brand-sum">Sum</span>
                            </div>
                            <p className="login-brand-tagline">ระบบสรุปการประชุมอัจฉริยะ ระดับองค์กร</p>
                        </div>
                    </div>
                </div>
                <div className="login-right-panel">
                    <div className="login-form-wrapper" style={{ textAlign: 'center' }}>
                        <div style={{
                            width: 64, height: 64, borderRadius: '50%',
                            background: '#f0fdf4', display: 'flex', alignItems: 'center',
                            justifyContent: 'center', margin: '0 auto 1rem', fontSize: '2rem'
                        }}>
                            &#10003;
                        </div>
                        <h1 className="login-welcome-title">ลงทะเบียนสำเร็จ</h1>
                        <p className="login-welcome-subtitle" style={{ marginBottom: '1.5rem' }}>
                            บัญชีของคุณอยู่ระหว่างรอการอนุมัติจากผู้ดูแลระบบ<br />
                            คุณจะสามารถเข้าสู่ระบบได้หลังจากได้รับการอนุมัติ
                        </p>
                        <button
                            className="login-btn-new"
                            onClick={() => navigate('/login')}
                        >
                            กลับไปหน้าเข้าสู่ระบบ
                        </button>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div className="login-split-container">
            {/* Left Panel */}
            <div className="login-left-panel">
                <div className="login-grid-bg" />
                <div className="login-left-content">
                    <div className="login-brand">
                        <div className="login-brand-logo">
                            <span className="brand-tim">Tim</span><span className="brand-sum">Sum</span>
                        </div>
                        <p className="login-brand-tagline">ระบบสรุปการประชุมอัจฉริยะ ระดับองค์กร</p>
                        <ul className="login-feature-list">
                            <li>ถอดเสียงด้วย AI แม่นยำ 98%+ รองรับภาษาไทย-อังกฤษ</li>
                            <li>จดจำผู้พูด ระบุชื่อ-ตำแหน่ง อัตโนมัติ</li>
                            <li>เลือกประเภทการประชุม 11 แบบ</li>
                            <li>ส่ง transcript + summary ทางอีเมลทันที</li>
                        </ul>
                    </div>
                </div>
            </div>

            {/* Right Panel */}
            <div className="login-right-panel">
                <div className="login-form-wrapper">
                    <div className="login-form-header">
                        <h1 className="login-welcome-title">สมัครสมาชิก</h1>
                        <p className="login-welcome-subtitle">
                            สร้างบัญชีเพื่อเริ่มใช้งาน <span className="login-brand-inline">TimSum</span>
                        </p>
                    </div>

                    <form className="login-form-new" onSubmit={handleSubmit}>
                        {error && (
                            <div className="login-error-new">
                                <span>&#10060;</span> {error}
                            </div>
                        )}

                        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                            <div className="login-field">
                                <label htmlFor="first_name">ชื่อ *</label>
                                <input
                                    id="first_name" name="first_name" type="text"
                                    className="login-input-new" placeholder="ชื่อ"
                                    value={form.first_name} onChange={handleChange} required
                                />
                            </div>
                            <div className="login-field">
                                <label htmlFor="last_name">นามสกุล *</label>
                                <input
                                    id="last_name" name="last_name" type="text"
                                    className="login-input-new" placeholder="นามสกุล"
                                    value={form.last_name} onChange={handleChange} required
                                />
                            </div>
                        </div>

                        <div className="login-field">
                            <label htmlFor="email">อีเมล *</label>
                            <input
                                id="email" name="email" type="email"
                                className="login-input-new" placeholder="user@company.co.th"
                                value={form.email} onChange={handleChange} required
                            />
                        </div>

                        <div className="login-field">
                            <label htmlFor="phone">เบอร์โทรศัพท์</label>
                            <input
                                id="phone" name="phone" type="tel"
                                className="login-input-new" placeholder="08X-XXX-XXXX"
                                value={form.phone} onChange={handleChange}
                            />
                        </div>

                        <div className="login-field">
                            <label htmlFor="organization">องค์กร / บริษัท</label>
                            <input
                                id="organization" name="organization" type="text"
                                className="login-input-new" placeholder="ชื่อองค์กร"
                                value={form.organization} onChange={handleChange}
                            />
                        </div>

                        <div className="login-field">
                            <label htmlFor="password">รหัสผ่าน * (อย่างน้อย 8 ตัวอักษร)</label>
                            <input
                                id="password" name="password" type="password"
                                className="login-input-new" placeholder="&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;"
                                value={form.password} onChange={handleChange} required minLength={8}
                            />
                        </div>

                        <div className="login-field">
                            <label htmlFor="confirmPassword">ยืนยันรหัสผ่าน *</label>
                            <input
                                id="confirmPassword" name="confirmPassword" type="password"
                                className="login-input-new" placeholder="&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;&#8226;"
                                value={form.confirmPassword} onChange={handleChange} required minLength={8}
                            />
                        </div>

                        <button
                            type="submit"
                            className="login-btn-new"
                            disabled={isLoading || !form.first_name || !form.last_name || !form.email || !form.password || !form.confirmPassword}
                        >
                            {isLoading ? 'กำลังลงทะเบียน...' : 'สมัครสมาชิก'}
                        </button>
                    </form>

                    <p className="login-register-text">
                        มีบัญชีอยู่แล้ว?{' '}
                        <Link to="/login" className="login-register-link">เข้าสู่ระบบ</Link>
                    </p>
                </div>
            </div>
        </div>
    );
};

export default Register;
