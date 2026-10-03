# Specification: Laya browser decision profile

## Requirement: Preserve full browser decision inputs

WHEN constructing a category-navigation decision, the request SHALL contain
current page URL, title, readable non-interactive text, action history, and
every observed category link. Correct-target metadata and snapshot refs remain
outside the model payload.

### Scenario: First answer is executed and scored

GIVEN a preregistered goal and fresh Camofox snapshot
WHEN the model returns an offered choice
THEN the runner clicks only the current ref mapped to that choice
AND counts success only if the independently read URL matches the expected
destination exactly
AND records invalid answers, click failures, or wrong URLs without retry.

## Requirement: Route pinned browser checkpoint

WHEN `model:"laya-browser-v10s"` is selected through the existing
authenticated SystemOne API, the service SHALL apply the pinned format-v3
transform and native prediction only for that profile.

### Scenario: Keep model outcomes and source pins attributable

GIVEN pinned base and browser profiles
WHEN a comparison runs
THEN it records source and weight revisions, actual route, native context,
truncation, first choice, click result, final URL, and latency per case
AND reports only results supported by the Books-to-Scrape test set.

## Requirement: Exit idle model workers independently

WHEN a profile is selected, the existing HTTP parent SHALL spawn or reuse
only that profile's child process and terminate it 600 seconds after its last
completed request when no request is in flight.

### Scenario: Keep worker traffic private and preserve auth

GIVEN a valid public API request and a started worker
WHEN the parent forwards the request
THEN private IPC transports complete request and response/error JSON
AND the API key remains in the parent
AND workers expose no network listener
AND the external HTTP contract remains unchanged.

### Scenario: Terminate only the idle profile

GIVEN one profile worker reaches its idle deadline
WHEN it has no in-flight request
THEN only that process exits, while other profile workers remain unaffected
AND its next request starts a fresh process from pinned checkpoint files.

### Scenario: Health does not affect worker lifecycle

GIVEN a profile has no worker or an active TTL
WHEN liveness or metadata health is read
THEN the response reports current process state without spawn, inference, or
TTL refresh.

### Scenario: Report observed memory behavior

GIVEN a worker exits after its idle period
WHEN memory evidence is recorded
THEN process and cgroup measurements are reported separately from model
object residency
AND no OS reclamation claim is made without observed evidence.
