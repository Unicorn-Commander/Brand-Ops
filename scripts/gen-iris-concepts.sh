#!/usr/bin/env bash
# gen-iris-concepts.sh
#
# Generate THREE Iris concept avatars, one per director-prop option, so the owner
# can pick a face for Iris (the Brand-Ops cross-brand Director / Lt Col). See the
# decision and art brief in docs/go-to-market/iris-identity.md.
#
# WHAT THIS DOES (when YOU, the operator, run it):
#   - loads OPENROUTER_API_KEY from .env if present (the openrouter backend path)
#   - runs the confirmed command three times:
#       uv run brand-ops generate --brand brand-ops --asset avatar \
#         --variant mascot --backend openrouter --topic "<prop direction>"
#   - the three concepts are the three props from the brief:
#       1) jeweler's / designer's loupe held to one eye
#       2) fanned deck of color-swatch cards
#       3) artist's palette
#   - outputs land in brand-kits/brand-ops/generated/avatar/
#
# THIS SPENDS MONEY. Each generate call hits the OpenRouter image model
# (nano-banana 2). Three calls = three images. Nothing here runs until you run it.
#
# IMPORTANT (read appendix A of docs/go-to-market/iris-identity.md):
#   The current `avatar` prompt template in src/brand_ops/prompt_builder.py does
#   NOT interpolate {topic}. Until the one-line template change in that doc is
#   applied, the --topic prop direction below is INERT and all three concepts
#   compose the SAME prompt (the swatch-fan prop baked into the brand's
#   style_keywords). The script still runs and still produces three images today;
#   they will simply all lean on the swatch-fan prop until the {topic} token is
#   added. Once that token is added, these three calls produce three DISTINCT
#   concepts (loupe / swatch fan / palette).
#
# DRY RUN FIRST (recommended): pass --dry-run to compose the prompts and spend
# nothing:
#   scripts/gen-iris-concepts.sh --dry-run
#
# Usage:
#   scripts/gen-iris-concepts.sh            # generate the three concepts (spends)
#   scripts/gen-iris-concepts.sh --dry-run  # compose prompts only, no spend

set -euo pipefail

# Resolve the repo root from this script's location so it runs from anywhere.
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." >/dev/null 2>&1 && pwd)"
cd "${REPO_ROOT}"

DRY_RUN=""
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN="--dry-run"
  echo "[gen-iris-concepts] DRY RUN: composing prompts only, no generation, no spend."
fi

# Load OPENROUTER_API_KEY (and the other OPENROUTER_* settings) from .env if
# present, without clobbering anything already in the environment. We only need
# this for a real run; a dry run does not call the backend.
if [[ -f .env ]]; then
  # shellcheck disable=SC1091
  set -a
  source .env
  set +a
fi

if [[ -z "${DRY_RUN}" ]]; then
  if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
    echo "[gen-iris-concepts] ERROR: OPENROUTER_API_KEY is not set." >&2
    echo "  Set it in .env (or export it) before a real run, or pass --dry-run." >&2
    exit 1
  fi
  echo "[gen-iris-concepts] OPENROUTER_API_KEY found. This run WILL generate 3 images and spend money."
  echo "[gen-iris-concepts] Outputs go to: brand-kits/brand-ops/generated/avatar/"
fi

BRAND="brand-ops"
ASSET="avatar"
VARIANT="mascot"
BACKEND="openrouter"

# The three director-prop concepts. The label is just for the operator's log; the
# topic string is the prop direction that steers each concept (active once the
# {topic} token is added to the avatar template, see the doc).
CONCEPT_LABELS=(
  "1-loupe"
  "2-swatch-fan"
  "3-palette"
)
CONCEPT_TOPICS=(
  "Iris the brand director holds a jeweler's loupe up to one eye, appraising a brand mark; the loupe is drawn large and close to her face so it reads at small sizes."
  "Iris the brand director holds up a fanned deck of color-swatch cards beside her cheek, spread like a hand of cards, in the brand's violet and cyan."
  "Iris the brand director holds up a small artist's thumb-palette beside her face with dabs of the brand's violet and cyan paint."
)

gen_one() {
  local label="$1"
  local topic="$2"
  echo
  echo "=============================================================="
  echo "[gen-iris-concepts] concept ${label}"
  echo "  prop: ${topic}"
  echo "=============================================================="
  # The confirmed command (dry-run verified to compose an on-brand,
  # reference-conditioned Iris prompt). ${DRY_RUN} is empty for a real run.
  uv run brand-ops generate \
    --brand "${BRAND}" \
    --asset "${ASSET}" \
    --variant "${VARIANT}" \
    --backend "${BACKEND}" \
    --topic "${topic}" \
    ${DRY_RUN}
}

for i in "${!CONCEPT_LABELS[@]}"; do
  gen_one "${CONCEPT_LABELS[$i]}" "${CONCEPT_TOPICS[$i]}"
done

echo
echo "[gen-iris-concepts] done."
if [[ -n "${DRY_RUN}" ]]; then
  echo "  (dry run only: nothing generated, nothing spent)"
else
  echo "  Review the three concepts in brand-kits/brand-ops/generated/avatar/"
  echo "  Then: pick one, promote it to brand-kits/brand-ops/imported/avatar/iris-director.png"
  echo "  (mark it approved, like meeting-ops-lead.png), wire Director.avatarUrl"
  echo "  (see docs/go-to-market/iris-identity.md), and generate the"
  echo "  thinking / success / alert expression variants of the chosen concept."
fi
