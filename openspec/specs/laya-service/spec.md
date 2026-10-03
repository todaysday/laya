# Laya Service Specification

## Purpose

Specify the pinned ARM64 Laya decision service, browser v10s routing, the
per-profile worker lifecycle, and the measured Books to Scrape prompt trial.

## Deployed configuration and verified results

The service runs pinned Laya source
`fa9a2a7070b1789912a49ae24603bbfb1a78b001` with English and multilingual
weights at `convaiinnovations/laya@55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`.
The multilingual profile uses its `multilingual` subfolder. Browser profile
`laya-browser-v10s` uses
`cklxx/laya-browser@adf912be85ff9221ee171551778456b133c1af75/v10s/` and a
format-v3 adapter. The existing authenticated SystemOne API remains the only
external model endpoint. Each profile loads in its own child worker; the HTTP
parent holds authentication and does not load model weights.

Worker contracts passed 10/10, router/readiness contracts 15/15, and the v3
adapter suite 4/4 after Code Simplifier and a post-pass rerun. Invalid bearer
authentication returned 401. Serial profile smokes returned offered choices
from all 51 options, and browser v10s used its native 1024/768 context.

On the frozen ten-goal English Books to Scrape set, base Laya selected the
target label in 1/10 cases and completed 0/10 exact click-plus-URL outcomes.
Browser v10s selected the target label in 9/10 and completed 8/10 exact
outcomes. The `Children's` goal with visible label `Childrens` remains a model
choice failure. These results apply only to this task set.

All three workers exited after their natural 600-second idle periods with no
requests in flight; the HTTP service remained active. Cgroup current was
177,356,800 B with no workers, 6,547,693,568 B with all three loaded, and
182,960,128 B after expiry. Parent RSS returned to its 258,809,856 B
pre-load baseline. A post-expiry browser request spawned a fresh worker,
preserved all 51 choices without truncation, and returned an offered choice
using native 1024/768 defaults. Cold worker load was 9.607 s and total API
elapsed time was 12.025 s for this single reload smoke.

## Requirements

### Requirement: Pin all routed checkpoints

WHEN the service is deployed, the operator SHALL use an immutable source
commit, image digest, and revision/path mapping for each registered profile.

#### Scenario: Reproducible profiles

GIVEN the deployment manifest
WHEN the service starts
THEN it identifies the pinned Laya source and the English, multilingual, and
browser checkpoints
AND the browser v10s profile uses format-v3 conversion
AND no model or image uses a floating revision.

### Requirement: Route browser v10s through the existing API

WHEN a valid authenticated request selects `laya-browser-v10s`, the service SHALL route it through the browser-specific format-v3 adapter and worker.

#### Scenario: Browser choice uses native prediction

GIVEN the verified browser checkpoint is available on disk
WHEN the client selects `model:"laya-browser-v10s"`
THEN the worker applies format-v3 conversion and calls native Laya prediction
AND preserves all state text and offered choices
AND omitted context overrides preserve native 1024/768 settings
AND native usage, route, truncation, and effective context are observable
AND English and multilingual routes retain their native behavior.

### Requirement: Isolate each profile in a lazy child worker

WHEN a request selects a profile, the HTTP parent SHALL start or reuse only
that profile's worker and terminate it 600 seconds after its last completed
request when no request is in flight.

#### Scenario: Parent authenticates and forwards complete data

GIVEN a valid authenticated model request
WHEN the parent forwards it over private IPC
THEN complete request and response/error data cross the local channel
AND the credential remains in the parent
AND workers expose no network listener
AND the public HTTP contract remains unchanged.

#### Scenario: Expiry releases only an idle profile process

GIVEN the selected worker has no in-flight request and reaches its idle
deadline
WHEN the supervisor expires it
THEN only that worker exits and is reaped
AND other profile workers remain available
AND a later request starts a fresh worker from the pinned files.

#### Scenario: Health does not change worker residency

GIVEN a worker is absent or has a pending idle deadline
WHEN health is read
THEN health reports current state without spawning a worker, running model
inference, or refreshing the idle timer.

### Requirement: Prepare complete blind browser choices

WHEN the Books to Scrape category prompt is constructed, the harness SHALL
include compact page context and every observed category link while keeping
target metadata outside the model request.

#### Scenario: Request is grounded in the current snapshot

GIVEN a fresh snapshot and registered category goal
WHEN the SystemOne request is serialized
THEN its state includes current URL, title, readable non-interactive text,
and action history
AND all observed category links are offered uniformly with visible labels
and roles
AND target refs and expected URLs are absent from the model payload
AND the client does not silently trim input or shortlist by goal.

### Requirement: Count navigation only after the browser action

WHEN a held-out choice is scored, the operator SHALL execute its current
snapshot ref and check the exact preregistered destination URL.

#### Scenario: First attempt is immutable

GIVEN a frozen prompt and fresh complete category inventory
WHEN the model returns a choice key
THEN only that offered key maps to a ref from that snapshot
AND success requires the click and exact URL postcondition
AND invalid answers, failed clicks, and incorrect URLs remain failures without
gold fallback, retries, or replacement cases.

### Requirement: Keep prompt tests serial and site-scoped

WHEN the frozen prompt is evaluated, the operator SHALL run the same ten
preregistered tasks serially through the existing server.

#### Scenario: Pilot and held-out sets stay disjoint

GIVEN pilot goals Religion, Nonfiction, and Music and final goals Travel,
Mystery, Historical Fiction, Sequential Art, Classics, Philosophy, Romance,
Women's Fiction, Fiction, and Children's
WHEN prompt wording is tuned and frozen
THEN exact destination URLs are preregistered from the observed page
AND final goals are excluded from prompt tuning
AND outcomes are reported only for the tested site's category navigation.
