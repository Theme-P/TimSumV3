import { useEffect, useState } from 'react'

const API_BASE = '/api'

function UsageBar({ label, used, limit }) {
    const pct = limit > 0 ? Math.min((used / limit) * 100, 100) : 0
    const isNearLimit = pct >= 80
    return (
        <div style={{ marginBottom: '0.75rem' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.82rem', marginBottom: 4 }}>
                <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
                <span style={{ fontWeight: 600, color: isNearLimit ? 'var(--error)' : 'var(--text-primary)' }}>
                    {typeof used === 'number' && used % 1 !== 0 ? used.toFixed(1) : used} / {limit >= 99999 ? '\u221e' : limit}
                </span>
            </div>
            <div style={{ height: 6, background: 'var(--bg-tertiary)', borderRadius: 999, overflow: 'hidden' }}>
                <div style={{
                    height: '100%', borderRadius: 999, transition: 'width 0.5s',
                    width: `${pct}%`,
                    background: isNearLimit
                        ? 'linear-gradient(90deg, #e57368, #c0392b)'
                        : 'var(--accent-gradient)',
                }} />
            </div>
        </div>
    )
}

function ProfileModal({ isOpen, onClose, userInfo, token }) {
    const [pkgData, setPkgData] = useState(null)

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

    useEffect(() => {
        if (!isOpen || !token) return
        fetch(`${API_BASE}/user/package`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => {
                if (data.success && data.package) setPkgData(data.package)
            })
            .catch(() => {})
    }, [isOpen, token])

    if (!isOpen) return null

    const pkg = pkgData?.package
    const usage = pkgData?.usage || {}
    const limits = pkg?.limits || {}

    return (
        <div className="settings-overlay" onClick={onClose}>
            <div
                className="settings-modal"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-labelledby="profile-title"
            >
                <header className="settings-header">
                    <h2 id="profile-title" className="settings-title">
                        <span>👤</span> โปรไฟล์
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

                    {/* ── Package & Usage section ── */}
                    {pkg && (
                        <section className="settings-section">
                            <h3 className="settings-section-title">
                                <span className="settings-section-icon">📦</span>
                                แพ็กเกจ & การใช้งาน
                            </h3>
                            <div className="settings-about" style={{ marginBottom: '0.75rem' }}>
                                <div className="settings-about-row">
                                    <span>แพ็กเกจปัจจุบัน</span>
                                    <span style={{ fontWeight: 700, color: 'var(--accent-primary)' }}>
                                        {pkg.name}
                                    </span>
                                </div>
                                {pkg.price > 0 && (
                                    <div className="settings-about-row">
                                        <span>ราคา</span>
                                        <span>{pkg.price.toLocaleString()} บาท / {pkg.billing_cycle === 'yearly' ? 'ปี' : 'เดือน'}</span>
                                    </div>
                                )}
                                <div className="settings-about-row">
                                    <span>รอบใช้งาน</span>
                                    <span>{pkgData.usage_reset_month || '—'}</span>
                                </div>
                            </div>
                            <p className="settings-section-desc">
                                สถิติการใช้งานเดือนนี้
                            </p>
                            <div style={{ padding: '0 0.25rem' }}>
                                <UsageBar
                                    label="จำนวนไฟล์"
                                    used={usage.files_this_month || 0}
                                    limit={limits.max_files_per_month || 0}
                                />
                                <UsageBar
                                    label="AI สรุปประชุม"
                                    used={usage.ai_summaries_this_month || 0}
                                    limit={limits.ai_summary_per_month || 0}
                                />
                                <UsageBar
                                    label="นาทีถอดเสียง"
                                    used={usage.transcription_minutes_this_month || 0}
                                    limit={limits.transcription_minutes_per_month || 0}
                                />
                            </div>
                        </section>
                    )}
                </div>
            </div>
        </div>
    )
}

export default ProfileModal
