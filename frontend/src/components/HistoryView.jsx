import { useState, useEffect } from 'react'
import { useAuth } from '../contexts/AuthContext'
import ResultsTabs from './ResultsTabs'

const API_BASE = '/api'

function HistoryView() {
    const [sessions, setSessions] = useState([])
    const [loading, setLoading] = useState(true)
    const [error, setError] = useState(null)
    const [expandedId, setExpandedId] = useState(null)
    const [detailData, setDetailData] = useState(null)
    const [detailLoading, setDetailLoading] = useState(false)

    const { token } = useAuth()

    useEffect(() => {
        fetchHistory()
    }, [])

    const fetchHistory = async () => {
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
    }

    const handleToggle = async (sessionId) => {
        if (expandedId === sessionId) {
            setExpandedId(null)
            setDetailData(null)
            return
        }

        setExpandedId(sessionId)
        setDetailLoading(true)
        setDetailData(null)
        try {
            const res = await fetch(`${API_BASE}/history/${sessionId}`, {
                headers: { 'Authorization': `Bearer ${token}` },
            })
            if (!res.ok) throw new Error('Failed to load session')
            const data = await res.json()
            setDetailData(data.session)
        } catch {
            setDetailData(null)
        } finally {
            setDetailLoading(false)
        }
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
                <div className="history-empty-icon">📂</div>
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
                            <div className="history-card-icon">🎵</div>
                            <div className="history-card-info">
                                <div className="history-card-filename">{session.audio_file}</div>
                                <div className="history-card-meta">
                                    <span>📅 {formatDate(session.created_at)}</span>
                                    <span>⏱️ {formatDuration(session.audio_length_seconds)}</span>
                                    <span>👥 {session.speaker_count} คน</span>
                                    <span>📁 {session.meeting_type_name}</span>
                                </div>
                            </div>
                            <div className={`history-card-chevron ${expandedId === session._id ? 'open' : ''}`}>
                                ▼
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
                            {detailData && (
                                <ResultsTabs
                                    result={{
                                        audio_file: detailData.audio_file,
                                        audio_length_seconds: detailData.audio_length_seconds,
                                        processing_time: detailData.processing_time,
                                        transcript: detailData.transcript,
                                        summary: detailData.summary,
                                    }}
                                    meetingType={detailData.meeting_type_id}
                                    token={token}
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
