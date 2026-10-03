# Tasks

- [x] Record the blind-menu and compact-state baselines separately.
- [x] Freeze pilot wording and the disjoint ten-goal manifest before final
  inference.
- [x] Run ten first-attempt browser decisions through base Laya and ten through
  browser v10s; retain every click and URL result.
- [x] Repair Camofox exact role/name ref resolution; fresh regression passes
  3/3, including empty names. Do not rerun or reclassify original outcomes.
- [x] Record current 600-second shared-process TTL and memory evidence without
  claiming it returns RSS to the OS.
- [x] Add red contracts for per-profile child-worker spawn, private IPC,
  structured errors, auth isolation, independent 600-second exit, in-flight
  protection, worker failure handling, and health without spawn or TTL refresh.
  Initial red failures reproduced missing supervisor/proxy behavior; after
  Code Simplifier and a post-pass rerun, pinned-image suites pass fake-worker
  10/10, router/readiness 15/15, and v3 adapter 4/4.
- [x] Implement one supervised worker per profile under the existing HTTP
  service; preserve the public API and avoid added input/output/time caps.
  Runtime is deployed; three distinct worker PIDs and serial choices on each
  profile were verified.
- [x] Verify natural 600-second child exits, reload behavior, and parent/worker
  memory after expiry. All three child processes exited with no requests in
  flight, cgroup usage returned near startup baseline, and the existing API
  service stayed active. A post-expiry browser request spawned a fresh worker
  and returned an offered choice from the complete candidate set.
