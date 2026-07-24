import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';
import ConsentModal from './ConsentModal';

/**
 * @param {object} props
 * @param {React.ReactNode} props.children
 * @param {string} [props.requiredRole] - "admin" means admin or superadmin
 */
const ProtectedRoute = ({ children, requiredRole }) => {
    const {
        token,
        isAuthenticated,
        isLoading,
        userRole,
        profileChecked,
        consentChecked,
        needsConsent,
        authCheckError,
        retryAuthChecks,
        logout,
        markConsented,
    } = useAuth();
    const location = useLocation();

    if (isLoading || (isAuthenticated && !authCheckError && (!profileChecked || !consentChecked))) {
        return (
            <div className="auth-gate" role="status" aria-live="polite">
                <div className="history-spinner" />
                <div>กำลังตรวจสอบสิทธิ์และความยินยอม...</div>
            </div>
        );
    }

    if (!isAuthenticated) {
        return <Navigate to="/login" state={{ from: location }} replace />;
    }

    if (authCheckError) {
        return (
            <div className="auth-gate" role="alert">
                <h2>ไม่สามารถยืนยันสิทธิ์การใช้งานได้</h2>
                <p>{authCheckError}</p>
                <div className="auth-gate-actions">
                    <button className="btn btn-primary" onClick={retryAuthChecks}>ลองใหม่</button>
                    <button className="btn btn-secondary" onClick={logout}>ออกจากระบบ</button>
                </div>
            </div>
        );
    }

    // Role check: "admin" allows both admin and superadmin
    if (requiredRole === 'admin' && userRole !== 'admin' && userRole !== 'superadmin') {
        return <Navigate to="/" replace />;
    }

    if (needsConsent) {
        return <ConsentModal token={token} onConsented={markConsented} />;
    }

    return children;
};

export default ProtectedRoute;
