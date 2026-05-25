import { useState, useEffect, useRef, useCallback } from 'react'

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
    const [dragOver, setDragOver] = useState(false)
    const audioRef = useRef(null)
    const fileInputRef = useRef(null)

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

    const handleUpload = async () => {
        if (!selectedFile || !speakerName.trim()) return

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

            setSuccess('เพิ่มตัวอย่างเสียงเรียบร้อยแล้ว ✨')
            setSpeakerName('')
            setSpeakerPosition('')
            setSelectedFile(null)
            setShowUpload(false)
            fetchSamples()
        } catch (err) {
            setError(err.message)
        } finally {
            setUploading(false)
        }
    }

    const handleDelete = async (sampleId) => {
        if (!confirm('ต้องการลบตัวอย่างเสียงนี้ใช่ไหม?')) return

        try {
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

    const handlePlay = (sampleId) => {
        if (playingId === sampleId) {
            // Stop playing
            if (audioRef.current) {
                audioRef.current.pause()
                audioRef.current = null
            }
            setPlayingId(null)
            return
        }

        // Stop previous
        if (audioRef.current) {
            audioRef.current.pause()
        }

        const audio = new Audio(`${API_BASE}/voice-samples/${sampleId}/play`)
        // Set auth header via fetch workaround
        fetch(`${API_BASE}/voice-samples/${sampleId}/play`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.blob())
            .then(blob => {
                const url = URL.createObjectURL(blob)
                const a = new Audio(url)
                a.onended = () => {
                    setPlayingId(null)
                    URL.revokeObjectURL(url)
                }
                a.play()
                audioRef.current = a
                setPlayingId(sampleId)
            })
            .catch(() => setError('ไม่สามารถเล่นเสียงได้'))
    }

    const handleFileDrop = (e) => {
        e.preventDefault()
        setDragOver(false)
        const file = e.dataTransfer?.files?.[0]
        if (file && file.type.startsWith('audio/')) {
            setSelectedFile(file)
        }
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
                    <span className="voice-library-spinner">🔄</span>
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
                        <span>🎙️</span> คลังเสียง
                    </h3>
                    <span className="voice-library-count">{samples.length}/20 ตัวอย่าง</span>
                </div>
                <button
                    className="voice-library-add-btn"
                    onClick={() => setShowUpload(!showUpload)}
                    disabled={samples.length >= 20}
                >
                    {showUpload ? '✕ ยกเลิก' : '+ เพิ่มเสียง'}
                </button>
            </div>

            {/* Status messages */}
            {error && (
                <div className="voice-library-alert voice-library-alert-error">
                    ❌ {error}
                    <button onClick={() => setError(null)}>✕</button>
                </div>
            )}
            {success && (
                <div className="voice-library-alert voice-library-alert-success">
                    ✅ {success}
                    <button onClick={() => setSuccess(null)}>✕</button>
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
                            onChange={(e) => setSelectedFile(e.target.files?.[0] || null)}
                        />
                        {selectedFile ? (
                            <div className="voice-library-dropzone-file">
                                <span className="voice-library-dropzone-icon">🎵</span>
                                <span className="voice-library-dropzone-name">{selectedFile.name}</span>
                                <span className="voice-library-dropzone-size">
                                    {(selectedFile.size / 1024 / 1024).toFixed(1)} MB
                                </span>
                            </div>
                        ) : (
                            <div className="voice-library-dropzone-empty">
                                <span className="voice-library-dropzone-icon">📁</span>
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
                        disabled={!selectedFile || !speakerName.trim() || uploading}
                    >
                        {uploading ? (
                            <>🔄 กำลังวิเคราะห์เสียง...</>
                        ) : (
                            <>🎙️ บันทึกตัวอย่างเสียง</>
                        )}
                    </button>
                </div>
            )}

            {/* Sample list */}
            {samples.length === 0 ? (
                <div className="voice-library-empty">
                    <span className="voice-library-empty-icon">🎤</span>
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
                                    <span>⏱️ {formatDuration(sample.duration_seconds)}</span>
                                    <span>·</span>
                                    <span>{new Date(sample.created_at).toLocaleDateString('th-TH')}</span>
                                </div>
                            </div>
                            <div className="voice-library-item-actions">
                                <button
                                    className={`voice-library-play-btn ${playingId === sample._id ? 'playing' : ''}`}
                                    onClick={() => handlePlay(sample._id)}
                                    title={playingId === sample._id ? 'หยุดเล่น' : 'เล่นเสียง'}
                                >
                                    {playingId === sample._id ? '⏹' : '▶️'}
                                </button>
                                <button
                                    className="voice-library-delete-btn"
                                    onClick={() => handleDelete(sample._id)}
                                    title="ลบตัวอย่างเสียง"
                                >
                                    🗑️
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
