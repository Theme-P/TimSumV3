import React, { createContext, useState, useContext, useEffect, useCallback } from 'react';

const AuthContext = createContext(null);

/**
 * Check if a JWT token is expired or about to expire (within 5 minutes).
 */
function isTokenExpired(token) {
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        // Expired if less than 5 minutes remaining
        return payload.exp * 1000 < Date.now() + 5 * 60 * 1000;
    } catch {
        return true;
    }
}

export const AuthProvider = ({ children }) => {
    const [token, setToken] = useState(null);
    const [isLoading, setIsLoading] = useState(true);

    const logout = useCallback(() => {
        setToken(null);
        localStorage.removeItem('timsum_token');
    }, []);

    useEffect(() => {
        // Check for saved token on initial load
        const savedToken = localStorage.getItem('timsum_token');
        if (savedToken && !isTokenExpired(savedToken)) {
            setToken(savedToken);
        } else if (savedToken) {
            // Token expired — clean up
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
            if (expiresIn <= 0) {
                logout();
                return;
            }
            const timer = setTimeout(logout, expiresIn);
            return () => clearTimeout(timer);
        } catch {
            logout();
        }
    }, [token, logout]);

    const login = (newToken) => {
        setToken(newToken);
        localStorage.setItem('timsum_token', newToken);
    };

    // Extract role from JWT payload
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
            isLoading
        }}>
            {children}
        </AuthContext.Provider>
    );
};

export const useAuth = () => useContext(AuthContext);
