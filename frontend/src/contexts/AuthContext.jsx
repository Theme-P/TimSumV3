import { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'

const AuthContext = createContext(null)
const API_BASE = '/api'

function decodeToken(token) {
    try {
        return JSON.parse(atob(token.split('.')[1]))
    } catch {
        return null
    }
}

function isTokenExpired(token) {
    const payload = decodeToken(token)
    return !payload?.exp || payload.exp * 1000 < Date.now() + 5 * 60 * 1000
}

async function readJson(response) {
    return response.json().catch(() => ({}))
}

export const AuthProvider = ({ children }) => {
    const [token, setToken] = useState(null)
    const [isLoading, setIsLoading] = useState(true)
    const [user, setUser] = useState(null)
    const [profileChecked, setProfileChecked] = useState(false)
    const [consentChecked, setConsentChecked] = useState(false)
    const [needsConsent, setNeedsConsent] = useState(false)
    const [authCheckError, setAuthCheckError] = useState(null)
    const [authCheckAttempt, setAuthCheckAttempt] = useState(0)

    const logout = useCallback(() => {
        setToken(null)
        setUser(null)
        setProfileChecked(false)
        setConsentChecked(false)
        setNeedsConsent(false)
        setAuthCheckError(null)
        localStorage.removeItem('timsum_token')
    }, [])

    useEffect(() => {
        const savedToken = localStorage.getItem('timsum_token')
        if (savedToken && !isTokenExpired(savedToken)) {
            setToken(savedToken)
        } else if (savedToken) {
            localStorage.removeItem('timsum_token')
        }
        setIsLoading(false)
    }, [])

    useEffect(() => {
        if (!token) return undefined
        const payload = decodeToken(token)
        const expiresIn = (payload?.exp || 0) * 1000 - Date.now()
        if (expiresIn <= 0) {
            logout()
            return undefined
        }
        const timer = setTimeout(logout, expiresIn)
        return () => clearTimeout(timer)
    }, [token, logout])

    useEffect(() => {
        if (!token) {
            setUser(null)
            setProfileChecked(false)
            setConsentChecked(false)
            setNeedsConsent(false)
            setAuthCheckError(null)
            return undefined
        }

        const controller = new AbortController()
        setProfileChecked(false)
        setConsentChecked(false)
        setAuthCheckError(null)

        const loadSecurityContext = async () => {
            try {
                const headers = { Authorization: `Bearer ${token}` }
                const [profileResponse, consentResponse] = await Promise.all([
                    fetch(`${API_BASE}/user/profile`, { headers, signal: controller.signal }),
                    fetch(`${API_BASE}/consent`, { headers, signal: controller.signal }),
                ])

                if (profileResponse.status === 401 || consentResponse.status === 401) {
                    logout()
                    return
                }

                if (!profileResponse.ok || !consentResponse.ok) {
                    throw new Error('ไม่สามารถตรวจสอบโปรไฟล์และความยินยอมได้')
                }

                const [profileData, consentData] = await Promise.all([
                    readJson(profileResponse),
                    readJson(consentResponse),
                ])

                if (!profileData?.id || !profileData?.role) {
                    throw new Error('ข้อมูลโปรไฟล์จากเซิร์ฟเวอร์ไม่สมบูรณ์')
                }

                setUser(profileData)
                setNeedsConsent(!consentData.all_required_consented)
                setProfileChecked(true)
                setConsentChecked(true)
            } catch (error) {
                if (error.name === 'AbortError') return
                setUser(null)
                setProfileChecked(false)
                setConsentChecked(false)
                setNeedsConsent(false)
                setAuthCheckError(error.message || 'ไม่สามารถตรวจสอบสิทธิ์การใช้งานได้')
            }
        }

        loadSecurityContext()
        return () => controller.abort()
    }, [token, authCheckAttempt, logout])

    const login = useCallback((newToken) => {
        setUser(null)
        setProfileChecked(false)
        setConsentChecked(false)
        setNeedsConsent(false)
        setAuthCheckError(null)
        setToken(newToken)
        localStorage.setItem('timsum_token', newToken)
    }, [])

    const retryAuthChecks = useCallback(() => {
        setAuthCheckError(null)
        setAuthCheckAttempt((attempt) => attempt + 1)
    }, [])

    const refreshProfile = useCallback(async () => {
        if (!token) return null

        const response = await fetch(`${API_BASE}/user/profile`, {
            headers: { Authorization: `Bearer ${token}` },
        })
        if (response.status === 401) {
            logout()
            return null
        }
        if (!response.ok) {
            throw new Error('ไม่สามารถโหลดข้อมูลโปรไฟล์ล่าสุดได้')
        }

        const profileData = await readJson(response)
        if (!profileData?.id || !profileData?.role) {
            throw new Error('ข้อมูลโปรไฟล์จากเซิร์ฟเวอร์ไม่สมบูรณ์')
        }
        setUser(profileData)
        setProfileChecked(true)
        return profileData
    }, [token, logout])

    const markConsented = useCallback(() => {
        setNeedsConsent(false)
        setConsentChecked(true)
        setAuthCheckError(null)
    }, [])

    const value = useMemo(() => ({
        token,
        user,
        isAuthenticated: !!token,
        userRole: user?.role || 'user',
        login,
        logout,
        isLoading,
        profileChecked,
        consentChecked,
        needsConsent,
        authCheckError,
        retryAuthChecks,
        refreshProfile,
        markConsented,
    }), [
        token,
        user,
        login,
        logout,
        isLoading,
        profileChecked,
        consentChecked,
        needsConsent,
        authCheckError,
        retryAuthChecks,
        refreshProfile,
        markConsented,
    ])

    return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export const useAuth = () => useContext(AuthContext)
