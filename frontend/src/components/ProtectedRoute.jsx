import React from 'react';
import { Navigate } from 'react-router-dom';
import { jwtDecode } from 'jwt-decode';

const ProtectedRoute = ({ children, requiredRole }) => {
  const token = localStorage.getItem('token');
  
  if (!token) {
    return <Navigate to="/login" replace />;
  }

  try {
    // Decoding the token to ensure it's a valid JWT
    jwtDecode(token);
    
    // As per instruction, check role if required
    if (requiredRole) {
      const role = localStorage.getItem('role');
      if (role !== requiredRole) {
        return <Navigate to="/login" replace />;
      }
    }
    
    return children;
  } catch (error) {
    // If token is invalid
    localStorage.removeItem('token');
    return <Navigate to="/login" replace />;
  }
};

export default ProtectedRoute;
