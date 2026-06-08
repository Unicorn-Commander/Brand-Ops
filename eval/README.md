# Agent model eval (Qwen vs Claude)

Settles which model each Brand-Ops agent should default to. Four tasks mirror
the real agent roles and run against both models through the same gateway the
agents use (`brand_ops.brigade.client`), with deterministic scoring.

| Task | Agent role | Scored on |
|---|---|---|
| brand_research | BrandResearchAgent | valid JSON profile with the required fields |
| asset_pack | AssetPackAgent | sound plan: valid asset types and the right backend per type |
| prompt_compose | prompt composition | follows the hard copy rules (no em-dash, no marketing-speak) |
| critique | BrandCriticAgent | catches an off-brand asset with issues and a fix |

## Run

One OpenRouter key reaches both models:

```bash
export OPENROUTER_API_KEY=...        # or LLM_API_KEY; add it to ~/.env to persist
uv run python eval/run_eval.py
```

It prints a per-task score table and a recommended default model per agent
(it prefers Qwen when it is within 0.1 of Claude and clears a 0.7 bar, since
Qwen is the free local option).

## Caveat: the local Qwen 3.6

Your production Qwen 3.6 runs on the cluster (midboy1) and is not reachable from
a laptop, so by default this uses an OpenRouter Qwen-class model as a stand-in.
Override the ids, and re-confirm against the exact local model at deploy when the
Ops-Center gateway is reachable:

```bash
QWEN_MODEL=qwen/qwen3-235b-a22b \
CLAUDE_MODEL=anthropic/claude-3.7-sonnet \
LLM_BASE_URL=https://openrouter.ai/api/v1 \
  uv run python eval/run_eval.py
```

Set `LLM_BASE_URL` to the Ops-Center gateway at deploy to test the real local Qwen.
