import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        border: "hsl(214 32% 91%)",
        background: "hsl(0 0% 100%)",
        foreground: "hsl(222 47% 11%)",
        muted: { DEFAULT: "hsl(210 40% 96%)", foreground: "hsl(215 16% 47%)" },
        card: { DEFAULT: "hsl(0 0% 100%)", foreground: "hsl(222 47% 11%)" },
        primary: { DEFAULT: "hsl(222 47% 11%)", foreground: "hsl(210 40% 98%)" },
        success: "hsl(142 71% 45%)",
        destructive: "hsl(0 84% 60%)",
      },
      borderRadius: {
        lg: "0.5rem",
        md: "0.375rem",
        sm: "0.25rem",
      },
    },
  },
} satisfies Config;