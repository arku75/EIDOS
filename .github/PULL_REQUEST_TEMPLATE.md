## Summary

What changes, in one paragraph?

## Why

What problem or evidence gap does this address?

## Evidence level

Mark the strongest state this PR actually reaches:

- [ ] Implemented — code/docs exist
- [ ] Wired — the runtime reaches the new path
- [ ] Executed — the path was run
- [ ] Measured — before/after evidence was collected
- [ ] Reused — the result changed a later task or decision

Do not check a stronger state unless the PR includes reproducible evidence.

## Verification

Commands/tests run:

```text
# paste exact commands and concise results
```

Environment / assumptions:

- OS:
- Python/runtime:
- X11/Wayland/browser if relevant:
- External services/models if relevant:

## Safety and rollback

- [ ] No secrets, private DBs, browser profiles or machine-specific private state added
- [ ] Respects `constitution.toml` and capability authorization boundaries
- [ ] No silent escalation of body/tool permissions
- [ ] Risky changes have an explicit rollback/recovery path
- [ ] No destructive migration without backup/migration evidence
- [ ] Existing user/private data is preserved

Rollback plan:

## Architecture rules

- [ ] `claim ≠ action ≠ success`: success is tied to an observed effect where applicable
- [ ] New state/weights/memory have a demonstrated downstream consumer
- [ ] Direct DB access follows the repository's canonical DB abstraction
- [ ] Experimental mechanisms remain isolated until their integration gate passes
- [ ] Documentation distinguishes VERIFIED / INFERRED / EXPERIMENTAL claims

## Documentation

- [ ] README / architecture / install / known issues updated when behavior changes
- [ ] Public claims remain conservative and reproducible
- [ ] No AGI/consciousness/biological-equivalence claim is introduced as fact

## Related issue

Closes / relates to:
