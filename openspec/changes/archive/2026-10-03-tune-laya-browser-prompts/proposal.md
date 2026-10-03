# Proposal: add Laya browser category decisions

## Why

Base Laya's earlier blind-menu requests were truncated or selected the wrong
category. On the frozen compact-state prompt and ten Books-to-Scrape category
goals, base Laya selected the target label in 1/10 cases and completed 0/10
exact click-plus-URL outcomes. Browser v10s selected the target label in 9/10
and completed 8/10 exact outcomes. These results apply only to this task set
and do not imply general browser-agent accuracy.

## Selected design

Keep the existing authenticated `POST /v1/systemone` service and add the
explicit selector `model:"laya-browser-v10s"`. Preserve the existing English
and multilingual routes. The browser checkpoint is
`cklxx/laya-browser@adf912be85ff9221ee171551778456b133c1af75/v10s/`, using Laya
source `fa9a2a7070b1789912a49ae24603bbfb1a78b001`; its format-v3 transform
runs only on the browser profile.

The selected memory boundary is one supervised child process per model
profile under the existing HTTP service. Spawn only the requested profile,
send complete requests and structured errors over private local IPC, keep
bearer authentication in the HTTP parent, protect in-flight requests, and
exit each worker 600 seconds after its last completion. Health must not spawn
workers or refresh idle timers. Do not add another HTTP service, endpoint,
listener, public route, request cap, or compatibility model alias.

## Verified outcome and open work

The Camofox exact role/name regression passed 3/3. Original prompt-trial
first attempts remain immutable. Worker contracts passed 10/10, router and
readiness contracts 15/15, and the v3 adapter suite 4/4 after Code Simplifier
and post-pass rerun. All three workers exited after their natural 600-second
idle periods; cgroup use and parent RSS returned to their measured startup
baselines while the HTTP service remained active. A post-expiry browser call
loaded a fresh worker and returned an offered choice from all 51 options
without truncation. Remaining unrelated adapter/integration work is listed in
scoped `TODO.md`.
