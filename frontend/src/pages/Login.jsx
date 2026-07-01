import { useState } from 'react';
import { useNavigate, useLocation, Link } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import Icon from '../components/ui/Icon';
import '../styles/Login.css';

const API_BASE = '/api';

async function readResponseData(response) {
    const body = await response.text();
    if (!body) return {};

    try {
        return JSON.parse(body);
    } catch {
        return {};
    }
}

const Login = () => {
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [error, setError] = useState('');
    const [isLoading, setIsLoading] = useState(false);

    const { login } = useAuth();
    const navigate = useNavigate();
    const location = useLocation();

    const from = location.state?.from?.pathname || '/';

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

            const data = await readResponseData(response);

            if (!response.ok) {
                const fallbackMessage = response.status >= 500
                    ? `เซิร์ฟเวอร์ยังไม่พร้อมใช้งาน (HTTP ${response.status})`
                    : 'อีเมลหรือรหัสผ่านไม่ถูกต้อง';
                throw new Error(data.detail || data.message || fallbackMessage);
            }

            if (!data.token) {
                throw new Error('เซิร์ฟเวอร์ตอบกลับไม่สมบูรณ์ กรุณาลองใหม่');
            }

            login(data.token);
            navigate(from, { replace: true });
        } catch (err) {
            const message = err instanceof TypeError
                ? 'ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ได้ กรุณาลองใหม่'
                : err.message;
            setError(message || 'อีเมลหรือรหัสผ่านไม่ถูกต้อง');
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
                                <Icon name="x-circle" /> {error}
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
