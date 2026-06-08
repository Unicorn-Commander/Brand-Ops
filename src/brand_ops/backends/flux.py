"""Local FLUX.1 [dev] image backend, driving a ComfyUI server over its HTTP API.

A direct adapter onto a self-hosted ComfyUI instance running FLUX.1 [dev]. It
satisfies the ImageBackend protocol so the engine, CLI, MCP server, REST API,
and autonomy agents all drive it the same way as any cloud backend (gemini,
openai, recraft). There is no wrapper around a CLI script: we speak ComfyUI's
documented HTTP API directly.

ComfyUI HTTP API contract used here (confirmed against the current API):
  * POST {url}/prompt    -> enqueue a workflow, body {"prompt": <graph>,
                            "client_id": <uuid>}; returns {"prompt_id": ...}.
  * GET  {url}/history/{prompt_id} -> execution record; when finished the body is
                            {<prompt_id>: {"outputs": {<node_id>: {"images":
                            [{"filename","subfolder","type"}, ...]}}, "status": ...}}.
  * GET  {url}/view?filename=&subfolder=&type=output -> raw image bytes (PNG).
  * GET  {url}/system_stats -> 200 with a JSON health/stats payload; used by health().

The "prompt" the API wants is the *API format* of a workflow: a flat dict keyed
by node id, where each value is {"class_type": ..., "inputs": {...}} and node
references are [<node_id>, <output_index>] pairs. This is NOT the UI export
format (no "pos"/"widgets_values"); it is what ComfyUI's "Save (API Format)"
produces and what /prompt validates.

httpx / json / time are imported lazily inside the methods that need them, so
`import brand_ops.backends.flux` works without the `backends` extra installed
(health() can still run and report False on any connection error).

FLUX has no native alpha channel, so transparency is handled as a documented
limitation (see _TRANSPARENCY_NOTE), never by faking a white background.
"""
from __future__ import annotations

from .base import (
    BackendNotConfigured,
    GenRequest,
    GenResult,
)

# --- defaults ----------------------------------------------------------------

# Model weight file ComfyUI loads for FLUX.1 [dev]. settings.flux_model is the
# logical name we report in GenResult.model; the workflow loads this file from
# ComfyUI's models/diffusion_models (a.k.a. unet) directory.
_DEFAULT_UNET = "flux1-dev.safetensors"

# Text encoders for the DualCLIPLoader in "flux" mode, and the FLUX VAE.
_CLIP_T5 = "t5xxl_fp16.safetensors"
_CLIP_L = "clip_l.safetensors"
_VAE = "ae.safetensors"

# Sampling defaults for FLUX.1 [dev]. FLUX dev is guidance-distilled: KSampler
# cfg is kept at 1.0 (CFG is a no-op for the distilled model) and the real
# guidance is carried by the FluxGuidance node (~3.5).
_DEFAULT_STEPS = 20
_DEFAULT_GUIDANCE = 3.5
_DEFAULT_SAMPLER = "euler"
_DEFAULT_SCHEDULER = "simple"
_DEFAULT_SEED = 0

# Polling budget for /history. FLUX.1 [dev] at 1024x1024/20 steps is typically
# tens of seconds on a 3090-class GPU; allow generous headroom for a busy queue.
_POLL_TIMEOUT_S = 600.0
_POLL_INTERVAL_S = 1.5
_HEALTH_TIMEOUT_S = 3.0
_ENQUEUE_TIMEOUT_S = 30.0
_VIEW_TIMEOUT_S = 120.0

_TRANSPARENCY_NOTE = (
    "FLUX.1 [dev] has no native alpha channel; output is opaque RGB PNG. "
    "TODO: add an optional background-removal post step (e.g. rembg / a "
    "matting model) to honor req.transparent instead of faking a white bg."
)

_REFERENCE_NOTE = (
    "reference_images ignored: this base FLUX.1 [dev] text-to-image graph has "
    "no IP-Adapter / Redux / ControlNet node wired in. TODO: branch the "
    "workflow to load a Flux IP-Adapter (or Redux StyleModelApply) and feed "
    "the references when those custom nodes are available on the ComfyUI server."
)


