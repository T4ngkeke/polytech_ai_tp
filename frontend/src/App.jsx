/**
 * App.jsx — React Router v6 route definitions for Edu-LLM v3.
 *
 * Route Structure:
 *   /login           → Login page (public)
 *   /                → MainLayout (requires auth)
 *     /admin/users   → Admin (admin only)
 *     /teacher/*     → Teacher (teacher + admin)
 *     /chat          → Chat (all authenticated)
 */

import { Routes, Route, Navigate } from 'react-router-dom';

import ProtectedRoute from './components/ProtectedRoute';
import MainLayout from './components/MainLayout';
import Login from './pages/Login';
import Chat from './pages/Chat';
import Teacher from './pages/Teacher';
import Admin from './pages/Admin';

export default function App() {
  return (
    <Routes>
      {/* ── Public ── */}
      <Route path="/login" element={<Login />} />

      {/* ── Authenticated routes ── */}
      <Route element={<ProtectedRoute />}>
        <Route element={<MainLayout />}>
          {/* Admin only */}
          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route path="/admin/users" element={<Admin />} />
          </Route>

          {/* Teacher + Admin */}
          <Route element={<ProtectedRoute allowedRoles={['admin', 'teacher']} />}>
            <Route path="/teacher/students" element={<Teacher />} />
          </Route>

          {/* All authenticated users */}
          <Route element={<ProtectedRoute allowedRoles={['admin', 'teacher', 'student']} />}>
            <Route path="/chat" element={<Chat />} />
          </Route>
        </Route>
      </Route>

      {/* ── Fallback ── */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
