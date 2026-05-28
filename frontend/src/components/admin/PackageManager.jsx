import { useState, useEffect, useCallback } from 'react';

const API_BASE = '/api';

const BILLING_OPTIONS = [
    { value: 'monthly', label: 'รายเดือน' },
    { value: 'yearly', label: 'รายปี' },
    { value: 'one-time', label: 'ครั้งเดียว' },
];

const EMPTY_FORM = {
    name: '', description: '', price: 0, billing_cycle: 'monthly', tier: 0,
    limits: {
        transcription_minutes_per_month: 180, max_audio_minutes_per_file: 30,
        max_files_per_month: 6, ai_summary_per_month: 6, custom_prompt_enabled: false,
    },
};

function PackageManager({ token }) {
    const [packages, setPackages] = useState([]);
    const [loading, setLoading] = useState(true);
    const [showForm, setShowForm] = useState(false);
    const [editingId, setEditingId] = useState(null);
    const [form, setForm] = useState({ ...EMPTY_FORM });
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [showInactive, setShowInactive] = useState(false);

    const headers = { 'Authorization': `Bearer ${token}`, 'Content-Type': 'application/json' };

    const fetchPackages = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch(`${API_BASE}/admin/packages?active_only=${!showInactive}`, { headers });
            if (res.ok) {
                const data = await res.json();
                setPackages(data.packages || []);
            }
        } catch { /* ignore */ } finally {
            setLoading(false);
        }
    }, [token, showInactive]);

    useEffect(() => { fetchPackages(); }, [fetchPackages]);

    const openCreate = () => {
        setEditingId(null);
        setForm({ ...EMPTY_FORM, limits: { ...EMPTY_FORM.limits } });
        setShowForm(true);
        setError(null);
    };

    const openEdit = (pkg) => {
        setEditingId(pkg._id);
        setForm({
            name: pkg.name || '',
            description: pkg.description || '',
            price: pkg.price || 0,
            billing_cycle: pkg.billing_cycle || 'monthly',
            tier: pkg.tier || 0,
            limits: {
                transcription_minutes_per_month: pkg.limits?.transcription_minutes_per_month ?? 180,
                max_audio_minutes_per_file: pkg.limits?.max_audio_minutes_per_file ?? 30,
                max_files_per_month: pkg.limits?.max_files_per_month ?? 6,
                ai_summary_per_month: pkg.limits?.ai_summary_per_month ?? 6,
                custom_prompt_enabled: pkg.limits?.custom_prompt_enabled ?? false,
            },
        });
        setShowForm(true);
        setError(null);
    };

    const handleSave = async () => {
        setSaving(true);
        setError(null);
        try {
            const url = editingId
                ? `${API_BASE}/admin/packages/${editingId}`
                : `${API_BASE}/admin/packages`;
            const method = editingId ? 'PUT' : 'POST';
            const res = await fetch(url, { method, headers, body: JSON.stringify(form) });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'บันทึกไม่สำเร็จ');
            setShowForm(false);
            fetchPackages();
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    };

    const handleDelete = async (pkg) => {
        if (!confirm(`ต้องการปิดใช้งานแพ็กเกจ "${pkg.name}" หรือไม่?\n(ผู้ใช้ที่มีแพ็กเกจนี้จะไม่ได้รับผลกระทบ)`)) return;
        try {
            const res = await fetch(`${API_BASE}/admin/packages/${pkg._id}`, { method: 'DELETE', headers });
            if (!res.ok) {
                const data = await res.json();
                throw new Error(data.detail || 'ลบไม่สำเร็จ');
            }
            fetchPackages();
        } catch (err) {
            alert(err.message);
        }
    };

    const updateField = (field, value) => setForm(prev => ({ ...prev, [field]: value }));
    const updateLimit = (field, value) => setForm(prev => ({
        ...prev, limits: { ...prev.limits, [field]: value },
    }));

    const inputStyle = {
        width: '100%', padding: '0.5rem 0.75rem', borderRadius: 8,
        border: '1px solid var(--border-color)', background: 'var(--surface-elevated)',
        color: 'var(--text-primary)', fontSize: '0.88rem', fontFamily: 'var(--font-thai)',
    };
    const labelStyle = { fontSize: '0.82rem', fontWeight: 600, color: 'var(--text-secondary)', marginBottom: 4, display: 'block' };

    return (
        <div>
            {/* Header */}
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1rem' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <label style={{ fontSize: '0.82rem', color: 'var(--text-secondary)', display: 'flex', alignItems: 'center', gap: 4 }}>
                        <input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} />
                        แสดงที่ปิดใช้งาน
                    </label>
                </div>
                <button onClick={openCreate} style={{
                    padding: '0.5rem 1.25rem', borderRadius: 8, border: 'none',
                    background: 'var(--accent-primary)', color: '#fff', fontWeight: 600,
                    fontSize: '0.88rem', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                }}>
                    + สร้างแพ็กเกจ
                </button>
            </div>

            {/* Package Table */}
            {loading ? (
                <div className="history-loading"><div className="history-spinner" /><span>กำลังโหลด...</span></div>
            ) : packages.length === 0 ? (
                <div className="history-empty"><h3>ไม่มีแพ็กเกจ</h3></div>
            ) : (
                <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' }}>
                        <thead>
                            <tr style={{ borderBottom: '2px solid var(--border-color)', textAlign: 'left' }}>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600 }}>ชื่อ</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600 }}>ราคา</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600 }}>รอบชำระ</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600, textAlign: 'center' }}>นาที/เดือน</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600, textAlign: 'center' }}>ไฟล์/เดือน</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600, textAlign: 'center' }}>ผู้ใช้</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600 }}>สถานะ</th>
                                <th style={{ padding: '0.6rem 0.5rem', fontWeight: 600 }}></th>
                            </tr>
                        </thead>
                        <tbody>
                            {packages.map(pkg => (
                                <tr key={pkg._id} style={{
                                    borderBottom: '1px solid var(--border-color)',
                                    opacity: pkg.is_active === false ? 0.5 : 1,
                                }}>
                                    <td style={{ padding: '0.6rem 0.5rem' }}>
                                        <div style={{ fontWeight: 600 }}>{pkg.name}</div>
                                        <div style={{ fontSize: '0.78rem', color: 'var(--text-muted)' }}>{pkg.description}</div>
                                    </td>
                                    <td style={{ padding: '0.6rem 0.5rem' }}>{pkg.price?.toLocaleString()} ฿</td>
                                    <td style={{ padding: '0.6rem 0.5rem' }}>
                                        {BILLING_OPTIONS.find(b => b.value === pkg.billing_cycle)?.label || pkg.billing_cycle}
                                    </td>
                                    <td style={{ padding: '0.6rem 0.5rem', textAlign: 'center' }}>
                                        {pkg.limits?.transcription_minutes_per_month?.toLocaleString()}
                                    </td>
                                    <td style={{ padding: '0.6rem 0.5rem', textAlign: 'center' }}>
                                        {pkg.limits?.max_files_per_month?.toLocaleString()}
                                    </td>
                                    <td style={{ padding: '0.6rem 0.5rem', textAlign: 'center', fontWeight: 600 }}>
                                        {pkg.user_count || 0}
                                    </td>
                                    <td style={{ padding: '0.6rem 0.5rem' }}>
                                        <span style={{
                                            fontSize: '0.75rem', fontWeight: 600, padding: '0.15rem 0.5rem', borderRadius: 999,
                                            background: pkg.is_active !== false ? 'rgba(45,138,78,0.12)' : 'rgba(127,140,141,0.12)',
                                            color: pkg.is_active !== false ? '#2d8a4e' : '#7f8c8d',
                                        }}>
                                            {pkg.is_active !== false ? 'ใช้งาน' : 'ปิด'}
                                        </span>
                                    </td>
                                    <td style={{ padding: '0.6rem 0.5rem' }}>
                                        {(pkg.tier || 0) < 10 && (
                                            <div style={{ display: 'flex', gap: '0.4rem' }}>
                                                <button onClick={() => openEdit(pkg)} style={{
                                                    padding: '0.3rem 0.7rem', borderRadius: 6, fontSize: '0.78rem',
                                                    border: '1px solid var(--border-color)', background: 'transparent',
                                                    color: 'var(--text-primary)', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                }}>แก้ไข</button>
                                                {pkg.is_active !== false && (
                                                    <button onClick={() => handleDelete(pkg)} style={{
                                                        padding: '0.3rem 0.7rem', borderRadius: 6, fontSize: '0.78rem',
                                                        border: '1px solid var(--error)', background: 'transparent',
                                                        color: 'var(--error)', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                                    }}>ปิด</button>
                                                )}
                                            </div>
                                        )}
                                    </td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Create/Edit Form Modal */}
            {showForm && (
                <div style={{
                    position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)',
                    display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
                }} onClick={() => setShowForm(false)}>
                    <div onClick={e => e.stopPropagation()} style={{
                        background: 'var(--bg-primary)', borderRadius: 16, padding: '1.5rem',
                        width: '90%', maxWidth: 520, maxHeight: '90vh', overflowY: 'auto',
                        border: '1px solid var(--border-color)',
                    }}>
                        <h3 style={{ marginTop: 0, marginBottom: '1rem' }}>
                            {editingId ? 'แก้ไขแพ็กเกจ' : 'สร้างแพ็กเกจใหม่'}
                        </h3>

                        {error && <div className="upload-error" style={{ marginBottom: '0.75rem' }}>{error}</div>}

                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.75rem' }}>
                            <div>
                                <label style={labelStyle}>ชื่อแพ็กเกจ</label>
                                <input style={inputStyle} value={form.name} onChange={e => updateField('name', e.target.value)} />
                            </div>
                            <div>
                                <label style={labelStyle}>รายละเอียด</label>
                                <input style={inputStyle} value={form.description} onChange={e => updateField('description', e.target.value)} />
                            </div>
                            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: '0.75rem' }}>
                                <div>
                                    <label style={labelStyle}>ราคา (฿)</label>
                                    <input style={inputStyle} type="number" min="0" value={form.price} onChange={e => updateField('price', Number(e.target.value))} />
                                </div>
                                <div>
                                    <label style={labelStyle}>รอบชำระ</label>
                                    <select style={inputStyle} value={form.billing_cycle} onChange={e => updateField('billing_cycle', e.target.value)}>
                                        {BILLING_OPTIONS.map(b => <option key={b.value} value={b.value}>{b.label}</option>)}
                                    </select>
                                </div>
                                <div>
                                    <label style={labelStyle}>Tier</label>
                                    <input style={inputStyle} type="number" min="0" max="9" value={form.tier} onChange={e => updateField('tier', Number(e.target.value))} />
                                </div>
                            </div>

                            <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '0.75rem', marginTop: '0.25rem' }}>
                                <div style={{ fontWeight: 600, fontSize: '0.88rem', marginBottom: '0.5rem' }}>ขีดจำกัด</div>
                                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.75rem' }}>
                                    <div>
                                        <label style={labelStyle}>นาทีถอดเสียง/เดือน</label>
                                        <input style={inputStyle} type="number" min="0" value={form.limits.transcription_minutes_per_month}
                                            onChange={e => updateLimit('transcription_minutes_per_month', Number(e.target.value))} />
                                    </div>
                                    <div>
                                        <label style={labelStyle}>นาทีสูงสุด/ไฟล์</label>
                                        <input style={inputStyle} type="number" min="0" value={form.limits.max_audio_minutes_per_file}
                                            onChange={e => updateLimit('max_audio_minutes_per_file', Number(e.target.value))} />
                                    </div>
                                    <div>
                                        <label style={labelStyle}>ไฟล์/เดือน</label>
                                        <input style={inputStyle} type="number" min="0" value={form.limits.max_files_per_month}
                                            onChange={e => updateLimit('max_files_per_month', Number(e.target.value))} />
                                    </div>
                                    <div>
                                        <label style={labelStyle}>AI สรุป/เดือน</label>
                                        <input style={inputStyle} type="number" min="0" value={form.limits.ai_summary_per_month}
                                            onChange={e => updateLimit('ai_summary_per_month', Number(e.target.value))} />
                                    </div>
                                </div>
                                <label style={{ display: 'flex', alignItems: 'center', gap: 6, marginTop: '0.6rem', fontSize: '0.85rem' }}>
                                    <input type="checkbox" checked={form.limits.custom_prompt_enabled}
                                        onChange={e => updateLimit('custom_prompt_enabled', e.target.checked)} />
                                    เปิดใช้ Custom Prompt
                                </label>
                            </div>
                        </div>

                        <div style={{ display: 'flex', gap: '0.5rem', justifyContent: 'flex-end', marginTop: '1.25rem' }}>
                            <button onClick={() => setShowForm(false)} style={{
                                padding: '0.5rem 1.25rem', borderRadius: 8, border: '1px solid var(--border-color)',
                                background: 'transparent', color: 'var(--text-primary)', fontWeight: 600,
                                fontSize: '0.88rem', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                            }}>ยกเลิก</button>
                            <button onClick={handleSave} disabled={saving || !form.name} style={{
                                padding: '0.5rem 1.25rem', borderRadius: 8, border: 'none',
                                background: 'var(--accent-primary)', color: '#fff', fontWeight: 600,
                                fontSize: '0.88rem', cursor: 'pointer', fontFamily: 'var(--font-thai)',
                                opacity: saving || !form.name ? 0.5 : 1,
                            }}>{saving ? 'กำลังบันทึก...' : 'บันทึก'}</button>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default PackageManager;
