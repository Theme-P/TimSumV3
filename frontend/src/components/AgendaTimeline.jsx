import React from 'react'
import Icon from './ui/Icon'

/**
 * AgendaTimeline — Displays detected meeting agendas as a vertical timeline.
 * Each agenda item shows title, time range, speakers, summary, decisions, and action items.
 *
 * Props:
 *   agendas: Array of agenda objects from backend
 *   detectionMode: "formal_agenda" | "topic_segments" | "single_topic"
 */
export default function AgendaTimeline({ agendas, detectionMode }) {
    if (!agendas || agendas.length === 0) {
        return null
    }

    const formatTime = (seconds) => {
        const mins = Math.floor(seconds / 60)
        const secs = Math.floor(seconds % 60)
        return `${mins}:${secs.toString().padStart(2, '0')}`
    }

    const modeBadge = detectionMode === 'formal_agenda' ? 'วาระ' : 'หัวข้อ'

    return (
        <div className="agenda-timeline">
            <div className="agenda-header">
                <h3 className="icon-label"><Icon name="clipboard-list" /> {detectionMode === 'formal_agenda' ? 'วาระการประชุม' : 'หัวข้อการประชุม'}</h3>
                <span className="agenda-mode-badge">{modeBadge} {agendas.length} รายการ</span>
            </div>

            <div className="agenda-list">
                {agendas.map((agenda, index) => (
                    <div key={index} className="agenda-item">
                        <div className="agenda-item-marker">
                            <div className="agenda-number">{agenda.agenda_number}</div>
                            {index < agendas.length - 1 && <div className="agenda-connector" />}
                        </div>

                        <div className="agenda-item-content">
                            <div className="agenda-item-header">
                                <h4 className="agenda-title">{agenda.title}</h4>
                                <span className="agenda-time">
                                    {formatTime(agenda.start_time)} — {formatTime(agenda.end_time)}
                                </span>
                            </div>

                            {agenda.speakers && agenda.speakers.length > 0 && (
                                <div className="agenda-speakers">
                                    {agenda.speakers.map((speaker, sIdx) => (
                                        <span key={sIdx} className="agenda-speaker-tag">{speaker}</span>
                                    ))}
                                </div>
                            )}

                            {agenda.summary && (
                                <div className="agenda-summary">
                                    {agenda.summary.split('\n').map((line, lIdx) => {
                                        const trimmed = line.trim()
                                        if (!trimmed) return null
                                        if (trimmed.startsWith('- ') || trimmed.startsWith('• ')) {
                                            return <li key={lIdx}>{trimmed.substring(2)}</li>
                                        }
                                        return <p key={lIdx}>{trimmed}</p>
                                    })}
                                </div>
                            )}

                            {agenda.decisions && agenda.decisions.length > 0 && (
                                <div className="agenda-section decisions">
                                    <span className="agenda-section-label icon-label"><Icon name="check-circle" /> มติ/ข้อตกลง</span>
                                    <ul>
                                        {agenda.decisions.map((d, dIdx) => (
                                            <li key={dIdx}>{d}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {agenda.action_items && agenda.action_items.length > 0 && (
                                <div className="agenda-section action-items">
                                    <span className="agenda-section-label icon-label"><Icon name="pin" /> งานมอบหมาย</span>
                                    <ul>
                                        {agenda.action_items.map((a, aIdx) => (
                                            <li key={aIdx}>{a}</li>
                                        ))}
                                    </ul>
                                </div>
                            )}

                            {agenda.confidence !== undefined && (
                                <div className="agenda-confidence">
                                    ความเชื่อมั่น: {Math.round(agenda.confidence * 100)}%
                                </div>
                            )}
                        </div>
                    </div>
                ))}
            </div>
        </div>
    )
}
