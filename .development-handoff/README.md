# Aurum development ownership (operational branch)

This branch coordinates one product writer. Never merge it into main or a product branch. It contains no secrets and changes no runtime or database.

Read state.json from the CURRENT ref of codex/development-control, not from a cached URL or a source checkout. The control ref SHA and state generation jointly identify the grant. A schedule or a message is never a grant.

An actor may change product source/publish only while phase=active, owner matches it, run_id/generation match its accepted grant, and the actual product branch head matches the recorded/locally verified expected head. Read the current control ref again immediately before publication/merge. Unexpected state, active/queued previous writer, branch movement or incomplete GitHub response stops writes. This protocol serializes grants; it does not change GitHub ACLs or make product and control updates one atomic transaction.

Transfer LOCAL -> CLOUD:
1. Stop local editing; preserve all tracked/untracked intended source in the same remote product branch or an exact immutable candidate tree/manifest. Never publish .env, databases or secrets. WIP is not green and cannot merge.
2. Verify full current main/branch/PR state and cloud schedule remains paused. Append a control commit from the exact current control parent: phase=offered, owner=local, generation+1, pending_transfer={id,from:local,to:cloud,head,tree,manifest,remaining_step,failure_counts}. The old writer performs no further product writes.
3. Dispatch the EXISTING cloud thread with that control commit/generation/transfer ID. It must read actual state and exact source, verify tool readiness, and append an acceptance commit (phase=active, owner=cloud, new run_id, pending_transfer=null, generation+1) from the exact offered parent. Only then may it write. Verify acceptance before telling the user handoff succeeded. Enable only the existing cloud schedule after acceptance.
4. If dispatch/ack fails, preserve offered state and report incomplete transfer; never assume timeout or PC shutdown grants ownership.

Transfer CLOUD -> LOCAL:
1. Pause the existing cloud schedule; request stop/checkpoint only. Verify active turn ended and obtain the writer's acknowledgement (or verify idle+paused with no queued product request). Preserve latest source/head, read actual GitHub.
2. Cloud offers as above; local accepts from exact offered control parent. If cloud cannot acknowledge, keep schedule paused and explicitly reconcile unknown pending work/branch before any recovery grant.

Control commits are append-only with normal fast-forward update_ref(force=false, expected_sha=current control parent). Read back actual ref and state after every update or lost response. Concurrent children cannot both advance a normal non-force ref; stale grants must reread, never force, delete, reset, or auto-takeover. Only THIS operational branch uses commit messages with [skip ci]; product branch commits/merge messages never use skip markers and require all eight final-head contexts.

Only the current owner publishes/merges the product. Independent reviewers and cloud ENVIRONMENT-REPAIR may inspect or repair isolated tools without writing product files/refs/PRs. Tool restoration never authorizes dependency-version changes, network-policy bypass, paid services or writes outside the allowed workspace.

Current item: UNIT-MIXED-ELECTRONIC-REFUND-POSTING-01. Confirmation PR419 is already merged; do not repeat it. Historical failure counters remain in the saved manifest/checkpoint. Main merge never authorizes deployment, service restart or shared-DB migration.
