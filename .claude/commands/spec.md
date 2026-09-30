---
description: Create or update a feature spec from the template before any coding
argument-hint: <feature-name>
---

Create a spec for: **$ARGUMENTS**

1. Read `docs/design/HLD.md` and `docs/specs/_template.md`.
2. If a spec for this feature already exists in `docs/specs/`, update it instead of creating a new one.
3. Otherwise create `docs/specs/<kebab-case-name>.md` from the template with Status `Draft`.
4. Fill every section. Acceptance criteria must be testable. Flag anything that conflicts with the HLD.
5. Add a task line for it under the right phase in `docs/plans/implementation-plan.md`.
6. Stop and ask me to approve. Do not write implementation code.
