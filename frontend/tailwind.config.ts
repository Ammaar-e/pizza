import type { Config } from "tailwindcss";

export default {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        brand: { 50: "#fff4ed", 100: "#ffe6d4", 500: "#e8590c", 600: "#d9480f", 700: "#b33a0b" },
        ink: { 900: "#111827", 800: "#1f2937", 700: "#374151" },
      },
      fontFamily: { sans: ["Inter", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"] },
    },
  },
  plugins: [],
} satisfies Config;
