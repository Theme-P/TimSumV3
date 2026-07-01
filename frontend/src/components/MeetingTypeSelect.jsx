import { useState, useEffect } from 'react'
import Icon from './ui/Icon'

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
                <span className="icon-label"><Icon name="folder" /> ประเภทการประชุม</span>
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
                <Icon name="chevron-down" className="select-arrow" />
            </div>

            {/* Show selected meeting type details */}
            {selectedType && selectedType.id > 0 && (
                <div className="meeting-type-detail">
                    <span className="meeting-type-structure">
                        <span className="icon-label"><Icon name="pin" /> โครงสร้าง: {selectedType.structure}</span>
                    </span>
                    {selectedType.key_focus && (
                        <span className="meeting-type-focus">
                            <span className="icon-label"><Icon name="target" /> เน้น: {selectedType.key_focus}</span>
                        </span>
                    )}
                </div>
            )}
            {selectedType && selectedType.id === 0 && (
                <div className="meeting-type-detail auto">
                    <span className="meeting-type-structure">
                        <span className="icon-label"><Icon name="bot" /> AI จะวิเคราะห์เนื้อหาและเลือกประเภทให้อัตโนมัติ</span>
                    </span>
                </div>
            )}
        </div>
    )
}

export default MeetingTypeSelect
