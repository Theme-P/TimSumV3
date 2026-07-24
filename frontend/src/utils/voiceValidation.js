export const MAX_VOICE_FILE_BYTES = 10 * 1024 * 1024
export const MIN_VOICE_DURATION_SECONDS = 5
export const MAX_VOICE_DURATION_SECONDS = 30

const SUPPORTED_EXTENSIONS = new Set([
    'mp3', 'wav', 'm4a', 'mp4', 'aac', 'ogg', 'webm', 'flac',
])

export function getVoiceFileValidationError(file, durationSeconds = null) {
    if (!file) return 'กรุณาเลือกไฟล์เสียง'

    const extension = file.name?.split('.').pop()?.toLowerCase() || ''
    const hasSupportedType = file.type?.startsWith('audio/') || file.type === 'video/mp4'
    if (!hasSupportedType && !SUPPORTED_EXTENSIONS.has(extension)) {
        return 'รองรับเฉพาะไฟล์เสียง MP3, WAV, M4A, AAC, OGG, WebM และ FLAC'
    }

    if (file.size > MAX_VOICE_FILE_BYTES) {
        return 'ไฟล์เสียงต้องมีขนาดไม่เกิน 10 MB'
    }

    if (durationSeconds !== null) {
        if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) {
            return 'ไม่สามารถอ่านความยาวไฟล์เสียงได้'
        }
        if (durationSeconds < MIN_VOICE_DURATION_SECONDS || durationSeconds > MAX_VOICE_DURATION_SECONDS) {
            return 'ไฟล์เสียงต้องมีความยาวระหว่าง 5–30 วินาที'
        }
    }

    return null
}

export function readAudioDuration(file, timeoutMs = 10000) {
    return new Promise((resolve, reject) => {
        const objectUrl = URL.createObjectURL(file)
        const audio = document.createElement('audio')
        let settled = false

        const finish = (callback, value) => {
            if (settled) return
            settled = true
            clearTimeout(timeout)
            audio.removeAttribute('src')
            audio.load()
            URL.revokeObjectURL(objectUrl)
            callback(value)
        }

        const timeout = setTimeout(() => {
            finish(reject, new Error('หมดเวลาตรวจสอบไฟล์เสียง'))
        }, timeoutMs)

        audio.preload = 'metadata'
        audio.onloadedmetadata = () => finish(resolve, audio.duration)
        audio.onerror = () => finish(reject, new Error('ไม่สามารถอ่านไฟล์เสียงได้'))
        audio.src = objectUrl
    })
}
