import Icon from './ui/Icon'

const STEPS = [
    { id: 0, label: 'โหลดโมเดล' },
    { id: 1, label: 'โหลดเสียง' },
    { id: 2, label: 'ถอดเสียง' },
    { id: 3, label: 'แยกผู้พูด' },
    { id: 4, label: 'ตรวจวาระ' },
    { id: 5, label: 'สรุป AI' },
    { id: 6, label: 'บันทึกผลลัพธ์' },
]

function ProcessingStatus({ currentStep, progress, summaryCoverage }) {
    const showCoverage = currentStep >= 5 && summaryCoverage?.totalSegments > 0

    return (
        <div className="processing-status">
            <h3 className="processing-title">
                <Icon name="refresh" className="ui-icon-spin" />
                กำลังประมวลผล...
            </h3>

            {/* Progress Bar */}
            <div className="progress-bar-container">
                <div
                    className="progress-bar"
                    style={{ width: `${progress}%` }}
                />
            </div>
            <div className="progress-percent">{progress}%</div>

            {showCoverage && (
                <div className="summary-coverage-progress" aria-live="polite">
                    <div className="summary-coverage-header">
                        <span>Coverage Transcript</span>
                        <strong>{summaryCoverage.percentage.toFixed(1)}%</strong>
                    </div>
                    <div className="summary-coverage-track">
                        <div
                            className="summary-coverage-fill"
                            style={{ width: `${Math.min(100, Math.max(0, summaryCoverage.percentage))}%` }}
                        />
                    </div>
                    <small>
                        {summaryCoverage.coveredSegments.toLocaleString()} / {summaryCoverage.totalSegments.toLocaleString()} segments
                    </small>
                </div>
            )}

            {/* Steps */}
            <div className="processing-steps">
                {STEPS.map((step) => {
                    const isCompleted = currentStep > step.id
                    const isActive = currentStep === step.id

                    return (
                        <div
                            key={step.id}
                            className={`step ${isCompleted ? 'completed' : ''} ${isActive ? 'active' : ''}`}
                        >
                            <span className="step-icon">
                                {isCompleted
                                    ? <Icon name="check-circle" />
                                    : isActive
                                        ? <Icon name="refresh" className="ui-icon-spin" />
                                        : <Icon name="clock" />}
                            </span>
                            <span>{step.label}</span>
                        </div>
                    )
                })}
            </div>
        </div>
    )
}

export default ProcessingStatus
