/**
 * App.jsx — React Router v6 route definitions for Edu-LLM v6.
 *
 * Route Structure:
 *   /login           → Login page (public)
 *   /register        → Register page (public, student self-signup)
 *   /                → MainLayout (requires auth)
 *     /admin         → Admin Dashboard (admin only)
 *     /teacher       → Teacher Workspace (teacher + admin)
 *     /chat/:labId?  → Student chat with hierarchical sidebar
 */

import { Routes, Route, Navigate } from 'react-router-dom';

import ProtectedRoute from './components/ProtectedRoute';
import MainLayout from './components/MainLayout';
import Login from './pages/Login';
import Register from './pages/Register';
import Chat from './pages/Chat';
import Teacher from './pages/Teacher';
import Admin from './pages/Admin';
import Account from './pages/Account';

export default function App() {
  return (
    <Routes>
      {/* ── Public ── */}
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />

      {/* ── Authenticated routes ── */}
      <Route element={<ProtectedRoute />}>
        <Route element={<MainLayout />}>
          {/* Admin only */}
          <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
            <Route path="/admin" element={<Admin />} />
          </Route>

          {/* Teacher + Admin */}
          <Route element={<ProtectedRoute allowedRoles={['admin', 'teacher']} />}>
            <Route path="/teacher" element={<Teacher />} />
          </Route>

          {/* All authenticated users — Chat is the unified student workspace */}
          <Route element={<ProtectedRoute allowedRoles={['admin', 'teacher', 'student']} />}>
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:labId" element={<Chat />} />
            <Route path="/account" element={<Account />} />
          </Route>
        </Route>
      </Route>

      {/* ── Fallback ── */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
