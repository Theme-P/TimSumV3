import React, { createContext, useState, useContext, useEffect, useCallback } from 'react'

const ThemeContext = createContext(null)

const STORAGE_KEY = 'timsum_theme'
const VALID_THEMES = ['light', 'dark', 'system']

function resolveTheme(pref) {
    if (pref === 'system') {
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
    }
    return pref
}

function applyTheme(pref) {
    document.documentElement.setAttribute('data-theme', resolveTheme(pref))
}

export const ThemeProvider = ({ children }) => {
    const [theme, setThemeState] = useState(() => {
        const saved = localStorage.getItem(STORAGE_KEY)
        return VALID_THEMES.includes(saved) ? saved : 'system'
    })

    useEffect(() => {
        applyTheme(theme)
        if (theme !== 'system') return

        const mq = window.matchMedia('(prefers-color-scheme: dark)')
        const handler = () => applyTheme('system')
        mq.addEventListener('change', handler)
        return () => mq.removeEventListener('change', handler)
    }, [theme])

    const setTheme = useCallback((next) => {
        if (!VALID_THEMES.includes(next)) return
        localStorage.setItem(STORAGE_KEY, next)
        setThemeState(next)
    }, [])

    return (
        <ThemeContext.Provider value={{ theme, setTheme }}>
            {children}
        </ThemeContext.Provider>
    )
}

export const useTheme = () => useContext(ThemeContext)
