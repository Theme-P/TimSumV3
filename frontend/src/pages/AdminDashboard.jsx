import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Link } from 'react-router-dom';

const API_BASE = '/api';

function getUserInfo(token) {
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        const name = payload.username || payload.email || '';
        return {
            initials: name.substring(0, 2).toUpperCase(),
            username: payload.username || '',
            email: payload.email || '',
            role: payload.role || 'user',
        };
    } catch {
        return { initials: '', username: '', email: '', role: 'user' };
    }
}

const STATUS_LABELS = {
    pending: { text: 'รอการอนุมัติ', color: '#c68a19', bg: 'rgba(198,138,25,0.12)' },
    approved: { text: 'อนุมัติแล้ว', color: '#2d8a4e', bg: 'rgba(45,138,78,0.12)' },
    rejected: { text: 'ถูกปฏิเสธ', color: '#c0392b', bg: 'rgba(192,57,43,0.12)' },
    suspended: { text: 'ถูกระงับ', color: '#7f8c8d', bg: 'rgba(127,140,141,0.12)' },
};

function AdminDashboard() {
    const { token, logout } = useAuth();
    const userInfo = token ? getUserInfo(token) : { initials: '', username: '', email: '', role: 'user' };

    const [activeTab, setActiveTab] = useState('pending');
    const [users, setUsers] = useState([]);
    const [stats, setStats] = useState({});
    const [loading, setLoading] = useState(true);
    const [actionLoading, setActionLoading] = useState(null);
    const [error, setError] = useState(null);
    const [showDropdown, setShowDropdown] = useState(false);
    const [packages, setPackages] = useState([]);
    const [assigningPkg, setAssigningPkg] = useState(null);
    const [activityLogs, setActivityLogs] = useState([]);
    const [activityLoading, setActivityLoading] = useState(false);
    const [queueStats, setQueueStats] = useState(null);
    const [queueJobs, setQueueJobs] = useState([]);
    const [queueLoading, setQueueLoading] = useState(false);
    const [cancellingJob, setCancellingJob] = useState(null);
    const dropdownRef = useRef(null);

    const ACTION_LABELS = {
        login: 'เข้าสู่ระบบ', logout: 'ออกจากระบบ', login_failed: 'เข้าสู่ระบบล้มเหลว',
        register: 'ลงทะเบียน', upload_audio: 'อัปโหลดไฟล์เสียง',
        view_session: 'ดูผลการประมวลผล', view_history: 'ดูประวัติ',
        export_transcript: 'ดาวน์โหลด Transcript', export_summary: 'ดาวน์โหลดสรุป',
        send_email: 'ส่งอีเมล', update_profile: 'แก้ไขโปรไฟล์', change_password: 'เปลี่ยนรหัสผ่าน',
        voice_sample_upload: 'อัปโหลด Voice Sample', voice_sample_delete: 'ลบ Voice Sample',
        admin_approve_user: 'อนุมัติผู้ใช้', admin_reject_user: 'ปฏิเสธผู้ใช้',
        admin_suspend_user: 'ระงับผู้ใช้', admin_assign_package: 'กำหนดแพ็กเกจ',
        consent_given: 'ยินยอม PDPA', consent_withdrawn: 'ถอนการยินยอม',
    };

    const headers = { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' };

    // Close dropdown on outside click
    useEffect(() => {
        const handleClickOutside = (e) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
                setShowDropdown(false);
            }
        };
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const fetchUsers = useCallback(async (status) => {
        setLoading(true);
        setError(null);
        try {
            const url = status === 'all'
                ? `${API_BASE}/admin/users`
                : `${API_BASE}/admin/users?status=${status}`;
            const res = await fetch(url, { headers });
            if (!res.ok) throw new Error('ไม่สามารถโหลดข้อมูลผู้ใช้ได้');
            const data = await res.json();
            setUsers(data.users || []);
        } catch (err) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, [token]);

    const fetchStats = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/admin/users/stats`, { headers });
            if (res.ok) {
                const data = await res.json();
                setStats(data.counts || {});
            }
        } catch { /* ignore */ }
    }, [token]);

    const fetchPackages = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/admin/packages`, { headers });
            if (res.ok) {
                const data = await res.json();
                setPackages(data.packages || []);
            }
        } catch { /* ignore */ }
    }, [token]);

    useEffect(() => {
        fetchUsers(activeTab);
        fetchStats();
        fetchPackages();
    }, [activeTab]);

    const handleAssignPackage = async (userId, packageId) => {
        setAssigningPkg(userId);
        try {
            const res = await fetch(`${API_BASE}/admin/users/${userId}/package`, {
                method: 'PUT',
                headers,
                body: JSON.stringify({ package_id: packageId }),
            });
            if (!res.ok) throw new Error('กำหนดแพ็กเกจไม่สำเร็จ');
        } catch (err) {
            alert(err.message);
        } finally {
            setAssigningPkg(null);
        }
    };

    const handleAction = async (userId, action) => {
        setActionLoading(userId);
        try {
            const res = await fetch(`${API_BASE}/admin/users/${userId}/${action}`, {
                method: 'PUT',
                headers,
            });
            if (!res.ok) throw new Error('ดำเนินการไม่สำเร็จ');
            // Refresh
            await Promise.all([fetchUsers(activeTab), fetchStats()]);
        } catch (err) {
            alert(err.message);
        } finally {
            setActionLoading(null);
        }
    };

    const fetchActivityLogs = useCallback(async () => {
        setActivityLoading(true);
        try {
            const res = await fetch(`${API_BASE}/admin/activity-logs?limit=100`, { headers });
            if (res.ok) {
                const data = await res.json();
                setActivityLogs(data.logs || []);
            }
        } catch { /* ignore */ } finally {
            setActivityLoading(false);
        }
    }, [token]);

    const fetchQueueData = useCallback(async () => {
        setQueueLoading(true);
        try {
            const [statsRes, tasksRes] = await Promise.all([
                fetch(`${API_BASE}/admin/queue/stats`, { headers }),
                fetch(`${API_BASE}/admin/queue/tasks?limit=50`, { headers }),
            ]);
            if (statsRes.ok) {
                const data = await statsRes.json();
                setQueueStats(data.stats || null);
            }
            if (tasksRes.ok) {
                const data = await tasksRes.json();
                setQueueJobs(data.jobs || []);
            }
        } catch { /* ignore */ } finally {
            setQueueLoading(false);
        }
    }, [token]);

    const handleCancelJob = async (jobId) => {
        if (!window.confirm('ยืนยันการยกเลิกงานนี้?')) return;
        setCancellingJob(jobId);
        try {
            const res = await fetch(`${API_BASE}/admin/queue/tasks/${jobId}`, {
                method: 'DELETE', headers,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'ยกเลิกไม่สำเร็จ');
            fetchQueueData();
        } catch (err) {
            alert(err.message);
        } finally {
            setCancellingJob(null);
        }
    };

    useEffect(() => {
        if (activeTab === 'activity') fetchActivityLogs();
        if (activeTab === 'queue') {
            fetchQueueData();
            const interval = setInterval(fetchQueueData, 30000);
            return () => clearInterval(interval);
        }
    }, [activeTab]);

    const tabs = [
        { key: 'pending', label: 'รอการอนุมัติ', count: stats.pending || 0 },
        { key: 'approved', label: 'อนุมัติแล้ว', count: stats.approved || 0 },
        { key: 'rejected', label: 'ถูกปฏิเสธ', count: stats.rejected || 0 },
        { key: 'suspended', label: 'ถูกระงับ', count: stats.suspended || 0 },
        { key: 'all', label: 'ทั้งหมด', count: Object.values(stats).reduce((a, b) => a + b, 0) },
        { key: 'activity', label: 'Activity Log', count: null },
        { key: 'queue', label: 'Queue Monitor', count: null },
    ];

    return (
        <div className="app-wrapper">
            {/* Navbar */}
            <nav className="app-nav">
                <Link to="/" className="nav-logo" style={{ textDecoration: 'none' }}>
                    Tim<span>Sum</span>
                </Link>
                <div className="nav-tabs">
                    <Link to="/" className="nav-tab" style={{ textDecoration: 'none' }}>
                        หน้าหลัก
                    </Link>
                    <button className="nav-tab nav-tab-active">
                        จัดการผู้ใช้
                    </button>
                </div>
                <div className="nav-right">
                    <div className="nav-avatar-wrapper" ref={dropdownRef}>
                        <div
                            className="nav-avatar"
                            onClick={() => setShowDropdown(prev => !prev)}
                        >
                            {userInfo.initials}
                        </div>
                        {showDropdown && (
                            <div className="nav-dropdown">
                                <div className="nav-dropdown-header">
                                    <span className="nav-dropdown-name">{userInfo.username}</span>
                                    <span className="nav-dropdown-email">{userInfo.email}</span>
                                </div>
                                <div className="nav-dropdown-divider" />
                                <button className="nav-dropdown-item">
                                    <span className="nav-dropdown-item-icon">&#128100;</span>
                                    โปรไฟล์
                                </button>
                                <div className="nav-dropdown-divider" />
                                <button className="nav-dropdown-item nav-dropdown-logout" onClick={logout}>
                                    <span className="nav-dropdown-item-icon">&#8594;</span>
                                    ออกจากระบบ
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </nav>

            {/* Content */}
            <div className="upload-content" style={{ maxWidth: 960 }}>
                <div className="upload-page-header">
                    <h1>จัดการผู้ใช้งาน</h1>
                    <p>อนุมัติ ปฏิเสธ หรือระงับบัญชีผู้ใช้ที่ลงทะเบียนเข้าใช้งาน</p>
                </div>

                {/* Stats Cards */}
                <div style={{
                    display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
                    gap: '0.75rem', marginBottom: '1.5rem'
                }}>
                    {tabs.filter(t => t.key !== 'all').map(t => {
                        const s = STATUS_LABELS[t.key] || {};
                        return (
                            <div key={t.key} onClick={() => setActiveTab(t.key)} style={{
                                background: activeTab === t.key ? s.bg : 'var(--surface-elevated)',
                                border: `1.5px solid ${activeTab === t.key ? s.color : 'var(--border-color)'}`,
                                borderRadius: 12, padding: '1rem 1.25rem', cursor: 'pointer',
                                transition: 'all 0.15s',
                            }}>
                                <div style={{ fontSize: '1.75rem', fontWeight: 700, color: s.color }}>
                                    {t.count}
                                </div>
                                <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: 2 }}>
                                    {t.label}
                                </div>
                            </div>
                        );
                    })}
                </div>

                {/* Tabs */}
                <div style={{
                    display: 'flex', gap: '0.35rem', marginBottom: '1rem',
                    borderBottom: '1px solid var(--border-color)', paddingBottom: '0.5rem',
                    overflowX: 'auto',
                }}>
                    {tabs.map(t => (
                        <button key={t.key} onClick={() => setActiveTab(t.key)}
                            style={{
                                padding: '0.5rem 1rem', borderRadius: 8, border: 'none',
                                fontSize: '0.88rem', fontWeight: 600, cursor: 'pointer',
                                fontFamily: 'var(--font-thai)',
                                background: activeTab === t.key ? 'var(--text-primary)' : 'transparent',
                                color: activeTab === t.key ? 'var(--bg-primary)' : 'var(--text-secondary)',
                                transition: 'all 0.15s', whiteSpace: 'nowrap',
                            }}>
                            {t.label}{t.count !== null ? ` (${t.count})` : ''}
                        </button>
                    ))}
                </div>

                {/* Queue Monitor Tab */}
                {activeTab === 'queue' ? (
                    <div>
                        {/* Stats */}
                        {queueStats && (
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px,1fr))', gap: '0.75rem', marginBottom: '1.25rem' }}>
                                {[
                                    { label: 'รอดำเนินการ', value: queueStats.queued, color: '#c68a19' },
                                    { label: 'กำลังประมวลผล', value: queueStats.processing, color: '#2563eb' },
                                    { label: 'สำเร็จวันนี้', value: queueStats.completed_today, color: '#2d8a4e' },
                                    { label: 'ล้มเหลว', value: queueStats.failed, color: '#c0392b' },
                                    { label: 'ยกเลิกแล้ว', value: queueStats.cancelled, color: '#7f8c8d' },
                                ].map(s => (
                                    <div key={s.label} style={{
                                        background: 'var(--surface-elevated)', border: `1.5px solid var(--border-color)`,
                                        borderRadius: 12, padding: '0.85rem 1rem',
                                    }}>
                                        <div style={{ fontSize: '1.6rem', fontWeight: 700, color: s.color }}>{s.value}</div>
                                        <div style={{ fontSize: '0.8rem', color: 'var(--text-secondary)', marginTop: 2 }}>{s.label}</div>
                                    </div>
                                ))}
                            </div>
                        )}
                        {/* Job list */}
                        {queueLoading && !queueJobs.length ? (
                            <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลด...</span></div>
                        ) : queueJobs.length === 0 ? (
                            <div className="history-empty"><h3>ยังไม่มีงานในระบบ</h3></div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                                {/* Header */}
                                <div style={{
                                    display: 'grid', gridTemplateColumns: '2fr 1.2fr 1fr 1.4fr auto',
                                    gap: '0.75rem', padding: '0.4rem 0.75rem',
                                    fontSize: '0.75rem', color: 'var(--text-muted)', fontWeight: 600,
                                }}>
                                    <span>ชื่อไฟล์</span><span>User ID</span><span>สถานะ</span><span>เวลา</span><span></span>
                                </div>
                                {queueJobs.map(job => {
                                    const statusColor = {
                                        queued: '#c68a19', processing: '#2563eb',
                                        completed: '#2d8a4e', failed: '#c0392b', cancelled: '#7f8c8d',
                                    }[job.status] || 'var(--text-muted)';
                                    const canCancel = job.status === 'queued' || job.status === 'processing';
                                    return (
                                        <div key={job._id} style={{
                                            display: 'grid', gridTemplateColumns: '2fr 1.2fr 1fr 1.4fr auto',
                                            alignItems: 'center', gap: '0.75rem',
                                            padding: '0.6rem 0.75rem', borderRadius: 8,
                                            background: 'var(--bg-secondary)', fontSize: '0.83rem',
                                        }}>
                                            <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-primary)' }}>
                                                {job.audio_file || '—'}
                                            </span>
                                            <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: '0.74rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                                {job.user_id}
                                            </span>
                                            <span style={{ color: statusColor, fontWeight: 600, fontSize: '0.78rem' }}>
                                                {job.status}
                                            </span>
                                            <span style={{ color: 'var(--text-muted)', fontSize: '0.76rem' }}>
                                                {job.created_at ? new Date(job.created_at).toLocaleString('th-TH') : '—'}
                                            </span>
                                            <span>
                                                {canCancel && (
                                                    <button
                                                        onClick={() => handleCancelJob(job._id)}
                                                        disabled={cancellingJob === job._id}
                                                        style={{
                                                            padding: '0.25rem 0.6rem', borderRadius: 6, border: '1px solid var(--error)',
                                                            background: 'transparent', color: 'var(--error)', fontSize: '0.74rem',
                                                            cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                            opacity: cancellingJob === job._id ? 0.5 : 1,
                                                        }}>
                                                        {cancellingJob === job._id ? '...' : 'ยกเลิก'}
                                                    </button>
                                                )}
                                            </span>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                ) : activeTab === 'activity' ? (
                    <div>
                        {activityLoading ? (
                            <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลด...</span></div>
                        ) : activityLogs.length === 0 ? (
                            <div className="history-empty"><h3>ยังไม่มี Activity Log</h3></div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                                {activityLogs.map((log, i) => (
                                    <div key={i} style={{
                                        display: 'grid', gridTemplateColumns: '1fr 2fr 1.5fr',
                                        alignItems: 'center', gap: '0.75rem',
                                        padding: '0.65rem 1rem', borderRadius: 8,
                                        background: 'var(--bg-secondary)', fontSize: '0.84rem',
                                    }}>
                                        <span style={{ color: 'var(--text-muted)', fontFamily: 'monospace', fontSize: '0.78rem', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                                            {log.user_id}
                                        </span>
                                        <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                                            {ACTION_LABELS[log.action] || log.action}
                                            {log.metadata?.filename && (
                                                <span style={{ fontWeight: 400, color: 'var(--text-muted)', marginLeft: '0.4rem', fontSize: '0.78rem' }}>
                                                    · {log.metadata.filename}
                                                </span>
                                            )}
                                        </span>
                                        <span style={{ color: 'var(--text-muted)', fontSize: '0.78rem', textAlign: 'right' }}>
                                            {new Date(log.timestamp).toLocaleString('th-TH')}
                                        </span>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                ) : (
                <>
                {/* Error */}
                {error && (
                    <div className="upload-error" style={{ marginBottom: '1rem' }}>
                        {error}
                    </div>
                )}

                {/* Loading */}
                {loading ? (
                    <div className="history-loading">
                        <div className="history-spinner" />
                        <span>กำลังโหลด...</span>
                    </div>
                ) : users.length === 0 ? (
                    <div className="history-empty">
                        <div className="history-empty-icon">&#128101;</div>
                        <h3>ไม่มีผู้ใช้ในหมวดนี้</h3>
                    </div>
                ) : (
                    /* User List */
                    <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                        {users.map(user => {
                            const st = STATUS_LABELS[user.status] || STATUS_LABELS.pending;
                            const isActioning = actionLoading === user._id;
                            return (
                                <div key={user._id} className="upload-card" style={{ marginBottom: 0 }}>
                                    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '1rem' }}>
                                        {/* Avatar */}
                                        <div className="nav-avatar" style={{ width: 42, height: 42, fontSize: '0.85rem', flexShrink: 0 }}>
                                            {(user.username || user.email || '').substring(0, 2).toUpperCase()}
                                        </div>
                                        {/* Info */}
                                        <div style={{ flex: 1, minWidth: 0 }}>
                                            <div style={{ display: 'flex', alignItems: 'center', gap: '0.6rem', flexWrap: 'wrap' }}>
                                                <span style={{ fontWeight: 600, fontSize: '0.95rem' }}>
                                                    {user.first_name && user.last_name
                                                        ? `${user.first_name} ${user.last_name}`
                                                        : user.username || user.email}
                                                </span>
                                                <span style={{
                                                    fontSize: '0.75rem', fontWeight: 600, padding: '0.15rem 0.6rem',
                                                    borderRadius: 999, background: st.bg, color: st.color,
                                                }}>
                                                    {st.text}
                                                </span>
                                                {user.role && user.role !== 'user' && (
                                                    <span style={{
                                                        fontSize: '0.72rem', fontWeight: 600, padding: '0.15rem 0.5rem',
                                                        borderRadius: 999, background: 'rgba(184,134,11,0.12)',
                                                        color: 'var(--accent-primary)',
                                                    }}>
                                                        {user.role}
                                                    </span>
                                                )}
                                            </div>
                                            <div style={{ fontSize: '0.82rem', color: 'var(--text-muted)', marginTop: 4 }}>
                                                {user.email}
                                                {user.organization && <> &middot; {user.organization}</>}
                                                {user.phone && <> &middot; {user.phone}</>}
                                            </div>
                                            {user.registered_at && (
                                                <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: 2 }}>
                                                    ลงทะเบียน: {new Date(user.registered_at).toLocaleString('th-TH')}
                                                </div>
                                            )}
                                        </div>
                                        {/* Actions */}
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.5rem', flexShrink: 0, alignItems: 'flex-end' }}>
                                            {/* Package assignment (for approved non-admin users) */}
                                            {user.status === 'approved' && user.role === 'user' && packages.length > 0 && (
                                                <select
                                                    disabled={assigningPkg === user._id}
                                                    onChange={(e) => {
                                                        if (e.target.value) handleAssignPackage(user._id, e.target.value);
                                                    }}
                                                    defaultValue=""
                                                    style={{
                                                        padding: '0.4rem 0.6rem', borderRadius: 8, fontSize: '0.78rem',
                                                        border: '1px solid var(--border-color)', background: 'var(--surface-elevated)',
                                                        color: 'var(--text-primary)', fontFamily: 'var(--font-thai)',
                                                        cursor: 'pointer', minWidth: 140,
                                                    }}
                                                >
                                                    <option value="" disabled>กำหนดแพ็กเกจ</option>
                                                    {packages.filter(p => (p.tier || 0) < 10).map(p => (
                                                        <option key={p._id} value={p._id}>{p.name}</option>
                                                    ))}
                                                </select>
                                            )}
                                            <div style={{ display: 'flex', gap: '0.5rem', flexWrap: 'wrap' }}>
                                                {user.status === 'pending' && (
                                                    <>
                                                        <button
                                                            onClick={() => handleAction(user._id, 'approve')}
                                                            disabled={isActioning}
                                                            style={{
                                                                padding: '0.45rem 1rem', borderRadius: 8, border: 'none',
                                                                background: '#2d8a4e', color: '#fff', fontWeight: 600,
                                                                fontSize: '0.82rem', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                                opacity: isActioning ? 0.5 : 1,
                                                            }}>
                                                            {isActioning ? '...' : 'อนุมัติ'}
                                                        </button>
                                                        <button
                                                            onClick={() => handleAction(user._id, 'reject')}
                                                            disabled={isActioning}
                                                            style={{
                                                                padding: '0.45rem 1rem', borderRadius: 8,
                                                                border: '1px solid var(--error)', background: 'transparent',
                                                                color: 'var(--error)', fontWeight: 600, fontSize: '0.82rem',
                                                                cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                                opacity: isActioning ? 0.5 : 1,
                                                            }}>
                                                            {isActioning ? '...' : 'ปฏิเสธ'}
                                                        </button>
                                                    </>
                                                )}
                                                {user.status === 'approved' && user.role === 'user' && (
                                                    <button
                                                        onClick={() => handleAction(user._id, 'suspend')}
                                                        disabled={isActioning}
                                                        style={{
                                                            padding: '0.45rem 1rem', borderRadius: 8,
                                                            border: '1px solid var(--text-muted)', background: 'transparent',
                                                            color: 'var(--text-secondary)', fontWeight: 600, fontSize: '0.82rem',
                                                            cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                            opacity: isActioning ? 0.5 : 1,
                                                        }}>
                                                        ระงับ
                                                    </button>
                                                )}
                                                {(user.status === 'rejected' || user.status === 'suspended') && (
                                                    <button
                                                        onClick={() => handleAction(user._id, 'approve')}
                                                        disabled={isActioning}
                                                        style={{
                                                            padding: '0.45rem 1rem', borderRadius: 8, border: 'none',
                                                            background: '#2d8a4e', color: '#fff', fontWeight: 600,
                                                            fontSize: '0.82rem', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                            opacity: isActioning ? 0.5 : 1,
                                                        }}>
                                                        อนุมัติ
                                                    </button>
                                                )}
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
                </>
                )}
            </div>
        </div>
    );
}

export default AdminDashboard;
