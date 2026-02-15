import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(160 16% 80%)",
        input: "hsl(160 16% 80%)",
        ring: "hsl(167 78% 27%)",
        background: "hsl(46 39% 94%)",
        foreground: "hsl(158 25% 12%)",
        primary: {
          DEFAULT: "hsl(167 78% 27%)",
          foreground: "hsl(0 0% 100%)"
        },
        secondary: {
          DEFAULT: "hsl(155 35% 95%)",
          foreground: "hsl(158 25% 16%)"
        },
        muted: {
          DEFAULT: "hsl(152 22% 92%)",
          foreground: "hsl(158 10% 35%)"
        },
        card: {
          DEFAULT: "hsl(0 0% 100% / 0.82)",
          foreground: "hsl(158 25% 12%)"
        },
        destructive: {
          DEFAULT: "hsl(0 72% 43%)",
          foreground: "hsl(0 0% 100%)"
        }
      },
      borderRadius: {
        lg: "1rem",
        md: "0.75rem",
        sm: "0.5rem"
      }
    }
  },
  plugins: [],
};

export default config;