# --- workflow template -------------------------------------------------------
#
# Canonical FLUX.1 [dev] text-to-image graph in ComfyUI API format. Node ids are
# strings (ComfyUI uses string keys in the API format). _build_graph() deep-copies
# this and fills the prompt, dimensions, seed, steps and guidance.
#
# Graph wiring:
#   "10" UNETLoader        -> model
#   "11" DualCLIPLoader    -> clip (t5xxl + clip_l, type "flux")
#   "12" VAELoader         -> vae
#   "6"  CLIPTextEncode    -> positive conditioning (the prompt goes here)
#   "7"  CLIPTextEncode    -> negative conditioning (empty; FLUX needs no neg)
#   "22" FluxGuidance      -> applies guidance to the positive conditioning
#   "5"  EmptySD3LatentImage -> latent canvas (width x height)
#   "3"  KSampler          -> denoise (cfg 1.0, FluxGuidance carries guidance)
#   "8"  VAEDecode         -> image
#   "9"  SaveImage         -> writes to output/ ; history exposes its "images"
#
_WORKFLOW_TEMPLATE: dict = {
    "10": {
        "class_type": "UNETLoader",
        "inputs": {
            "unet_name": _DEFAULT_UNET,
            "weight_dtype": "default",
        },
    },
    "11": {
        "class_type": "DualCLIPLoader",
        "inputs": {
            "clip_name1": _CLIP_T5,
            "clip_name2": _CLIP_L,
            "type": "flux",
        },
    },
    "12": {
        "class_type": "VAELoader",
        "inputs": {
            "vae_name": _VAE,
        },
    },
    "6": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "",  # filled with req.prompt
            "clip": ["11", 0],
        },
    },
    "7": {
        "class_type": "CLIPTextEncode",
        "inputs": {
            "text": "",  # FLUX.1 [dev] needs no negative prompt
            "clip": ["11", 0],
        },
    },
    "22": {
        "class_type": "FluxGuidance",
        "inputs": {
            "guidance": _DEFAULT_GUIDANCE,
            "conditioning": ["6", 0],
        },
    },
    "5": {
        "class_type": "EmptySD3LatentImage",
        "inputs": {
            "width": 1024,
            "height": 1024,
            "batch_size": 1,
        },
    },
    "3": {
        "class_type": "KSampler",
        "inputs": {
            "seed": _DEFAULT_SEED,
            "steps": _DEFAULT_STEPS,
            "cfg": 1.0,
            "sampler_name": _DEFAULT_SAMPLER,
            "scheduler": _DEFAULT_SCHEDULER,
            "denoise": 1.0,
            "model": ["10", 0],
            "positive": ["22", 0],
            "negative": ["7", 0],
            "latent_image": ["5", 0],
        },
    },
    "8": {
        "class_type": "VAEDecode",
        "inputs": {
            "samples": ["3", 0],
            "vae": ["12", 0],
        },
    },
    "9": {
        "class_type": "SaveImage",
        "inputs": {
            "filename_prefix": "brand_ops_flux",
            "images": ["8", 0],
        },
    },
}


