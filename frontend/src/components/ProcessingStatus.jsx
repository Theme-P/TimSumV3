import Icon from './ui/Icon'

const STEPS = [
    { id: 0, label: 'โหลดโมเดล' },
    { id: 1, label: 'โหลดเสียง' },
    { id: 2, label: 'ถอดเสียง' },
    { id: 3, label: 'แยกผู้พูด' },
    { id: 4, label: 'สรุป AI' },
]

function ProcessingStatus({ currentStep, progress }) {
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
