# Design: Laya browser profile and prompt evaluation

## Selected architecture

Keep the existing authenticated Laya SystemOne service and add the browser
checkpoint as a third explicit profile, `laya-browser-v10s`. Preserve the
existing English and multilingual routes. Use Laya source commit
`fa9a2a7070b1789912a49ae24603bbfb1a78b001` and checkpoint
`cklxx/laya-browser@adf912be85ff9221ee171551778456b133c1af75/v10s/`.
The checkpoint uses format v3; the browser worker applies that format
conversion and then calls the pinned native Laya prediction method. Do not
copy the reference server's lossy string slices, option cap, or chunking.

Run one supervised child process per profile below the existing HTTP parent.
The parent remains responsible for bearer authentication, selector handling,
and the public response. It sends complete request JSON to a worker over
private local IPC. Workers load only their selected checkpoint and expose no
network listener. The API key never enters a worker's environment, arguments,
or IPC payload. This keeps the external endpoint and auth contract unchanged
while making each model's resident memory independently releasable.

Start workers only when selected. Each worker remains alive during inference
and until 600 seconds after the last request completes. Do not terminate a
worker with an in-flight request. On expiry, terminate and reap only that
worker; keep all model files on disk. A later request starts a clean worker.
Do not add LRU/count-based eviction, memory caps, CPU caps, or thread caps.
Health may report loaded profile, worker state, process identifier, and idle
deadline, but must not load a model, invoke inference, or refresh the timer.

Serialize spawn, in-flight accounting, expiry, and process-handle changes with
one lifecycle lock. A request that registers before expiry cancels the pending
exit; if expiry wins, reap the old idle process before starting a new one.
Worker errors cross IPC as structured data and use the existing API error
mapping. A worker failure clears only that profile and leaves the HTTP parent
and other workers available.

## Browser request construction

Use the existing `POST /v1/systemone` API with the explicit browser selector.
The `state` text contains the current page URL, title, readable
non-interactive text, and action history. For the Books to Scrape category
task, `questions.click_target.criteria` contains every category link observed
in that snapshot, with a uniform visible label and role description. The
natural-language goal belongs in the fixed click-target instructions. Do not
use the goal or expected destination to filter candidates. Do not send a
correct ref, target URL, or gold label to the model.

Map opaque choice keys to current snapshot refs and expected URLs only in an
offline scoring manifest. Click only the key the model returned, using its
ref from that same snapshot. Count success only when a separate URL read
matches the preregistered destination exactly. Invalid answers, stale refs,
failed clicks, and wrong URLs remain failures; never substitute the gold ref,
retry a case, or replace a failed case.

The browser profile keeps checkpoint-native context defaults: `max_len=1024`
and `head_max_len_train=768` when request overrides are absent. Record the
actual requested and effective values and native truncation/usage data. The
base profile comparison used explicit `max_len=8192` and
`head_max_len=8192`. The harness adds no context, character, candidate, output,
deadline, or retry limit and does not silently trim state or descriptions.

## Prompt selection and evaluation

Register pilot goals Religion, Nonfiction, and Music separately from the ten
held-out goals: Travel, Mystery, Historical Fiction, Sequential Art, Classics,
Philosophy, Romance, Women's Fiction, Fiction, and Children's. Register each
expected URL from the observed page before making requests. Tune only on pilot
attempts. Freeze the prompt, serializer, candidate builder, and held-out
manifest before the final calls. Preserve every pilot iteration and every
first-attempt response.

Run the same ten held-out cases first through base Laya and then through the
browser profile, strictly serially through the existing server. Refresh the
Camofox snapshot and refs for each task. Keep actual route, model pin, native
usage, truncation, answer, click, URL, and elapsed time per case. Report
label-selection and exact click-plus-URL outcomes separately. Results describe
this site-specific task set only.

## Lifecycle and request pseudocode

```text
handle_http(request):
    authenticate_in_parent(request)
    profile = resolve_supported_profile(request.model)
    with lifecycle_lock:
        worker = registry[profile]
        if worker is absent or exited:
            worker = spawn(profile, pinned_profile_config[profile])
            registry[profile] = worker
        cancel_pending_idle_exit(worker)
        worker.in_flight += 1

    worker_failed = false
    try:
        response = ipc_round_trip(worker, complete_request_json(request))
        return existing_api_response(response)
    except WorkerProcessError as error:
        worker_failed = true
        return existing_api_error(error)
    finally:
        with lifecycle_lock:
            worker.in_flight -= 1
            if worker_failed or worker.exited:
                if registry[profile] == worker:
                    terminate_and_reap(worker)
                    registry[profile] = absent
            elif registry[profile] == worker and worker.in_flight == 0:
                worker.expires_at = monotonic_now() + 600 seconds
                schedule_profile_exit(profile, worker.expires_at)

expire_profile(profile, expected_worker, deadline):
    with lifecycle_lock:
        if registry[profile] != expected_worker:
            return
        if expected_worker.in_flight != 0:
            return
        if monotonic_now() < deadline:
            reschedule(profile, deadline)
            return
        terminate_and_reap(expected_worker)
        registry[profile] = absent

health():
    return parent_and_worker_metadata_without_spawn_or_timer_refresh()
```

The server serializes model inference for the selected comparison. This does
not add a user-facing concurrency or latency limit. Do not include the API key
in worker messages. Keep cold-load time separate from decision latency.

## Verified results and evidence

The frozen ten-case set scored base Laya at 1/10 expected labels and 0/10
exact click-plus-URL outcomes. Browser v10s scored 9/10 expected labels and
8/10 exact outcomes. The `Children's` goal with visible label `Childrens`
remains an observed model-choice failure. Three earlier clicks chose the
correct Fiction link but were misresolved by Camofox; the exact role/name
resolver regression passed 3/3, including an empty accessible name. The
original held-out failures were not rerun.

Pinned-image network-off contract tests passed after Code Simplifier and a
post-pass rerun: worker contracts 10/10, unified router/readiness 15/15, and
format-v3 adapter 4/4. Production checks rejected an invalid bearer token,
loaded all three profiles into distinct workers, and returned an offered
choice from the complete 51-option menu. The browser profile used its native
1024/768 defaults.

Natural 600-second expiry was observed independently for all three workers.
After all workers exited with no in-flight requests, the existing HTTP service
remained active. Cgroup current moved from 177,356,800 bytes at empty startup
to 6,547,693,568 bytes with all three workers loaded, then to 182,960,128
bytes after expiry. Parent RSS returned to its 258,809,856-byte pre-load
baseline. A post-expiry browser request spawned a fresh worker, preserved all
51 options, returned an offered key without truncation at 1024/768, and
reported 9.607 seconds of cold worker load and 12.025 seconds end-to-end.
This is a single reload smoke, not a latency distribution.

The detailed deployment and per-case measurements remain in the private
operator workspace; this public copy omits hostnames, private addresses,
credentials, and local secret paths.
