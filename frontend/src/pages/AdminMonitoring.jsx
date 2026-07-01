import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Link } from 'react-router-dom';
import ServerResources from '../components/admin/ServerResources';
import Icon from '../components/ui/Icon';

const API_BASE = '/api';

const QUEUE_STATUS_OPTIONS = [
    { key: 'all', label: 'ทั้งหมด', statKey: 'total', color: '#6b7280' },
    { key: 'queued', label: 'รอดำเนินการ', statKey: 'queued', color: '#c68a19' },
    { key: 'processing', label: 'กำลังประมวลผล', statKey: 'processing', color: '#2563eb' },
    { key: 'completed', label: 'สำเร็จ', statKey: 'completed', color: '#2d8a4e' },
    { key: 'failed', label: 'ล้มเหลว', statKey: 'failed', color: '#c0392b' },
    { key: 'cancelled', label: 'ยกเลิกแล้ว', statKey: 'cancelled', color: '#7f8c8d' },
];

const QUEUE_STATUS_LABELS = Object.fromEntries(
    QUEUE_STATUS_OPTIONS.filter(option => option.key !== 'all')
        .map(option => [option.key, option.label]),
);

const PROCESS_STEP_LABELS = {
    queued: 'รอ Worker รับงาน',
    loading_model: 'โหลดโมเดล',
    loading_audio: 'โหลดไฟล์เสียง',
    transcribing: 'ถอดเสียง',
    diarizing: 'แยกผู้พูด',
    summarizing: 'สรุปการประชุม',
    completed: 'เสร็จสมบูรณ์',
};

function UserIdentity({ user, userId }) {
    const displayName = user?.display_name || 'ไม่พบข้อมูลผู้ใช้';
    const secondary = user?.email || user?.username || (userId ? `ID: ${userId}` : '—');
    return (
        <span className="monitor-user" title={userId ? `User ID: ${userId}` : undefined}>
            <strong>{displayName}</strong>
            <small>{secondary}</small>
        </span>
    );
}

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

