"""
services/__init__.py — Service layer package for Edu-LLM v7.

All business/DB logic lives here. Routers are thin controllers that:
  1. Validate authentication & authorization (ownership checks)
  2. Call a service function
  3. Return the result

No router imports another router. Both Admin and Teacher routers
call these same service functions — Admin skips ownership checks,
Teacher enforces them.
"""
