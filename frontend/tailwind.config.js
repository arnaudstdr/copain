import typography from "@tailwindcss/typography";

// Mappe chaque token couleur (variables CSS de src/index.css, définies en
// triplets RGB « R G B ») via `rgb(var(--x) / <alpha-value>)` : Tailwind peut
// alors composer les modificateurs d'alpha (`bg-accent/16`, `text-text2/80`).
// Les tokens composites (glass, blooms, ombres) ne sont pas des couleurs de
// palette : ils restent consommés via `var(--x)` en valeurs arbitraires.
const rgb = (name) => `rgb(var(--${name}) / <alpha-value>)`;

/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: rgb("bg"),
        bg2: rgb("bg2"),
        bg3: rgb("bg3"),
        bg4: rgb("bg4"),
        border: rgb("border"),
        border2: rgb("border2"),
        text: rgb("text"),
        text2: rgb("text2"),
        text3: rgb("text3"),
        accent: rgb("accent"),
        accent2: rgb("accent2"),
        green: rgb("green"),
        red: rgb("red"),
        orange: rgb("orange"),
        purple: rgb("purple"),
        blue: rgb("blue"),
        "on-accent": rgb("on-accent"),
      },
      fontFamily: {
        display: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
        sans: ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "Roboto", "Helvetica", "Arial", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [typography],
};
