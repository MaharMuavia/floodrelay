import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        depth: "var(--depth)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        line: "var(--line)",
        ink: "var(--ink)",
        "ink-muted": "var(--ink-muted)",
        signal: "var(--signal)",
        rescue: "var(--rescue)",
        medical: "var(--medical)",
        stable: "var(--stable)",
        water: "var(--water)",
      },
      fontFamily: {
        // One family for the whole interface; mono only for comparable data.
        display: ["var(--font-mona)", "system-ui", "sans-serif"],
        sans: ["var(--font-mona)", "system-ui", "sans-serif"],
        mono: ["var(--font-plex-mono)", "ui-monospace", "monospace"],
      },
      fontSize: {
        "12": ["0.75rem", { lineHeight: "1.1rem" }],
        "13": ["0.8125rem", { lineHeight: "1.2rem" }],
        "15": ["0.9375rem", { lineHeight: "1.4rem" }],
        "18": ["1.125rem", { lineHeight: "1.6rem" }],
        "24": ["1.5rem", { lineHeight: "1.9rem" }],
        "34": ["2.125rem", { lineHeight: "2.4rem" }],
      },
    },
  },
  plugins: [],
};
export default config;
