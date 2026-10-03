# Laya browser-agent follow-ups

## Completed

- [x] Publish the selected worker design and pseudocode, archived `laya-service` spec delta, and merged living spec in standard OpenSpec layout; strict living-spec validation passes.

## Remaining follow-ups

- Natural expiry and memory behavior are verified for the deployed
  one-child-process-per-profile design. Each worker exited after 600 idle
  seconds without an in-flight request; the existing API service stayed up,
  aggregate cgroup usage returned near its no-model baseline, and a fresh
  browser request successfully reloaded its checkpoint. This outcome is
  deployment-specific and does not change the prior shared-process RSS result.
- Build the existing browser micro-loop's Camofox-to-BrowserUse adapter and
  validate ref-to-coordinate mapping. The measured run used Camofox refs and
  did not establish coordinate compatibility.
- Evaluate the visible `Childrens` link label against the goal `Children's` on
  a separately preregistered set. Preserve current held-out result.
- Add OmniRoute routing for Laya's SystemOne API as separate integration work.
