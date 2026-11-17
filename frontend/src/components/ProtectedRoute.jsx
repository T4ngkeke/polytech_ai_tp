/**
 * ProtectedRoute.jsx — Role-gated route wrapper for Edu-LLM.
 */

import { Navigate, Outlet } from 'react-router-dom';
import useAuthStore, { DEFAULT_ROUTES } from '../store/authStore';
import ChoosePassword from '../pages/ChoosePassword';

export default function ProtectedRoute({ allowedRoles }) {
  const token = useAuthStore((s) => s.token);
  const userRole = useAuthStore((s) => s.userRole);
  const isHydrated = useAuthStore((s) => s.isHydrated);
  const mustChangePassword = useAuthStore((s) => s.mustChangePassword);

  // Still loading auth state — show nothing (avoids flash)
  if (!isHydrated) {
    return (
      <div className="flex items-center justify-center h-screen bg-ink-deep">
        <div className="w-8 h-8 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
      </div>
    );
  }

  // Not authenticated → login
  if (!token) {
    return <Navigate to="/login" replace />;
  }

  // An admin reset the password: nothing else until a new one is chosen
  // (the server answers 403 password_change_required meanwhile).
  if (mustChangePassword) {
    return <ChoosePassword />;
  }

  // Authenticated but wrong role → redirect to their default route
  if (allowedRoles && !allowedRoles.includes(userRole)) {
    const fallback = DEFAULT_ROUTES[userRole] || '/account';
    return <Navigate to={fallback} replace />;
  }

  return <Outlet />;
}
