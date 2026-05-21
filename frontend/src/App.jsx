import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import ProtectedRoute from './components/ProtectedRoute';
import Login from './pages/Login';
import Register from './pages/Register';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import MainApp from './pages/MainApp';
import AdminDashboard from './pages/AdminDashboard';

function App() {
    return (
        <ThemeProvider>
            <AuthProvider>
            <Router>
                <Routes>
                    <Route path="/login" element={<Login />} />
                    <Route path="/register" element={<Register />} />
                    <Route path="/forgot-password" element={<ForgotPassword />} />
                    <Route path="/reset-password" element={<ResetPassword />} />

                    {/* Protected routes */}
                    <Route
                        path="/"
                        element={
                            <ProtectedRoute>
                                <MainApp />
                            </ProtectedRoute>
                        }
                    />

                    {/* Admin route */}
                    <Route
                        path="/admin"
                        element={
                            <ProtectedRoute requiredRole="admin">
                                <AdminDashboard />
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
