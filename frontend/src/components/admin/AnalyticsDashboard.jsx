import { useState, useEffect, useCallback } from 'react';

const API_BASE = '/api';

function StatCard({ label, value, sub, color }) {
    return (
        <div style={{
            background: 'var(--surface-elevated)', border: '1px solid var(--border-color)',
            borderRadius: 12, padding: '1rem 1.25rem', minWidth: 0,
        }}>
            <div style={{ fontSize: '1.75rem', fontWeight: 700, color: color || 'var(--text-primary)' }}>
                {typeof value === 'number' ? value.toLocaleString() : value}
            </div>
            <div style={{ fontSize: '0.85rem', color: 'var(--text-secondary)', marginTop: 2 }}>{label}</div>
            {sub && <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)', marginTop: 2 }}>{sub}</div>}
        </div>
    );
}

function SimpleBarChart({ data, dataKey, label, color = 'var(--accent-primary)', height = 160 }) {
    if (!data || data.length === 0) return null;
    const maxVal = Math.max(...data.map(d => d[dataKey] || 0), 1);

    return (
        <div style={{
            background: 'var(--surface-elevated)', border: '1px solid var(--border-color)',
            borderRadius: 12, padding: '1rem 1.25rem',
        }}>
            <div style={{ fontSize: '0.88rem', fontWeight: 600, marginBottom: '0.75rem' }}>{label}</div>
            <div style={{
                display: 'flex', alignItems: 'flex-end', gap: 2, height,
                borderBottom: '1px solid var(--border-color)', paddingBottom: 4,
            }}>
                {data.map((d, i) => {
                    const val = d[dataKey] || 0;
                    const pct = (val / maxVal) * 100;
                    return (
                        <div key={i} title={`${d.date}: ${val}`} style={{
                            flex: 1, minWidth: 0,
                            height: `${Math.max(pct, 2)}%`,
                            background: color, borderRadius: '3px 3px 0 0',
                            opacity: 0.8, cursor: 'default',
                            transition: 'opacity 0.15s',
                        }}
                            onMouseEnter={e => e.target.style.opacity = 1}
                            onMouseLeave={e => e.target.style.opacity = 0.8}
                        />
                    );
                })}
            </div>
            <div style={{
                display: 'flex', justifyContent: 'space-between',
                fontSize: '0.72rem', color: 'var(--text-muted)', marginTop: 4,
            }}>
                <span>{data[0]?.date?.slice(5)}</span>
                <span>{data[data.length - 1]?.date?.slice(5)}</span>
            </div>
        </div>
    );
}

function PackagePieChart({ packages }) {
    if (!packages || packages.length === 0) return null;
    const active = packages.filter(p => p.user_count > 0);
    const total = active.reduce((sum, p) => sum + p.user_count, 0);
    if (total === 0) return null;

    const colors = ['#d4a843', '#2d8a4e', '#3498db', '#e74c3c', '#9b59b6', '#1abc9c', '#f39c12'];

    return (
        <div style={{
            background: 'var(--surface-elevated)', border: '1px solid var(--border-color)',
            borderRadius: 12, padding: '1rem 1.25rem',
        }}>
            <div style={{ fontSize: '0.88rem', fontWeight: 600, marginBottom: '0.75rem' }}>ผู้ใช้ต่อแพ็กเกจ</div>
            {/* Stacked bar */}
            <div style={{ display: 'flex', height: 28, borderRadius: 8, overflow: 'hidden', marginBottom: '0.75rem' }}>
                {active.map((pkg, i) => (
                    <div key={pkg.package_id} title={`${pkg.name}: ${pkg.user_count}`} style={{
                        width: `${(pkg.user_count / total) * 100}%`,
                        background: colors[i % colors.length],
                        minWidth: 4,
                    }} />
                ))}
            </div>
            {/* Legend */}
            <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem 1rem' }}>
                {active.map((pkg, i) => (
                    <div key={pkg.package_id} style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: '0.82rem' }}>
                        <div style={{ width: 10, height: 10, borderRadius: 3, background: colors[i % colors.length], flexShrink: 0 }} />
                        <span>{pkg.name}</span>
                        <span style={{ color: 'var(--text-muted)', fontWeight: 600 }}>{pkg.user_count}</span>
                    </div>
                ))}
            </div>
        </div>
    );
}

function AnalyticsDashboard({ token }) {
    const [overview, setOverview] = useState(null);
    const [daily, setDaily] = useState([]);
    const [pkgStats, setPkgStats] = useState([]);
    const [loading, setLoading] = useState(true);
    const [days, setDays] = useState(30);

    const headers = { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' };

    const fetchAll = useCallback(async () => {
        setLoading(true);
        try {
            const [ovRes, dailyRes, pkgRes] = await Promise.all([
                fetch(`${API_BASE}/admin/analytics/overview`, { headers }),
                fetch(`${API_BASE}/admin/analytics/daily?days=${days}`, { headers }),
                fetch(`${API_BASE}/admin/analytics/packages`, { headers }),
            ]);
            if (ovRes.ok) { const d = await ovRes.json(); setOverview(d); }
            if (dailyRes.ok) { const d = await dailyRes.json(); setDaily(d.daily || []); }
            if (pkgRes.ok) { const d = await pkgRes.json(); setPkgStats(d.packages || []); }
        } catch { /* ignore */ } finally {
            setLoading(false);
        }
    }, [token, days]);

    useEffect(() => { fetchAll(); }, [fetchAll]);

    if (loading) {
        return <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลด...</span></div>;
    }

    return (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
            {/* Overview Cards */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(150px, 1fr))', gap: '0.75rem' }}>
                <StatCard label="ผู้ใช้ทั้งหมด" value={overview?.total_users || 0} color="#3498db"
                    sub={`อนุมัติ ${overview?.user_counts?.approved || 0} | รอ ${overview?.user_counts?.pending || 0}`} />
                <StatCard label="Sessions วันนี้" value={overview?.sessions_today || 0} color="#2d8a4e"
                    sub={`ทั้งหมด ${(overview?.sessions_total || 0).toLocaleString()}`} />
                <StatCard label="นาทีเดือนนี้" value={overview?.minutes_this_month || 0} color="var(--accent-primary)" />
                <StatCard label="Jobs กำลังทำ" value={overview?.active_jobs || 0}
                    color={overview?.active_jobs > 0 ? '#e67e22' : 'var(--text-muted)'} />
            </div>

            {/* Period selector */}
            <div style={{ display: 'flex', gap: '0.35rem' }}>
                {[7, 14, 30, 60].map(d => (
                    <button key={d} onClick={() => setDays(d)} style={{
                        padding: '0.35rem 0.85rem', borderRadius: 8, border: 'none', fontSize: '0.82rem',
                        fontWeight: 600, cursor: 'pointer', fontFamily: 'var(--font-thai)',
                        background: days === d ? 'var(--text-primary)' : 'var(--surface-elevated)',
                        color: days === d ? 'var(--bg-primary)' : 'var(--text-secondary)',
                    }}>{d} วัน</button>
                ))}
            </div>

            {/* Charts */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                <SimpleBarChart data={daily} dataKey="sessions" label="Sessions ต่อวัน" color="var(--accent-primary)" />
                <SimpleBarChart data={daily} dataKey="registrations" label="ลงทะเบียนต่อวัน" color="#3498db" />
            </div>

            <PackagePieChart packages={pkgStats} />
        </div>
    );
}

export default AnalyticsDashboard;
