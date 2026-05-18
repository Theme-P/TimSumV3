import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import ProtectedRoute from './components/ProtectedRoute';
import Login from './pages/Login';
import MainApp from './pages/MainApp';

function App() {
    return (
        <ThemeProvider>
            <AuthProvider>
            <Router>
                <Routes>
                    <Route path="/login" element={<Login />} />
                    
                    {/* Protected routes */}
                    <Route 
                        path="/" 
                        element={
                            <ProtectedRoute>
                                <MainApp />
                            </ProtectedRoute>
                        } 
                    />
                    
                    {/* Catch all redirect to root */}
                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
            </Router>
            </AuthProvider>
        </ThemeProvider>
    );
}

export default App;
