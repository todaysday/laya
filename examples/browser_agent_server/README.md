# Browser-agent service example

This optional integration adds the pinned `v10s` browser checkpoint to Laya's existing `/v1/systemone` API. The HTTP process supervises one lazily started worker per profile; an idle worker is terminated after 600 seconds while the API process stays available. The integration targets Laya source commit `fa9a2a7070b1789912a49ae24603bbfb1a78b001` and checkpoint `cklxx/laya-browser`, revision `adf912be85ff9221ee171551778456b133c1af75`, subfolder `v10s`.

The source checkpoint remains outside the image and repository. Download the pinned subfolder to a host directory. Its tokenizer configuration already matches this Laya source, so the mount can stay read-only. Recheck this if either checkpoint or source pin changes:

```sh
python - <<'PY'
from huggingface_hub import snapshot_download

snapshot_download(
    repo_id="cklxx/laya-browser",
    revision="adf912be85ff9221ee171551778456b133c1af75",
    allow_patterns=["v10s/**"],
    local_dir="/srv/laya/browser-checkpoint",
)
PY
```

Create the two local configuration files. Put the API key in a host file readable by Docker; Compose mounts it as a secret and Laya's image entrypoint reads it through `LAYA_API_KEY_FILE`. The key stays in the HTTP parent process; profile workers receive inference requests over private local IPC.

```sh
cp examples/browser_agent_server/runtime.env.example examples/browser_agent_server/runtime.env
cp examples/browser_agent_server/compose.env.example examples/browser_agent_server/compose.env
chmod 600 examples/browser_agent_server/runtime.env examples/browser_agent_server/compose.env
docker compose \
  --env-file examples/browser_agent_server/compose.env \
  -f examples/browser_agent_server/compose.example.yml \
  up --build -d
```

Compose binds port `8100` to loopback on the host; put an authenticated private proxy in front if another machine needs access. Send browser decisions to `/v1/systemone` with `model: "laya-browser-v10s"`. The authenticated `/health` response reports each worker state and its idle timer. Production observation confirmed all three workers exited after their natural 600-second idle periods while the HTTP service stayed active; see the playbook for measurements and the post-expiry reload result.

The adapter preserves the full format-v3 page state and every offered choice. The end-to-end prompt, request example, validation results, checkpoint provenance, and measured limits are in [the browser-agent playbook](../../docs/browser-agent-playbook.md). It builds on the upstream [Laya browser-agent guide](https://github.com/NandhaKishorM/laya/blob/fa9a2a7070b1789912a49ae24603bbfb1a78b001/docs/finetune_browser_agent.md) and the pinned [`cklxx/laya-browser` model card](https://huggingface.co/cklxx/laya-browser/blob/adf912be85ff9221ee171551778456b133c1af75/README.md), which marks the checkpoint Apache-2.0. The optional [Camofox exact-reference patch](patches/README.md) is scoped to its v2.4.6 release. No checkpoint weights or test captures are stored in this repository.
