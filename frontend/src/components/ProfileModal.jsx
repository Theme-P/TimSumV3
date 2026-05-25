import { useEffect, useState } from 'react'
import VoiceLibrary from './VoiceLibrary'

const API_BASE = '/api'

const ACTION_LABELS = {
    login: 'เข้าสู่ระบบ', logout: 'ออกจากระบบ', login_failed: 'เข้าสู่ระบบล้มเหลว',
    register: 'ลงทะเบียน', upload_audio: 'อัปโหลดไฟล์เสียง',
    view_session: 'ดูผลการประมวลผล', view_history: 'ดูประวัติ',
    export_transcript: 'ดาวน์โหลด Transcript', export_summary: 'ดาวน์โหลดสรุป',
    send_email: 'ส่งอีเมล', update_profile: 'แก้ไขโปรไฟล์',
    change_password: 'เปลี่ยนรหัสผ่าน', voice_sample_upload: 'อัปโหลด Voice Sample',
    voice_sample_delete: 'ลบ Voice Sample', consent_given: 'ยินยอมการใช้งาน',
    consent_withdrawn: 'ถอนการยินยอม',
}

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

function parseUtcDate(timestamp) {
    if (!timestamp) return new Date(NaN);
    let tsStr = String(timestamp);
    // Add 'Z' only if no timezone info present (+00:00, -05:30, or Z)
    if (tsStr.includes('T') && !tsStr.endsWith('Z') && !/[+-]\d{2}:\d{2}$/.test(tsStr)) {
        tsStr += 'Z';
    }
    return new Date(tsStr);
}

function RelativeTime({ timestamp }) {
    const [timeString, setTimeString] = useState('');

    useEffect(() => {
        const updateTime = () => {
            const date = parseUtcDate(timestamp);
            const rtf = new Intl.RelativeTimeFormat(navigator.language || 'th-TH', { numeric: 'auto' });
            const elapsed = (date.getTime() - Date.now()) / 1000;
            
            if (Math.abs(elapsed) < 60) {
                setTimeString(rtf.format(Math.round(elapsed), 'second'));
            } else if (Math.abs(elapsed) < 3600) {
                setTimeString(rtf.format(Math.round(elapsed / 60), 'minute'));
            } else if (Math.abs(elapsed) < 86400) {
                setTimeString(rtf.format(Math.round(elapsed / 3600), 'hour'));
            } else if (Math.abs(elapsed) < 2592000) {
                setTimeString(rtf.format(Math.round(elapsed / 86400), 'day'));
            } else {
                setTimeString(date.toLocaleString());
            }
        };

        updateTime();
        const interval = setInterval(updateTime, 60000);
        return () => clearInterval(interval);
    }, [timestamp]);

    return <span title={parseUtcDate(timestamp).toLocaleString()}>{timeString}</span>;
}

