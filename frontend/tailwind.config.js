/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        backdrop: '#07090D',
        panel: '#0E131B',
        signal: '#3DF5FF',
        healthy: '#3DFFA2',
        amber: '#FFC53D',
        orange: '#FF7A2F',
        danger: '#FF3D5A',
        muted: '#7D8794',
      },
      fontFamily: {
        display: ['"Space Grotesk"', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'monospace'],
      },
      boxShadow: {
        panel: '0 0 0 1px rgba(255,255,255,0.06)',
      },
    },
  },
  plugins: [],
};
