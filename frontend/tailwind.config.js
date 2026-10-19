const v = (name) => `rgb(var(--${name}) / <alpha-value>)`;

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
      // Colors are CSS variables (RGB channels) set per theme in index.css,
      // so opacity modifiers such as border-cyan/40 keep working.
      colors: {
        // Surfaces, from the page background up to raised menus
        'ink-deep': v('ink-deep'),
        'ink-base': v('ink-base'),
        'ink-raised': v('ink-raised'),
        'ink-surface': v('ink-surface'),
        'ink-hover': v('ink-hover'),
        'ink-elevated': v('ink-elevated'),

        // Text, from strongest to faintest
        'cream': v('cream'),
        'cream-secondary': v('cream-secondary'),
        'cream-muted': v('cream-muted'),
        'cream-faint': v('cream-faint'),
        // Text on solid cyan or danger buttons
        'on-accent': v('on-accent'),

        // Borders
        'border-subtle': 'rgb(var(--line) / var(--line-subtle))',
        'border-default': 'rgb(var(--line) / var(--line-default))',
        'border-strong': 'rgb(var(--line) / var(--line-strong))',

        // Cyan accent
        'cyan': v('cyan'),
        'cyan-hover': v('cyan-hover'),
        'cyan-muted': 'rgb(var(--cyan) / 0.15)',
        'cyan-faint': 'rgb(var(--cyan) / 0.08)',

        // Gold secondary (also used for warnings)
        'gold': v('gold'),
        'gold-hover': v('gold-hover'),
        'gold-muted': 'rgb(var(--gold) / 0.15)',

        // Utility colors
        'success': v('success'),
        'success-muted': 'rgb(var(--success) / 0.15)',
        'danger': v('danger'),
        'danger-hover': v('danger-hover'),
        'danger-muted': 'rgb(var(--danger) / 0.12)',
        'danger-deep': 'rgb(var(--danger) / 0.06)',

        // Legacy compat aliases (used by some existing classes)
        'accent': v('cyan'),
        'accent-hover': v('cyan-hover'),
        'accent-muted': 'rgb(var(--cyan) / 0.15)',
        'accent-warm': v('gold'),
        'accent-warm-muted': 'rgb(var(--gold) / 0.15)',
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
          '0%, 100%': { boxShadow: '0 0 0 0 rgb(var(--cyan) / 0.3)' },
          '50%': { boxShadow: '0 0 16px 4px rgb(var(--cyan) / 0.12)' },
        },
        'spin': {
          to: { transform: 'rotate(360deg)' },
        },
      },
      boxShadow: {
        'glow': '0 0 24px rgb(var(--cyan) / 0.12)',
        'glow-gold': '0 0 24px rgb(var(--gold) / 0.10)',
        'elevated': 'var(--shadow-elevated)',
        'card': 'var(--shadow-card)',
        'inner-glow': 'inset 0 1px 0 rgb(var(--line) / 0.04)',
      },
    },
  },
  plugins: [],
}