function ProfileModal({ isOpen, onClose, userInfo, token }) {
    const [pkgData, setPkgData] = useState(null)
    const [activeTab, setActiveTab] = useState('profile')
    const [profile, setProfile] = useState({
        first_name: '',
        last_name: '',
        phone: '',
        organization: ''
    })
    const [passwordData, setPasswordData] = useState({
        current_password: '',
        new_password: '',
        confirm_password: ''
    })
    
    const [profileStatus, setProfileStatus] = useState({ type: '', message: '' })
    const [passwordStatus, setPasswordStatus] = useState({ type: '', message: '' })
    const [isLoading, setIsLoading] = useState(false)
    const [activityLogs, setActivityLogs] = useState([])
    const [consentData, setConsentData] = useState(null)

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
        // Fetch package
        fetch(`${API_BASE}/user/package`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => {
                if (data.success && data.package) {
                    // API returns data.package or data (adjusting based on backend)
                    setPkgData(data.package || data)
                } else if (data.status) {
                     setPkgData(data)
                }
            })
            .catch(() => {})
            
        // Fetch profile
        fetch(`${API_BASE}/user/profile`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => {
                if (data.username) {
                    setProfile({
                        first_name: data.first_name || '',
                        last_name: data.last_name || '',
                        phone: data.phone || '',
                        organization: data.organization || ''
                    })
                }
            })
            .catch(() => {})

        // Fetch activity logs and auto-sync every 15s
        const fetchLogs = () => {
            fetch(`${API_BASE}/user/activity-logs?limit=10`, {
                headers: { 'Authorization': `Bearer ${token}` },
            })
                .then(r => r.json())
                .then(data => setActivityLogs(data.logs || []))
                .catch(() => {})
        };
        fetchLogs();
        const logInterval = setInterval(fetchLogs, 15000);

        // Fetch consent status
        fetch(`${API_BASE}/consent`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => setConsentData(data.consents || null))
            .catch(() => {})
            
        return () => clearInterval(logInterval);
    }, [isOpen, token])

    if (!isOpen) return null

    const pkg = pkgData?.package
    const usage = pkgData?.usage || {}
    const limits = pkg?.limits || {}
    const voiceEnrollmentEnabled = !!limits.voice_enrollment_enabled

    const handleProfileUpdate = async (e) => {
        e.preventDefault()
        setIsLoading(true)
        setProfileStatus({ type: '', message: '' })
        
        try {
            const res = await fetch(`${API_BASE}/user/profile`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify(profile)
            })
            const data = await res.json()
            if (!res.ok) throw new Error(data.detail || data.message || 'อัปเดตโปรไฟล์ไม่สำเร็จ')
            
            setProfileStatus({ type: 'success', message: 'อัปเดตข้อมูลโปรไฟล์เรียบร้อยแล้ว' })
        } catch (err) {
            setProfileStatus({ type: 'error', message: err.message })
        } finally {
            setIsLoading(false)
        }
    }

    const handlePasswordUpdate = async (e) => {
        e.preventDefault()
        
        if (passwordData.new_password !== passwordData.confirm_password) {
            setPasswordStatus({ type: 'error', message: 'รหัสผ่านใหม่และการยืนยันไม่ตรงกัน' })
            return
        }
        
        setIsLoading(true)
        setPasswordStatus({ type: '', message: '' })
        
        try {
            const res = await fetch(`${API_BASE}/user/change-password`, {
                method: 'PUT',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    current_password: passwordData.current_password,
                    new_password: passwordData.new_password
                })
            })
            const data = await res.json()
            if (!res.ok) throw new Error(data.detail || data.message || 'เปลี่ยนรหัสผ่านไม่สำเร็จ')
            
            setPasswordStatus({ type: 'success', message: 'เปลี่ยนรหัสผ่านเรียบร้อยแล้ว' })
            setPasswordData({ current_password: '', new_password: '', confirm_password: '' })
        } catch (err) {
            setPasswordStatus({ type: 'error', message: err.message })
        } finally {
            setIsLoading(false)
        }
    }

    return (
        <div className="settings-overlay" onClick={onClose}>
            <div
                className="settings-modal"
                onClick={(e) => e.stopPropagation()}
                role="dialog"
                aria-modal="true"
                aria-labelledby="profile-title"
                style={{ width: '600px', maxWidth: '95%' }}
            >
                <header className="settings-header">
                    <h2 id="profile-title" className="settings-title">
                        <span>👤</span> จัดการโปรไฟล์
                    </h2>
                    <button
                        className="settings-close"
                        onClick={onClose}
                        aria-label="ปิด"
                    >
                        ✕
                    </button>
                </header>
                
                <div className="settings-tabs">
                    <button 
                        className={`settings-tab ${activeTab === 'profile' ? 'active' : ''}`}
                        onClick={() => setActiveTab('profile')}
                    >
                        ข้อมูลส่วนตัว
                    </button>
                    <button 
                        className={`settings-tab ${activeTab === 'security' ? 'active' : ''}`}
                        onClick={() => setActiveTab('security')}
                    >
                        ความปลอดภัย
                    </button>
                    <button 
                        className={`settings-tab ${activeTab === 'package' ? 'active' : ''}`}
                        onClick={() => setActiveTab('package')}
                    >
                        แพ็กเกจ
                    </button>
                    {voiceEnrollmentEnabled && (
                        <button
                            className={`settings-tab ${activeTab === 'voice' ? 'active' : ''}`}
                            onClick={() => setActiveTab('voice')}
                        >
                            🎙️ คลังเสียง
                        </button>
                    )}
                    <button
                        className={`settings-tab ${activeTab === 'activity' ? 'active' : ''}`}
                        onClick={() => setActiveTab('activity')}
                    >
                        ประวัติการใช้งาน
                    </button>
                    <button
                        className={`settings-tab ${activeTab === 'consent' ? 'active' : ''}`}
                        onClick={() => setActiveTab('consent')}
                    >
                        การยินยอม (PDPA)
                    </button>
                </div>

                <div className="settings-body" style={{ minHeight: '350px' }}>
                    {activeTab === 'profile' && (
                        <section className="settings-section">
                            <h3 className="settings-section-title">
                                <span className="settings-section-icon">👤</span> บัญชีผู้ใช้ ({userInfo?.email})
                            </h3>
                            
                            {profileStatus.message && (
                                <div style={{ 
                                    padding: '10px', 
                                    marginBottom: '15px', 
                                    borderRadius: '6px', 
                                    backgroundColor: profileStatus.type === 'success' ? 'rgba(52, 168, 83, 0.1)' : 'rgba(234, 67, 53, 0.1)',
                                    color: profileStatus.type === 'success' ? '#34A853' : '#EA4335',
                                    border: `1px solid ${profileStatus.type === 'success' ? 'rgba(52, 168, 83, 0.2)' : 'rgba(234, 67, 53, 0.2)'}`
                                }}>
                                    {profileStatus.type === 'success' ? '✅' : '❌'} {profileStatus.message}
                                </div>
                            )}

                            <form onSubmit={handleProfileUpdate}>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '15px', marginBottom: '15px' }}>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>ชื่อ</label>
                                        <input 
                                            type="text" 
                                            value={profile.first_name} 
                                            onChange={e => setProfile({...profile, first_name: e.target.value})}
                                            style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                            required
                                        />
                                    </div>
                                    <div>
                                        <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>นามสกุล</label>
                                        <input 
                                            type="text" 
                                            value={profile.last_name} 
                                            onChange={e => setProfile({...profile, last_name: e.target.value})}
                                            style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                            required
                                        />
                                    </div>
                                </div>
                                <div style={{ marginBottom: '15px' }}>
                                    <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>เบอร์โทรศัพท์</label>
                                    <input 
                                        type="tel" 
                                        value={profile.phone} 
                                        onChange={e => setProfile({...profile, phone: e.target.value})}
                                        style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                    />
                                </div>
                                <div style={{ marginBottom: '25px' }}>
                                    <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>องค์กร / หน่วยงาน</label>
                                    <input 
                                        type="text" 
                                        value={profile.organization} 
                                        onChange={e => setProfile({...profile, organization: e.target.value})}
                                        style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                    />
                                </div>
                                <div style={{ textAlign: 'right' }}>
                                    <button 
                                        type="submit" 
                                        disabled={isLoading}
                                        style={{ padding: '10px 20px', borderRadius: '6px', border: 'none', backgroundColor: 'var(--accent-primary)', color: 'white', fontWeight: 500, cursor: isLoading ? 'not-allowed' : 'pointer' }}
                                    >
                                        {isLoading ? 'กำลังบันทึก...' : 'บันทึกข้อมูล'}
                                    </button>
                                </div>
                            </form>
                        </section>
                    )}

                    {activeTab === 'security' && (
                        <section className="settings-section">
                            <h3 className="settings-section-title">
                                <span className="settings-section-icon">🔒</span> เปลี่ยนรหัสผ่าน
                            </h3>
                            
                            {passwordStatus.message && (
                                <div style={{ 
                                    padding: '10px', 
                                    marginBottom: '15px', 
                                    borderRadius: '6px', 
                                    backgroundColor: passwordStatus.type === 'success' ? 'rgba(52, 168, 83, 0.1)' : 'rgba(234, 67, 53, 0.1)',
                                    color: passwordStatus.type === 'success' ? '#34A853' : '#EA4335',
                                    border: `1px solid ${passwordStatus.type === 'success' ? 'rgba(52, 168, 83, 0.2)' : 'rgba(234, 67, 53, 0.2)'}`
                                }}>
                                    {passwordStatus.type === 'success' ? '✅' : '❌'} {passwordStatus.message}
                                </div>
                            )}

                            <form onSubmit={handlePasswordUpdate}>
                                <div style={{ marginBottom: '15px' }}>
                                    <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>รหัสผ่านปัจจุบัน</label>
                                    <input 
                                        type="password" 
                                        value={passwordData.current_password} 
                                        onChange={e => setPasswordData({...passwordData, current_password: e.target.value})}
                                        style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                        required
                                    />
                                </div>
                                <div style={{ marginBottom: '15px' }}>
                                    <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>รหัสผ่านใหม่</label>
                                    <input 
                                        type="password" 
                                        value={passwordData.new_password} 
                                        onChange={e => setPasswordData({...passwordData, new_password: e.target.value})}
                                        style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                        required
                                        minLength={8}
                                    />
                                    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', marginTop: '5px' }}>* ต้องมีอย่างน้อย 8 ตัวอักษร</div>
                                </div>
                                <div style={{ marginBottom: '25px' }}>
                                    <label style={{ display: 'block', fontSize: '14px', marginBottom: '5px', color: 'var(--text-secondary)' }}>ยืนยันรหัสผ่านใหม่</label>
                                    <input 
                                        type="password" 
                                        value={passwordData.confirm_password} 
                                        onChange={e => setPasswordData({...passwordData, confirm_password: e.target.value})}
                                        style={{ width: '100%', padding: '10px', borderRadius: '6px', border: '1px solid var(--border-color)', backgroundColor: 'var(--bg-secondary)', color: 'var(--text-primary)' }}
                                        required
                                        minLength={8}
                                    />
                                </div>
                                <div style={{ textAlign: 'right' }}>
                                    <button 
                                        type="submit" 
                                        disabled={isLoading}
                                        style={{ padding: '10px 20px', borderRadius: '6px', border: 'none', backgroundColor: 'var(--accent-primary)', color: 'white', fontWeight: 500, cursor: isLoading ? 'not-allowed' : 'pointer' }}
                                    >
                                        {isLoading ? 'กำลังบันทึก...' : 'เปลี่ยนรหัสผ่าน'}
                                    </button>
                                </div>
                            </form>
                        </section>
                    )}

                    {activeTab === 'package' && (
                        <section className="settings-section">
                            <h3 className="settings-section-title">
                                <span className="settings-section-icon">📦</span>
                                แพ็กเกจ & การใช้งาน
                            </h3>
                            {pkg ? (
                                <>
                                    <div className="settings-about" style={{ marginBottom: '1.5rem' }}>
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
                                    <h4 style={{ fontSize: '14px', margin: '0 0 10px 0', color: 'var(--text-primary)' }}>สถิติการใช้งานเดือนนี้</h4>
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
                                </>
                            ) : (
                                <div style={{ textAlign: 'center', padding: '30px 0', color: 'var(--text-secondary)' }}>
                                    กำลังโหลดข้อมูลแพ็กเกจ...
                                </div>
                            )}
                        </section>
                    )}

                    {activeTab === 'voice' && voiceEnrollmentEnabled && (
                        <section className="settings-section">
                            <VoiceLibrary token={token} />
                        </section>
                    )}

                    {activeTab === 'activity' && (
                        <section className="settings-section">
                            <h3 className="settings-section-title">
                                <span className="settings-section-icon">📋</span> ประวัติการใช้งาน (10 รายการล่าสุด)
                            </h3>
                            {activityLogs.length === 0 ? (
                                <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '2rem 0' }}>ยังไม่มีประวัติการใช้งาน</p>
                            ) : (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                    {activityLogs.slice(0, 10).map((log, i) => (
                                        <div key={i} style={{
                                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                                            padding: '0.6rem 0.75rem', borderRadius: '8px',
                                            background: 'var(--bg-secondary)', fontSize: '0.84rem',
                                        }}>
                                            <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                                                {ACTION_LABELS[log.action] || log.action}
                                            </span>
                                            <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem' }}>
                                                <RelativeTime timestamp={log.timestamp} />
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            )}
                        </section>
                    )}

                    {activeTab === 'consent' && (
                        <section className="settings-section">
                            <h3 className="settings-section-title">
                                <span className="settings-section-icon">🛡️</span> การยินยอม PDPA
                            </h3>
                            {consentData ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                    {Object.entries(consentData).map(([type, info]) => (
                                        <div key={type} style={{
                                            padding: '0.9rem 1rem', borderRadius: '10px',
                                            background: 'var(--bg-secondary)',
                                            border: `1px solid ${info.consented ? 'rgba(45,138,78,0.25)' : 'var(--border-color)'}`,
                                            borderLeft: `3px solid ${info.consented ? 'var(--success)' : 'var(--text-muted)'}`,
                                        }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                                                <div>
                                                    <span style={{ fontWeight: 600, fontSize: '0.9rem', color: 'var(--text-primary)' }}>
                                                        {info.label}
                                                    </span>
                                                    {info.required && (
                                                        <span style={{ marginLeft: '0.5rem', fontSize: '0.72rem', padding: '0.1rem 0.4rem', borderRadius: '999px', background: 'rgba(184,134,11,0.15)', color: 'var(--accent-primary)' }}>
                                                            จำเป็น
                                                        </span>
                                                    )}
                                                    {info.version_outdated && (
                                                        <span style={{ marginLeft: '0.4rem', fontSize: '0.72rem', padding: '0.1rem 0.4rem', borderRadius: '999px', background: 'rgba(192,57,43,0.15)', color: 'var(--error)' }}>
                                                            เวอร์ชั่นใหม่
                                                        </span>
                                                    )}
                                                </div>
                                                <span style={{ fontSize: '0.85rem', fontWeight: 600, color: info.consented ? 'var(--success)' : 'var(--text-muted)' }}>
                                                    {info.consented ? '✓ ยินยอมแล้ว' : '✗ ยังไม่ยินยอม'}
                                                </span>
                                            </div>
                                            {info.consented_at && (
                                                <p style={{ margin: '0.3rem 0 0', fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                                                    ยินยอมเมื่อ: {parseUtcDate(info.consented_at).toLocaleString()} · เวอร์ชัน {info.current_version}
                                                </p>
                                            )}
                                        </div>
                                    ))}
                                    <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.5rem' }}>
                                        หากต้องการถอนการยินยอมที่ไม่บังคับ หรือขอลบข้อมูล กรุณาติดต่อผู้ดูแลระบบ
                                    </p>
                                </div>
                            ) : (
                                <p style={{ color: 'var(--text-muted)', textAlign: 'center', padding: '2rem 0' }}>กำลังโหลด...</p>
                            )}
                        </section>
                    )}
                </div>
            </div>
            
            <style jsx="true">{`
                .settings-tabs {
                    display: flex;
                    border-bottom: 1px solid var(--border-color);
                    margin-bottom: 20px;
                    padding: 0 24px;
                }
                .settings-tab {
                    background: none;
                    border: none;
                    padding: 12px 16px;
                    font-size: 14px;
                    font-weight: 500;
                    color: var(--text-secondary);
                    cursor: pointer;
                    border-bottom: 2px solid transparent;
                    transition: all 0.2s;
                }
                .settings-tab:hover {
                    color: var(--text-primary);
                }
                .settings-tab.active {
                    color: var(--accent-primary);
                    border-bottom-color: var(--accent-primary);
                }
            `}</style>
        </div>
    )
}

export default ProfileModal
