# Engineering standard

Every product change must be reviewable as a chain from intent to evidence. A PR is not complete
because implementation exists or a test suite is green in aggregate.

## Required change record

Before merge, a feature or material fix must identify:

1. **Requirements** — stable IDs for user outcomes, system behavior, constraints, and failure modes.
2. **Design** — the components and boundaries that satisfy each requirement, including graceful
   degradation and explicit non-goals.
3. **Verification** — tests mapped back to requirement IDs at the appropriate levels.

The record can live in an existing specification or a focused document under `docs/`. Update it in
the same commit as the implementation whenever behavior changes.

## Verification levels

- **Unit:** pure rules, transformations, validation, and failure branches.
- **Integration:** component boundaries such as API → engine → persistence.
- **System/package:** installed artifacts and commands from outside the checkout.
- **User acceptance:** the complete user journey in a real browser or equivalent production surface.

Not every requirement needs a test at every level. Every requirement does need an objective check,
and every critical path needs both boundary-level and user-level coverage. Test names or a trace
matrix must make the relationship discoverable without reconstructing it from implementation.

## Merge evidence

PR descriptions must link the applicable specification and report the exact verification commands.
Known gaps, skipped levels, or environment failures are blockers unless explicitly accepted and
recorded by the operator. Silent skips and broad claims such as “tests pass” are not sufficient.
