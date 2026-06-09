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

function AdminLLMSettings() {
    const { token, logout } = useAuth();
    const userInfo = token ? getUserInfo(token) : { initials: '', username: '', email: '', role: 'user' };

    const [templates, setTemplates] = useState([]);
    const [selectedTemplateId, setSelectedTemplateId] = useState(null);
    const [formData, setFormData] = useState({ system_prompt: '', temperature: 0.4, max_tokens: 4000 });
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [testing, setTesting] = useState(false);
    const [testResult, setTestResult] = useState('');
    const [testUserPrompt, setTestUserPrompt] = useState('กรุณาสรุปให้หน่อย:\nนายก: วันนี้เรามาประชุมเรื่องงบประมาณประจำปี\nนายข: เห็นด้วยครับ ควรเพิ่มงบการตลาด\nนายก: โอเค สรุปตามนั้น');
    
    const [showDropdown, setShowDropdown] = useState(false);
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

    const fetchTemplates = useCallback(async () => {
        setLoading(true);
        try {
            const res = await fetch(`${API_BASE}/admin/meeting-templates`, { headers });
            if (res.ok) {
                const data = await res.json();
                setTemplates(data || []);
                if (data.length > 0 && !selectedTemplateId) {
                    setSelectedTemplateId(data[0].meeting_type_id);
                    setFormData({
                        system_prompt: data[0].system_prompt,
                        temperature: data[0].temperature,
                        max_tokens: data[0].max_tokens,
                    });
                }
            }
        } catch { /* ignore */ } finally {
            setLoading(false);
        }
    }, [token, selectedTemplateId]);

    useEffect(() => {
        fetchTemplates();
    }, [fetchTemplates]);

    const handleSelectTemplate = (id) => {
        const tmpl = templates.find(t => t.meeting_type_id === id);
        if (tmpl) {
            setSelectedTemplateId(id);
            setFormData({
                system_prompt: tmpl.system_prompt,
                temperature: tmpl.temperature,
                max_tokens: tmpl.max_tokens,
            });
            setTestResult('');
        }
    };

    const handleSave = async () => {
        setSaving(true);
        try {
            const res = await fetch(`${API_BASE}/admin/meeting-templates/${selectedTemplateId}`, {
                method: 'PUT',
                headers,
                body: JSON.stringify(formData)
            });
            if (!res.ok) {
                const err = await res.json();
                throw new Error(err.detail || 'บันทึกไม่สำเร็จ');
            }
            alert('บันทึกการตั้งค่าเรียบร้อยแล้ว');
            await fetchTemplates(); // Refresh
        } catch (err) {
            alert(err.message);
        } finally {
            setSaving(false);
        }
    };

    const handleTest = async () => {
        if (!testUserPrompt.trim()) return alert('กรุณากรอก User Prompt เพื่อทดสอบ');
        setTesting(true);
        setTestResult('');
        try {
            const res = await fetch(`${API_BASE}/admin/meeting-templates/test`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    system_prompt: formData.system_prompt,
                    user_prompt: testUserPrompt,
                    temperature: formData.temperature,
                    max_tokens: formData.max_tokens
                })
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'การทดสอบล้มเหลว');
            setTestResult(data.result);
        } catch (err) {
            setTestResult(`Error: ${err.message}`);
        } finally {
            setTesting(false);
        }
    };

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
                    <Link to="/admin/monitoring" className="nav-tab" style={{ textDecoration: 'none' }}>ระบบ & คิว</Link>
                    <button className="nav-tab nav-tab-active">ตั้งค่า LLM</button>
                </div>
                <div className="nav-right">
                    <div className="nav-avatar-wrapper" ref={dropdownRef}>
                        <div className="nav-avatar" onClick={() => setShowDropdown(prev => !prev)}>
                            {userInfo.initials}
                        </div>
                        {showDropdown && (
                            <div className="nav-dropdown">
                                <div className="nav-dropdown-header">
                                    <span className="nav-dropdown-name">{userInfo.username}</span>
                                    <span className="nav-dropdown-email">{userInfo.email}</span>
                                </div>
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

            <div className="upload-content" style={{ maxWidth: 1200, display: 'flex', gap: '1.5rem', alignItems: 'flex-start' }}>
                {/* Sidebar List */}
                <div style={{ flex: '0 0 280px', background: 'var(--surface-elevated)', borderRadius: 12, padding: '1rem', border: '1px solid var(--border-color)' }}>
                    <h3 style={{ marginBottom: '1rem', fontSize: '1.1rem', color: 'var(--text-primary)' }}>ประเภทการประชุม</h3>
                    {loading ? (
                        <p style={{ color: 'var(--text-muted)' }}>กำลังโหลด...</p>
                    ) : (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '0.4rem' }}>
                            {templates.map(t => (
                                <button 
                                    key={t.meeting_type_id}
                                    onClick={() => handleSelectTemplate(t.meeting_type_id)}
                                    style={{
                                        textAlign: 'left', padding: '0.75rem 1rem', borderRadius: 8,
                                        border: '1px solid',
                                        borderColor: selectedTemplateId === t.meeting_type_id ? 'var(--primary-color)' : 'transparent',
                                        background: selectedTemplateId === t.meeting_type_id ? 'rgba(37,99,235,0.05)' : 'transparent',
                                        color: selectedTemplateId === t.meeting_type_id ? 'var(--primary-color)' : 'var(--text-secondary)',
                                        cursor: 'pointer', fontFamily: 'var(--font-thai)', fontWeight: selectedTemplateId === t.meeting_type_id ? 600 : 400,
                                        transition: 'all 0.15s'
                                    }}
                                >
                                    {t.thai_name}
                                </button>
                            ))}
                        </div>
                    )}
                </div>

                {/* Editor Area */}
                <div style={{ flex: 1, background: 'var(--surface-elevated)', borderRadius: 12, padding: '1.5rem', border: '1px solid var(--border-color)' }}>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '1.5rem' }}>
                        <h2 style={{ margin: 0 }}>ตั้งค่า Prompt พื้นฐาน</h2>
                        <button 
                            className="btn-primary" 
                            onClick={handleSave} 
                            disabled={saving || loading}
                            style={{ padding: '0.5rem 1.5rem', borderRadius: 8, fontSize: '0.9rem' }}
                        >
                            {saving ? 'กำลังบันทึก...' : 'บันทึกการตั้งค่า'}
                        </button>
                    </div>

                    <div style={{ display: 'flex', gap: '1rem', marginBottom: '1rem' }}>
                        <div style={{ flex: 1 }}>
                            <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>Temperature (0.0 - 1.0)</label>
                            <input 
                                type="number" step="0.1" min="0" max="1" 
                                value={formData.temperature} 
                                onChange={e => setFormData({ ...formData, temperature: parseFloat(e.target.value) })}
                                style={{ width: '100%', padding: '0.6rem', borderRadius: 8, border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
                            />
                        </div>
                        <div style={{ flex: 1 }}>
                            <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>Max Tokens</label>
                            <input 
                                type="number" step="100" min="100" max="16000" 
                                value={formData.max_tokens} 
                                onChange={e => setFormData({ ...formData, max_tokens: parseInt(e.target.value) })}
                                style={{ width: '100%', padding: '0.6rem', borderRadius: 8, border: '1px solid var(--border-color)', background: 'var(--bg-primary)', color: 'var(--text-primary)' }}
                            />
                        </div>
                    </div>

                    <div style={{ marginBottom: '2rem' }}>
                        <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>System Prompt</label>
                        <textarea 
                            value={formData.system_prompt} 
                            onChange={e => setFormData({ ...formData, system_prompt: e.target.value })}
                            rows={15}
                            style={{ 
                                width: '100%', padding: '0.75rem', borderRadius: 8, 
                                border: '1px solid var(--border-color)', background: 'var(--bg-primary)', 
                                color: 'var(--text-primary)', fontFamily: 'monospace', fontSize: '0.85rem',
                                resize: 'vertical'
                            }}
                        />
                        <p style={{ fontSize: '0.8rem', color: 'var(--text-muted)', marginTop: '0.4rem' }}>
                            รองรับตัวแปร: <code>{`{num_speakers}`}</code> และ <code>{`{custom_prompt}`}</code> (ถ้าไม่มี <code>{`{custom_prompt}`}</code> ระบบจะต่อท้ายให้โดยอัตโนมัติ)
                        </p>
                    </div>

                    {/* Testing Section */}
                    <div style={{ borderTop: '1px solid var(--border-color)', paddingTop: '1.5rem' }}>
                        <h3 style={{ marginBottom: '1rem', fontSize: '1rem' }}>ทดสอบ Prompt</h3>
                        <div style={{ display: 'flex', gap: '1rem', alignItems: 'flex-start' }}>
                            <div style={{ flex: 1 }}>
                                <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>User Prompt (ข้อมูลจำลอง)</label>
                                <textarea 
                                    value={testUserPrompt} 
                                    onChange={e => setTestUserPrompt(e.target.value)}
                                    rows={8}
                                    style={{ 
                                        width: '100%', padding: '0.75rem', borderRadius: 8, 
                                        border: '1px solid var(--border-color)', background: 'var(--bg-primary)', 
                                        color: 'var(--text-primary)', fontFamily: 'monospace', fontSize: '0.85rem',
                                        resize: 'vertical'
                                    }}
                                />
                                <button 
                                    className="btn-outline" 
                                    onClick={handleTest}
                                    disabled={testing}
                                    style={{ marginTop: '0.75rem', padding: '0.4rem 1.25rem', fontSize: '0.85rem', borderRadius: 6 }}
                                >
                                    {testing ? 'กำลังทดสอบ...' : 'ทดสอบ'}
                                </button>
                            </div>
                            <div style={{ flex: 1 }}>
                                <label style={{ display: 'block', marginBottom: '0.4rem', fontSize: '0.85rem', fontWeight: 600, color: 'var(--text-secondary)' }}>ผลลัพธ์จาก LLM</label>
                                <div style={{ 
                                    width: '100%', height: '200px', overflowY: 'auto',
                                    padding: '0.75rem', borderRadius: 8, 
                                    border: '1px solid var(--border-color)', background: 'var(--bg-secondary)', 
                                    color: 'var(--text-primary)', fontSize: '0.85rem', whiteSpace: 'pre-wrap'
                                }}>
                                    {testResult || <span style={{ color: 'var(--text-muted)' }}>คลิกทดสอบเพื่อดูผลลัพธ์</span>}
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}

export default AdminLLMSettings;
