import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import App from '../App';

// Mock the components so we only test routing
vi.mock('../pages/Login', () => ({ default: () => <div data-testid="login-page">Login</div> }));
vi.mock('../pages/Chat', () => ({ default: () => <div data-testid="chat-page">Chat</div> }));
vi.mock('../pages/Teacher', () => ({ default: () => <div data-testid="teacher-page">Teacher</div> }));
vi.mock('../pages/Admin', () => ({ default: () => <div data-testid="admin-page">Admin</div> }));

// Mock ProtectedRoute to simulate token/role checks
vi.mock('../components/ProtectedRoute', () => {
  return {
    default: ({ children, requiredRole }) => {
      const token = localStorage.getItem('token');
      const role = localStorage.getItem('role');
      if (!token) return <div data-testid="redirected">Redirected to Login</div>;
      if (requiredRole === 'admin' && role !== 'admin') return <div data-testid="redirected">Redirected to Login</div>;
      if (requiredRole === 'teacher' && role !== 'teacher' && role !== 'admin') return <div data-testid="redirected">Redirected to Login</div>;
      return children;
    }
  };
});

describe('App Routing', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  const renderWithPath = (path) => {
    window.history.pushState({}, 'Test page', path);
    return render(<App />);
  };

  it('renders login page on /login', () => {
    renderWithPath('/login');
    expect(screen.getByTestId('login-page')).toBeInTheDocument();
  });

  it('redirects to /chat on /', async () => {
    // If not logged in, it will first go to /chat then ProtectedRoute redirects to login
    // Let's set token to see it actually hits chat
    localStorage.setItem('token', 'fake.token');
    renderWithPath('/');
    await waitFor(() => {
      expect(screen.getByTestId('chat-page')).toBeInTheDocument();
    });
  });

  it('protects /chat route', async () => {
    renderWithPath('/chat');
    // Without token, ProtectedRoute renders our mock redirect
    await waitFor(() => {
      expect(screen.getByTestId('redirected')).toBeInTheDocument();
    });
  });

  it('allows access to /teacher if role is teacher', async () => {
    localStorage.setItem('token', 'fake.token');
    localStorage.setItem('role', 'teacher');
    renderWithPath('/teacher');
    await waitFor(() => {
      expect(screen.getByTestId('teacher-page')).toBeInTheDocument();
    });
  });

  it('protects /admin route from teachers', async () => {
    localStorage.setItem('token', 'fake.token');
    localStorage.setItem('role', 'teacher');
    renderWithPath('/admin');
    await waitFor(() => {
      expect(screen.getByTestId('redirected')).toBeInTheDocument();
    });
  });

  it('allows access to /admin if role is admin', async () => {
    localStorage.setItem('token', 'fake.token');
    localStorage.setItem('role', 'admin');
    renderWithPath('/admin');
    await waitFor(() => {
      expect(screen.getByTestId('admin-page')).toBeInTheDocument();
    });
  });
});
