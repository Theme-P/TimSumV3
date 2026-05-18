const STEPS = [
    { id: 0, label: 'โหลดโมเดล', icon: '🧠' },
    { id: 1, label: 'โหลดเสียง', icon: '🎵' },
    { id: 2, label: 'ถอดเสียง', icon: '✍️' },
    { id: 3, label: 'แยกผู้พูด', icon: '👥' },
    { id: 4, label: 'สรุป AI', icon: '📝' },
]

function ProcessingStatus({ currentStep, progress }) {
    return (
        <div className="processing-status">
            <h3 className="processing-title">
                <span className="spinner">⏳</span>
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
                                {isCompleted ? '✓' : isActive ? '●' : '○'}
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
