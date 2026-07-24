import { useEffect, useMemo, useState } from 'react'
import AgendaTimeline from './AgendaTimeline'
import Icon from './ui/Icon'

const API_BASE = '/api'
const TRANSCRIPT_PAGE_SIZE = 150

function ResultsTabs({ result, meetingType = 0, token }) {
    const [activeTab, setActiveTab] = useState('transcript')
    const [downloading, setDownloading] = useState(null)
    const [visibleTranscriptCount, setVisibleTranscriptCount] = useState(TRANSCRIPT_PAGE_SIZE)
    const [transcriptQuery, setTranscriptQuery] = useState('')
    const [statusMessage, setStatusMessage] = useState(null)

    useEffect(() => {
        setVisibleTranscriptCount(TRANSCRIPT_PAGE_SIZE)
        setTranscriptQuery('')
        setStatusMessage(null)
    }, [result.audio_file, result.audio_length_seconds])

    const formatTime = (seconds = 0) => {
        const mins = Math.floor(seconds / 60)
        const secs = Math.floor(seconds % 60)
        return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`
    }

    // Build speaker name mapping: "คนพูด 1" -> "ชื่อจริง (ตำแหน่ง)"
    const buildSpeakerDisplayName = (speakerLabel) => {
        if (!speakerLabel) return 'ไม่ระบุ'
        // Backend already maps names if provided
        return speakerLabel
    }

    const handleDownloadTranscriptDocx = async () => {
        setDownloading('transcript')
        setStatusMessage(null)
        try {
            const response = await fetch(`${API_BASE}/export/transcript`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token && { 'Authorization': `Bearer ${token}` }),
                },
                body: JSON.stringify({
                    segments: result.transcript.segments,
                    audio_file: result.audio_file,
                    audio_length_seconds: result.audio_length_seconds
                })
            })

            if (!response.ok) throw new Error('Export failed')

            const blob = await response.blob()
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = 'transcript.docx'
            a.click()
            URL.revokeObjectURL(url)
            setStatusMessage({ type: 'success', text: 'ดาวน์โหลด Transcript แล้ว' })
        } catch (err) {
            setStatusMessage({ type: 'error', text: 'เกิดข้อผิดพลาดในการดาวน์โหลด: ' + err.message })
        } finally {
            setDownloading(null)
        }
    }

    const handleDownloadSummaryDocx = async () => {
        setDownloading('summary')
        setStatusMessage(null)
        try {
            const response = await fetch(`${API_BASE}/export/summary`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    ...(token && { 'Authorization': `Bearer ${token}` }),
                },
                body: JSON.stringify({
                    summary: result.summary,
                    speaker_summary: result.transcript.speaker_summary,
                    meeting_type_id: meetingType,
                    agendas: result.agendas || [],
                    summary_warning: result.summary_metadata?.user_warning || '',
                })
            })

            if (!response.ok) throw new Error('Export failed')

            const blob = await response.blob()
            const url = URL.createObjectURL(blob)
            const a = document.createElement('a')
            a.href = url
            a.download = 'summary.docx'
            a.click()
            URL.revokeObjectURL(url)
            setStatusMessage({ type: 'success', text: 'ดาวน์โหลด Summary แล้ว' })
        } catch (err) {
            setStatusMessage({ type: 'error', text: 'เกิดข้อผิดพลาดในการดาวน์โหลด: ' + err.message })
        } finally {
            setDownloading(null)
        }
    }

    // Calculate speaker percentages
    const speakerStats = result.transcript.speaker_summary || { speaking_time: {}, word_count: {} }
    const totalSpeakingTime = useMemo(
        () => Object.values(speakerStats.speaking_time || {}).reduce((a, b) => a + b, 0),
        [speakerStats],
    )
    const transcriptSegments = result.transcript.segments || []
    const filteredSegments = useMemo(() => {
        const query = transcriptQuery.trim().toLowerCase()
        if (!query) return transcriptSegments
        return transcriptSegments.filter(segment => {
            const text = `${segment.speaker || ''} ${segment.text || ''}`.toLowerCase()
            return text.includes(query)
        })
    }, [transcriptSegments, transcriptQuery])
    const visibleSegments = filteredSegments.slice(0, visibleTranscriptCount)
    const summaryWarning = result.summary_metadata?.user_warning
    const summaryStatus = result.summary_status || result.summary_metadata?.summary_status || 'completed'
    const coveragePercentage = Number(
        result.coverage_percentage ?? result.summary_metadata?.coverage_percentage ?? 100,
    )
    const hasSummary = Boolean((result.summary || '').trim()) && summaryStatus !== 'failed'

    return (
        <div>
            {/* Processing Info */}
            <div className="results-meta">
                <span className="icon-label"><Icon name="clock" /> ประมวลผล {(result.processing_time?.total || 0).toFixed(1)} วินาที</span>
                <span className="icon-label"><Icon name="file-audio" /> ความยาว {formatTime(result.audio_length_seconds)}</span>
                <span className="icon-label"><Icon name="file-text" /> {transcriptSegments.length.toLocaleString()} segments</span>
                <span className={`summary-status-badge summary-status-${summaryStatus}`}>
                    {summaryStatus === 'partially_completed'
                        ? `สรุปบางส่วน ${coveragePercentage.toFixed(1)}%`
                        : summaryStatus === 'failed'
                            ? 'สรุปไม่สำเร็จ'
                            : 'สรุปครบ'}
                </span>
            </div>

            {summaryWarning && (
                <div className={`results-status ${summaryStatus === 'failed' ? 'results-status-error' : 'results-status-warning'} summary-warning`} role="alert">
                    <span className="icon-label">
                        <Icon name="alert-circle" />
                        {summaryWarning}
                    </span>
                </div>
            )}

            {/* Tabs */}
            <div className="tabs">
                <button
                    className={`tab ${activeTab === 'transcript' ? 'active' : ''}`}
                    onClick={() => setActiveTab('transcript')}
                >
                    <span className="icon-label"><Icon name="file-text" /> Transcript</span>
                </button>
                <button
                    className={`tab ${activeTab === 'summary' ? 'active' : ''}`}
                    onClick={() => setActiveTab('summary')}
                >
                    <span className="icon-label"><Icon name="bar-chart" /> Summary</span>
                </button>
                {result.agendas && result.agendas.length > 0 && (
                    <button
                        className={`tab ${activeTab === 'agendas' ? 'active' : ''}`}
                        onClick={() => setActiveTab('agendas')}
                    >
                        <span className="icon-label"><Icon name="clipboard-list" /> วาระ ({result.agendas.length})</span>
                    </button>
                )}
                <button
                    className={`tab ${activeTab === 'speakers' ? 'active' : ''}`}
                    onClick={() => setActiveTab('speakers')}
                >
                    <span className="icon-label"><Icon name="users" /> Speakers</span>
                </button>
            </div>

            {/* Tab Content */}
            <div className="tab-content">
                {/* Transcript Tab */}
                {activeTab === 'transcript' && (
                    <div>
                        <div className="transcript-toolbar">
                            <input
                                className="transcript-search"
                                type="search"
                                placeholder="ค้นหาใน transcript หรือชื่อผู้พูด"
                                value={transcriptQuery}
                                onChange={(e) => {
                                    setTranscriptQuery(e.target.value)
                                    setVisibleTranscriptCount(TRANSCRIPT_PAGE_SIZE)
                                }}
                            />
                            <span className="transcript-count">
                                แสดง {Math.min(visibleSegments.length, filteredSegments.length).toLocaleString()} / {filteredSegments.length.toLocaleString()}
                            </span>
                        </div>

                        {visibleSegments.length === 0 ? (
                            <div className="transcript-empty">ไม่พบข้อความที่ค้นหา</div>
                        ) : (
                            visibleSegments.map((segment, index) => (
                                <div key={`${segment.start}-${segment.end}-${index}`} className="transcript-segment">
                                    <div className="segment-header">
                                        <span className="segment-time">
                                            {formatTime(segment.start)} - {formatTime(segment.end)}
                                        </span>
                                        <span className="segment-speaker">
                                            {buildSpeakerDisplayName(segment.speaker)}
                                        </span>
                                    </div>
                                    <p className="segment-text">{segment.text}</p>
                                </div>
                            ))
                        )}

                        {filteredSegments.length > visibleTranscriptCount && (
                            <div className="transcript-load-more">
                                <button
                                    className="btn btn-secondary"
                                    onClick={() => setVisibleTranscriptCount(prev => prev + TRANSCRIPT_PAGE_SIZE)}
                                >
                                    โหลดเพิ่มอีก {Math.min(TRANSCRIPT_PAGE_SIZE, filteredSegments.length - visibleTranscriptCount).toLocaleString()} segments
                                </button>
                            </div>
                        )}
                    </div>
                )}

                {/* Summary Tab */}
                {activeTab === 'summary' && (
                    <div className="summary-content">
                        {hasSummary ? result.summary : 'ไม่มี Summary ที่ใช้งานได้สำหรับงานนี้ กรุณาตรวจสอบ Transcript'}
                    </div>
                )}

                {/* Speakers Tab */}
                {activeTab === 'speakers' && (
                    <div className="speaker-stats">
                        {Object.entries(speakerStats.speaking_time).map(([speaker, time]) => {
                            const percentage = totalSpeakingTime > 0
                                ? (time / totalSpeakingTime) * 100
                                : 0
                            const wordCount = speakerStats.word_count[speaker] || 0

                            return (
                                <div key={speaker} className="speaker-stat-item">
                                    <div className="speaker-avatar">
                                        {speaker.charAt(0)}
                                    </div>
                                    <div className="speaker-info">
                                        <div className="speaker-name">{speaker}</div>
                                        <div className="speaker-meta">
                                            {formatTime(time)} ({percentage.toFixed(1)}%) • {wordCount} คำ
                                        </div>
                                    </div>
                                    <div className="speaker-bar">
                                        <div
                                            className="speaker-bar-fill"
                                            style={{ width: `${percentage}%` }}
                                        />
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                )}

                {/* Agendas Tab */}
                {activeTab === 'agendas' && result.agendas && result.agendas.length > 0 && (
                    <AgendaTimeline
                        agendas={result.agendas}
                        detectionMode={result.detection_mode || 'topic_segments'}
                    />
                )}
            </div>

            {/* Actions */}
            <div className="actions">
                <button
                    className="btn btn-primary"
                    onClick={handleDownloadTranscriptDocx}
                    disabled={downloading === 'transcript'}
                >
                    <span className="icon-label">
                        <Icon name={downloading === 'transcript' ? 'refresh' : 'download'} className={downloading === 'transcript' ? 'ui-icon-spin' : ''} />
                        {downloading === 'transcript' ? 'กำลังสร้าง...' : 'ดาวน์โหลด Transcript (DOCX)'}
                    </span>
                </button>
                <button
                    className="btn btn-primary"
                    onClick={handleDownloadSummaryDocx}
                    disabled={downloading === 'summary' || !hasSummary}
                >
                    <span className="icon-label">
                        <Icon name={downloading === 'summary' ? 'refresh' : 'download'} className={downloading === 'summary' ? 'ui-icon-spin' : ''} />
                        {downloading === 'summary' ? 'กำลังสร้าง...' : 'ดาวน์โหลด Summary (DOCX)'}
                    </span>
                </button>
            </div>

            {statusMessage && (
                <div className={`results-status results-status-${statusMessage.type}`}>
                    {statusMessage.text}
                </div>
            )}
        </div>
    )
}

export default ResultsTabs
