import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useAuth } from '../contexts/AuthContext';
import { Link } from 'react-router-dom';

const API_BASE = '/api';

const DEFAULT_LLM_FORM = {
    primary_model: 'gpt-4.1',
    fallback_models: 'qwen2.5:72b-instruct-q4_K_M\nscb10x/typhoon2.1-gemma3-12b',
    temperature: 0.3,
    max_tokens: 4000,
};

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

function normalizeNumber(value, fallback) {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
}

function parseFallbackModels(value) {
    return value
        .split('\n')
        .map(item => item.trim())
        .filter(Boolean);
}

function AdminLLMSettings() {
    const { token, logout } = useAuth();
    const userInfo = token ? getUserInfo(token) : { initials: '', username: '', email: '', role: 'user' };
    const headers = useMemo(() => ({
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
    }), [token]);

    const [activeSection, setActiveSection] = useState('models');

    const [llmForm, setLlmForm] = useState(DEFAULT_LLM_FORM);
    const [llmLoading, setLlmLoading] = useState(true);
    const [llmSaving, setLlmSaving] = useState(false);
    const [llmTesting, setLlmTesting] = useState(false);
    const [llmTestResult, setLlmTestResult] = useState('');
    const [llmTestPrompt, setLlmTestPrompt] = useState('ช่วยสรุปข้อความนี้ให้เป็น bullet points:\nวันนี้ทีมตกลงให้ปรับแผนส่งมอบเป็นวันศุกร์ และให้คุณสมชายรับผิดชอบประสานงานลูกค้า');

    const [templates, setTemplates] = useState([]);
    const [selectedTemplateId, setSelectedTemplateId] = useState(null);
    const [templateForm, setTemplateForm] = useState({ system_prompt: '', temperature: 0.4, max_tokens: 4000 });
    const [templatesLoading, setTemplatesLoading] = useState(true);
    const [templateSaving, setTemplateSaving] = useState(false);
    const [templateTesting, setTemplateTesting] = useState(false);
    const [templateTestResult, setTemplateTestResult] = useState('');
    const [templateTestPrompt, setTemplateTestPrompt] = useState('กรุณาสรุปให้หน่อย:\nนายก: วันนี้เรามาประชุมเรื่องงบประมาณประจำปี\nนายข: เห็นด้วยครับ ควรเพิ่มงบการตลาด\nนายก: โอเค สรุปตามนั้น');
    const [feedback, setFeedback] = useState(null);

    const [showDropdown, setShowDropdown] = useState(false);
    const dropdownRef = useRef(null);
    const selectedTemplateIdRef = useRef(null);

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
        selectedTemplateIdRef.current = selectedTemplateId;
    }, [selectedTemplateId]);

    useEffect(() => {
        if (!feedback) return;
        const timer = setTimeout(() => setFeedback(null), 4500);
        return () => clearTimeout(timer);
    }, [feedback]);

    const hydrateLlmForm = (config) => {
        setLlmForm({
            primary_model: config.primary_model || DEFAULT_LLM_FORM.primary_model,
            fallback_models: (config.fallback_models || []).join('\n') || DEFAULT_LLM_FORM.fallback_models,
            temperature: config.temperature ?? DEFAULT_LLM_FORM.temperature,
            max_tokens: config.max_tokens ?? DEFAULT_LLM_FORM.max_tokens,
        });
    };

    const fetchLlmConfig = useCallback(async () => {
        setLlmLoading(true);
        try {
            const res = await fetch(`${API_BASE}/admin/llm-configs`, { headers });
            if (!res.ok) throw new Error('โหลด LLM config ไม่สำเร็จ');
            const data = await res.json();
            const defaultConfig = data.find(item => item.name === 'default_fallback') || data[0];
            if (defaultConfig) hydrateLlmForm(defaultConfig);
        } catch (err) {
            setLlmTestResult(`Error: ${err.message}`);
        } finally {
            setLlmLoading(false);
        }
    }, [headers]);

    const fetchTemplates = useCallback(async () => {
        setTemplatesLoading(true);
        try {
            const res = await fetch(`${API_BASE}/admin/meeting-templates`, { headers });
            if (!res.ok) throw new Error('โหลด template ไม่สำเร็จ');
            const data = await res.json();
            setTemplates(data || []);
            if (data.length > 0) {
                const selected = data.find(item => item.meeting_type_id === selectedTemplateIdRef.current) || data[0];
                setSelectedTemplateId(selected.meeting_type_id);
                setTemplateForm({
                    system_prompt: selected.system_prompt,
                    temperature: selected.temperature,
                    max_tokens: selected.max_tokens,
                });
            }
        } catch (err) {
            setTemplateTestResult(`Error: ${err.message}`);
        } finally {
            setTemplatesLoading(false);
        }
    }, [headers]);

    useEffect(() => {
        fetchLlmConfig();
        fetchTemplates();
    }, [fetchLlmConfig, fetchTemplates]);

    const handleSelectTemplate = (id) => {
        const tmpl = templates.find(t => t.meeting_type_id === id);
        if (!tmpl) return;
        setSelectedTemplateId(id);
        setTemplateForm({
            system_prompt: tmpl.system_prompt,
            temperature: tmpl.temperature,
            max_tokens: tmpl.max_tokens,
        });
        setTemplateTestResult('');
    };

    const handleSaveLlmConfig = async () => {
        const fallbackModels = parseFallbackModels(llmForm.fallback_models);
        if (!llmForm.primary_model.trim()) {
            setFeedback({ type: 'error', text: 'กรุณาระบุ Primary model' });
            return;
        }
        if (fallbackModels.length === 0) {
            setFeedback({ type: 'error', text: 'กรุณาระบุ Fallback model อย่างน้อย 1 รายการ' });
            return;
        }

        setLlmSaving(true);
        setFeedback(null);
        try {
            const res = await fetch(`${API_BASE}/admin/llm-configs/default_fallback`, {
                method: 'PUT',
                headers,
                body: JSON.stringify({
                    primary_model: llmForm.primary_model.trim(),
                    fallback_models: fallbackModels,
                    temperature: normalizeNumber(llmForm.temperature, 0.3),
                    max_tokens: normalizeNumber(llmForm.max_tokens, 4000),
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'บันทึก LLM config ไม่สำเร็จ');
            hydrateLlmForm(data);
            setFeedback({ type: 'success', text: 'บันทึก LLM config เรียบร้อยแล้ว' });
        } catch (err) {
            setFeedback({ type: 'error', text: err.message });
        } finally {
            setLlmSaving(false);
        }
    };

    const handleTestLlmConfig = async () => {
        if (!llmTestPrompt.trim()) {
            setFeedback({ type: 'error', text: 'กรุณากรอกข้อความทดสอบ' });
            return;
        }

        setLlmTesting(true);
        setLlmTestResult('');
        setFeedback(null);
        try {
            const res = await fetch(`${API_BASE}/admin/llm-configs/test`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    system_prompt: 'คุณคือผู้ช่วยสรุปข้อความอย่างกระชับและแม่นยำ ตอบเป็นภาษาไทย',
                    user_prompt: llmTestPrompt,
                    primary_model: llmForm.primary_model.trim(),
                    fallback_models: parseFallbackModels(llmForm.fallback_models),
                    temperature: normalizeNumber(llmForm.temperature, 0.3),
                    max_tokens: normalizeNumber(llmForm.max_tokens, 4000),
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'ทดสอบ LLM config ไม่สำเร็จ');
            setLlmTestResult(data.result);
        } catch (err) {
            setLlmTestResult(`Error: ${err.message}`);
        } finally {
            setLlmTesting(false);
        }
    };

    const handleSaveTemplate = async () => {
        if (!selectedTemplateId) return;

        setTemplateSaving(true);
        setFeedback(null);
        try {
            const res = await fetch(`${API_BASE}/admin/meeting-templates/${selectedTemplateId}`, {
                method: 'PUT',
                headers,
                body: JSON.stringify({
                    system_prompt: templateForm.system_prompt,
                    temperature: normalizeNumber(templateForm.temperature, 0.4),
                    max_tokens: normalizeNumber(templateForm.max_tokens, 4000),
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'บันทึก template ไม่สำเร็จ');
            setFeedback({ type: 'success', text: 'บันทึก prompt template เรียบร้อยแล้ว' });
            await fetchTemplates();
        } catch (err) {
            setFeedback({ type: 'error', text: err.message });
        } finally {
            setTemplateSaving(false);
        }
    };

    const handleTestTemplate = async () => {
        if (!templateTestPrompt.trim()) {
            setFeedback({ type: 'error', text: 'กรุณากรอก User Prompt เพื่อทดสอบ' });
            return;
        }

        setTemplateTesting(true);
        setTemplateTestResult('');
        setFeedback(null);
        try {
            const res = await fetch(`${API_BASE}/admin/meeting-templates/test`, {
                method: 'POST',
                headers,
                body: JSON.stringify({
                    system_prompt: templateForm.system_prompt,
                    user_prompt: templateTestPrompt,
                    temperature: normalizeNumber(templateForm.temperature, 0.4),
                    max_tokens: normalizeNumber(templateForm.max_tokens, 4000),
                }),
            });
            const data = await res.json();
            if (!res.ok) throw new Error(data.detail || 'การทดสอบล้มเหลว');
            setTemplateTestResult(data.result);
        } catch (err) {
            setTemplateTestResult(`Error: ${err.message}`);
        } finally {
            setTemplateTesting(false);
        }
    };

    return (
        <div className="app-wrapper">
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

            <main className="llm-settings-page">
                <aside className="llm-settings-sidebar">
                    <div className="llm-section-switch">
                        <button
                            className={`llm-section-btn ${activeSection === 'models' ? 'active' : ''}`}
                            onClick={() => setActiveSection('models')}
                        >
                            Runtime Models
                        </button>
                        <button
                            className={`llm-section-btn ${activeSection === 'templates' ? 'active' : ''}`}
                            onClick={() => setActiveSection('templates')}
                        >
                            Prompt Templates
                        </button>
                    </div>

                    {activeSection === 'models' && (
                        <div className="llm-sidebar-note">
                            <span className="llm-note-label">Config</span>
                            <strong>default_fallback</strong>
                            <p>ใช้กับการสรุป, ทดสอบ prompt และ fallback ผ่าน Ollama</p>
                        </div>
                    )}

                    {activeSection === 'templates' && (
                        <div className="llm-template-list">
                            <h3>ประเภทการประชุม</h3>
                            {templatesLoading ? (
                                <p className="llm-muted">กำลังโหลด...</p>
                            ) : (
                                templates.map(t => (
                                    <button
                                        key={t.meeting_type_id}
                                        className={`llm-template-item ${selectedTemplateId === t.meeting_type_id ? 'active' : ''}`}
                                        onClick={() => handleSelectTemplate(t.meeting_type_id)}
                                    >
                                        {t.thai_name}
                                    </button>
                                ))
                            )}
                        </div>
                    )}
                </aside>

                {activeSection === 'models' && (
                    <section className="llm-settings-panel">
                        {feedback && (
                            <div className={`llm-feedback llm-feedback-${feedback.type}`}>
                                {feedback.text}
                            </div>
                        )}

                        <div className="llm-panel-header">
                            <div>
                                <h2>ตั้งค่า Runtime LLM</h2>
                                <p>กำหนด primary model บน NTC Gateway และ fallback models สำหรับ Ollama</p>
                            </div>
                            <button className="btn-primary llm-save-btn" onClick={handleSaveLlmConfig} disabled={llmSaving || llmLoading}>
                                {llmSaving ? 'กำลังบันทึก...' : 'บันทึก Config'}
                            </button>
                        </div>

                        <div className="llm-form-grid">
                            <label className="llm-field llm-field-wide">
                                <span>Primary Model</span>
                                <input
                                    value={llmForm.primary_model}
                                    onChange={e => setLlmForm({ ...llmForm, primary_model: e.target.value })}
                                    disabled={llmLoading}
                                />
                            </label>

                            <label className="llm-field">
                                <span>Temperature</span>
                                <input
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    max="1"
                                    value={llmForm.temperature}
                                    onChange={e => setLlmForm({ ...llmForm, temperature: e.target.value })}
                                    disabled={llmLoading}
                                />
                            </label>

                            <label className="llm-field">
                                <span>Max Tokens</span>
                                <input
                                    type="number"
                                    step="100"
                                    min="100"
                                    max="16000"
                                    value={llmForm.max_tokens}
                                    onChange={e => setLlmForm({ ...llmForm, max_tokens: e.target.value })}
                                    disabled={llmLoading}
                                />
                            </label>

                            <label className="llm-field llm-field-wide">
                                <span>Fallback Models</span>
                                <textarea
                                    rows={5}
                                    value={llmForm.fallback_models}
                                    onChange={e => setLlmForm({ ...llmForm, fallback_models: e.target.value })}
                                    disabled={llmLoading}
                                />
                                <small>ใส่ 1 model ต่อ 1 บรรทัด ระบบจะลองตามลำดับเมื่อ primary model ล้มเหลว</small>
                            </label>
                        </div>

                        <div className="llm-test-area">
                            <div>
                                <label className="llm-field">
                                    <span>ข้อความทดสอบ</span>
                                    <textarea
                                        rows={8}
                                        value={llmTestPrompt}
                                        onChange={e => setLlmTestPrompt(e.target.value)}
                                    />
                                </label>
                                <button className="btn-outline llm-test-btn" onClick={handleTestLlmConfig} disabled={llmTesting || llmLoading}>
                                    {llmTesting ? 'กำลังทดสอบ...' : 'ทดสอบ Config'}
                                </button>
                            </div>
                            <div className="llm-result-box">
                                {llmTestResult || <span>ผลลัพธ์จะแสดงที่นี่</span>}
                            </div>
                        </div>
                    </section>
                )}

                {activeSection === 'templates' && (
                    <section className="llm-settings-panel">
                        {feedback && (
                            <div className={`llm-feedback llm-feedback-${feedback.type}`}>
                                {feedback.text}
                            </div>
                        )}

                        <div className="llm-panel-header">
                            <div>
                                <h2>ตั้งค่า Prompt Template</h2>
                                <p>แก้ system prompt และ parameter เฉพาะประเภทการประชุม</p>
                            </div>
                            <button className="btn-primary llm-save-btn" onClick={handleSaveTemplate} disabled={templateSaving || templatesLoading}>
                                {templateSaving ? 'กำลังบันทึก...' : 'บันทึก Template'}
                            </button>
                        </div>

                        <div className="llm-form-grid">
                            <label className="llm-field">
                                <span>Temperature</span>
                                <input
                                    type="number"
                                    step="0.1"
                                    min="0"
                                    max="1"
                                    value={templateForm.temperature}
                                    onChange={e => setTemplateForm({ ...templateForm, temperature: e.target.value })}
                                />
                            </label>

                            <label className="llm-field">
                                <span>Max Tokens</span>
                                <input
                                    type="number"
                                    step="100"
                                    min="100"
                                    max="16000"
                                    value={templateForm.max_tokens}
                                    onChange={e => setTemplateForm({ ...templateForm, max_tokens: e.target.value })}
                                />
                            </label>

                            <label className="llm-field llm-field-wide">
                                <span>System Prompt</span>
                                <textarea
                                    rows={16}
                                    value={templateForm.system_prompt}
                                    onChange={e => setTemplateForm({ ...templateForm, system_prompt: e.target.value })}
                                />
                                <small>รองรับตัวแปร {'{num_speakers}'} และ {'{custom_prompt}'}</small>
                            </label>
                        </div>

                        <div className="llm-test-area">
                            <div>
                                <label className="llm-field">
                                    <span>User Prompt ทดสอบ</span>
                                    <textarea
                                        rows={8}
                                        value={templateTestPrompt}
                                        onChange={e => setTemplateTestPrompt(e.target.value)}
                                    />
                                </label>
                                <button className="btn-outline llm-test-btn" onClick={handleTestTemplate} disabled={templateTesting}>
                                    {templateTesting ? 'กำลังทดสอบ...' : 'ทดสอบ Template'}
                                </button>
                            </div>
                            <div className="llm-result-box">
                                {templateTestResult || <span>ผลลัพธ์จะแสดงที่นี่</span>}
                            </div>
                        </div>
                    </section>
                )}
            </main>
        </div>
    );
}

export default AdminLLMSettings;
