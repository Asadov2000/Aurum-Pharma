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
          // Fixed deep-teal brand ramp for occasional direct use / charts.
          50: "#ecfdf7",
          100: "#cff5ea",
          200: "#a1ead7",
          300: "#67d8bf",
          400: "#33bda4",
          500: "#13a087",
          600: "#0d8070",
          700: "#0e6a5e",
          800: "#10544c",
          900: "#10443e",
          950: "#04302c",
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
        // Local system fonts keep the app fast and deterministic offline.
        sans: [
          "Segoe UI",
          "system-ui",
          "-apple-system",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        // Headings use the same metrics to avoid layout shifts after startup.
        display: [
          "Segoe UI",
          "system-ui",
          "-apple-system",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      // Soft, low-contrast elevation — never harsh. Tinted with the cool slate
      // ink so shadows read as depth, not grey smudge.
      boxShadow: {
        sm: "0 1px 2px 0 hsl(202 45% 11% / 0.05), 0 1px 3px 0 hsl(202 45% 11% / 0.06)",
        md: "0 2px 4px -1px hsl(202 45% 11% / 0.06), 0 4px 10px -2px hsl(202 45% 11% / 0.08)",
        lg: "0 6px 12px -3px hsl(202 45% 11% / 0.08), 0 16px 28px -6px hsl(202 45% 11% / 0.12)",
        xl: "0 10px 20px -5px hsl(202 45% 11% / 0.12), 0 28px 52px -10px hsl(202 45% 11% / 0.16)",
      },
      borderRadius: {
        sm: "0.375rem",
        md: "0.5rem",
        lg: "0.75rem",
        xl: "1rem",
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
