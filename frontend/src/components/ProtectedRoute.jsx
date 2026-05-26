/**
 * ProtectedRoute.jsx — Role-gated route wrapper for Edu-LLM v3.
 *
 * Usage:
 *   <Route element={<ProtectedRoute allowedRoles={['admin']} />}>
 *     <Route path="users" element={<Admin />} />
 *   </Route>
 */

import { Navigate, Outlet } from 'react-router-dom';
import useAuthStore, { DEFAULT_ROUTES } from '../store/authStore';

export default function ProtectedRoute({ allowedRoles }) {
  const token = useAuthStore((s) => s.token);
  const userRole = useAuthStore((s) => s.userRole);
  const isHydrated = useAuthStore((s) => s.isHydrated);

  // Still loading auth state — show nothing (avoids flash)
  if (!isHydrated) {
    return (
      <div className="flex items-center justify-center h-screen bg-bg-primary">
        <div className="w-8 h-8 rounded-full border-2 border-accent border-t-transparent animate-[spin_0.8s_linear_infinite]" />
      </div>
    );
  }

  // Not authenticated → login
  if (!token) {
    return <Navigate to="/login" replace />;
  }

  // Authenticated but wrong role → redirect to their default route
  if (allowedRoles && !allowedRoles.includes(userRole)) {
    const fallback = DEFAULT_ROUTES[userRole] || '/chat';
    return <Navigate to={fallback} replace />;
  }

  return <Outlet />;
}
