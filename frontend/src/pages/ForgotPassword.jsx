import { useState } from 'react';
import { Link } from 'react-router-dom';
import '../styles/Login.css'; // Reuse login styles

const API_BASE = '/api';

const ForgotPassword = () => {
    const [email, setEmail] = useState('');
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setMessage('');
        setIsLoading(true);

        try {
            const response = await fetch(`${API_BASE}/auth/forgot-password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email }),
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || data.message || 'เกิดข้อผิดพลาด');
            }

            setMessage(data.message);
            setEmail('');
        } catch (err) {
            setError(err.message || 'เกิดข้อผิดพลาดในการส่งคำขอ');
        } finally {
            setIsLoading(false);
        }
    };

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
                <div className="login-form-wrapper">
                    <div className="login-form-header">
                        <h1 className="login-welcome-title">ลืมรหัสผ่าน?</h1>
                        <p className="login-welcome-subtitle">
                            กรุณากรอกอีเมลของคุณเพื่อรับลิงก์รีเซ็ตรหัสผ่าน
                        </p>
                    </div>

                    <form className="login-form-new" onSubmit={handleSubmit}>
                        {error && (
                            <div className="login-error-new">
                                <span>❌</span> {error}
                            </div>
                        )}
                        {message && (
                            <div className="login-error-new" style={{ backgroundColor: 'rgba(52, 168, 83, 0.1)', color: '#34A853', borderColor: 'rgba(52, 168, 83, 0.2)' }}>
                                <span>✅</span> {message}
                            </div>
                        )}

                        <div className="login-field">
                            <label htmlFor="email">อีเมล</label>
                            <input
                                id="email"
                                type="email"
                                className="login-input-new"
                                placeholder="user@company.co.th"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                required
                            />
                        </div>

                        <button
                            type="submit"
                            className="login-btn-new"
                            disabled={isLoading || !email}
                        >
                            {isLoading ? 'กำลังส่ง...' : 'ส่งลิงก์รีเซ็ตรหัสผ่าน'}
                        </button>
                    </form>

                    <p className="login-register-text" style={{ marginTop: '20px' }}>
                        <Link to="/login" className="login-register-link">กลับสู่หน้าเข้าสู่ระบบ</Link>
                    </p>
                </div>
            </div>
        </div>
    );
};

export default ForgotPassword;
