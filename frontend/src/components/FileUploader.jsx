import { useState, useRef } from 'react'

function FileUploader({ file, onFileSelect, disabled }) {
    const [isDragOver, setIsDragOver] = useState(false)
    const inputRef = useRef(null)

    const allowedTypes = [
        'audio/mpeg',
        'audio/wav',
        'audio/mp4',
        'audio/x-m4a',
        'audio/flac',
        'audio/ogg',
        'audio/webm',
        'video/mp4',
        'video/webm',
    ]

    const allowedExtensions = '.mp3,.wav,.m4a,.flac,.ogg,.webm,.mp4'

    const handleDragOver = (e) => {
        e.preventDefault()
        if (!disabled) setIsDragOver(true)
    }

    const handleDragLeave = (e) => {
        e.preventDefault()
        setIsDragOver(false)
    }

    const handleDrop = (e) => {
        e.preventDefault()
        setIsDragOver(false)

        if (disabled) return

        const droppedFile = e.dataTransfer.files[0]
        if (droppedFile && validateFile(droppedFile)) {
            onFileSelect(droppedFile)
        }
    }

    const handleFileChange = (e) => {
        const selectedFile = e.target.files[0]
        if (selectedFile && validateFile(selectedFile)) {
            onFileSelect(selectedFile)
        }
    }

    const validateFile = (file) => {
        const ext = '.' + file.name.split('.').pop().toLowerCase()
        const validExt = allowedExtensions.split(',').includes(ext)
        const validType = allowedTypes.includes(file.type) || file.type === ''
        return validExt || validType
    }

    const handleClick = () => {
        if (!disabled) inputRef.current?.click()
    }

    const handleRemove = (e) => {
        e.stopPropagation()
        onFileSelect(null)
        if (inputRef.current) inputRef.current.value = ''
    }

    const formatFileSize = (bytes) => {
        if (bytes < 1024) return bytes + ' B'
        if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
        return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
    }

    if (file) {
        return (
            <div className="file-selected">
                <span>🎵</span>
                <span className="file-selected-name">
                    {file.name} ({formatFileSize(file.size)})
                </span>
                <button
                    className="file-remove-btn"
                    onClick={handleRemove}
                    disabled={disabled}
                >
                    ✕
                </button>
            </div>
        )
    }

    return (
        <div
            className={`file-upload ${isDragOver ? 'drag-over' : ''}`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={handleClick}
        >
            <input
                ref={inputRef}
                type="file"
                accept={allowedExtensions}
                onChange={handleFileChange}
                style={{ display: 'none' }}
                disabled={disabled}
            />
            <div className="file-upload-icon">
                <svg width="40" height="40" viewBox="0 0 24 24" fill="none" xmlns="http://www.w3.org/2000/svg">
                    <rect x="9" y="2" width="6" height="11" rx="3" fill="#9ca3af"/>
                    <path d="M5 11a7 7 0 0 0 14 0" stroke="#9ca3af" strokeWidth="1.8" strokeLinecap="round"/>
                    <line x1="12" y1="18" x2="12" y2="22" stroke="#9ca3af" strokeWidth="1.8" strokeLinecap="round"/>
                    <line x1="8" y1="22" x2="16" y2="22" stroke="#9ca3af" strokeWidth="1.8" strokeLinecap="round"/>
                </svg>
            </div>
            <p className="file-upload-text">
                ลากไฟล์มาวางที่นี่ หรือคลิกเพื่อเลือกไฟล์
            </p>
            <p className="file-upload-hint">MP3 · MP4 · M4A · WAV</p>
        </div>
    )
}

export default FileUploader
