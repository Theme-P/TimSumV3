import { useState, useEffect } from 'react'

const API_BASE = '/api'

const TIER_STYLES = {
    0: { label: 'Basic', color: '#6b5b4a', border: '#9a8b78' },
    1: { label: 'Pro', color: '#b8860b', border: '#d4a017' },
    2: { label: 'Enterprise', color: '#2874a6', border: '#3498db' },
    10: { label: 'Admin', color: '#7f8c8d', border: '#95a5a6' },
    99: { label: 'Super Admin', color: '#8e44ad', border: '#9b59b6' },
}

function PackageBadge({ token }) {
    const [pkg, setPkg] = useState(null)

    useEffect(() => {
        if (!token) return
        fetch(`${API_BASE}/user/package`, {
            headers: { 'Authorization': `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => {
                if (data.success && data.package?.package) {
                    setPkg(data.package.package)
                }
            })
            .catch(() => {})
    }, [token])

    if (!pkg) return null

    const tier = pkg.tier ?? 0
    const style = TIER_STYLES[tier] || TIER_STYLES[0]

    return (
        <button
            className="nav-pro-badge"
            style={{
                borderColor: style.border,
                color: style.color,
            }}
            title={pkg.description || pkg.name}
        >
            {pkg.name || `TimSum ${style.label}`}
        </button>
    )
}

export default PackageBadge
