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
          contrast: withAlpha("--success-contrast"),
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
          contrast: withAlpha("--danger-contrast"),
        },
        info: {
          DEFAULT: withAlpha("--info"),
          subtle: withAlpha("--info-subtle"),
          foreground: withAlpha("--info-foreground"),
        },
      },
      fontFamily: {
        // Local variable system fonts keep the app fast and deterministic offline.
        sans: [
          "Segoe UI Variable Text",
          "Segoe UI",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
        // Headings use the same local family so startup never causes layout shifts.
        display: [
          "Segoe UI Variable Display",
          "Segoe UI",
          "-apple-system",
          "BlinkMacSystemFont",
          "system-ui",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      // Hairline elevation stays cheap to paint on low-end Windows hardware.
      boxShadow: {
        sm: "0 1px 2px hsl(215 30% 12% / 0.05)",
        md: "0 3px 8px hsl(215 30% 12% / 0.08)",
        lg: "0 8px 20px hsl(215 30% 12% / 0.12)",
        xl: "0 14px 36px hsl(215 30% 12% / 0.16)",
      },
      borderRadius: {
        sm: "0.375rem",
        md: "0.5rem",
        lg: "0.5rem",
        xl: "0.5rem",
      },
      transitionDuration: {
        fast: "120ms",
        base: "160ms",
        slow: "200ms",
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
