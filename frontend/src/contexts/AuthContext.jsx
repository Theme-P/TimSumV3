import React, { createContext, useState, useContext, useEffect, useCallback } from 'react';

const AuthContext = createContext(null);
const API_BASE = import.meta.env.VITE_API_URL || 'http://localhost:8000';

function isTokenExpired(token) {
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        return payload.exp * 1000 < Date.now() + 5 * 60 * 1000;
    } catch {
        return true;
    }
}

export const AuthProvider = ({ children }) => {
    const [token, setToken] = useState(null);
    const [isLoading, setIsLoading] = useState(true);
    const [consentChecked, setConsentChecked] = useState(false);
    const [needsConsent, setNeedsConsent] = useState(false);

    const logout = useCallback(() => {
        setToken(null);
        setConsentChecked(false);
        setNeedsConsent(false);
        localStorage.removeItem('timsum_token');
    }, []);

    useEffect(() => {
        const savedToken = localStorage.getItem('timsum_token');
        if (savedToken && !isTokenExpired(savedToken)) {
            setToken(savedToken);
        } else if (savedToken) {
            localStorage.removeItem('timsum_token');
        }
        setIsLoading(false);
    }, []);

    // Auto-logout when token expires
    useEffect(() => {
        if (!token) return;
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            const expiresIn = payload.exp * 1000 - Date.now();
            if (expiresIn <= 0) { logout(); return; }
            const timer = setTimeout(logout, expiresIn);
            return () => clearTimeout(timer);
        } catch {
            logout();
        }
    }, [token, logout]);

    // Check consent status whenever token changes
    useEffect(() => {
        if (!token) { setConsentChecked(false); setNeedsConsent(false); return; }
        fetch(`${API_BASE}/api/consent`, {
            headers: { Authorization: `Bearer ${token}` },
        })
            .then(r => r.json())
            .then(data => {
                setNeedsConsent(!data.all_required_consented);
                setConsentChecked(true);
            })
            .catch(() => {
                // If consent check fails, don't block the user
                setNeedsConsent(false);
                setConsentChecked(true);
            });
    }, [token]);

    const login = (newToken) => {
        setToken(newToken);
        setConsentChecked(false);
        localStorage.setItem('timsum_token', newToken);
    };

    const markConsented = () => setNeedsConsent(false);

    let userRole = 'user';
    if (token) {
        try {
            const payload = JSON.parse(atob(token.split('.')[1]));
            userRole = payload.role || 'user';
        } catch { /* ignore */ }
    }

    return (
        <AuthContext.Provider value={{
            token,
            isAuthenticated: !!token,
            userRole,
            login,
            logout,
            isLoading,
            needsConsent,
            consentChecked,
            markConsented,
        }}>
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => useContext(AuthContext);
