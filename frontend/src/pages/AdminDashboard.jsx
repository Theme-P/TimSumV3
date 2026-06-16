import { useState, useEffect, useCallback, useRef } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Link } from 'react-router-dom';
import PackageManager from '../components/admin/PackageManager';

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
    const [activeAdminView, setActiveAdminView] = useState('users');
    const [packageRequests, setPackageRequests] = useState([]);
    const [requestStatusFilter, setRequestStatusFilter] = useState('pending');
    const [requestsLoading, setRequestsLoading] = useState(false);
    const [requestActionLoading, setRequestActionLoading] = useState(null);
    const dropdownRef = useRef(null);

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

    const fetchPackageRequests = useCallback(async () => {
        setRequestsLoading(true);
        try {
            const query = requestStatusFilter === 'all' ? '' : `?status=${requestStatusFilter}`;
            const res = await fetch(`${API_BASE}/admin/package-requests${query}`, { headers });
            if (res.ok) {
                const data = await res.json();
                setPackageRequests(data.requests || []);
            }
        } catch { /* ignore */ } finally {
            setRequestsLoading(false);
        }
    }, [token, requestStatusFilter]);

    useEffect(() => {
        fetchUsers(activeTab);
        fetchStats();
        fetchPackages();
        fetchPackageRequests();
    }, [activeTab, requestStatusFilter]);

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

    const handlePackageRequestAction = async (requestId, action) => {
        const adminNote = action === 'reject'
            ? window.prompt('เหตุผลที่ปฏิเสธ (ไม่บังคับ)', '') || ''
            : '';
        if (action === 'approve' && !window.confirm('ยืนยันอนุมัติและเปลี่ยนแพ็กเกจให้ผู้ใช้นี้?')) {
            return;
        }

        setRequestActionLoading(requestId);
        try {
            const res = await fetch(`${API_BASE}/admin/package-requests/${requestId}/${action}`, {
                method: 'PUT',
                headers,
                body: JSON.stringify({ admin_note: adminNote, reset_usage: true }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'ดำเนินการคำขอไม่สำเร็จ');
            await Promise.all([fetchPackageRequests(), fetchUsers(activeTab), fetchPackages()]);
        } catch (err) {
            alert(err.message);
        } finally {
            setRequestActionLoading(null);
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

    const handleDeleteUser = async (userId) => {
        if (!window.confirm("ยืนยันการลบผู้ใช้? การกระทำนี้จะลบข้อมูลทั้งหมดที่เกี่ยวข้องและไม่สามารถย้อนกลับได้")) return;
        setActionLoading(userId);
        try {
            const res = await fetch(`${API_BASE}/admin/users/${userId}`, {
                method: 'DELETE',
                headers,
            });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || 'ลบผู้ใช้ไม่สำเร็จ');
            }
            await Promise.all([fetchUsers(activeTab), fetchStats()]);
        } catch (err) {
            alert(err.message);
        } finally {
            setActionLoading(null);
        }
    };

    const tabs = [
        { key: 'pending', label: 'รอการอนุมัติ', count: stats.pending || 0 },
        { key: 'approved', label: 'อนุมัติแล้ว', count: stats.approved || 0 },
        { key: 'rejected', label: 'ถูกปฏิเสธ', count: stats.rejected || 0 },
        { key: 'suspended', label: 'ถูกระงับ', count: stats.suspended || 0 },
        { key: 'all', label: 'ทั้งหมด', count: Object.values(stats).reduce((a, b) => a + b, 0) },
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
                    <button className="nav-tab nav-tab-active">จัดการผู้ใช้</button>
                    <Link to="/admin/monitoring" className="nav-tab" style={{ textDecoration: 'none' }}>ระบบ & คิว</Link>
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

                <div style={{
                    display: 'flex', gap: '0.5rem', marginBottom: '1.25rem',
                    borderBottom: '1px solid var(--border-color)', paddingBottom: '0.75rem',
                    overflowX: 'auto',
                }}>
                    {[
                        { key: 'users', label: 'ผู้ใช้งาน' },
                        { key: 'requests', label: `คำขอแพ็กเกจ (${packageRequests.filter(r => r.status === 'pending').length})` },
                        { key: 'packages', label: 'จัดการแพ็กเกจ' },
                    ].map(view => (
                        <button key={view.key} onClick={() => setActiveAdminView(view.key)}
                            style={{
                                padding: '0.55rem 1rem', borderRadius: 8, border: 'none',
                                fontSize: '0.88rem', fontWeight: 700, cursor: 'pointer',
                                fontFamily: 'var(--font-thai)',
                                background: activeAdminView === view.key ? 'var(--text-primary)' : 'transparent',
                                color: activeAdminView === view.key ? 'var(--bg-primary)' : 'var(--text-secondary)',
                                whiteSpace: 'nowrap',
                            }}>
                            {view.label}
                        </button>
                    ))}
                </div>

                {activeAdminView === 'users' && (
                    <>
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
                                                {userInfo.role === 'superadmin' && user.role !== 'superadmin' && (
                                                    <button
                                                        onClick={() => handleDeleteUser(user._id)}
                                                        disabled={isActioning}
                                                        style={{
                                                            padding: '0.45rem 1rem', borderRadius: 8,
                                                            border: '1px solid var(--error)', background: 'transparent',
                                                            color: 'var(--error)', fontWeight: 600, fontSize: '0.82rem',
                                                            cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                            opacity: isActioning ? 0.5 : 1,
                                                        }}>
                                                        {isActioning ? '...' : 'ลบผู้ใช้'}
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

                {activeAdminView === 'requests' && (
                    <div>
                        <div style={{
                            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
                            marginBottom: '1rem', gap: '1rem', flexWrap: 'wrap',
                        }}>
                            <div>
                                <h2 style={{ margin: 0, fontSize: '1.05rem' }}>คำขอเปลี่ยนแพ็กเกจ</h2>
                                <p style={{ margin: '0.2rem 0 0', fontSize: '0.85rem', color: 'var(--text-secondary)' }}>
                                    ตรวจสอบและอนุมัติการ upgrade/downgrade จากผู้ใช้
                                </p>
                            </div>
                            <select
                                value={requestStatusFilter}
                                onChange={e => setRequestStatusFilter(e.target.value)}
                                style={{
                                    padding: '0.5rem 0.75rem', borderRadius: 8,
                                    border: '1px solid var(--border-color)',
                                    background: 'var(--surface-elevated)',
                                    color: 'var(--text-primary)',
                                    fontFamily: 'var(--font-thai)',
                                }}
                            >
                                <option value="pending">รอพิจารณา</option>
                                <option value="approved">อนุมัติแล้ว</option>
                                <option value="rejected">ปฏิเสธแล้ว</option>
                                <option value="cancelled">ยกเลิกแล้ว</option>
                                <option value="all">ทั้งหมด</option>
                            </select>
                        </div>

                        {requestsLoading ? (
                            <div className="history-loading">
                                <div className="history-spinner" />
                                <span>กำลังโหลด...</span>
                            </div>
                        ) : packageRequests.length === 0 ? (
                            <div className="history-empty">
                                <h3>ไม่มีคำขอในสถานะนี้</h3>
                            </div>
                        ) : (
                            <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                                {packageRequests.map(req => {
                                    const fullName = req.user?.first_name || req.user?.last_name
                                        ? `${req.user?.first_name || ''} ${req.user?.last_name || ''}`.trim()
                                        : req.user?.username || req.user?.email || 'ผู้ใช้';
                                    const isWorking = requestActionLoading === req._id;
                                    return (
                                        <div key={req._id} className="upload-card" style={{ marginBottom: 0 }}>
                                            <div style={{ display: 'flex', justifyContent: 'space-between', gap: '1rem', alignItems: 'flex-start' }}>
                                                <div style={{ minWidth: 0 }}>
                                                    <div style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                                                        <strong>{fullName}</strong>
                                                        <span style={{
                                                            fontSize: '0.72rem', fontWeight: 700,
                                                            padding: '0.12rem 0.5rem', borderRadius: 999,
                                                            background: req.status === 'pending' ? 'rgba(198,138,25,0.12)' : 'var(--bg-tertiary)',
                                                            color: req.status === 'pending' ? '#c68a19' : 'var(--text-secondary)',
                                                        }}>
                                                            {req.status}
                                                        </span>
                                                        <span style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>
                                                            {req.request_type}
                                                        </span>
                                                    </div>
                                                    <div style={{ fontSize: '0.84rem', color: 'var(--text-secondary)', marginTop: 6 }}>
                                                        {req.current_package?.name || 'ไม่มีแพ็กเกจ'} → <strong>{req.requested_package?.name}</strong>
                                                    </div>
                                                    <div style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: 4 }}>
                                                        {req.user?.email}
                                                        {req.user?.organization && <> · {req.user.organization}</>}
                                                        {req.requested_at && <> · ส่งเมื่อ {new Date(req.requested_at).toLocaleString('th-TH')}</>}
                                                    </div>
                                                    {req.note && (
                                                        <div style={{
                                                            marginTop: '0.65rem', padding: '0.6rem 0.75rem',
                                                            borderRadius: 8, background: 'var(--bg-tertiary)',
                                                            fontSize: '0.84rem', color: 'var(--text-secondary)',
                                                        }}>
                                                            {req.note}
                                                        </div>
                                                    )}
                                                    {req.admin_note && (
                                                        <div style={{ marginTop: '0.4rem', fontSize: '0.8rem', color: 'var(--text-muted)' }}>
                                                            หมายเหตุแอดมิน: {req.admin_note}
                                                        </div>
                                                    )}
                                                </div>
                                                {req.status === 'pending' && (
                                                    <div style={{ display: 'flex', gap: '0.5rem', flexShrink: 0 }}>
                                                        <button
                                                            onClick={() => handlePackageRequestAction(req._id, 'approve')}
                                                            disabled={isWorking}
                                                            style={{
                                                                padding: '0.45rem 0.9rem', borderRadius: 8, border: 'none',
                                                                background: '#2d8a4e', color: '#fff', fontWeight: 700,
                                                                fontSize: '0.82rem', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                                opacity: isWorking ? 0.5 : 1,
                                                            }}
                                                        >
                                                            อนุมัติ
                                                        </button>
                                                        <button
                                                            onClick={() => handlePackageRequestAction(req._id, 'reject')}
                                                            disabled={isWorking}
                                                            style={{
                                                                padding: '0.45rem 0.9rem', borderRadius: 8,
                                                                border: '1px solid var(--error)', background: 'transparent',
                                                                color: 'var(--error)', fontWeight: 700, fontSize: '0.82rem',
                                                                cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                                opacity: isWorking ? 0.5 : 1,
                                                            }}
                                                        >
                                                            ปฏิเสธ
                                                        </button>
                                                    </div>
                                                )}
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                )}

                {activeAdminView === 'packages' && (
                    <div className="upload-card" style={{ marginBottom: 0 }}>
                        <PackageManager token={token} />
                    </div>
                )}
            </div>
        </div>
    );
}

export default AdminDashboard;
