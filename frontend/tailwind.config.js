/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        paper: 'var(--paper)',
        'paper-2': 'var(--paper-2)',
        ink: 'var(--ink)',
        mute: 'var(--mute)',
        line: 'var(--line)',
        accent: 'var(--accent)',
        teal: 'var(--teal)',
        healthy: 'var(--sev-healthy)',
        mild: 'var(--sev-mild)',
        moderate: 'var(--sev-moderate)',
        severe: 'var(--sev-severe)',
      },
      fontFamily: {
        display: ['Archivo', 'Space Grotesk', 'sans-serif'],
        sans: ['"Space Grotesk"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      gridTemplateColumns: { 12: 'repeat(12, minmax(0, 1fr))' },
    },
  },
  plugins: [],
};
