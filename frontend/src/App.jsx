import React from 'react';
import { BrowserRouter as Router, Routes, Route, Navigate } from 'react-router-dom';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { ThemeProvider } from './contexts/ThemeContext';
import ProtectedRoute from './components/ProtectedRoute';
import ConsentModal from './components/ConsentModal';
import Login from './pages/Login';
import Register from './pages/Register';
import ForgotPassword from './pages/ForgotPassword';
import ResetPassword from './pages/ResetPassword';
import PrivacyPolicy from './pages/PrivacyPolicy';
import TermsOfService from './pages/TermsOfService';
import MainApp from './pages/MainApp';
import AdminDashboard from './pages/AdminDashboard';
import AdminMonitoring from './pages/AdminMonitoring';

function ConsentGate({ children }) {
    const { token, needsConsent, consentChecked, markConsented } = useAuth();
    if (token && consentChecked && needsConsent) {
        return <ConsentModal token={token} onConsented={markConsented} />;
    }
    return children;
}

function App() {
    return (
        <ThemeProvider>
            <AuthProvider>
            <Router>
                <ConsentGate>
                <Routes>
                    <Route path="/login" element={<Login />} />
                    <Route path="/register" element={<Register />} />
                    <Route path="/forgot-password" element={<ForgotPassword />} />
                    <Route path="/reset-password" element={<ResetPassword />} />
                    <Route path="/privacy-policy" element={<PrivacyPolicy />} />
                    <Route path="/terms" element={<TermsOfService />} />

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

                    {/* Admin Monitoring route */}
                    <Route
                        path="/admin/monitoring"
                        element={
                            <ProtectedRoute requiredRole="admin">
                                <AdminMonitoring />
                            </ProtectedRoute>
                        }
                    />

                    <Route path="*" element={<Navigate to="/" replace />} />
                </Routes>
                </ConsentGate>
            </Router>
            </AuthProvider>
        </ThemeProvider>
    );
}

export default App;
