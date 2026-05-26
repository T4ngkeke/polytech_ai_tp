/** @type {import('tailwindcss').Config} */
export default {
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        display: ['"Outfit"', 'system-ui', 'sans-serif'],
        body: ['"DM Sans"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', 'monospace'],
      },
      colors: {
        'bg-primary': '#0b1120',
        'bg-secondary': '#111827',
        'bg-surface': '#1a2332',
        'bg-surface-hover': '#1f2b3d',
        'bg-elevated': '#243044',
        'bg-glass': 'rgba(26, 35, 50, 0.75)',

        'border-subtle': 'rgba(148, 163, 184, 0.12)',
        'border-default': 'rgba(148, 163, 184, 0.2)',
        'border-focus': '#06b6d4',

        'text-primary': '#f1f5f9',
        'text-secondary': '#94a3b8',
        'text-muted': '#64748b',
        'text-inverse': '#0b1120',

        'accent': '#06b6d4',
        'accent-hover': '#22d3ee',
        'accent-muted': 'rgba(6, 182, 212, 0.15)',
        'accent-warm': '#f59e0b',
        'accent-warm-muted': 'rgba(245, 158, 11, 0.15)',

        'success': '#10b981',
        'success-muted': 'rgba(16, 185, 129, 0.15)',
        'danger': '#ef4444',
        'danger-hover': '#f87171',
        'danger-muted': 'rgba(239, 68, 68, 0.15)',

        'user-bubble': '#1a2332',
        'llm-bubble': '#0e2a3d',
      },
      animation: {
        'fade-in': 'fade-in 0.35s ease-out both',
        'slide-in-left': 'slide-in-left 0.3s ease-out both',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(8px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-left': {
          from: { opacity: '0', transform: 'translateX(-16px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(6, 182, 212, 0.3)' },
          '50%': { boxShadow: '0 0 16px 4px rgba(6, 182, 212, 0.15)' },
        },
      },
      boxShadow: {
        'glow': '0 0 20px rgba(6, 182, 212, 0.15)',
        'elevated': '0 8px 32px rgba(0, 0, 0, 0.4)',
        'card': '0 2px 12px rgba(0, 0, 0, 0.25)',
      },
    },
  },
  plugins: [],
}
