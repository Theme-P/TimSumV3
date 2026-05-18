import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
    plugins: [react()],
    server: {
        host: '0.0.0.0',
        port: 3000,
        // Polling is needed for HMR to detect changes on bind-mounted
        // volumes from Windows/macOS hosts into Linux containers.
        watch: {
            usePolling: true,
            interval: 300,
        },
        proxy: {
            '/api': {
                target: process.env.VITE_API_PROXY || 'http://localhost:8000',
                changeOrigin: true,
            }
        }
    },
    build: {
        outDir: 'dist',
    }
})
