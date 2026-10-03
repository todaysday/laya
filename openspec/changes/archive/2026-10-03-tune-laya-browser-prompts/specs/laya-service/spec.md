# Specification Delta: Laya browser decisions

## MODIFIED Requirements

### Requirement: Pinned ARM64 Laya service

WHEN the Laya endpoint is deployed, the operator SHALL run the reviewed
native ARM64 CPU release with immutable source, image, and checkpoint pins,
loading English, multilingual, and browser profiles on demand and defaulting
to multilingual.

#### Scenario: Browser checkpoint is reproducible

GIVEN the deployment manifest
WHEN the service starts
THEN it records source commit
`fa9a2a7070b1789912a49ae24603bbfb1a78b001`, English/multilingual weights
`convaiinnovations/laya@55cf4c4ebb4ebe31b2550e8bdf3bd21b99753851`, and browser
checkpoint `cklxx/laya-browser@adf912be85ff9221ee171551778456b133c1af75/v10s/`
AND the multilingual profile uses its `multilingual` subfolder
AND the browser profile uses the pinned format-v3 adapter
AND no image or checkpoint uses a floating revision.

## ADDED Requirements

### Requirement: Resolve browser snapshot refs by exact identity

WHEN a browser ref resolver locates an interactive element, it SHALL match the
stored role and full accessible name exactly, including an empty name, before
using the stored occurrence index.

#### Scenario: Similar and empty names resolve to their own elements

GIVEN a page with `Historical Fiction` before `Fiction` and an unnamed
accessible element
WHEN the matching snapshot refs are clicked in a regression
THEN each ref reaches its own destination and the unnamed ref remains bound
to its stored role and occurrence.

### Requirement: Build a complete blind category-choice request

WHEN the Books to Scrape category request is built, the harness SHALL put
compact page context in `state`, every observed category link in
`criteria`, and keep target metadata outside the model request.

#### Scenario: Input is complete and does not expose the target

GIVEN a fresh browser snapshot and preregistered category goal
WHEN a SystemOne request is serialized
THEN `state` contains current URL, title, readable non-interactive text, and
action history
AND each observed category link is offered with uniform visible-label and
role description
AND target refs, expected URLs, gold labels, and target metadata are absent
from the request
AND no goal-driven shortlist or silent client-side trimming is applied.

### Requirement: Route browser v10s through the existing API

WHEN an authenticated request selects browser v10s, the service SHALL dispatch
the pinned checkpoint through its format-v3 adapter and profile worker.

#### Scenario: Browser profile uses native prediction

GIVEN the existing authenticated SystemOne service and the verified local
checkpoint snapshot
WHEN the request selects `model:"laya-browser-v10s"`
THEN the browser worker applies format-v3 conversion and calls native Laya
prediction
AND English and multilingual profiles retain their existing route behavior
AND omitted context overrides preserve the checkpoint's native 1024/768
defaults
AND the adapter preserves all state and candidates and reports native usage
and truncation
AND no additional HTTP endpoint, listener, or auth boundary is added.

### Requirement: Isolate model profiles in independent lazy workers

WHEN a request selects one of the three profiles, the HTTP parent SHALL spawn
or reuse only that profile's child worker and exit it 600 seconds after its
last completed request when no request is in flight.

#### Scenario: Parent authenticates and forwards complete data

GIVEN a valid authenticated request
WHEN the parent forwards the request to the selected worker
THEN private IPC carries complete request and response or structured error
AND the API key remains only in the parent
AND workers expose no network listener
AND the external API contract remains unchanged.

#### Scenario: Expiry affects only an idle worker

GIVEN one worker has no in-flight request and reaches its 600-second idle
deadline
WHEN the supervisor expires it
THEN only that process is terminated and reaped
AND other workers remain available
AND a later request starts a fresh worker from pinned files.

#### Scenario: Health does not start work or extend residency

GIVEN a worker is absent or has an idle deadline
WHEN liveness or authenticated health is queried
THEN health reports current state without spawning a worker, running
inference, or refreshing the idle timer.

### Requirement: Tune only on preregistered pilot tasks

WHEN the prompt is chosen for the held-out set, the operator SHALL tune only
on the three registered pilot categories and freeze the prompt, serializer,
candidate builder, and final manifest before held-out calls.

#### Scenario: Pilot and held-out goals are disjoint

GIVEN pilot goals Religion, Nonfiction, and Music and final goals Travel,
Mystery, Historical Fiction, Sequential Art, Classics, Philosophy, Romance,
Women's Fiction, Fiction, and Children's
WHEN pilot calls are made
THEN every goal and expected destination is preregistered from the observed
page before inference
AND all prompt revisions and pilot outcomes are retained
AND no pilot goal or expected target is reused in final evaluation.

### Requirement: Score navigation only after the real action

WHEN a held-out category decision is evaluated, the operator SHALL execute
the first choice through its current snapshot ref and verify the exact goal
URL.

#### Scenario: First answer controls click and score

GIVEN a frozen prompt and fresh snapshot with complete candidates
WHEN the model returns a choice key
THEN only that offered key maps to its current ref
AND success requires the click to reach the preregistered exact URL
AND invalid answers, failed clicks, and wrong URLs remain failures without
gold fallback, retry, or replacement case
AND the complete raw result, latency, route, and native usage are recorded.

### Requirement: Keep model requests serial on the existing server

WHEN pilot or held-out model inference runs, the operator SHALL use the
existing private Laya service and send one request at a time.

#### Scenario: Base and browser routes use the same frozen cases

GIVEN the ten registered category tasks and frozen prompt
WHEN base and browser v10s profiles are evaluated sequentially
THEN each profile receives the same goal and candidate inventory
AND the base request uses explicit `8192/8192` context while browser v10s
uses its native defaults unless supplied explicitly
AND first-attempt choice and navigation outcomes are reported separately
AND no local model run, unrelated service restart, request cap, or retry is
added to the test.
