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
        body: ['"Inter"', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', '"Fira Code"', 'ui-monospace', 'monospace'],
      },
      colors: {
        // Dossier Noir — Deep charcoal ink foundations
        'ink-deep': '#0c0e12',
        'ink-base': '#13161c',
        'ink-raised': '#1a1d25',
        'ink-surface': '#21252f',
        'ink-hover': '#282d39',
        'ink-elevated': '#303643',

        // Warm cream text hierarchy
        'cream': '#e8e2d6',
        'cream-secondary': '#a8a193',
        'cream-muted': '#706b62',
        'cream-faint': '#4a463f',

        // Borders
        'border-subtle': 'rgba(232, 226, 214, 0.08)',
        'border-default': 'rgba(232, 226, 214, 0.14)',
        'border-strong': 'rgba(232, 226, 214, 0.22)',

        // Cyan accent
        'cyan': '#06b6d4',
        'cyan-hover': '#22d3ee',
        'cyan-muted': 'rgba(6, 182, 212, 0.15)',
        'cyan-faint': 'rgba(6, 182, 212, 0.08)',

        // Burnished gold secondary
        'gold': '#c9a84c',
        'gold-hover': '#d9bc6a',
        'gold-muted': 'rgba(201, 168, 76, 0.15)',

        // Utility colors
        'success': '#3ecf71',
        'success-muted': 'rgba(62, 207, 113, 0.15)',
        'danger': '#ff2d2d',
        'danger-hover': '#ff5252',
        'danger-muted': 'rgba(255, 45, 45, 0.12)',
        'danger-deep': 'rgba(255, 45, 45, 0.06)',

        // Legacy compat aliases (used by some existing classes)
        'accent': '#06b6d4',
        'accent-hover': '#22d3ee',
        'accent-muted': 'rgba(6, 182, 212, 0.15)',
        'accent-warm': '#c9a84c',
        'accent-warm-muted': 'rgba(201, 168, 76, 0.15)',
      },
      animation: {
        'fade-in': 'fade-in 0.4s ease-out both',
        'slide-up': 'slide-up 0.4s ease-out both',
        'slide-down': 'slide-down 0.35s ease-out both',
        'slide-in-left': 'slide-in-left 0.3s ease-out both',
        'scale-in': 'scale-in 0.3s ease-out both',
        'stagger-in': 'stagger-in 0.5s ease-out both',
      },
      keyframes: {
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(6px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-up': {
          from: { opacity: '0', transform: 'translateY(20px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-down': {
          from: { opacity: '0', transform: 'translateY(-12px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'slide-in-left': {
          from: { opacity: '0', transform: 'translateX(-16px)' },
          to: { opacity: '1', transform: 'translateX(0)' },
        },
        'scale-in': {
          from: { opacity: '0', transform: 'scale(0.95)' },
          to: { opacity: '1', transform: 'scale(1)' },
        },
        'stagger-in': {
          from: { opacity: '0', transform: 'translateY(12px) scale(0.98)' },
          to: { opacity: '1', transform: 'translateY(0) scale(1)' },
        },
        'pulse-glow': {
          '0%, 100%': { boxShadow: '0 0 0 0 rgba(6, 182, 212, 0.3)' },
          '50%': { boxShadow: '0 0 16px 4px rgba(6, 182, 212, 0.12)' },
        },
        'spin': {
          to: { transform: 'rotate(360deg)' },
        },
      },
      boxShadow: {
        'glow': '0 0 24px rgba(6, 182, 212, 0.12)',
        'glow-gold': '0 0 24px rgba(201, 168, 76, 0.10)',
        'elevated': '0 8px 40px rgba(0, 0, 0, 0.5)',
        'card': '0 2px 16px rgba(0, 0, 0, 0.3)',
        'inner-glow': 'inset 0 1px 0 rgba(232, 226, 214, 0.04)',
      },
    },
  },
  plugins: [],
}
