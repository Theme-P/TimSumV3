import { useEffect } from 'react'
import { useTheme } from '../contexts/ThemeContext'

const THEME_OPTIONS = [
    { id: 'light', icon: '☀️', label: 'สว่าง', hint: 'ใช้ธีมสีอ่อนเสมอ' },
    { id: 'dark', icon: '🌙', label: 'มืด', hint: 'ใช้ธีมสีเข้มเสมอ' },
    { id: 'system', icon: '🖥️', label: 'ระบบ', hint: 'ปรับตามการตั้งค่าของอุปกรณ์' },
]

function SettingsModal({ isOpen, onClose, userInfo }) {
    const { theme, setTheme } = useTheme()

    useEffect(() => {
        if (!isOpen) return
        const handleEsc = (e) => {
            if (e.key === 'Escape') onClose()
        }
        document.addEventListener('keydown', handleEsc)
        document.body.style.overflow = 'hidden'
        return () => {
            document.removeEventListener('keydown', handleEsc)
            document.body.style.overflow = ''
        }
    }, [isOpen, onClose])

    if (!isOpen) return null

    return (
        <div className="settings-overlay" onClick={onClose}>
            <div
                className="settings-modal"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-labelledby="settings-title"
            >
                <header className="settings-header">
                    <h2 id="settings-title" className="settings-title">
                        <span>⚙️</span> ตั้งค่า
                    </h2>
                    <button
                        className="settings-close"
                        onClick={onClose}
                        aria-label="ปิด"
                    >
                        ✕
                    </button>
                </header>

                <div className="settings-body">
                    {/* ── Account section ── */}
                    <section className="settings-section">
                        <h3 className="settings-section-title">
                            <span className="settings-section-icon">👤</span>
                            บัญชีผู้ใช้
                        </h3>
                        <div className="settings-account">
                            <div className="settings-account-avatar">
                                {userInfo?.initials || 'ผู้'}
                            </div>
                            <div className="settings-account-info">
                                <div className="settings-account-name">
                                    {userInfo?.username || '—'}
                                </div>
                                <div className="settings-account-email">
                                    {userInfo?.email || '—'}
                                </div>
                            </div>
                        </div>
                    </section>

                    {/* ── Appearance section ── */}
                    <section className="settings-section">
                        <h3 className="settings-section-title">
                            <span className="settings-section-icon">🎨</span>
                            ธีมการแสดงผล
                        </h3>
                        <p className="settings-section-desc">
                            เลือกรูปแบบธีมที่ต้องการใช้งาน — สามารถสลับได้ตลอดเวลา
                        </p>
                        <div className="settings-theme-grid">
                            {THEME_OPTIONS.map((opt) => (
                                <button
                                    key={opt.id}
                                    className={`settings-theme-card ${theme === opt.id ? 'active' : ''}`}
                                    onClick={() => setTheme(opt.id)}
                                >
                                    <span className="settings-theme-icon">{opt.icon}</span>
                                    <span className="settings-theme-label">{opt.label}</span>
                                    <span className="settings-theme-hint">{opt.hint}</span>
                                    {theme === opt.id && (
                                        <span className="settings-theme-check">✓</span>
                                    )}
                                </button>
                            ))}
                        </div>
                    </section>

                    {/* ── About section ── */}
                    <section className="settings-section">
                        <h3 className="settings-section-title">
                            <span className="settings-section-icon">ℹ️</span>
                            เกี่ยวกับ
                        </h3>
                        <div className="settings-about">
                            <div className="settings-about-row">
                                <span>แอปพลิเคชัน</span>
                                <span>TimSum V3</span>
                            </div>
                            <div className="settings-about-row">
                                <span>เวอร์ชัน</span>
                                <span>3.0.0</span>
                            </div>
                        </div>
                    </section>
                </div>
            </div>
        </div>
    )
}

export default SettingsModal
