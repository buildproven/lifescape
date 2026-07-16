const js = require("@eslint/js");
const globals = require("globals");
const security = require("eslint-plugin-security");

module.exports = [
  {
    ignores: ["**/node_modules/**", "**/dist/**", "**/.venv/**", "**/coverage/**"],
  },
  js.configs.recommended,
  security.configs.recommended,
  {
    files: ["src/retirement_engine/static/**/*.js"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "script",
      globals: globals.browser,
    },
    rules: {
      complexity: ["warn", 15],
      "max-depth": ["warn", 4],
      "max-params": ["warn", 5],
      "security/detect-object-injection": "off",
    },
  },
];
