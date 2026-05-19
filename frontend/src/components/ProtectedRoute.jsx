import React from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

/**
 * @param {object} props
 * @param {React.ReactNode} props.children
 * @param {string} [props.requiredRole] - "admin" means admin or superadmin
 */
const ProtectedRoute = ({ children, requiredRole }) => {
    const { isAuthenticated, isLoading, userRole } = useAuth();
    const location = useLocation();

    if (isLoading) {
        return (
            <div className="app-container" style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', minHeight: '100vh' }}>
                <div style={{ color: 'white', fontSize: '1.2rem' }}>กำลังโหลด...</div>
            </div>
        );
    }

    if (!isAuthenticated) {
        return <Navigate to="/login" state={{ from: location }} replace />;
    }

    // Role check: "admin" allows both admin and superadmin
    if (requiredRole === 'admin' && userRole !== 'admin' && userRole !== 'superadmin') {
        return <Navigate to="/" replace />;
    }

    return children;
};

export default ProtectedRoute;
