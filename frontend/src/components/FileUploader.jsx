import { useState, useRef } from 'react'
import Icon from './ui/Icon'

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
                <Icon name="file-audio" />
                <span className="file-selected-name">
                    {file.name} ({formatFileSize(file.size)})
                </span>
                <button
                    className="file-remove-btn"
                    onClick={handleRemove}
                    disabled={disabled}
                    aria-label="นำไฟล์ออก"
                >
                    <Icon name="x-circle" />
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
                <Icon name="upload" size={40} />
            </div>
            <p className="file-upload-text">
                ลากไฟล์มาวางที่นี่ หรือคลิกเพื่อเลือกไฟล์
            </p>
            <p className="file-upload-hint">MP3 · MP4 · M4A · WAV</p>
        </div>
    )
}

export default FileUploader
