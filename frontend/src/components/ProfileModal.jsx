import { useEffect, useState } from 'react'
import VoiceLibrary from './VoiceLibrary'
import Icon from './ui/Icon'

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
    package_request_create: 'ขอเปลี่ยนแพ็กเกจ',
    package_request_cancel: 'ยกเลิกคำขอแพ็กเกจ',
}

const PACKAGE_REQUEST_STATUS = {
    pending: { text: 'รอพิจารณา', color: '#c68a19', bg: 'rgba(198,138,25,0.12)' },
    approved: { text: 'อนุมัติแล้ว', color: '#2d8a4e', bg: 'rgba(45,138,78,0.12)' },
    rejected: { text: 'ถูกปฏิเสธ', color: '#c0392b', bg: 'rgba(192,57,43,0.12)' },
    cancelled: { text: 'ยกเลิกแล้ว', color: '#7f8c8d', bg: 'rgba(127,140,141,0.12)' },
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
    const [availablePackages, setAvailablePackages] = useState([])
    const [packageRequests, setPackageRequests] = useState([])
    const [selectedPackageId, setSelectedPackageId] = useState('')
    const [packageRequestNote, setPackageRequestNote] = useState('')
    const [packageRequestStatus, setPackageRequestStatus] = useState({ type: '', message: '' })
    const [packageRequestLoading, setPackageRequestLoading] = useState(false)

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

        fetch(`${API_BASE}/packages`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => setAvailablePackages(data.packages || []))
            .catch(() => {})

        const fetchPackageRequests = () => {
            fetch(`${API_BASE}/user/package-requests`, {
                headers: { 'Authorization': `Bearer ${token}` },
            })
                .then(r => r.json())
                .then(data => setPackageRequests(data.requests || []))
                .catch(() => {})
        }
        fetchPackageRequests()
            
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
    const pendingPackageRequest = packageRequests.find(req => req.status === 'pending')
    const requestablePackages = availablePackages.filter(item => item._id !== pkg?._id)

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

    const refreshPackageRequests = async () => {
        const res = await fetch(`${API_BASE}/user/package-requests`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
        const data = await res.json()
        if (data.success) setPackageRequests(data.requests || [])
    }

    const handlePackageRequestSubmit = async (e) => {
        e.preventDefault()
        if (!selectedPackageId) {
            setPackageRequestStatus({ type: 'error', message: 'กรุณาเลือกแพ็กเกจที่ต้องการ' })
            return
        }
        setPackageRequestLoading(true)
        setPackageRequestStatus({ type: '', message: '' })
        try {
            const res = await fetch(`${API_BASE}/user/package-requests`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`
                },
                body: JSON.stringify({
                    requested_package_id: selectedPackageId,
                    note: packageRequestNote,
                })
            })
            const data = await res.json()
            if (!res.ok) throw new Error(data.detail || 'ส่งคำขอไม่สำเร็จ')
            setPackageRequestStatus({ type: 'success', message: data.message || 'ส่งคำขอเรียบร้อยแล้ว' })
            setSelectedPackageId('')
            setPackageRequestNote('')
            await refreshPackageRequests()
        } catch (err) {
            setPackageRequestStatus({ type: 'error', message: err.message })
        } finally {
            setPackageRequestLoading(false)
        }
    }

    const handleCancelPackageRequest = async (requestId) => {
        if (!window.confirm('ยืนยันยกเลิกคำขอนี้?')) return
        setPackageRequestLoading(true)
        setPackageRequestStatus({ type: '', message: '' })
        try {
            const res = await fetch(`${API_BASE}/user/package-requests/${requestId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            })
            const data = await res.json()
            if (!res.ok) throw new Error(data.detail || 'ยกเลิกคำขอไม่สำเร็จ')
            setPackageRequestStatus({ type: 'success', message: data.message || 'ยกเลิกคำขอเรียบร้อย' })
            await refreshPackageRequests()
        } catch (err) {
            setPackageRequestStatus({ type: 'error', message: err.message })
        } finally {
            setPackageRequestLoading(false)
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
                        <Icon name="user" /> จัดการโปรไฟล์
                    </h2>
                    <button
                        className="settings-close"
                        onClick={onClose}
                        aria-label="ปิด"
                    >
                        <Icon name="x-circle" />
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
                            <span className="icon-label"><Icon name="mic" /> คลังเสียง</span>
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
                                <Icon name="user" className="settings-section-icon" /> บัญชีผู้ใช้ ({userInfo?.email})
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
                                    <span className="icon-label"><Icon name={profileStatus.type === 'success' ? 'check-circle' : 'x-circle'} /> {profileStatus.message}</span>
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
                                <Icon name="lock" className="settings-section-icon" /> เปลี่ยนรหัสผ่าน
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
                                    <span className="icon-label"><Icon name={passwordStatus.type === 'success' ? 'check-circle' : 'x-circle'} /> {passwordStatus.message}</span>
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
                                <Icon name="box" className="settings-section-icon" />
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

                                    <div style={{
                                        marginTop: '1.5rem',
                                        paddingTop: '1rem',
                                        borderTop: '1px solid var(--border-color)',
                                    }}>
                                        <h4 style={{ fontSize: '14px', margin: '0 0 10px 0', color: 'var(--text-primary)' }}>
                                            ขอเปลี่ยนแพ็กเกจ
                                        </h4>

                                        {packageRequestStatus.message && (
                                            <div style={{
                                                padding: '10px',
                                                marginBottom: '12px',
                                                borderRadius: '6px',
                                                backgroundColor: packageRequestStatus.type === 'success' ? 'rgba(52, 168, 83, 0.1)' : 'rgba(234, 67, 53, 0.1)',
                                                color: packageRequestStatus.type === 'success' ? '#34A853' : '#EA4335',
                                                border: `1px solid ${packageRequestStatus.type === 'success' ? 'rgba(52, 168, 83, 0.2)' : 'rgba(234, 67, 53, 0.2)'}`,
                                                fontSize: '0.86rem',
                                            }}>
                                                {packageRequestStatus.message}
                                            </div>
                                        )}

                                        {pendingPackageRequest ? (
                                            <div style={{
                                                padding: '0.9rem 1rem',
                                                borderRadius: '10px',
                                                background: 'rgba(198,138,25,0.08)',
                                                border: '1px solid rgba(198,138,25,0.22)',
                                            }}>
                                                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem', alignItems: 'flex-start' }}>
                                                    <div>
                                                        <div style={{ fontWeight: 700, color: 'var(--text-primary)' }}>
                                                            รอพิจารณา: {pendingPackageRequest.requested_package?.name}
                                                        </div>
                                                        <div style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', marginTop: 4 }}>
                                                            ส่งเมื่อ {pendingPackageRequest.requested_at ? parseUtcDate(pendingPackageRequest.requested_at).toLocaleString() : '—'}
                                                        </div>
                                                        {pendingPackageRequest.note && (
                                                            <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginTop: 4 }}>
                                                                หมายเหตุ: {pendingPackageRequest.note}
                                                            </div>
                                                        )}
                                                    </div>
                                                    <button
                                                        type="button"
                                                        onClick={() => handleCancelPackageRequest(pendingPackageRequest._id)}
                                                        disabled={packageRequestLoading}
                                                        style={{
                                                            padding: '0.4rem 0.75rem',
                                                            borderRadius: 8,
                                                            border: '1px solid var(--text-muted)',
                                                            background: 'transparent',
                                                            color: 'var(--text-secondary)',
                                                            cursor: packageRequestLoading ? 'not-allowed' : 'pointer',
                                                            fontFamily: 'var(--font-thai)',
                                                            fontWeight: 600,
                                                            flexShrink: 0,
                                                        }}
                                                    >
                                                        ยกเลิก
                                                    </button>
                                                </div>
                                            </div>
                                        ) : (
                                            <form onSubmit={handlePackageRequestSubmit}>
                                                <div style={{ marginBottom: '0.75rem' }}>
                                                    <label style={{ display: 'block', fontSize: '0.84rem', marginBottom: 5, color: 'var(--text-secondary)' }}>
                                                        เลือกแพ็กเกจที่ต้องการ
                                                    </label>
                                                    <select
                                                        value={selectedPackageId}
                                                        onChange={e => setSelectedPackageId(e.target.value)}
                                                        style={{
                                                            width: '100%',
                                                            padding: '10px',
                                                            borderRadius: '6px',
                                                            border: '1px solid var(--border-color)',
                                                            backgroundColor: 'var(--bg-secondary)',
                                                            color: 'var(--text-primary)',
                                                            fontFamily: 'var(--font-thai)',
                                                        }}
                                                    >
                                                        <option value="">เลือกแพ็กเกจ</option>
                                                        {requestablePackages.map(item => (
                                                            <option key={item._id} value={item._id}>
                                                                {item.name} · {item.price?.toLocaleString()} บาท/{item.billing_cycle === 'yearly' ? 'ปี' : 'เดือน'}
                                                            </option>
                                                        ))}
                                                    </select>
                                                </div>
                                                <div style={{ marginBottom: '0.75rem' }}>
                                                    <label style={{ display: 'block', fontSize: '0.84rem', marginBottom: 5, color: 'var(--text-secondary)' }}>
                                                        หมายเหตุถึงผู้ดูแลระบบ
                                                    </label>
                                                    <textarea
                                                        rows={3}
                                                        value={packageRequestNote}
                                                        onChange={e => setPackageRequestNote(e.target.value)}
                                                        placeholder="เช่น ต้องการเพิ่มจำนวนไฟล์ต่อเดือน หรือใช้งานคลังเสียง"
                                                        style={{
                                                            width: '100%',
                                                            padding: '10px',
                                                            borderRadius: '6px',
                                                            border: '1px solid var(--border-color)',
                                                            backgroundColor: 'var(--bg-secondary)',
                                                            color: 'var(--text-primary)',
                                                            fontFamily: 'var(--font-thai)',
                                                            resize: 'vertical',
                                                        }}
                                                    />
                                                </div>
                                                <div style={{ textAlign: 'right' }}>
                                                    <button
                                                        type="submit"
                                                        disabled={packageRequestLoading || !selectedPackageId}
                                                        style={{
                                                            padding: '0.55rem 1rem',
                                                            borderRadius: 8,
                                                            border: 'none',
                                                            background: 'var(--accent-primary)',
                                                            color: '#fff',
                                                            fontWeight: 700,
                                                            cursor: packageRequestLoading || !selectedPackageId ? 'not-allowed' : 'pointer',
                                                            opacity: packageRequestLoading || !selectedPackageId ? 0.55 : 1,
                                                            fontFamily: 'var(--font-thai)',
                                                        }}
                                                    >
                                                        {packageRequestLoading ? 'กำลังส่ง...' : 'ส่งคำขอ'}
                                                    </button>
                                                </div>
                                            </form>
                                        )}

                                        {packageRequests.length > 0 && (
                                            <div style={{ marginTop: '1rem' }}>
                                                <h4 style={{ fontSize: '14px', margin: '0 0 8px 0', color: 'var(--text-primary)' }}>
                                                    ประวัติคำขอล่าสุด
                                                </h4>
                                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem' }}>
                                                    {packageRequests.slice(0, 3).map(req => {
                                                        const status = PACKAGE_REQUEST_STATUS[req.status] || PACKAGE_REQUEST_STATUS.pending
                                                        return (
                                                            <div key={req._id} style={{
                                                                padding: '0.65rem 0.75rem',
                                                                borderRadius: 8,
                                                                background: 'var(--bg-secondary)',
                                                                fontSize: '0.82rem',
                                                            }}>
                                                                <div style={{ display: 'flex', justifyContent: 'space-between', gap: '0.75rem' }}>
                                                                    <span style={{ fontWeight: 600 }}>
                                                                        {req.requested_package?.name || 'แพ็กเกจ'}
                                                                    </span>
                                                                    <span style={{
                                                                        padding: '0.1rem 0.45rem',
                                                                        borderRadius: 999,
                                                                        background: status.bg,
                                                                        color: status.color,
                                                                        fontWeight: 700,
                                                                        flexShrink: 0,
                                                                    }}>
                                                                        {status.text}
                                                                    </span>
                                                                </div>
                                                                {req.admin_note && (
                                                                    <div style={{ marginTop: 4, color: 'var(--text-muted)' }}>
                                                                        หมายเหตุแอดมิน: {req.admin_note}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        )
                                                    })}
                                                </div>
                                            </div>
                                        )}
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
                                <Icon name="clipboard-list" className="settings-section-icon" /> ประวัติการใช้งาน (10 รายการล่าสุด)
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
                                <Icon name="shield" className="settings-section-icon" /> การยินยอม PDPA
                            </h3>
                            {consentData ? (
                                <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                    {Object.entries(consentData)
                                        .filter(([type]) => type !== 'marketing')
                                        .map(([type, info]) => (
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
                                                    <span className="icon-label" style={{ fontSize: '0.85rem', fontWeight: 600, color: info.consented ? 'var(--success)' : 'var(--text-muted)' }}>
                                                        <Icon name={info.consented ? 'check-circle' : 'x-circle'} /> {info.consented ? 'ยินยอมแล้ว' : 'ยังไม่ยินยอม'}
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
                                        หากต้องการขอลบข้อมูล กรุณาติดต่อผู้ดูแลระบบ
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
