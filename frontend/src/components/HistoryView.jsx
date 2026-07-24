import { useState, useCallback, useEffect, useMemo, useRef } from 'react'
import { useAuth } from '../contexts/AuthContext'
import ResultsTabs from './ResultsTabs'
import SpeakerIdentification from './SpeakerIdentification'
import Icon from './ui/Icon'
import { applySpeakerMapping } from '../utils/speakerMapping'

const API_BASE = '/api'

function HistoryDetailContent({ session, token, defaultEmail }) {
    const [speakerMapping, setSpeakerMapping] = useState({})
    const [speakerPanelCollapsed, setSpeakerPanelCollapsed] = useState(true)
    const [emailRecipient, setEmailRecipient] = useState(defaultEmail || '')
    const [sendStatus, setSendStatus] = useState(null)
    const [sendingEmail, setSendingEmail] = useState(false)

    const baseResult = useMemo(() => ({
        audio_file: session.audio_file,
        audio_length_seconds: session.audio_length_seconds,
        processing_time: session.processing_time,
        transcript: session.transcript,
        summary: session.summary,
        summary_metadata: session.summary_metadata || {},
        summary_status: session.summary_status || session.summary_metadata?.summary_status || 'completed',
        is_partial_summary: session.is_partial_summary || false,
        coverage_percentage: session.coverage_percentage ?? session.summary_metadata?.coverage_percentage ?? 100,
        agendas: session.agendas || [],
        detection_mode: session.detection_mode || 'single_topic',
        speaker_clips: session.speaker_clips || {},
        suggested_names: session.suggested_names || {},
    }), [session])

    const mappedResult = useMemo(() => {
        return applySpeakerMapping(baseResult, speakerMapping)
    }, [baseResult, speakerMapping])

    const handleMappingChange = useCallback((mapping) => {
        setSpeakerMapping(mapping)
        setSendStatus(null)
    }, [])

    const handleResendEmail = async () => {
        const recipient = emailRecipient.trim()
        if (!recipient) {
            setSendStatus({ type: 'error', text: 'กรุณากรอกอีเมลผู้รับ' })
            return
        }

        setSendingEmail(true)
        setSendStatus(null)
        try {
            const response = await fetch(`${API_BASE}/email-results`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${token}`,
                },
                body: JSON.stringify({
                    recipient_email: recipient,
                    file_name: mappedResult.audio_file || 'meeting',
                    summary: mappedResult.summary || '',
                    segments: mappedResult.transcript?.segments || [],
                    audio_file: mappedResult.audio_file || '',
                    audio_length_seconds: mappedResult.audio_length_seconds || 0,
                    speaker_summary: mappedResult.transcript?.speaker_summary || {},
                    meeting_type_id: session.meeting_type_id || 0,
                    agendas: mappedResult.agendas || [],
                    summary_status: mappedResult.summary_status,
                    is_partial_summary: mappedResult.is_partial_summary,
                    coverage_percentage: mappedResult.coverage_percentage,
                    summary_warning: mappedResult.summary_metadata?.user_warning || '',
                }),
            })
            const data = await response.json().catch(() => ({}))
            if (!response.ok) {
                throw new Error(data.detail || 'ส่งอีเมลไม่สำเร็จ')
            }
            setSendStatus({ type: 'success', text: `ส่งซ้ำให้ ${recipient} เรียบร้อยแล้ว` })
        } catch (err) {
            setSendStatus({ type: 'error', text: err.message || 'ส่งอีเมลไม่สำเร็จ' })
        } finally {
            setSendingEmail(false)
        }
    }

    const speakerCount = Object.keys(baseResult.transcript?.speaker_summary?.speaking_time || {}).length
    const mappedSpeakerCount = Object.keys(speakerMapping).length

    return (
        <>
            <div className="history-detail-toolbar">
                <div>
                    <div className="history-detail-kicker">จัดการผลลัพธ์</div>
                    <h3 className="history-detail-title">{session.audio_file}</h3>
                </div>
                <div className="history-detail-badges">
                    <span className="history-detail-badge">
                        <Icon name="users" />
                        {mappedSpeakerCount}/{speakerCount} Speaker
                    </span>
                    <span className="history-detail-badge">
                        <Icon name="mail" />
                        {baseResult.summary_status === 'failed' ? 'Transcript' : 'Summary + Transcript'}
                    </span>
                </div>
            </div>

            <div className="history-speaker-tools">
                <div className="history-resend-panel">
                    <div className="email-section-label">
                        <span className="email-dot" />
                        ส่งซ้ำ
                    </div>
                    <div className="history-resend-row">
                        <input
                            type="email"
                            className="email-input"
                            placeholder="อีเมลผู้รับ"
                            value={emailRecipient}
                            onChange={(event) => {
                                setEmailRecipient(event.target.value)
                                setSendStatus(null)
                            }}
                        />
                        <button
                            className="btn btn-primary history-resend-btn"
                            onClick={handleResendEmail}
                            disabled={sendingEmail || !emailRecipient.trim()}
                        >
                            <span className="icon-label">
                                <Icon name={sendingEmail ? 'refresh' : 'mail'} className={sendingEmail ? 'ui-icon-spin' : ''} />
                                {sendingEmail ? 'กำลังส่ง...' : 'ส่งซ้ำ'}
                            </span>
                        </button>
                    </div>
                    {sendStatus && (
                        <div className={`results-status results-status-${sendStatus.type}`}>
                            {sendStatus.text}
                        </div>
                    )}
                </div>

                <div className="history-speaker-panel">
                    <SpeakerIdentification
                        result={baseResult}
                        sessionId={session.clip_prefix || session._id}
                        token={token}
                        onMappingChange={handleMappingChange}
                        isCollapsed={speakerPanelCollapsed}
                        onToggleCollapse={() => setSpeakerPanelCollapsed(prev => !prev)}
                    />
                </div>
            </div>

            <div className="history-result-panel">
                <ResultsTabs
                    result={mappedResult}
                    meetingType={session.meeting_type_id}
                    token={token}
                />
            </div>
        </>
    )
}

function HistoryView() {
    const [sessions, setSessions] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [expandedId, setExpandedId] = useState(null)
    const [detailData, setDetailData] = useState(null)
    const [detailLoading, setDetailLoading] = useState(false)
    const [detailError, setDetailError] = useState(null)
    const detailRequestRef = useRef({ id: 0, controller: null })

    const { token, user } = useAuth()

    const fetchHistory = useCallback(async () => {
        if (!token) return
        setLoading(true)
        setError(null)
        try {
            const res = await fetch(`${API_BASE}/history`, {
                headers: { 'Authorization': `Bearer ${token}` },
            })
            if (!res.ok) throw new Error('Failed to load history')
            const data = await res.json()
            setSessions(data.sessions || [])
        } catch (err) {
            setError(err.message)
        } finally {
            setLoading(false)
        }
    }, [token])

    useEffect(() => {
        fetchHistory()
    }, [fetchHistory])

    useEffect(() => () => {
        detailRequestRef.current.controller?.abort()
    }, [])

    const fetchSessionDetail = useCallback(async (sessionId) => {
        detailRequestRef.current.controller?.abort()
        const controller = new AbortController()
        const requestId = detailRequestRef.current.id + 1
        detailRequestRef.current = { id: requestId, controller }

        setDetailLoading(true)
        setDetailData(null)
        setDetailError(null)
        try {
            const res = await fetch(`${API_BASE}/history/${sessionId}`, {
                headers: { 'Authorization': `Bearer ${token}` },
                signal: controller.signal,
            })
            if (!res.ok) throw new Error('ไม่สามารถโหลดรายละเอียดการประชุมได้')
            const data = await res.json()
            if (detailRequestRef.current.id !== requestId || controller.signal.aborted) return
            setDetailData(data.session)
        } catch (requestError) {
            if (requestError.name === 'AbortError' || detailRequestRef.current.id !== requestId) return
            setDetailData(null)
            setDetailError(requestError.message || 'ไม่สามารถโหลดรายละเอียดการประชุมได้')
        } finally {
            if (detailRequestRef.current.id === requestId) {
                setDetailLoading(false)
            }
        }
    }, [token])

    const handleToggle = async (sessionId) => {
        if (expandedId === sessionId) {
            detailRequestRef.current.controller?.abort()
            detailRequestRef.current.id += 1
            setExpandedId(null)
            setDetailData(null)
            setDetailError(null)
            setDetailLoading(false)
            return
        }

        setExpandedId(sessionId)
        await fetchSessionDetail(sessionId)
    }

    const formatDate = (isoStr) => {
        const d = new Date(isoStr)
        return d.toLocaleDateString('th-TH', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
        })
    }

    const formatDuration = (seconds) => {
        const mins = Math.floor(seconds / 60)
        const secs = Math.floor(seconds % 60)
        return `${mins}:${secs.toString().padStart(2, '0')}`
    }

    if (loading) {
        return (
            <div className="history-loading">
                <div className="history-spinner" />
                <p>กำลังโหลดประวัติ...</p>
            </div>
        )
    }

    if (error) {
        return (
            <div className="history-error">
                <span>เกิดข้อผิดพลาด: {error}</span>
                <button className="btn btn-secondary" onClick={fetchHistory}>ลองใหม่</button>
            </div>
        )
    }

    if (sessions.length === 0) {
        return (
            <div className="history-empty">
                <Icon name="folder" className="history-empty-icon" />
                <h3>ยังไม่มีประวัติการประชุม</h3>
                <p>เมื่อคุณอัปโหลดและประมวลผลไฟล์เสียง ประวัติจะปรากฏที่นี่</p>
            </div>
        )
    }

    return (
        <div className="history-list">
            {sessions.map((session) => (
                <div key={session._id} className="history-card-wrapper">
                    <div
                        className={`history-card ${expandedId === session._id ? 'history-card-active' : ''}`}
                        onClick={() => handleToggle(session._id)}
                    >
                        <div className="history-card-main">
                            <div className="history-card-icon"><Icon name="file-audio" /></div>
                            <div className="history-card-info">
                                <div className="history-card-filename">{session.audio_file}</div>
                                <span className={`summary-status-badge summary-status-${session.summary_status || session.summary_metadata?.summary_status || 'completed'}`}>
                                    {(session.summary_status || session.summary_metadata?.summary_status) === 'partially_completed'
                                        ? `สรุปบางส่วน ${Number(session.coverage_percentage ?? session.summary_metadata?.coverage_percentage ?? 0).toFixed(1)}%`
                                        : (session.summary_status || session.summary_metadata?.summary_status) === 'failed'
                                            ? 'สรุปไม่สำเร็จ'
                                            : 'สรุปครบ'}
                                </span>
                                <div className="history-card-meta">
                                    <span className="icon-label"><Icon name="calendar" /> {formatDate(session.created_at)}</span>
                                    <span className="icon-label"><Icon name="clock" /> {formatDuration(session.audio_length_seconds)}</span>
                                    <span className="icon-label"><Icon name="users" /> {session.speaker_count} คน</span>
                                    <span className="icon-label"><Icon name="folder" /> {session.meeting_type_name}</span>
                                </div>
                            </div>
                            <div className={`history-card-chevron ${expandedId === session._id ? 'open' : ''}`}>
                                <Icon name="chevron-down" />
                            </div>
                        </div>
                        {session.summary && (
                            <div className="history-card-preview">
                                {session.summary.substring(0, 150)}
                                {session.summary.length > 150 ? '...' : ''}
                            </div>
                        )}
                    </div>

                    {expandedId === session._id && (
                        <div className="history-detail">
                            {detailLoading && (
                                <div className="history-detail-loading">
                                    <div className="history-spinner" />
                                    <p>กำลังโหลดรายละเอียด...</p>
                                </div>
                            )}
                            {detailError && !detailLoading && (
                                <div className="history-detail-error" role="alert">
                                    <p>{detailError}</p>
                                    <button
                                        className="btn btn-secondary"
                                        onClick={() => fetchSessionDetail(session._id)}
                                    >
                                        ลองใหม่
                                    </button>
                                </div>
                            )}
                            {detailData && (
                                <HistoryDetailContent
                                    key={detailData._id}
                                    session={detailData}
                                    token={token}
                                    defaultEmail={user?.email || ''}
                                />
                            )}
                        </div>
                    )}
                </div>
            ))}
        </div>
    )
}

export default HistoryView
