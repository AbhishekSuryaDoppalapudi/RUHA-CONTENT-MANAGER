/** @type {import('tailwindcss').Config} */
const c = (v) => `rgb(var(--${v}) / <alpha-value>)`
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: ['class', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        bg: c('bg'), surface: c('surface'), surface2: c('surface2'), line: c('line'),
        ink: c('ink'), muted: c('muted'),
        primary: c('primary'), accent: c('accent'), teal: c('teal'),
        ok: c('ok'), warn: c('warn'), danger: c('danger'),
      },
      fontFamily: {
        display: ['"Outfit Variable"', 'Segoe UI', 'sans-serif'],
        sans: ['"Inter Variable"', 'Segoe UI', 'sans-serif'],
      },
      boxShadow: { glow: '0 0 0 1px rgb(var(--primary) / .35), 0 8px 30px -8px rgb(var(--primary) / .45)' },
      keyframes: {
        fadeUp: { '0%': { opacity: 0, transform: 'translateY(6px)' }, '100%': { opacity: 1, transform: 'none' } },
        shimmer: { '100%': { transform: 'translateX(100%)' } },
      },
      animation: { fadeUp: 'fadeUp .25s ease-out both' },
    },
  },
  plugins: [],
}
