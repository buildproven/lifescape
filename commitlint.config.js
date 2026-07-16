const REVIEW_TRAILER_PREFIXES = ["Reviewed-By:", "Break-Glass-Approval:", "Quality-Skip:"];

const lineLengthExceptReviewTrailers = (parsed, when = "always", maxLength = 100) => {
  const invalidLine = parsed.raw
    .split(/\r?\n/)
    .slice(1)
    .find(
      (line) =>
        line.length > maxLength &&
        !REVIEW_TRAILER_PREFIXES.some((prefix) => line.startsWith(prefix))
    );
  const valid = invalidLine === undefined;
  return [
    when === "never" ? !valid : valid,
    invalidLine ? `body/footer line exceeds ${maxLength} characters: ${invalidLine}` : "",
  ];
};

module.exports = {
  extends: ["@commitlint/config-conventional"],
  plugins: [
    {
      rules: {
        "line-length-except-review-trailers": lineLengthExceptReviewTrailers,
      },
    },
  ],
  rules: {
    // Type must be one of the conventional types
    "type-enum": [
      2,
      "always",
      [
        "feat", // New feature
        "fix", // Bug fix
        "docs", // Documentation only
        "style", // Formatting, no code change
        "refactor", // Code change that neither fixes a bug nor adds a feature
        "perf", // Performance improvement
        "test", // Adding tests
        "build", // Build system or external dependencies
        "ci", // CI configuration
        "chore", // Maintenance tasks
        "revert", // Revert a previous commit
      ],
    ],
    // Subject line max length
    "subject-max-length": [2, "always", 100],
    // Built-ins cannot distinguish full-SHA review trailers from ordinary prose.
    "body-max-line-length": [0],
    "footer-max-line-length": [0],
    "line-length-except-review-trailers": [2, "always", 100],
    // Subject must not be empty
    "subject-empty": [2, "never"],
    // Type must not be empty
    "type-empty": [2, "never"],
  },
};
