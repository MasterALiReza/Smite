/** @type {import('tailwindcss').Config} */
export default {
  darkMode: 'class',
  content: [
    "./index.html",
    "./src/**/*.{js,ts,jsx,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        obsidian: {
          950: '#070a13',
          900: '#0b0f19',
          850: '#0e1422',
          800: '#131b2e',
          700: '#1c2842',
        },
      },
      fontFamily: {
        sans: ['Plus Jakarta Sans', 'Vazirmatn', '-apple-system', 'BlinkMacSystemFont', 'Segoe UI', 'Roboto', 'sans-serif'],
        farsi: ['Vazirmatn', 'sans-serif'],
        mono: ['JetBrains Mono', 'ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      transitionTimingFunction: {
        spring: 'cubic-bezier(0.16, 1, 0.3, 1)',
        'bounce-subtle': 'cubic-bezier(0.34, 1.35, 0.64, 1)',
      },
      boxShadow: {
        'glow-sm': '0 0 15px -3px rgba(14, 165, 233, 0.25)',
        'glow-md': '0 0 25px -4px rgba(14, 165, 233, 0.35)',
        'glow-emerald': '0 0 20px -3px rgba(16, 185, 129, 0.3)',
        'glow-rose': '0 0 20px -3px rgba(244, 63, 94, 0.3)',
        'inner-bevel': 'inset 0 1px 1px 0 rgba(255, 255, 255, 0.08)',
      },
      borderRadius: {
        '3xl': '1.5rem',
        '2xl': '1rem',
        xl: '0.875rem',
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
      },
    },
  },
  plugins: [],
}

