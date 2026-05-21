import { useState, useEffect } from 'react';
import { Link, useSearchParams, useNavigate } from 'react-router-dom';
import '../styles/Login.css';

const API_BASE = '/api';

const ResetPassword = () => {
    const [searchParams] = useSearchParams();
    const token = searchParams.get('token');
    const navigate = useNavigate();

    const [password, setPassword] = useState('');
    const [confirmPassword, setConfirmPassword] = useState('');
    const [message, setMessage] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [isSuccess, setIsSuccess] = useState(false);

    useEffect(() => {
        if (!token) {
            setError('ไม่พบ Token สำหรับรีเซ็ตรหัสผ่าน กรุณากดลิงก์จากอีเมลอีกครั้ง');
        }
    }, [token]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setMessage('');

        if (password !== confirmPassword) {
            setError('รหัสผ่านและการยืนยันรหัสผ่านไม่ตรงกัน');
            return;
        }

        setIsLoading(true);

        try {
            const response = await fetch(`${API_BASE}/auth/reset-password`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ token, new_password: password }),
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || data.message || 'เกิดข้อผิดพลาด');
            }

            setMessage(data.message);
            setIsSuccess(true);
        } catch (err) {
            setError(err.message || 'เกิดข้อผิดพลาดในการเปลี่ยนรหัสผ่าน');
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
                        <h1 className="login-welcome-title">ตั้งรหัสผ่านใหม่</h1>
                        <p className="login-welcome-subtitle">
                            กรุณากรอกรหัสผ่านใหม่ที่คุณต้องการใช้งาน
                        </p>
                    </div>

                    {!isSuccess ? (
                        <form className="login-form-new" onSubmit={handleSubmit}>
                            {error && (
                                <div className="login-error-new">
                                    <span>❌</span> {error}
                                </div>
                            )}

                            <div className="login-field">
                                <label htmlFor="password">รหัสผ่านใหม่</label>
                                <input
                                    id="password"
                                    type="password"
                                    className="login-input-new"
                                    placeholder="••••••••"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    required
                                    disabled={!token}
                                />
                            </div>

                            <div className="login-field">
                                <label htmlFor="confirmPassword">ยืนยันรหัสผ่านใหม่</label>
                                <input
                                    id="confirmPassword"
                                    type="password"
                                    className="login-input-new"
                                    placeholder="••••••••"
                                    value={confirmPassword}
                                    onChange={(e) => setConfirmPassword(e.target.value)}
                                    required
                                    disabled={!token}
                                />
                            </div>

                            <button
                                type="submit"
                                className="login-btn-new"
                                disabled={isLoading || !token || !password || !confirmPassword}
                            >
                                {isLoading ? 'กำลังเปลี่ยนรหัสผ่าน...' : 'เปลี่ยนรหัสผ่าน'}
                            </button>
                        </form>
                    ) : (
                        <div className="login-form-new">
                            <div className="login-error-new" style={{ backgroundColor: 'rgba(52, 168, 83, 0.1)', color: '#34A853', borderColor: 'rgba(52, 168, 83, 0.2)' }}>
                                <span>✅</span> {message}
                            </div>
                            <button
                                type="button"
                                className="login-btn-new"
                                onClick={() => navigate('/login')}
                                style={{ marginTop: '20px' }}
                            >
                                ไปยังหน้าเข้าสู่ระบบ
                            </button>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
};

export default ResetPassword;
