import { useState, useEffect, useRef, useCallback } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import '../styles/Login.css';

const API_BASE = '/api';

const Login = () => {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [googleClientId, setGoogleClientId] = useState('');
    const googleBtnRef = useRef(null);

    const { login } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();

    const from = location.state?.from?.pathname || '/';

    const handleGoogleResponse = useCallback(async (response) => {
        setError('');
        setIsLoading(true);
        try {
            const res = await fetch(`${API_BASE}/auth/google`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ credential: response.credential }),
            });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || 'เข้าสู่ระบบด้วย Google ไม่สำเร็จ');
            }
            login(data.token);
            navigate(from, { replace: true });
        } catch (err) {
            setError(err.message || 'เกิดข้อผิดพลาดในการเข้าสู่ระบบด้วย Google');
        } finally {
            setIsLoading(false);
        }
    }, [from, login, navigate]);

    // Fetch Google Client ID and initialize GIS
    useEffect(() => {
        fetch(`${API_BASE}/auth/google/client-id`)
            .then(r => r.json())
            .then(data => {
                if (data.enabled && data.client_id) {
                    setGoogleClientId(data.client_id);
                }
            })
            .catch(() => {});
    }, []);

    useEffect(() => {
        if (!googleClientId || !window.google?.accounts?.id) return;
        window.google.accounts.id.initialize({
            client_id: googleClientId,
            callback: handleGoogleResponse,
        });
        if (googleBtnRef.current) {
            window.google.accounts.id.renderButton(googleBtnRef.current, {
                type: 'standard',
                theme: 'outline',
                size: 'large',
                text: 'signin_with',
                width: googleBtnRef.current.offsetWidth,
                locale: 'th',
            });
        }
    }, [googleClientId, handleGoogleResponse]);

    const handleSubmit = async (e) => {
        e.preventDefault();
        setError('');
        setIsLoading(true);

        try {
            const response = await fetch(`${API_BASE}/auth/login`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email, password }),
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || data.message || 'เกิดข้อผิดพลาดในการเข้าสู่ระบบ');
            }

            login(data.token);
            navigate(from, { replace: true });
        } catch (err) {
            setError(err.message || 'อีเมลหรือรหัสผ่านไม่ถูกต้อง');
        } finally {
            setIsLoading(false);
        }
    };

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
                        <h1 className="login-welcome-title">ยินดีต้อนรับ</h1>
                        <p className="login-welcome-subtitle">
                            เข้าสู่ระบบเพื่อเริ่มใช้งาน <span className="login-brand-inline">TimSum</span>
                        </p>
                    </div>

                    <form className="login-form-new" onSubmit={handleSubmit}>
                        {error && (
                            <div className="login-error-new">
                                <span>❌</span> {error}
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

                        <div className="login-field">
                            <div className="login-field-header">
                                <label htmlFor="password">รหัสผ่าน</label>
                                <Link to="/forgot-password" className="login-forgot-link">ลืมรหัสผ่าน?</Link>
                            </div>
                            <input
                                id="password"
                                type="password"
                                className="login-input-new"
                                placeholder="••••••••"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                required
                            />
                        </div>

                        <button
                            type="submit"
                            className="login-btn-new"
                            disabled={isLoading || !email || !password}
                        >
                            {isLoading ? 'กำลังตรวจสอบ...' : 'เข้าสู่ระบบ'}
                        </button>
                    </form>

                    <div className="login-divider">
                        <span>หรือ</span>
                    </div>

                    {googleClientId ? (
                        <div ref={googleBtnRef} className="login-google-gsi" />
                    ) : (
                        <button className="login-google-btn" type="button" disabled>
                            <svg width="18" height="18" viewBox="0 0 18 18" xmlns="http://www.w3.org/2000/svg">
                                <path d="M17.64 9.205c0-.639-.057-1.252-.164-1.841H9v3.481h4.844a4.14 4.14 0 0 1-1.796 2.716v2.259h2.908c1.702-1.567 2.684-3.875 2.684-6.615Z" fill="#4285F4"/>
                                <path d="M9 18c2.43 0 4.467-.806 5.956-2.18l-2.908-2.259c-.806.54-1.837.86-3.048.86-2.344 0-4.328-1.584-5.036-3.711H.957v2.332A8.997 8.997 0 0 0 9 18Z" fill="#34A853"/>
                                <path d="M3.964 10.71A5.41 5.41 0 0 1 3.682 9c0-.593.102-1.17.282-1.71V4.958H.957A8.996 8.996 0 0 0 0 9c0 1.452.348 2.827.957 4.042l3.007-2.332Z" fill="#FBBC05"/>
                                <path d="M9 3.58c1.321 0 2.508.454 3.44 1.345l2.582-2.58C13.463.891 11.426 0 9 0A8.997 8.997 0 0 0 .957 4.958L3.964 7.29C4.672 5.163 6.656 3.58 9 3.58Z" fill="#EA4335"/>
                            </svg>
                            ลงชื่อเข้าใช้ด้วย Google
                        </button>
                    )}

                    <p className="login-register-text">
                        ยังไม่มีบัญชี?{' '}
                        <Link to="/register" className="login-register-link">สมัครสมาชิก</Link>
                    </p>
                </div>
            </div>
        </div>
    );
};

export default Login;
