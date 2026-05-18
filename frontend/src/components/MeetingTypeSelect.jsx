import { useState, useEffect } from 'react'

const API_BASE = '/api'

function MeetingTypeSelect({ value, onChange, disabled }) {
    const [meetingTypes, setMeetingTypes] = useState([])
    const [loading, setLoading] = useState(true)

    useEffect(() => {
        fetch(`${API_BASE}/meeting-types`)
            .then(res => res.json())
            .then(data => {
                if (data.success && data.meeting_types) {
                    setMeetingTypes(data.meeting_types)
                }
            })
            .catch(() => {
                setMeetingTypes([
                    { id: 0, name: 'Auto-Detect', thai: 'ตรวจจับอัตโนมัติ', structure: 'วิเคราะห์จากเนื้อหา', key_focus: '' }
                ])
            })
            .finally(() => setLoading(false))
    }, [])

    const selectedType = meetingTypes.find(t => t.id === value)

    return (
        <div className="form-group">
            <label className="form-label">
                📁 ประเภทการประชุม
            </label>
            <div className="select-wrapper">
                <select
                    className="select-dropdown"
                    value={value}
                    onChange={(e) => onChange(Number(e.target.value))}
                    disabled={disabled || loading}
                >
                    {loading ? (
                        <option>กำลังโหลด...</option>
                    ) : (
                        meetingTypes.map((type) => (
                            <option key={type.id} value={type.id}>
                                {type.id === 0
                                    ? `ตรวจจับอัตโนมัติ`
                                    : `${type.thai} — ${type.name}`
                                }
                            </option>
                        ))
                    )}
                </select>
                <span className="select-arrow">▼</span>
            </div>

            {/* Show selected meeting type details */}
            {selectedType && selectedType.id > 0 && (
                <div className="meeting-type-detail">
                    <span className="meeting-type-structure">
                        📌 โครงสร้าง: {selectedType.structure}
                    </span>
                    {selectedType.key_focus && (
                        <span className="meeting-type-focus">
                            🎯 เน้น: {selectedType.key_focus}
                        </span>
                    )}
                </div>
            )}
            {selectedType && selectedType.id === 0 && (
                <div className="meeting-type-detail auto">
                    <span className="meeting-type-structure">
                        🤖 AI จะวิเคราะห์เนื้อหาและเลือกประเภทให้อัตโนมัติ
                    </span>
                </div>
            )}
        </div>
    )
}

export default MeetingTypeSelect
