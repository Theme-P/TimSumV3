import { useState } from 'react'

const MAX_CHARS = 500

function CustomPromptInput({ value, onChange, disabled }) {
    const [expanded, setExpanded] = useState(false)

    return (
        <div className="custom-prompt-section">
            <button
                type="button"
                className="custom-prompt-toggle"
                onClick={() => setExpanded(prev => !prev)}
                disabled={disabled}
            >
                <span className="custom-prompt-toggle-icon">{expanded ? '▾' : '▸'}</span>
                <span className="custom-prompt-toggle-icon-emoji">🤖</span>
                คำสั่งเพิ่มเติมสำหรับ AI (ไม่บังคับ)
            </button>
            {expanded && (
                <div className="custom-prompt-body">
                    <textarea
                        className="custom-prompt-textarea"
                        placeholder="เช่น สรุปเป็น bullet points, เน้น action items, สรุปเป็นภาษาอังกฤษ"
                        value={value}
                        onChange={(e) => {
                            if (e.target.value.length <= MAX_CHARS) {
                                onChange(e.target.value)
                            }
                        }}
                        disabled={disabled}
                        rows={3}
                    />
                    <div className="custom-prompt-footer">
                        <span className="custom-prompt-hint">
                            AI จะใช้คำสั่งนี้เป็นแนวทางเพิ่มเติมในการสรุปประชุม
                        </span>
                        <span className={`custom-prompt-count ${value.length >= MAX_CHARS ? 'at-limit' : ''}`}>
                            {value.length}/{MAX_CHARS}
                        </span>
                    </div>
                </div>
            )}
        </div>
    )
}

export default CustomPromptInput
