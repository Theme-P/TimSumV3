import { useState, useEffect, useRef } from 'react'
import Icon from './ui/Icon'

const API_BASE = '/api'

function SpeakerIdentification({ result, sessionId, token, onMappingChange, isCollapsed, onToggleCollapse }) {
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
    const [unavailableClips, setUnavailableClips] = useState({})
    const [playbackError, setPlaybackError] = useState('')
    const audioRef = useRef(null)
    const playbackRequestRef = useRef(null)

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
    }, [speakerNames, onMappingChange])

    useEffect(() => {
        return () => {
            playbackRequestRef.current?.abort()
            if (audioRef.current) {
                audioRef.current.pause()
                if (audioRef.current.objectUrl) {
                    URL.revokeObjectURL(audioRef.current.objectUrl)
                }
            }
        }
    }, [])

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

    const revokeCurrentAudioUrl = () => {
        if (audioRef.current?.objectUrl) {
            URL.revokeObjectURL(audioRef.current.objectUrl)
            audioRef.current.objectUrl = null
        }
    }

    const stopCurrentAudio = () => {
        playbackRequestRef.current?.abort()
        playbackRequestRef.current = null
        if (audioRef.current) {
            audioRef.current.pause()
            audioRef.current.currentTime = 0
        }
        revokeCurrentAudioUrl()
        audioRef.current = null
        setPlayingSpeaker(null)
    }

    const handlePlayClip = async (speaker) => {
        const clip = speakerClips[speaker]
        if (!clip || !sessionId || !token || unavailableClips[speaker]) return

        const clipUrl = `${API_BASE}/speaker-clip/${sessionId}/${clip.clip_filename}`

        if (playingSpeaker === speaker && audioRef.current) {
            stopCurrentAudio()
            return
        }

        stopCurrentAudio()
        setPlaybackError('')
        const controller = new AbortController()
        playbackRequestRef.current = controller

        try {
            const res = await fetch(clipUrl, {
                headers: { 'Authorization': `Bearer ${token}` },
                signal: controller.signal,
            })
            if (res.status === 404 || res.status === 410) {
                setUnavailableClips((current) => ({ ...current, [speaker]: true }))
                throw new Error('คลิปเสียงหมดอายุแล้วตามนโยบายการเก็บรักษา')
            }
            if (!res.ok) throw new Error('ไม่สามารถโหลดคลิปเสียงได้')

            const objectUrl = URL.createObjectURL(await res.blob())
            if (controller.signal.aborted) {
                URL.revokeObjectURL(objectUrl)
                return
            }
            const audio = new Audio(objectUrl)
            audio.objectUrl = objectUrl
            audioRef.current = audio
            setPlayingSpeaker(speaker)

            audio.onended = () => {
                stopCurrentAudio()
            }
            await audio.play()
        } catch (err) {
            if (err.name === 'AbortError') return
            stopCurrentAudio()
            setPlaybackError(err.message || 'ไม่สามารถเล่นคลิปเสียงได้')
        } finally {
            if (playbackRequestRef.current === controller) {
                playbackRequestRef.current = null
            }
        }
    }

    const filledCount = Object.values(speakerNames).filter(s => s.name.trim() !== '').length

    return (
        <div className="speaker-id-panel">
            <div className="speaker-id-header" onClick={onToggleCollapse} style={{ cursor: 'pointer' }}>
                <div className="speaker-id-title-row">
                    <h3 className="speaker-id-title">
                        <Icon name="users" /> ข้อมูลผู้เข้าร่วมประชุม
                        <span className="speaker-id-count">{filledCount}/{speakers.length}</span>
                    </h3>
                    <button className="btn-collapse" title={isCollapsed ? 'ขยาย' : 'ย่อ'}>
                        <span className="icon-label"><Icon name="chevron-down" className={isCollapsed ? '' : 'icon-rotate-180'} /> {isCollapsed ? 'แก้ไข' : 'ย่อ'}</span>
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
                            ? <>AI ตรวจพบชื่อ <strong>{detectedCount}</strong> จาก <strong>{speakers.length}</strong> คน แก้ไขได้ตลอดเวลา</>
                            : <>พบผู้พูด <strong>{speakers.length}</strong> คน — ฟังเสียงตัวอย่างแล้วกรอกชื่อ</>
                        }
                    </p>
                    {playbackError && (
                        <p className="speaker-clip-error" role="alert">{playbackError}</p>
                    )}

                    <div className="speaker-id-list">
                        {speakers.map((speaker, index) => {
                            const time = speakerStats.speaking_time[speaker] || 0
                            const pct = totalSpeakingTime > 0 ? (time / totalSpeakingTime) * 100 : 0
                            const wordCount = speakerStats.word_count[speaker] || 0
                            const clip = speakerClips[speaker]
                            const isPlaying = playingSpeaker === speaker
                            const hasSuggestion = !!suggestedNames[speaker]
                            const clipUnavailable = !!unavailableClips[speaker]

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
                                                title={clipUnavailable ? 'คลิปเสียงหมดอายุแล้ว' : (isPlaying ? 'หยุดเล่น' : 'เล่นตัวอย่างเสียง')}
                                                disabled={clipUnavailable}
                                            >
                                                <span className="icon-label">
                                                    <Icon name={isPlaying ? 'square' : 'play'} />
                                                    {clipUnavailable ? 'หมดอายุ' : (isPlaying ? 'หยุด' : 'ฟังเสียง')}
                                                </span>
                                            </button>
                                        )}
                                    </div>

                                    {clip && !clipUnavailable && (
                                        <div className="speaker-id-clip-info">
                                            <Icon name="volume" /> ตัวอย่างเสียง {Number(clip.duration || 0).toFixed(1)} วินาที
                                            ({formatTime(clip.start)} - {formatTime(clip.end)})
                                        </div>
                                    )}
                                    {clipUnavailable && (
                                        <div className="speaker-id-clip-info speaker-id-clip-expired">
                                            คลิปเสียงหมดอายุแล้ว แต่ Transcript และสรุปยังใช้งานได้ตามปกติ
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
