import type { Config } from "tailwindcss";

/** hsl(var(--token) / <alpha>) — lets `bg-primary/50` etc. work with theming. */
const withAlpha = (token: string) => `hsl(var(${token}) / <alpha-value>)`;

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  // Theme is driven by the [data-theme] attribute on <html>, set by
  // src/lib/theme.ts — not Tailwind's class strategy.
  theme: {
    extend: {
      colors: {
        background: withAlpha("--background"),
        surface: {
          DEFAULT: withAlpha("--surface"),
          raised: withAlpha("--surface-raised"),
        },
        border: withAlpha("--border"),
        input: withAlpha("--input"),
        ring: withAlpha("--ring"),
        foreground: {
          DEFAULT: withAlpha("--foreground"),
          secondary: withAlpha("--foreground-secondary"),
          muted: withAlpha("--foreground-muted"),
        },
        primary: {
          DEFAULT: withAlpha("--primary"),
          foreground: withAlpha("--primary-foreground"),
          // Fixed medical-teal ramp for occasional direct use / charts.
          50: "#f0fdfa",
          100: "#ccfbf1",
          200: "#99f6e4",
          300: "#5eead4",
          400: "#2dd4bf",
          500: "#14b8a6",
          600: "#0d9488",
          700: "#0f766e",
          800: "#115e59",
          900: "#134e4a",
        },
        success: {
          DEFAULT: withAlpha("--success"),
          subtle: withAlpha("--success-subtle"),
          foreground: withAlpha("--success-foreground"),
        },
        warning: {
          DEFAULT: withAlpha("--warning"),
          subtle: withAlpha("--warning-subtle"),
          foreground: withAlpha("--warning-foreground"),
        },
        danger: {
          DEFAULT: withAlpha("--danger"),
          subtle: withAlpha("--danger-subtle"),
          foreground: withAlpha("--danger-foreground"),
        },
        info: {
          DEFAULT: withAlpha("--info"),
          subtle: withAlpha("--info-subtle"),
          foreground: withAlpha("--info-foreground"),
        },
      },
      fontFamily: {
        sans: [
          "Inter",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      // Soft, low-contrast elevation — never harsh.
      boxShadow: {
        sm: "0 1px 2px 0 hsl(222 47% 11% / 0.04), 0 1px 3px 0 hsl(222 47% 11% / 0.06)",
        md: "0 2px 4px -1px hsl(222 47% 11% / 0.06), 0 4px 8px -2px hsl(222 47% 11% / 0.08)",
        lg: "0 4px 8px -2px hsl(222 47% 11% / 0.08), 0 12px 24px -4px hsl(222 47% 11% / 0.10)",
        xl: "0 8px 16px -4px hsl(222 47% 11% / 0.10), 0 24px 48px -8px hsl(222 47% 11% / 0.14)",
      },
      borderRadius: {
        sm: "0.25rem",
        md: "0.375rem",
        lg: "0.5rem",
        xl: "0.75rem",
      },
      transitionDuration: {
        fast: "150ms",
        base: "200ms",
        slow: "300ms",
      },
      zIndex: {
        dropdown: "1000",
        sticky: "1100",
        overlay: "1200",
        modal: "1300",
        popover: "1400",
        toast: "1500",
      },
    },
  },
  plugins: [],
} satisfies Config;
