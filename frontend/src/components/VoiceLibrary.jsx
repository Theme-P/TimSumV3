import { useState, useEffect, useRef, useCallback } from 'react'
import Icon from './ui/Icon'
import { getVoiceFileValidationError, readAudioDuration } from '../utils/voiceValidation'

const API_BASE = '/api'

function VoiceLibrary({ token }) {
    const [samples, setSamples] = useState([])
    const [loading, setLoading] = useState(true)
    const [uploading, setUploading] = useState(false)
    const [error, setError] = useState(null)
    const [success, setSuccess] = useState(null)
    const [playingId, setPlayingId] = useState(null)
    const [showUpload, setShowUpload] = useState(false)
    const [speakerName, setSpeakerName] = useState('')
    const [speakerPosition, setSpeakerPosition] = useState('')
    const [selectedFile, setSelectedFile] = useState(null)
    const [selectedDuration, setSelectedDuration] = useState(null)
    const [validatingFile, setValidatingFile] = useState(false)
    const [dragOver, setDragOver] = useState(false)
    const audioRef = useRef(null)
    const playbackRequestRef = useRef(null)
    const fileInputRef = useRef(null)

    const stopPlayback = useCallback(() => {
        playbackRequestRef.current?.abort()
        playbackRequestRef.current = null
        if (audioRef.current) {
            audioRef.current.pause()
            if (audioRef.current.objectUrl) {
                URL.revokeObjectURL(audioRef.current.objectUrl)
            }
            audioRef.current = null
        }
        setPlayingId(null)
    }, [])

    const fetchSamples = useCallback(async () => {
        try {
            const res = await fetch(`${API_BASE}/voice-samples`, {
                headers: { 'Authorization': `Bearer ${token}` },
            })
            if (!res.ok) throw new Error('Failed to load')
            const data = await res.json()
            setSamples(data.samples || [])
        } catch {
            setError('ไม่สามารถโหลดคลังเสียงได้')
        } finally {
            setLoading(false)
        }
    }, [token])

    useEffect(() => {
        fetchSamples()
    }, [fetchSamples])

    useEffect(() => () => stopPlayback(), [stopPlayback])

    const handleFileCandidate = async (file) => {
        setError(null)
        setSuccess(null)
        setSelectedFile(null)
        setSelectedDuration(null)

        const basicError = getVoiceFileValidationError(file)
        if (basicError) {
            setError(basicError)
            if (fileInputRef.current) fileInputRef.current.value = ''
            return
        }

        setValidatingFile(true)
        try {
            const duration = await readAudioDuration(file)
            const durationError = getVoiceFileValidationError(file, duration)
            if (durationError) throw new Error(durationError)
            setSelectedFile(file)
            setSelectedDuration(duration)
        } catch (validationError) {
            setError(validationError.message || 'ไม่สามารถตรวจสอบไฟล์เสียงได้')
            if (fileInputRef.current) fileInputRef.current.value = ''
        } finally {
            setValidatingFile(false)
        }
    }

    const handleUpload = async () => {
        if (!selectedFile || !speakerName.trim()) return

        const validationError = getVoiceFileValidationError(selectedFile, selectedDuration)
        if (validationError) {
            setError(validationError)
            return
        }

        setUploading(true)
        setError(null)
        setSuccess(null)

        try {
            const formData = new FormData()
            formData.append('audio', selectedFile)
            formData.append('speaker_name', speakerName.trim())
            if (speakerPosition.trim()) {
                formData.append('speaker_position', speakerPosition.trim())
            }

            const res = await fetch(`${API_BASE}/voice-samples`, {
                method: 'POST',
                headers: { 'Authorization': `Bearer ${token}` },
                body: formData,
            })

            if (!res.ok) {
                const err = await res.json().catch(() => ({}))
                throw new Error(err.detail || 'อัปโหลดไม่สำเร็จ')
            }

            setSuccess('เพิ่มตัวอย่างเสียงเรียบร้อยแล้ว')
            setSpeakerName('')
            setSpeakerPosition('')
            setSelectedFile(null)
            setSelectedDuration(null)
            if (fileInputRef.current) fileInputRef.current.value = ''
            setShowUpload(false)
            await fetchSamples()
        } catch (err) {
            setError(err.message)
        } finally {
            setUploading(false)
        }
    }

    const handleDelete = async (sampleId) => {
        if (!window.confirm('ต้องการลบตัวอย่างเสียงนี้ใช่ไหม?')) return

        try {
            if (playingId === sampleId) stopPlayback()
            const res = await fetch(`${API_BASE}/voice-samples/${sampleId}`, {
                method: 'DELETE',
                headers: { 'Authorization': `Bearer ${token}` },
            })
            if (!res.ok) throw new Error('Delete failed')
            setSamples(prev => prev.filter(s => s._id !== sampleId))
            setSuccess('ลบตัวอย่างเสียงเรียบร้อยแล้ว')
        } catch {
            setError('ไม่สามารถลบได้')
        }
    }

    const handlePlay = async (sampleId) => {
        if (playingId === sampleId) {
            stopPlayback()
            return
        }

        stopPlayback()
        setError(null)
        const controller = new AbortController()
        playbackRequestRef.current = controller

        try {
            const response = await fetch(`${API_BASE}/voice-samples/${sampleId}/play`, {
                headers: { 'Authorization': `Bearer ${token}` },
                signal: controller.signal,
            })
            if (!response.ok) {
                throw new Error(response.status === 404
                    ? 'ไม่พบตัวอย่างเสียงนี้แล้ว กรุณาโหลดรายการใหม่'
                    : 'ไม่สามารถโหลดตัวอย่างเสียงได้')
            }

            const url = URL.createObjectURL(await response.blob())
            if (controller.signal.aborted) {
                URL.revokeObjectURL(url)
                return
            }

            const audio = new Audio(url)
            audio.objectUrl = url
            audioRef.current = audio
            audio.onended = stopPlayback
            audio.onerror = () => {
                stopPlayback()
                setError('เบราว์เซอร์ไม่สามารถเล่นรูปแบบเสียงนี้ได้')
            }

            await audio.play()
            setPlayingId(sampleId)
        } catch (playbackError) {
            if (playbackError.name === 'AbortError') return
            stopPlayback()
            setError(playbackError.message || 'ไม่สามารถเล่นเสียงได้')
        } finally {
            if (playbackRequestRef.current === controller) {
                playbackRequestRef.current = null
            }
        }
    }

    const handleFileDrop = (e) => {
        e.preventDefault()
        setDragOver(false)
        const file = e.dataTransfer?.files?.[0]
        if (file) handleFileCandidate(file)
    }

    const formatDuration = (seconds) => {
        if (!seconds) return '—'
        const mins = Math.floor(seconds / 60)
        const secs = Math.floor(seconds % 60)
        return mins > 0 ? `${mins}:${secs.toString().padStart(2, '0')}` : `${secs}s`
    }

    if (loading) {
        return (
            <div className="voice-library">
                <div className="voice-library-loading">
                    <Icon name="refresh" className="voice-library-spinner" />
                    กำลังโหลดคลังเสียง...
                </div>
            </div>
        )
    }

    return (
        <div className="voice-library">
            {/* Header */}
            <div className="voice-library-header">
                <div className="voice-library-header-info">
                    <h3 className="voice-library-title">
                        <Icon name="mic" /> คลังเสียง
                    </h3>
                    <span className="voice-library-count">{samples.length}/20 ตัวอย่าง</span>
                </div>
                <button
                    className="voice-library-add-btn"
                    onClick={() => setShowUpload(!showUpload)}
                    disabled={samples.length >= 20}
                >
                    {showUpload ? 'ยกเลิก' : 'เพิ่มเสียง'}
                </button>
            </div>

            {/* Status messages */}
            {error && (
                <div className="voice-library-alert voice-library-alert-error">
                    <span className="icon-label"><Icon name="x-circle" /> {error}</span>
                    <button onClick={() => setError(null)} aria-label="ปิดข้อความ"><Icon name="x-circle" /></button>
                </div>
            )}
            {success && (
                <div className="voice-library-alert voice-library-alert-success">
                    <span className="icon-label"><Icon name="check-circle" /> {success}</span>
                    <button onClick={() => setSuccess(null)} aria-label="ปิดข้อความ"><Icon name="x-circle" /></button>
                </div>
            )}

            {/* Upload form */}
            {showUpload && (
                <div className="voice-library-upload">
                    <div
                        className={`voice-library-dropzone ${dragOver ? 'drag-over' : ''} ${selectedFile ? 'has-file' : ''}`}
                        onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                        onDragLeave={() => setDragOver(false)}
                        onDrop={handleFileDrop}
                        onClick={() => fileInputRef.current?.click()}
                    >
                        <input
                            ref={fileInputRef}
                            type="file"
                            accept="audio/*"
                            style={{ display: 'none' }}
                            onChange={(e) => handleFileCandidate(e.target.files?.[0] || null)}
                        />
                        {validatingFile ? (
                            <div className="voice-library-dropzone-empty">
                                <Icon name="refresh" className="voice-library-dropzone-icon ui-icon-spin" />
                                <span>กำลังตรวจสอบไฟล์เสียง...</span>
                            </div>
                        ) : selectedFile ? (
                            <div className="voice-library-dropzone-file">
                                <Icon name="file-audio" className="voice-library-dropzone-icon" />
                                <span className="voice-library-dropzone-name">{selectedFile.name}</span>
                                <span className="voice-library-dropzone-size">
                                    {(selectedFile.size / 1024 / 1024).toFixed(1)} MB · {formatDuration(selectedDuration)}
                                </span>
                            </div>
                        ) : (
                            <div className="voice-library-dropzone-empty">
                                <Icon name="upload" className="voice-library-dropzone-icon" />
                                <span>ลากไฟล์เสียงมาวางที่นี่ หรือคลิกเลือกไฟล์</span>
                                <span className="voice-library-dropzone-hint">
                                    MP3, WAV, M4A · ไม่เกิน 10 MB · ความยาว 5-30 วินาที
                                </span>
                            </div>
                        )}
                    </div>

                    <div className="voice-library-upload-fields">
                        <div className="voice-library-field">
                            <label>ชื่อผู้พูด *</label>
                            <input
                                type="text"
                                placeholder="เช่น คุณเจษฎา, คุณสมศรี"
                                value={speakerName}
                                onChange={(e) => setSpeakerName(e.target.value)}
                                maxLength={100}
                            />
                        </div>
                        <div className="voice-library-field">
                            <label>ตำแหน่ง (ไม่บังคับ)</label>
                            <input
                                type="text"
                                placeholder="เช่น ประธาน, ผู้จัดการ"
                                value={speakerPosition}
                                onChange={(e) => setSpeakerPosition(e.target.value)}
                                maxLength={100}
                            />
                        </div>
                    </div>

                    <button
                        className="voice-library-upload-btn"
                        onClick={handleUpload}
                        disabled={!selectedFile || !speakerName.trim() || uploading || validatingFile}
                    >
                        {uploading ? (
                            <><Icon name="refresh" className="ui-icon-spin" /> กำลังวิเคราะห์เสียง...</>
                        ) : (
                            <><Icon name="mic" /> บันทึกตัวอย่างเสียง</>
                        )}
                    </button>
                </div>
            )}

            {/* Sample list */}
            {samples.length === 0 ? (
                <div className="voice-library-empty">
                    <Icon name="mic" className="voice-library-empty-icon" />
                    <p className="voice-library-empty-title">ยังไม่มีตัวอย่างเสียง</p>
                    <p className="voice-library-empty-hint">
                        เพิ่มตัวอย่างเสียงของผู้พูดที่คุณรู้จัก เพื่อให้ระบบจับคู่ชื่อผู้พูดอัตโนมัติ
                    </p>
                </div>
            ) : (
                <div className="voice-library-list">
                    {samples.map(sample => (
                        <div key={sample._id} className="voice-library-item">
                            <div className="voice-library-item-info">
                                <div className="voice-library-item-name">
                                    {sample.speaker_name}
                                    {sample.speaker_position && (
                                        <span className="voice-library-item-position">
                                            ({sample.speaker_position})
                                        </span>
                                    )}
                                </div>
                                <div className="voice-library-item-meta">
                                    <span className="icon-label"><Icon name="clock" /> {formatDuration(sample.duration_seconds)}</span>
                                    <span>·</span>
                                    <span>{new Date(sample.created_at).toLocaleDateString('th-TH')}</span>
                                </div>
                            </div>
                            <div className="voice-library-item-actions">
                                <button
                                    className={`voice-library-play-btn ${playingId === sample._id ? 'playing' : ''}`}
                                    onClick={() => handlePlay(sample._id)}
                                    title={playingId === sample._id ? 'หยุดเล่น' : 'เล่นเสียง'}
                                    aria-label={playingId === sample._id ? 'หยุดเล่น' : 'เล่นเสียง'}
                                >
                                    <Icon name={playingId === sample._id ? 'square' : 'play'} />
                                </button>
                                <button
                                    className="voice-library-delete-btn"
                                    onClick={() => handleDelete(sample._id)}
                                    title="ลบตัวอย่างเสียง"
                                    aria-label="ลบตัวอย่างเสียง"
                                >
                                    <Icon name="trash" />
                                </button>
                            </div>
                        </div>
                    ))}
                </div>
            )}
        </div>
    )
}

export default VoiceLibrary
