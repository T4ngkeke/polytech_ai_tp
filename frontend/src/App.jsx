/**
 * App.jsx — React Router v6 route definitions for Edu-LLM.
 *
 * Route Structure:
 *   /login           → Login page (public)
 *   /register        → Register page (public, student self-signup)
 *   /                → MainLayout (requires auth)
 *     /admin         → Admin Dashboard (admin only)
 *     /teacher       → Teacher Workspace (teacher only)
 *     /chat/:labId?  → Student chat with hierarchical sidebar (student only)
 *     /source, /account → every role
 *
 * Each role has its own workspace: an admin or a teacher who opens another
 * one is sent back to their own (they have no classes or labs there).
 */

import { Routes, Route, Navigate } from 'react-router-dom';

import ProtectedRoute from './components/ProtectedRoute';
import MainLayout from './components/MainLayout';
import Login from './pages/Login';
import Register from './pages/Register';
import Chat from './pages/Chat';
import SourcePdf from './pages/SourcePdf';
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

          <Route element={<ProtectedRoute allowedRoles={['teacher']} />}>
            <Route path="/teacher" element={<Teacher />} />
          </Route>

          <Route element={<ProtectedRoute allowedRoles={['student']} />}>
            <Route path="/chat" element={<Chat />} />
            <Route path="/chat/:labId" element={<Chat />} />
          </Route>

          {/* Every role: a cited page, the account settings */}
          <Route element={<ProtectedRoute allowedRoles={['admin', 'teacher', 'student']} />}>
            <Route path="/source/:documentId" element={<SourcePdf />} />
            <Route path="/account" element={<Account />} />
          </Route>
        </Route>
      </Route>

      {/* ── Fallback ── */}
      <Route path="*" element={<Navigate to="/login" replace />} />
    </Routes>
  );
}
