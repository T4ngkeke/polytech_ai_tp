import { render, screen, waitFor } from '@testing-library/react';
import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import ProtectedRoute from '../ProtectedRoute';

// Mock jwt-decode
vi.mock('jwt-decode', () => ({
  jwtDecode: vi.fn(() => ({ sub: '123' }))
}));

// Mock fetch
global.fetch = vi.fn();

const renderRoute = (requiredRole) => {
  return render(
    <BrowserRouter>
      <Routes>
        <Route path="/login" element={<div>Login Page</div>} />
        <Route
          path="/protected"
          element={
            <ProtectedRoute requiredRole={requiredRole}>
              <div>Protected Content</div>
            </ProtectedRoute>
          }
        />
      </Routes>
    </BrowserRouter>
  );
};

describe('ProtectedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Simulate navigating to protected
    window.history.pushState({}, 'Test page', '/protected');
  });

  it('redirects to /login when no token present', async () => {
    renderRoute();
    await waitFor(() => {
      expect(screen.getByText('Login Page')).toBeInTheDocument();
      expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
    });
  });

  it('renders children when token is valid and no specific role is required', async () => {
    localStorage.setItem('token', 'fake.jwt.token');
    renderRoute();
    await waitFor(() => {
      expect(screen.getByText('Protected Content')).toBeInTheDocument();
    });
  });

  it('redirects to /login if role does not match requiredRole', async () => {
    // According to instructions, role is stored or fetched. Let's assume stored role here for simplicity.
    localStorage.setItem('token', 'fake.jwt.token');
    localStorage.setItem('role', 'student');
    
    renderRoute('admin');
    await waitFor(() => {
      expect(screen.getByText('Login Page')).toBeInTheDocument();
      expect(screen.queryByText('Protected Content')).not.toBeInTheDocument();
    });
  });
  
  it('renders children if role matches requiredRole', async () => {
    localStorage.setItem('token', 'fake.jwt.token');
    localStorage.setItem('role', 'teacher');
    
    renderRoute('teacher');
    await waitFor(() => {
      expect(screen.getByText('Protected Content')).toBeInTheDocument();
    });
  });
});
