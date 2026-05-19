import { useState, useCallback, useMemo, useRef, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import FileUploader from '../components/FileUploader'
import MeetingTypeSelect from '../components/MeetingTypeSelect'
import ProcessingStatus from '../components/ProcessingStatus'
import SpeakerIdentification from '../components/SpeakerIdentification'
import ResultsTabs from '../components/ResultsTabs'
import HistoryView from '../components/HistoryView'
import SettingsModal from '../components/SettingsModal'
import ProfileModal from '../components/ProfileModal'
import PackageBadge from '../components/PackageBadge'

const API_BASE = '/api'

// Decode JWT payload to get user info
function getUserInfo(token) {
    try {
        const payload = JSON.parse(atob(token.split('.')[1]))
        const name = payload.username || payload.email || ''
        return {
            initials: name.substring(0, 2).toUpperCase(),
            username: payload.username || '',
            email: payload.email || '',
            role: payload.role || 'user',
        }
    } catch {
        return { initials: 'ผู้', username: '', email: '', role: 'user' }
    }
}

function MainApp() {
    const [file, setFile] = useState(null)
    const [meetingType, setMeetingType] = useState(0)
    const [isProcessing, setIsProcessing] = useState(false)
    const [currentStep, setCurrentStep] = useState(0)
    const [progress, setProgress] = useState(0)
    const [result, setResult] = useState(null)
    const [sessionId, setSessionId] = useState(null)
    const [speakerMapping, setSpeakerMapping] = useState({})
    const [speakerPanelCollapsed, setSpeakerPanelCollapsed] = useState(false)
    const [error, setError] = useState(null)
    const [showDropdown, setShowDropdown] = useState(false)
    const [activeView, setActiveView] = useState('upload')
    const [emailRecipient, setEmailRecipient] = useState('')
    const [emailSending, setEmailSending] = useState(false)
    const [emailStatus, setEmailStatus] = useState(null)
    const [autoEmailStatus, setAutoEmailStatus] = useState(null)
    const [autoEmailError, setAutoEmailError] = useState(null)
    const [showSettings, setShowSettings] = useState(false)
    const [showProfile, setShowProfile] = useState(false)
    const dropdownRef = useRef(null)
    const resultLoadedRef = useRef(false)

    const { token, logout } = useAuth()
    const userInfo = token ? getUserInfo(token) : { initials: 'ผู้', username: '', email: '' }

    useEffect(() => {
        if (userInfo.email && !emailRecipient) setEmailRecipient(userInfo.email)
    }, [userInfo.email])

    useEffect(() => {
        const handleClickOutside = (e) => {
            if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
                setShowDropdown(false)
            }
        }
        document.addEventListener('mousedown', handleClickOutside)
        return () => document.removeEventListener('mousedown', handleClickOutside)
    }, [])

    const handleFileSelect = (selectedFile) => {
        if (sessionId) {
            fetch(`${API_BASE}/session/${sessionId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            }).catch(() => {})
        }
        setFile(selectedFile)
        setError(null)
        setResult(null)
        setSpeakerMapping({})
        setSpeakerPanelCollapsed(false)
        setSessionId(null)
        setEmailStatus(null)
        setAutoEmailStatus(null)
        setAutoEmailError(null)
        resultLoadedRef.current = false
    }

    const STEP_MAP = {
        queued: 0,
        model_load: 0,
        audio_load: 1,
        transcribing: 2,
        diarizing: 3,
        summarizing: 4,
        saving: 4,
        done: 5,
    }

    const pollJobStatus = useCallback(async (jobId) => {
        try {
            const res = await fetch(`${API_BASE}/jobs/${jobId}`, {
                headers: { 'Authorization': `Bearer ${token}` },
            })
            if (!res.ok) throw new Error('Failed to fetch job status')
            const job = await res.json()

            setProgress(job.progress || 0)
            setCurrentStep(STEP_MAP[job.current_step] ?? 0)
            setAutoEmailStatus(job.email_status || null)
            setAutoEmailError(job.email_error || null)

            if (job.status === 'failed') {
                setError(job.error || 'เกิดข้อผิดพลาดในการประมวลผล')
                setIsProcessing(false)
                return
            }

            // Load full result once when pipeline completes.
            if (job.status === 'completed' && !resultLoadedRef.current) {
                const resultRes = await fetch(`${API_BASE}/jobs/${jobId}/result`, {
                    headers: { 'Authorization': `Bearer ${token}` },
                })
                if (!resultRes.ok) throw new Error('Failed to fetch result')
                const data = await resultRes.json()
                setResult(data)
                setSessionId(data.session_id)
                setProgress(100)
                setCurrentStep(5)
                setIsProcessing(false)
                resultLoadedRef.current = true
            }

            // Keep polling while pipeline runs OR while an auto-send email is still in flight.
            const pipelineRunning = job.status !== 'completed' && job.status !== 'failed'
            const emailInFlight = job.email_status === 'queued' || job.email_status === 'sending'
            if (pipelineRunning || emailInFlight) {
                setTimeout(() => pollJobStatus(jobId), 3000)
            }
        } catch (err) {
            setError(err.message || 'เกิดข้อผิดพลาดในการติดตามสถานะ')
            setIsProcessing(false)
        }
    }, [token])

    const handleSubmit = async () => {
        if (!file) return

        setIsProcessing(true)
        setError(null)
        setResult(null)
        setSpeakerMapping({})
        setSpeakerPanelCollapsed(false)
        setCurrentStep(0)
        setProgress(0)
        setAutoEmailStatus(null)
        setAutoEmailError(null)
        setEmailStatus(null)
        resultLoadedRef.current = false

        try {
            const formData = new FormData()
            formData.append('audio', file)
            formData.append('meeting_type_id', meetingType)
            if (emailRecipient.trim()) {
                formData.append('email_recipient', emailRecipient.trim())
            }

            const response = await fetch(`${API_BASE}/transcribe-summarize`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: formData,
            })

            if (!response.ok) {
                const errorData = await response.json()
                throw new Error(errorData.detail || 'Upload failed')
            }

            const data = await response.json()
            // Start polling for job progress
            pollJobStatus(data.job_id)
        } catch (err) {
            setError(err.message || 'เกิดข้อผิดพลาดในการอัปโหลด')
            setIsProcessing(false)
        }
    }

    const handleMappingChange = useCallback((mapping) => {
        setSpeakerMapping(mapping)
    }, [])

    const handleSendEmail = async () => {
        if (!result || !emailRecipient.trim()) return
        setEmailSending(true)
        setEmailStatus(null)
        try {
            const fileNameNoExt = (file?.name || result.audio_file || 'meeting').replace(/\.[^.]+$/, '')
            const payload = {
                recipient_email: emailRecipient.trim(),
                file_name: fileNameNoExt,
                summary: displayResult.summary,
                segments: displayResult.transcript.segments,
                audio_file: displayResult.audio_file || '',
                audio_length_seconds: displayResult.audio_length_seconds || 0,
                speaker_summary: displayResult.transcript.speaker_summary,
                meeting_type_id: meetingType,
            }
            const res = await fetch(`${API_BASE}/email-results`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify(payload),
            })
            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.detail || 'ส่งอีเมลไม่สำเร็จ')
            }
            setEmailStatus({ type: 'success', message: `ส่งไปยัง ${emailRecipient.trim()} สำเร็จ` })
        } catch (err) {
            setEmailStatus({ type: 'error', message: err.message || 'เกิดข้อผิดพลาดในการส่งอีเมล' })
        } finally {
            setEmailSending(false)
        }
    }

    const displayResult = useMemo(() => {
        if (!result) return null
        if (!speakerMapping || Object.keys(speakerMapping).length === 0) return result

        const mapped = JSON.parse(JSON.stringify(result))

        mapped.transcript.segments = mapped.transcript.segments.map(seg => ({
            ...seg,
            speaker: speakerMapping[seg.speaker] || seg.speaker,
        }))

        let mappedSummary = mapped.summary
        for (const [generic, real] of Object.entries(speakerMapping)) {
            mappedSummary = mappedSummary.replaceAll(generic, real)
        }
        mapped.summary = mappedSummary

        const newSpeakingTime = {}
        const newWordCount = {}
        for (const [speaker, time] of Object.entries(mapped.transcript.speaker_summary.speaking_time)) {
            newSpeakingTime[speakerMapping[speaker] || speaker] = time
        }
        for (const [speaker, count] of Object.entries(mapped.transcript.speaker_summary.word_count)) {
            newWordCount[speakerMapping[speaker] || speaker] = count
        }
        mapped.transcript.speaker_summary = { speaking_time: newSpeakingTime, word_count: newWordCount }

        return mapped
    }, [result, speakerMapping])

    return (
        <div className="app-wrapper">
            {/* ── Navbar ── */}
            <nav className="app-nav">
                <div className="nav-logo">Tim<span>Sum</span></div>
                <div className="nav-tabs">
                    <button
                        className={`nav-tab ${activeView === 'upload' ? 'nav-tab-active' : ''}`}
                        onClick={() => setActiveView('upload')}
                    >
                        อัปโหลด
                    </button>
                    <button
                        className={`nav-tab ${activeView === 'history' ? 'nav-tab-active' : ''}`}
                        onClick={() => setActiveView('history')}
                    >
                        ประวัติ
                    </button>
                    {(userInfo.role === 'admin' || userInfo.role === 'superadmin') && (
                        <a href="/admin" className="nav-tab nav-tab-history" style={{ textDecoration: 'none' }}>
                            จัดการผู้ใช้
                        </a>
                    )}
                </div>
                <div className="nav-right">
                    <PackageBadge token={token} />
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
                                <button
                                    className="nav-dropdown-item"
                                    onClick={() => {
                                        setShowProfile(true)
                                        setShowDropdown(false)
                                    }}
                                >
                                    <span className="nav-dropdown-item-icon">👤</span>
                                    โปรไฟล์
                                </button>
                                <button
                                    className="nav-dropdown-item"
                                    onClick={() => {
                                        setShowSettings(true)
                                        setShowDropdown(false)
                                    }}
                                >
                                    <span className="nav-dropdown-item-icon">⚙️</span>
                                    ตั้งค่า
                                </button>
                                <div className="nav-dropdown-divider" />
                                <button className="nav-dropdown-item nav-dropdown-logout" onClick={logout}>
                                    <span className="nav-dropdown-item-icon">→</span>
                                    ออกจากระบบ
                                </button>
                            </div>
                        )}
                    </div>
                </div>
            </nav>

            {/* ── Main content ── */}
            <main className="upload-content">
                {activeView === 'upload' && (
                    <>
                        <div className="upload-page-header">
                            <h1>อัปโหลดไฟล์เสียงการประชุม</h1>
                            <p>รองรับ MP3, MP4, M4A, WAV · ขนาดสูงสุดตาม package ของคุณ</p>
                        </div>

                        {/* File upload */}
                        <div className="upload-card">
                            <FileUploader
                                file={file}
                                onFileSelect={handleFileSelect}
                                disabled={isProcessing}
                            />
                        </div>

                        {/* Meeting type */}
                        <div className="upload-card">
                            <MeetingTypeSelect
                                value={meetingType}
                                onChange={setMeetingType}
                                disabled={isProcessing}
                            />
                        </div>

                        {/* ── Email section ── */}
                        <div className="upload-card">
                            <div className="email-section-label">
                                <span className="email-dot" />
                                ส่งผลลัพธ์ไปยังอีเมลอัตโนมัติ (ไม่บังคับ)
                            </div>
                            <div className="email-input-row">
                                <input
                                    type="email"
                                    className="email-input"
                                    placeholder="user@company.co.th"
                                    value={emailRecipient}
                                    onChange={(e) => setEmailRecipient(e.target.value)}
                                    disabled={emailSending}
                                />
                                <button
                                    className="btn btn-primary email-send-btn"
                                    onClick={handleSendEmail}
                                    disabled={!result || !emailRecipient.trim() || emailSending}
                                    title={result ? 'ส่งซ้ำพร้อมชื่อ Speaker ที่แก้ไข' : 'กดได้หลังประมวลผลเสร็จ'}
                                >
                                    {emailSending
                                        ? '⏳ กำลังส่ง...'
                                        : (autoEmailStatus === 'sent' ? '📧 ส่งซ้ำ' : '📧 ส่ง')}
                                </button>
                            </div>

                            {/* Auto-send status (from worker, via job polling) */}
                            {autoEmailStatus === 'queued' && (
                                <p className="email-status email-status-info">
                                    📬 ระบบจะส่งอีเมลให้อัตโนมัติเมื่อประมวลผลเสร็จ
                                </p>
                            )}
                            {autoEmailStatus === 'sending' && (
                                <p className="email-status email-status-info">
                                    ⏳ กำลังส่งอีเมล...
                                </p>
                            )}
                            {autoEmailStatus === 'sent' && (
                                <p className="email-status email-status-success">
                                    ✅ ส่งอีเมลให้ {emailRecipient} เรียบร้อยแล้ว
                                </p>
                            )}
                            {autoEmailStatus === 'failed' && (
                                <p className="email-status email-status-error">
                                    ❌ ส่งอีเมลอัตโนมัติไม่สำเร็จ{autoEmailError ? ` — ${autoEmailError}` : ''}
                                </p>
                            )}

                            {/* Manual resend status */}
                            {emailStatus && (
                                <p className={`email-status email-status-${emailStatus.type}`}>
                                    {emailStatus.type === 'success' ? '✅' : '❌'} {emailStatus.message}
                                </p>
                            )}

                            <p className="email-hint">
                                กรอกอีเมลก่อนกด "เริ่มประมวลผล" — ระบบจะส่งไฟล์ Transcript และ Summary (DOCX) ให้อัตโนมัติเมื่อเสร็จ
                                {autoEmailStatus === 'sent' && ' ปุ่ม "ส่งซ้ำ" ใช้ส่งซ้ำหลังแก้ชื่อ Speaker'}
                            </p>
                        </div>

                        {/* Submit */}
                        <button
                            className="btn-start-process"
                            onClick={handleSubmit}
                            disabled={!file || isProcessing}
                        >
                            {isProcessing ? '⏳ กำลังประมวลผล...' : '✨ เริ่มประมวลผล'}
                        </button>

                        {/* Processing status */}
                        {isProcessing && (
                            <div className="upload-card">
                                <ProcessingStatus currentStep={currentStep} progress={progress} />
                            </div>
                        )}

                        {/* Error */}
                        {error && (
                            <div className="upload-error">
                                <span>❌</span>
                                <span>{error}</span>
                            </div>
                        )}

                        {/* Speaker identification */}
                        {result && (
                            <div className="upload-card">
                                <SpeakerIdentification
                                    result={result}
                                    sessionId={sessionId}
                                    onMappingChange={handleMappingChange}
                                    isCollapsed={speakerPanelCollapsed}
                                    onToggleCollapse={() => setSpeakerPanelCollapsed(prev => !prev)}
                                />
                            </div>
                        )}

                        {/* Results */}
                        {displayResult && (
                            <div className="upload-card results-section">
                                <ResultsTabs result={displayResult} meetingType={meetingType} token={token} />
                            </div>
                        )}
                    </>
                )}

                {activeView === 'history' && (
                    <>
                        <div className="upload-page-header">
                            <h1>ประวัติการประชุม</h1>
                            <p>ดูผลลัพธ์การประมวลผลที่ผ่านมาทั้งหมด</p>
                        </div>
                        <HistoryView />
                    </>
                )}
            </main>

            <ProfileModal
                isOpen={showProfile}
                onClose={() => setShowProfile(false)}
                userInfo={userInfo}
                token={token}
            />

            <SettingsModal
                isOpen={showSettings}
                onClose={() => setShowSettings(false)}
            />
        </div>
    )
}

export default MainApp
