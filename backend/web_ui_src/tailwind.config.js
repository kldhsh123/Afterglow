/** @type {import('tailwindcss').Config} */
// 时光信笺主题（与 frontend/tailwind.config.js 保持一致）
export default {
  content: ['./index.html', './src/**/*.{vue,ts}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        paper: '#F5F1E4',
        'paper-soft': '#FBF7E9',
        'paper-shade': '#EBE3CD',
        ink: '#1A2F4B',
        'ink-soft': '#4A5D7A',
        accent: '#8B5A2B',
        'accent-soft': '#C49A6C',
        warning: '#B85C38',
        night: {
          bg: '#101820',
          'bg-soft': '#172530',
          'bubble-user': '#1E2C3A',
          text: '#E6EAEF',
          'text-soft': '#9CA6B3',
          accent: '#D9B58A',
          'accent-soft': '#A88762',
        },
      },
      fontFamily: {
        serif: [
          '"Noto Serif SC"',
          '"Source Han Serif SC"',
          '"思源宋体"',
          '"宋体"',
          'STSong',
          'serif',
        ],
        sans: [
          '"Inter"',
          '"PingFang SC"',
          '"Microsoft YaHei"',
          'system-ui',
          'sans-serif',
        ],
      },
      boxShadow: {
        letter: '0 1px 2px rgba(26,47,75,0.04), 0 8px 24px rgba(26,47,75,0.06)',
        'letter-strong': '0 2px 6px rgba(26,47,75,0.08), 0 16px 40px rgba(26,47,75,0.1)',
      },
    },
  },
  plugins: [],
}