function AdminMonitoring() {
    const { token, logout } = useAuth();
    const userInfo = token ? getUserInfo(token) : { initials: '', username: '', email: '', role: 'user' };

    const [activeTab, setActiveTab] = useState('queue');
    const [showDropdown, setShowDropdown] = useState(false);
    const dropdownRef = useRef(null);

    // Activity Log state
    const [activityLogs, setActivityLogs] = useState([]);
    const [activityUsers, setActivityUsers] = useState([]);
    const [activityTotal, setActivityTotal] = useState(0);
    const [activityUserFilter, setActivityUserFilter] = useState('all');
    const [activitySortOrder, setActivitySortOrder] = useState('desc');
    const [activityLoading, setActivityLoading] = useState(false);

    // Queue state
    const [queueStats, setQueueStats] = useState(null);
    const [queueJobs, setQueueJobs] = useState([]);
    const [queueUsers, setQueueUsers] = useState([]);
    const [queueTotal, setQueueTotal] = useState(0);
    const [queueStatusFilter, setQueueStatusFilter] = useState('all');
    const [queueUserFilter, setQueueUserFilter] = useState('all');
    const [queueLoading, setQueueLoading] = useState(false);
    const [cancellingJob, setCancellingJob] = useState(null);
    const [notice, setNotice] = useState(null);

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

    const headers = useMemo(() => ({
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
    }), [token]);

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

    useEffect(() => {
        if (!notice) return;
        const timer = setTimeout(() => setNotice(null), 4500);
        return () => clearTimeout(timer);
    }, [notice]);

    const fetchActivityLogs = useCallback(async () => {
        setActivityLoading(true);
        try {
            const params = new URLSearchParams({
                limit: '100',
                order: activitySortOrder,
            });
            if (activityUserFilter !== 'all') params.set('user_id', activityUserFilter);
            const res = await fetch(`${API_BASE}/admin/activity-logs?${params.toString()}`, { headers });
            const data = await res.json();
            if (!res.ok) {
                throw new Error(data.detail || 'โหลด Activity Log ไม่สำเร็จ');
            }
            setActivityLogs(data.logs || []);
            setActivityUsers(data.users || []);
            setActivityTotal(data.total ?? 0);
        } catch (err) {
            setNotice({ type: 'error', text: err.message });
        } finally {
            setActivityLoading(false);
        }
    }, [headers, activitySortOrder, activityUserFilter]);

    const fetchQueueData = useCallback(async () => {
        setQueueLoading(true);
        try {
            const params = new URLSearchParams({ limit: '100' });
            if (queueStatusFilter !== 'all') params.set('status', queueStatusFilter);
            if (queueUserFilter !== 'all') params.set('user_id', queueUserFilter);
            const [statsRes, tasksRes] = await Promise.all([
                fetch(`${API_BASE}/admin/queue/stats`, { headers }),
                fetch(`${API_BASE}/admin/queue/tasks?${params.toString()}`, { headers }),
            ]);
            const statsData = await statsRes.json();
            const tasksData = await tasksRes.json();
            if (!statsRes.ok) {
                throw new Error(statsData.detail || 'โหลดสถานะคิวไม่สำเร็จ');
            }
            if (!tasksRes.ok) {
                throw new Error(tasksData.detail || 'โหลดรายการงานไม่สำเร็จ');
            }
            setQueueStats(statsData.stats || null);
            setQueueJobs(tasksData.jobs || []);
            setQueueUsers(tasksData.users || []);
            setQueueTotal(tasksData.total ?? tasksData.count ?? 0);
        } catch (err) {
            setNotice({ type: 'error', text: err.message });
        } finally {
            setQueueLoading(false);
        }
    }, [headers, queueStatusFilter, queueUserFilter]);

    const handleCancelJob = async (jobId) => {
        if (!window.confirm('ยืนยันการยกเลิกงานนี้?')) return;
        setCancellingJob(jobId);
        try {
            const res = await fetch(`${API_BASE}/admin/queue/tasks/${jobId}`, {
                method: 'DELETE', headers,
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'ยกเลิกไม่สำเร็จ');
            setNotice({ type: 'success', text: 'ยกเลิกงานเรียบร้อย' });
            fetchQueueData();
        } catch (err) {
            setNotice({ type: 'error', text: err.message });
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
    }, [activeTab, fetchActivityLogs, fetchQueueData]);

    const tabs = [
        { key: 'queue', label: 'Queue Monitor' },
        { key: 'activity', label: 'Activity Log' },
        { key: 'resources', label: 'ทรัพยากรเซิร์ฟเวอร์' },
    ];

    return (
        <div className="app-wrapper">
            {/* Navbar */}
            <nav className="app-nav">
                <Link to="/" className="nav-logo" style={{ textDecoration: 'none' }}>
                    Tim<span>Sum</span>
                </Link>
                <div className="nav-tabs">
                    <Link to="/" className="nav-tab" style={{ textDecoration: 'none' }}>หน้าหลัก</Link>
                    <Link to="/admin" className="nav-tab" style={{ textDecoration: 'none' }}>จัดการผู้ใช้</Link>
                    <button className="nav-tab nav-tab-active">ระบบ & คิว</button>
                    <Link to="/admin/llm" className="nav-tab" style={{ textDecoration: 'none' }}>ตั้งค่า LLM</Link>
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
                                    <Icon name="user" className="nav-dropdown-item-icon" />
                                    โปรไฟล์
                                </button>
                                <div className="nav-dropdown-divider" />
                                <button className="nav-dropdown-item nav-dropdown-logout" onClick={logout}>
                                    <Icon name="logout" className="nav-dropdown-item-icon" />
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
                    <h1>ระบบ & คิว</h1>
                    <p>ตรวจสอบสถานะคิวงาน, Activity Log และทรัพยากรเซิร์ฟเวอร์</p>
                </div>

                {/* Tabs */}
                <div style={{
                    display: 'flex', gap: '0.35rem', marginBottom: '1.5rem',
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
                            {t.label}
                        </button>
                    ))}
                </div>

                {/* Tab Content */}
                {notice && (
                    <div className={`admin-notice admin-notice-${notice.type}`}>
                        {notice.text}
                    </div>
                )}

                {activeTab === 'resources' ? (
                    <ServerResources />
                ) : activeTab === 'queue' ? (
                    <div>
                        {/* Queue Stats */}
                        {queueStats && (
                            <div className="monitor-stat-grid">
                                {QUEUE_STATUS_OPTIONS.map(option => (
                                    <button
                                        key={option.key}
                                        type="button"
                                        className={`monitor-stat-card ${queueStatusFilter === option.key ? 'active' : ''}`}
                                        style={{ '--monitor-status-color': option.color }}
                                        onClick={() => setQueueStatusFilter(option.key)}
                                        aria-pressed={queueStatusFilter === option.key}
                                    >
                                        <strong>
                                            {option.key === 'all'
                                                ? queueStats.total ?? ['queued', 'processing', 'completed', 'failed', 'cancelled']
                                                    .reduce((sum, key) => sum + (queueStats[key] || 0), 0)
                                                : queueStats[option.statKey] || 0}
                                        </strong>
                                        <span>{option.label}</span>
                                        {option.key === 'completed' && (
                                            <small>วันนี้ {queueStats.completed_today || 0}</small>
                                        )}
                                    </button>
                                ))}
                            </div>
                        )}

                        <div className="monitor-filter-bar">
                            <label>
                                <span>สถานะ Process</span>
                                <select
                                    value={queueStatusFilter}
                                    onChange={event => setQueueStatusFilter(event.target.value)}
                                >
                                    {QUEUE_STATUS_OPTIONS.map(option => (
                                        <option key={option.key} value={option.key}>{option.label}</option>
                                    ))}
                                </select>
                            </label>
                            <label>
                                <span>ผู้ใช้งาน</span>
                                <select
                                    value={queueUserFilter}
                                    onChange={event => setQueueUserFilter(event.target.value)}
                                >
                                    <option value="all">ผู้ใช้ทั้งหมด</option>
                                    {queueUsers.map(user => (
                                        <option key={user.id} value={user.id}>
                                            {user.display_name}{user.email ? ` — ${user.email}` : ''}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <button
                                type="button"
                                className="monitor-filter-reset"
                                onClick={() => {
                                    setQueueStatusFilter('all');
                                    setQueueUserFilter('all');
                                }}
                                disabled={queueStatusFilter === 'all' && queueUserFilter === 'all'}
                            >
                                ล้างตัวกรอง
                            </button>
                            <span className="monitor-filter-count">
                                แสดง {queueJobs.length} จาก {queueTotal} งาน
                            </span>
                        </div>

                        {/* Job list */}
                        {queueLoading && !queueJobs.length ? (
                            <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลด...</span></div>
                        ) : queueJobs.length === 0 ? (
                            <div className="history-empty">
                                <h3>{queueStatusFilter === 'all' && queueUserFilter === 'all' ? 'ยังไม่มีงานในระบบ' : 'ไม่พบงานตามตัวกรอง'}</h3>
                                {(queueStatusFilter !== 'all' || queueUserFilter !== 'all') && (
                                    <button
                                        className="btn btn-secondary"
                                        onClick={() => {
                                            setQueueStatusFilter('all');
                                            setQueueUserFilter('all');
                                        }}
                                    >
                                        แสดงงานทั้งหมด
                                    </button>
                                )}
                            </div>
                        ) : (
                            <div className="monitor-table-scroll">
                                <div className="monitor-table">
                                    <div className="monitor-table-row monitor-table-header queue-grid">
                                        <span>ชื่อไฟล์</span><span>ผู้ใช้งาน</span><span>สถานะ Process</span><span>เวลา</span><span></span>
                                    </div>
                                    {queueJobs.map(job => {
                                        const statusOption = QUEUE_STATUS_OPTIONS.find(option => option.key === job.status);
                                        const canCancel = job.status === 'queued' || job.status === 'processing';
                                        return (
                                            <div key={job._id} className="monitor-table-row queue-grid">
                                                <span className="monitor-file-name" title={job.audio_file}>{job.audio_file || '—'}</span>
                                                <UserIdentity user={job.user} userId={job.user_id} />
                                                <span className="monitor-process">
                                                    <strong style={{ color: statusOption?.color || 'var(--text-muted)' }}>
                                                        {QUEUE_STATUS_LABELS[job.status] || job.status}
                                                    </strong>
                                                    {(job.status === 'queued' || job.status === 'processing') && (
                                                        <small>
                                                            {PROCESS_STEP_LABELS[job.current_step] || job.current_step || 'รอข้อมูล'}
                                                            {Number.isFinite(job.progress) ? ` · ${job.progress}%` : ''}
                                                        </small>
                                                    )}
                                                </span>
                                                <span className="monitor-time">
                                                    {job.created_at ? new Date(job.created_at).toLocaleString('th-TH') : '—'}
                                                </span>
                                                <span className="monitor-row-action">
                                                    {canCancel && (
                                                        <button
                                                            onClick={() => handleCancelJob(job._id)}
                                                            disabled={cancellingJob === job._id}
                                                            className="monitor-cancel-button"
                                                        >
                                                            {cancellingJob === job._id ? 'กำลังยกเลิก...' : 'ยกเลิก'}
                                                        </button>
                                                    )}
                                                </span>
                                            </div>
                                        );
                                    })}
                                </div>
                            </div>
                        )}
                    </div>
                ) : activeTab === 'activity' ? (
                    <div>
                        <div className="monitor-filter-bar">
                            <label>
                                <span>ผู้ใช้งาน</span>
                                <select
                                    value={activityUserFilter}
                                    onChange={event => setActivityUserFilter(event.target.value)}
                                >
                                    <option value="all">ผู้ใช้ทั้งหมด</option>
                                    {activityUsers.map(user => (
                                        <option key={user.id} value={user.id}>
                                            {user.display_name}{user.email ? ` — ${user.email}` : ''}
                                        </option>
                                    ))}
                                </select>
                            </label>
                            <label>
                                <span>เรียงตามเวลา</span>
                                <select
                                    value={activitySortOrder}
                                    onChange={event => setActivitySortOrder(event.target.value)}
                                >
                                    <option value="desc">ใหม่สุดก่อน (Descending)</option>
                                    <option value="asc">เก่าสุดก่อน (Ascending)</option>
                                </select>
                            </label>
                            <button
                                type="button"
                                className="monitor-filter-reset"
                                onClick={() => {
                                    setActivityUserFilter('all');
                                    setActivitySortOrder('desc');
                                }}
                                disabled={activityUserFilter === 'all' && activitySortOrder === 'desc'}
                            >
                                ล้างตัวกรอง
                            </button>
                            <span className="monitor-filter-count">
                                แสดง {activityLogs.length} จาก {activityTotal} รายการ
                            </span>
                        </div>

                        {activityLoading ? (
                            <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลด...</span></div>
                        ) : activityLogs.length === 0 ? (
                            <div className="history-empty">
                                <h3>{activityUserFilter === 'all' ? 'ยังไม่มี Activity Log' : 'ไม่พบ Activity Log ของผู้ใช้นี้'}</h3>
                            </div>
                        ) : (
                            <div className="monitor-table-scroll">
                                <div className="monitor-table activity-table">
                                    <div className="monitor-table-row monitor-table-header activity-grid">
                                        <span>ผู้ใช้งาน</span><span>กิจกรรม</span><span>เวลา</span>
                                    </div>
                                    {activityLogs.map(log => (
                                        <div key={log._id} className="monitor-table-row activity-grid">
                                            <UserIdentity user={log.user} userId={log.user_id} />
                                            <span style={{ color: 'var(--text-primary)', fontWeight: 500 }}>
                                                {ACTION_LABELS[log.action] || log.action}
                                                {log.metadata?.filename && (
                                                    <span style={{ fontWeight: 400, color: 'var(--text-muted)', marginLeft: '0.4rem', fontSize: '0.78rem' }}>
                                                        · {log.metadata.filename}
                                                    </span>
                                                )}
                                            </span>
                                            <span className="monitor-time monitor-time-right">
                                                {new Date(log.timestamp).toLocaleString('th-TH')}
                                            </span>
                                        </div>
                                    ))}
                                </div>
                            </div>
                        )}
                    </div>
                ) : null}
            </div>
        </div>
    );
}

export default AdminMonitoring;
