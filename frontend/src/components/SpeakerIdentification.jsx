import { useState, useEffect, useRef } from 'react'

const API_BASE = '/api'

function SpeakerIdentification({ result, sessionId, onMappingChange, isCollapsed, onToggleCollapse }) {
    const speakerStats = result.transcript.speaker_summary
    const speakerClips = result.speaker_clips || {}
    const suggestedNames = result.suggested_names || {}

    const speakers = Object.keys(speakerStats.speaking_time).sort()

    const [speakerNames, setSpeakerNames] = useState(
        speakers.reduce((acc, speaker) => {
            const suggested = suggestedNames[speaker]
            acc[speaker] = {
                name: suggested?.name || '',
                position: suggested?.position || ''
            }
            return acc
        }, {})
    )
    const [playingSpeaker, setPlayingSpeaker] = useState(null)
    const audioRef = useRef(null)

    const totalSpeakingTime = Object.values(speakerStats.speaking_time).reduce((a, b) => a + b, 0)
    const detectedCount = Object.keys(suggestedNames).length

    // Notify parent whenever names change (live update)
    useEffect(() => {
        const mapping = {}
        for (const [speaker, info] of Object.entries(speakerNames)) {
            if (info.name.trim()) {
                mapping[speaker] = info.position.trim()
                    ? `${info.name.trim()} (${info.position.trim()})`
                    : info.name.trim()
            }
        }
        onMappingChange(mapping)
    }, [speakerNames])

    const formatTime = (seconds) => {
        const mins = Math.floor(seconds / 60)
        const secs = Math.floor(seconds % 60)
        return `${mins}:${secs.toString().padStart(2, '0')}`
    }

    const updateSpeakerField = (speaker, field, value) => {
        setSpeakerNames(prev => ({
            ...prev,
            [speaker]: { ...prev[speaker], [field]: value }
        }))
    }

    const handlePlayClip = (speaker) => {
        const clip = speakerClips[speaker]
        if (!clip || !sessionId) return

        const clipUrl = `${API_BASE}/speaker-clip/${sessionId}/${clip.clip_filename}`

        if (playingSpeaker === speaker && audioRef.current) {
            audioRef.current.pause()
            audioRef.current.currentTime = 0
            setPlayingSpeaker(null)
            return
        }

        if (audioRef.current) {
            audioRef.current.pause()
        }

        const audio = new Audio(clipUrl)
        audioRef.current = audio
        setPlayingSpeaker(speaker)

        audio.play().catch(err => {
            console.error('Audio play error:', err)
            setPlayingSpeaker(null)
        })

        audio.onended = () => {
            setPlayingSpeaker(null)
        }
    }

    const filledCount = Object.values(speakerNames).filter(s => s.name.trim() !== '').length

    return (
        <div className="speaker-id-panel">
            <div className="speaker-id-header" onClick={onToggleCollapse} style={{ cursor: 'pointer' }}>
                <div className="speaker-id-title-row">
                    <h3 className="speaker-id-title">
                        👥 ข้อมูลผู้เข้าร่วมประชุม
                        <span className="speaker-id-count">{filledCount}/{speakers.length}</span>
                    </h3>
                    <button className="btn-collapse" title={isCollapsed ? 'ขยาย' : 'ย่อ'}>
                        {isCollapsed ? '▼ แก้ไข' : '▲ ย่อ'}
                    </button>
                </div>
                {isCollapsed && filledCount > 0 && (
                    <p className="speaker-id-summary-text">
                        {Object.entries(speakerNames)
                            .filter(([, info]) => info.name.trim())
                            .map(([speaker, info]) => {
                                const display = info.position.trim()
                                    ? `${info.name.trim()} (${info.position.trim()})`
                                    : info.name.trim()
                                return display
                            })
                            .join(' • ')}
                    </p>
                )}
            </div>

            {!isCollapsed && (
                <>
                    <p className="speaker-id-subtitle">
                        {detectedCount > 0
                            ? <>AI ตรวจพบชื่อ <strong>{detectedCount}</strong> จาก <strong>{speakers.length}</strong> คน ✨ แก้ไขได้ตลอดเวลา</>
                            : <>พบผู้พูด <strong>{speakers.length}</strong> คน — ฟังเสียงตัวอย่างแล้วกรอกชื่อ</>
                        }
                    </p>

                    <div className="speaker-id-list">
                        {speakers.map((speaker, index) => {
                            const time = speakerStats.speaking_time[speaker] || 0
                            const pct = totalSpeakingTime > 0 ? (time / totalSpeakingTime) * 100 : 0
                            const wordCount = speakerStats.word_count[speaker] || 0
                            const clip = speakerClips[speaker]
                            const isPlaying = playingSpeaker === speaker
                            const hasSuggestion = !!suggestedNames[speaker]

                            return (
                                <div key={speaker} className={`speaker-id-card ${hasSuggestion ? 'auto-detected' : ''}`}>
                                    <div className="speaker-id-card-header">
                                        <div className="speaker-id-avatar">
                                            {index + 1}
                                        </div>
                                        <div className="speaker-id-info">
                                            <span className="speaker-id-label">
                                                {speaker}
                                                {hasSuggestion && <span className="speaker-id-badge">AI</span>}
                                            </span>
                                            <span className="speaker-id-meta">
                                                {formatTime(time)} ({pct.toFixed(1)}%) | {wordCount} words
                                            </span>
                                        </div>
                                        {clip && (
                                            <button
                                                className={`btn-play-clip ${isPlaying ? 'playing' : ''}`}
                                                onClick={(e) => { e.stopPropagation(); handlePlayClip(speaker) }}
                                                title={isPlaying ? 'หยุดเล่น' : 'เล่นตัวอย่างเสียง'}
                                            >
                                                {isPlaying ? '⏹️ หยุด' : '▶️ ฟังเสียง'}
                                            </button>
                                        )}
                                    </div>

                                    {clip && (
                                        <div className="speaker-id-clip-info">
                                            🔊 ตัวอย่างเสียง {clip.duration.toFixed(1)} วินาที
                                            ({formatTime(clip.start)} - {formatTime(clip.end)})
                                        </div>
                                    )}

                                    <div className="speaker-id-fields">
                                        <input
                                            type="text"
                                            className="speaker-id-input"
                                            placeholder="ชื่อ-สกุล"
                                            value={speakerNames[speaker]?.name || ''}
                                            onChange={(e) => updateSpeakerField(speaker, 'name', e.target.value)}
                                        />
                                        <input
                                            type="text"
                                            className="speaker-id-input"
                                            placeholder="ตำแหน่ง"
                                            value={speakerNames[speaker]?.position || ''}
                                            onChange={(e) => updateSpeakerField(speaker, 'position', e.target.value)}
                                        />
                                    </div>

                                    <div className="speaker-id-bar-container">
                                        <div
                                            className="speaker-id-bar-fill"
                                            style={{ width: `${pct}%` }}
                                        />
                                    </div>
                                </div>
                            )
                        })}
                    </div>
                </>
            )}
        </div>
    )
}

export default SpeakerIdentification