class Backend:
    """Local FLUX.1 [dev] backend adapter (ComfyUI HTTP API)."""

    name = "flux"

    def __init__(self, settings) -> None:
        self._settings = settings

    # --- health --------------------------------------------------------------

    def health(self) -> bool:
        """True iff ComfyUI answers GET /system_stats with HTTP 200.

        Never raises: a missing httpx dependency, any connection error,
        timeout, or non-200 all yield False so callers can treat the backend as
        simply unavailable.
        """
        url = self._base_url()
        try:
            import httpx  # lazy: absent dep => unavailable, not a crash

            with httpx.Client(timeout=_HEALTH_TIMEOUT_S) as client:
                resp = client.get(f"{url}/system_stats")
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - health must never raise
            return False

    # --- generate ------------------------------------------------------------

    def generate(self, req: GenRequest) -> GenResult:
        import json  # noqa: F401 - kept for symmetry / explicit lazy import policy
        import time
        import uuid

        import httpx

        url = self._base_url()
        client_id = uuid.uuid4().hex

        steps = int(req.extra.get("steps") or _DEFAULT_STEPS)
        guidance = float(req.extra.get("guidance") or _DEFAULT_GUIDANCE)
        base_seed = int(req.extra.get("seed", _DEFAULT_SEED))
        count = max(1, req.count)

        notes: list[str] = []
        if req.reference_images:
            # See _REFERENCE_NOTE: the base text-to-image graph has no IP-Adapter
            # node, so references are not applied. We proceed without them rather
            # than fail, and surface the limitation in meta.
            notes.append(_REFERENCE_NOTE)
        if req.transparent:
            notes.append(_TRANSPARENCY_NOTE)

        images: list[bytes] = []
        seeds: list[int] = []
        try:
            with httpx.Client(base_url=url) as client:
                for i in range(count):
                    seed = base_seed + i  # vary seed per image for variety
                    seeds.append(seed)
                    graph = _build_graph(
                        prompt=req.prompt,
                        width=req.width,
                        height=req.height,
                        seed=seed,
                        steps=steps,
                        guidance=guidance,
                    )
                    prompt_id = self._enqueue(client, graph, client_id)
                    outputs = self._await_history(client, prompt_id, time)
                    images.extend(self._collect_images(client, outputs))
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise BackendNotConfigured(
                f"ComfyUI not reachable at {url}"
            ) from exc

        meta: dict = {
            "comfyui_url": url,
            "unet": _DEFAULT_UNET,
            "steps": steps,
            "guidance": guidance,
            "sampler": _DEFAULT_SAMPLER,
            "scheduler": _DEFAULT_SCHEDULER,
            "seeds": seeds,
            "width": req.width,
            "height": req.height,
        }
        if notes:
            meta["notes"] = notes

        return GenResult(
            images=images,
            mime="image/png",
            backend=self.name,
            model=self._settings.flux_model,
            cost_cents=0.0,  # local GPU: no per-image API cost
            meta=meta,
        )

    # --- internals -----------------------------------------------------------

    def _base_url(self) -> str:
        return (self._settings.comfyui_url or "").rstrip("/")

    def _enqueue(self, client, graph: dict, client_id: str) -> str:
        """POST the graph to /prompt and return the prompt_id.

        Raises BackendNotConfigured on connection failure (handled by caller's
        except) and BackendError on an HTTP error or a malformed response.
        """
        from .base import BackendError

        resp = client.post(
            "/prompt",
            json={"prompt": graph, "client_id": client_id},
            timeout=_ENQUEUE_TIMEOUT_S,
        )
        if resp.status_code != 200:
            # ComfyUI returns 400 with a node_errors body when the graph is
            # invalid (e.g. a model file is missing on the server).
            detail = resp.text[:500]
            raise BackendError(
                f"ComfyUI rejected the workflow (HTTP {resp.status_code}): {detail}"
            )
        data = resp.json()
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise BackendError(f"ComfyUI /prompt returned no prompt_id: {data!r}")
        return prompt_id

    def _await_history(self, client, prompt_id: str, time_mod) -> dict:
        """Poll /history/{prompt_id} until outputs appear; return the outputs dict.

        Raises BackendError on timeout or a reported execution failure.
        """
        from .base import BackendError

        deadline = time_mod.monotonic() + _POLL_TIMEOUT_S
        while True:
            resp = client.get(f"/history/{prompt_id}", timeout=_HEALTH_TIMEOUT_S * 2)
            if resp.status_code == 200:
                history = resp.json()
                record = history.get(prompt_id)
                if record:
                    status = record.get("status") or {}
                    if status.get("status_str") == "error":
                        raise BackendError(
                            f"ComfyUI reported an execution error for {prompt_id}: "
                            f"{status!r}"
                        )
                    outputs = record.get("outputs")
                    if outputs:
                        return outputs
            if time_mod.monotonic() >= deadline:
                raise BackendError(
                    f"timed out after {_POLL_TIMEOUT_S:.0f}s waiting for ComfyUI "
                    f"to finish prompt {prompt_id}"
                )
            time_mod.sleep(_POLL_INTERVAL_S)

    def _collect_images(self, client, outputs: dict) -> list[bytes]:
        """Fetch every produced image via /view and return raw PNG bytes."""
        from .base import BackendError

        collected: list[bytes] = []
        for node_output in outputs.values():
            for image in node_output.get("images", []):
                # Skip non-output (e.g. "temp" preview) artifacts; SaveImage
                # writes type "output".
                if image.get("type") and image["type"] != "output":
                    continue
                resp = client.get(
                    "/view",
                    params={
                        "filename": image.get("filename", ""),
                        "subfolder": image.get("subfolder", ""),
                        "type": image.get("type", "output"),
                    },
                    timeout=_VIEW_TIMEOUT_S,
                )
                if resp.status_code != 200:
                    raise BackendError(
                        f"ComfyUI /view failed for {image!r} "
                        f"(HTTP {resp.status_code})"
                    )
                collected.append(resp.content)
        if not collected:
            raise BackendError("ComfyUI finished but produced no image outputs")
        return collected


def _build_graph(
    *,
    prompt: str,
    width: int,
    height: int,
    seed: int,
    steps: int,
    guidance: float,
) -> dict:
    """Deep-copy the FLUX template and fill in the per-request fields."""
    import copy

    graph = copy.deepcopy(_WORKFLOW_TEMPLATE)
    graph["6"]["inputs"]["text"] = prompt
    graph["5"]["inputs"]["width"] = int(width)
    graph["5"]["inputs"]["height"] = int(height)
    graph["3"]["inputs"]["seed"] = int(seed)
    graph["3"]["inputs"]["steps"] = int(steps)
    graph["22"]["inputs"]["guidance"] = float(guidance)
    return graph


def build(settings) -> Backend:
    """Factory used by load_backend to construct the flux backend."""
    return Backend(settings)
