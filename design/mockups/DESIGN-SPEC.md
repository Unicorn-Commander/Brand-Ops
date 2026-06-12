# Brand-Ops GUI v2 — "The brand is the interface"

Design spec for the full redesign. The mockups in this directory are the
visual contract; this document is the written contract. Together they are
sufficient for an implementing agent to build the real thing in `web/`.

Decided with Aaron 2026-06-11/12: **fully brand-adaptive** canvas ·
**full-app scope** · chrome = **dark** ("Obsidian", Brand-Ops' own product
brand with the prism signature), white-label-ready · **SaaS framing**: the
workspace (Magic Unicorn) is a tenant using the product the way a customer
would; its brands are content, never the app's identity. Brands can be
marked **Public** → read-only showcase portal.

---

## 0. The one idea

Every other tool in this market shows brands as rows inside the tool's own
skin. Brand-Ops holds each brand as machine-readable truth (tokens, fonts,
rules, voice) — so the app **re-skins itself to whatever brand you are
working on**. Entering Center Deep turns the workspace deep-ocean navy with
its own type; entering Majik's turns it purple-on-black. The portfolio level
wears the *chrome brand* (Brand-Ops' own dark "Obsidian" identity with
the prism signature — the white-label slot).

Everything else serves that idea:

- **Obsidian ↔ brand scene change.** Portfolio/Library/Standards live on
  Brand-Ops' own deep-space chrome; brand spaces are full immersion. Covers
  glow against the dark like lit screens in a studio — each casts its own
  brand-colored light (per-cover `--glowc` shadow).
- **The room is alive.** An omnibar (the Director's command line) and a live
  ops ticker sit at the top of the portfolio: agents visibly working,
  language-first command. Aura rings, HUD ticks, and mono readouts give
  hero marks an instrument-panel presence — restrained, never kitsch.
- **Neutral mats.** Assets are always judged on neutral fields (`--mat-light`
  / `--mat-dark`), never on tinted surrounds — color judgment stays honest
  in both environments.
- **Agents are present, not implied.** Stewards/Iris have presence chips,
  avatars, working states, and visible plans. Generation is a theater, not a
  spinner.
- **Every number traces to a rule.** Grades, compliance, coverage — each one
  shows its evidence (hue-arc pin, swatch pairs, rule IDs) and carries its
  own fix action.

## 1. Information architecture

```
PORTFOLIO (chrome env, "/")            ← the shelf: brand covers + architecture tree
│  ├─ Library  ("/library")            ← cross-brand vault (chrome env, mats)
│  ├─ Standards ("/standards")         ← grade board + report cards (chrome env)
│  ├─ Crew     ("/crew")               ← agents as staff: roster, orders, autonomy, doors
│  └─ New brand ("/brands/new")        ← 3 modes: extend house / from brand / from nothing
│
└─ BRAND SPACE ("/b/:slug/…", brand env — full re-skin)
   ├─ Overview  (HQ: hero, color, type, mark preview, kit, voice, team, activity)
   ├─ Mark      (anatomy: callouts, construction, variants, do/don't, lineage)
   ├─ Book      (living brand book: cover, TOC, plates; PDF export; portal share)
   ├─ Assets    (brand-scoped library, same components as the vault)
   ├─ Studio    (conversation + staging wall, scoped to this brand's steward)
   └─ Grade     (this brand's report card — same components as Standards)
```

Replaces the current flat nav (Brands/Studio/Library/Templates/Workflows/
Activity). **Templates** fold into Studio (the compositor is a backend
choice, not a destination). **Workflows** fold into Studio (agent runs are
conversation plans). **Activity** folds into HQ (per-brand) + Standards
(cross-brand); a global feed stays reachable via ⌘K.

Global Studio (no brand): talks to Iris (Director); she routes to stewards.

## 1.5 Agent citizenship (agents are first-class users)

Six commitments, visible in the UI everywhere:

1. **Presence** — agents show up as people do: presence dots, "composing
   run #214" states, the portfolio duty band (avatar cluster + what each is
   doing), steward chips in every brand topbar.
2. **Standing orders** — every agent carries editable orders ("keep kit
   full · flag drift >7% · propose, never publish") shown on its card in
   Crew and in the brand HQ team section. The agent re-reads them each run.
3. **The autonomy dial** — per-agent: `propose-only` / `auto with review` /
   `auto in budget`. Enforced at the lifecycle gate; changes logged. Shown
   as a chip wherever the agent acts (Crew, Studio composer).
4. **The approvals queue** — agents propose, the human decides. "Your
   sign-off · N" panel on the command deck; the human is the rarest, most
   protected resource in the loop (the intake chart marks the ONE human
   gate explicitly).
5. **Doors parity** — GUI (human) / MCP (agents) / API (services) are equal
   entrances to the same engine and the same gate. The Crew "Doors" panel
   shows non-human users AS users: calls today, top tools, live traffic
   feed with verdicts.
6. **Signed work** — every artifact carries actor, model, refs, cost
   (provenance rail, ticker, traffic feed). House rules HR-01..04 are the
   contract: propose-don't-publish, universal gate, hard budgets, signed
   work.

## 2. Token architecture (the heart of it)

Two CSS var layers, defined in `assets/system.css` of the mockups and to be
ported into Tailwind config + a runtime setter:

### Chrome tokens `--c-*` (the white-label slot) — "Obsidian"
Deep-space black (`#07070b`) + warm-white ink + a faint multi-hue nebula on
the body bg + **the prism**: an iridescent diffraction gradient
(`--prism`) that is Brand-Ops' own signature — the tool that holds every
brand's colors owns the whole spectrum. The prism appears ONLY as hairline
accents: wordmark underline, active-nav underline, omnibar edge ring, one
hero word (`.prism-text`). Fonts: `Instrument Sans` (display+body),
`Newsreader` italic (annotations), `JetBrains Mono` (data). Status:
`--c-go/warn/stop` (bright, dark-tuned).

**SaaS framing:** the topbar reads `Brand-Ops` (app brand, prism underline)
with a **workspace chip** on the right (`▦ Magic Unicorn ▾`) — the tenant.
Never "Brand-Ops by Magic Unicorn": MU is a customer of its own product.

**White-label:** chrome identity = one config object (name, mark, palette,
prism on/off, fonts) sourced from a brand record (`CHROME_BRAND=<slug>`).
An agency points it at their record → chrome + book portals wear their
identity. A `light` chrome variant is a later toggle; dark is canonical.

### Brand tokens `--b-*` (the active brand)
Set on `body[data-env="brand"]` when inside `/b/:slug`:
`--b-bg, --b-bg-glow1/2, --b-ink, --b-ink-2/3, --b-hairline, --b-primary,
--b-accent, --b-font-display, --b-font-body`.

Derivation from brand.yaml (all exist today):
- `bg` ← `visual.colors.background` · `ink` ← `foreground`
- `glow1` ← primary @ ~26–45% alpha · `glow2` ← accent @ ~12–25% alpha
  (radial fields, see each mockup's body style)
- `ink-2/3` ← ink @ 64% / 44% · `hairline` ← a light tint of primary @ ~13%
- fonts ← `visual.typography.display_font` / `primary_font`

**Font loading:** self-host via `@fontsource-variable/*` for the known
family set (Inter, Space Grotesk, Plus Jakarta Sans, Fraunces, JetBrains
Mono, Instrument Sans, Newsreader); lazy-import per brand entry, fall back
to Inter while loading (`font-display: swap`). Unknown families render the
fallback and surface a "font not bundled" note in HQ → Typography.

### Mats (constant)
`--mat-light: #f1efe9`, `--mat-dark: #15151a`. Never brand-tinted.

### Texture
Film-grain overlay (inline SVG feTurbulence, ~7% opacity,
`mix-blend-mode: overlay`) — kills flat-gradient sheen. Ambient effects per
brand derive from `style_keywords`, **capped at 2**, whisper-subtle, and
removed under `prefers-reduced-motion` (Center Deep: light rays + bubbles;
Magic Unicorn: aurora glows; default: glows only).

## 3. Type scale & voice of the chrome

- Microlabel: 10.5px / 600 / 0.16em tracking / uppercase — section eyebrows.
- Annotation: Newsreader italic 14.5–15.5px — the editorial "curator voice".
- Page titles: Instrument Sans 700, 38–42px, −0.025em.
- Brand hero wordmark: brand display font, `clamp(56px, 7vw, 88px)`,
  optional ink→accent gradient clip.
- Data (hex, costs, dims, timestamps): JetBrains Mono 10–12px.
- Copy rules: no em-dashes, no marketing-speak (house rules apply to UI copy).

## 4. Motion system

- **Ambient life:** ticker crawl (46s linear, pause on hover), omnibar
  caret blink, aura ring spin (36s/60s counter-rotating), live-dot pulse,
  statline deltas. All `prefers-reduced-motion`-safe.
- **Kill the page fade.** No `AnimatePresence mode="wait"` full-page
  transitions. Content choreographs in: `rise` (14px translate + fade,
  0.5s `cubic-bezier(0.2,0.8,0.2,1)`), staggered `.d1–.d6` (50–70ms steps).
- **Scene change** Portfolio cover → Brand HQ: shared-element morph via the
  **View Transitions API** (`document.startViewTransition` /
  react-router v7 `viewTransition`), cover expands into hero while fonts and
  color crossfade (~360ms). Fallback: simple crossfade.
- **Generation theater:** staging slots shimmer (1.6s linear sweep) →
  crossfade reveal on SSE arrival → compliance stamp animates
  (`stamp-in`: scale 1.6→0.94→1, 0.45s spring, −2° rotation).
- **Presence:** `dot-live` pulse (2.4s), steward "working dots" (1.2s).
- Everything honors `prefers-reduced-motion`.
- Hovers: covers lift −5px + shadow deepen (0.25s); paint fields −3px;
  assets −3px. Transform-only, 60fps.

## 5. Surfaces (one section per mockup)

### 01 Portfolio — the command deck
Header: workspace eyebrow, headline with one prism word ("standing by."),
statline with live deltas. **Omnibar**: Iris orb + a mid-typed command +
blinking caret + prism edge — the Director's command line (same engine as
Studio chat); quick-jump chips beneath. **Live ops ticker**: crawling mono
feed of real agent events (stamped/flagged/proposed/registered), colored
status dots, hover pauses, click-through. **The shelf**: brand covers =
miniature brand environments (own bg/glows/grain, name in the brand's
display font, real mark, paint-strip edge, grade chip, coverage microline,
live-pulse, parentage + **Public** chip where the brand showcases), each
casting brand-colored glow onto the page (`--glowc`). Full-width dashed
"New brand" band (3 modes). Architecture tree with per-node grade.
Cover → HQ is the shared-element morph.

### 02 Brand HQ (mock: Center Deep)
Brand-contextual topbar: `‹ Portfolio` (chrome exit) · brand mark + name ·
Overview/Mark/Book/Assets/Studio/Grade · steward presence chip · ⌘K.
Hero: giant gradient wordmark, tagline, 2-line description, actions (Open
Studio / Brand book / **Discuss the brand**), floating mark w/ glow + 9s
drift wrapped in an **aura ring** (counter-rotating conic halo in brand
colors) + **HUD ticks** and a mono readout ("MARK CD-01 · 1024 α ·
CANONICAL"), gradeboard (letter + score + open issues + report link).
Sections, each with microlabel + annotation + a **"Discuss ↗"** button that
deep-links Studio with that section as context:
- **Color** — paint wall: generous fields (not boxes), token path + contrast
  + AA/AAA in-field, name + hex, hover copy-hex.
- **Typography** — foundry specimens: 124px "Aa", alphabet line, weight ramp
  speaking a brand word ("Depth"); body+mono samples.
- **The mark** — light mat / dark mat / clear-space diagram (dashed bound,
  1× labels) + "Full anatomy →".
- **The kit** — 6 type cells: covered (thumb + approved count) vs missing
  (dashed, per-cell Generate); "Generate the N missing" + honest cost note.
- **Voice** — tone chips, struck-through banned terms, on-voice vs
  off-voice sample pair (off marked "caught at publish").
- **Team** — Steward card (mark avatar, presence, what it's doing), Iris
  card, specialists chips w/ status dots.
- **Recent** — stamped activity rows (pass/flagged chips, swatch-pair
  evidence inline, mono timestamps).

### 03 Mark anatomy (mock: Meeting-Ops Lt Col)
Construction sheet: faint grid + dashed construction circles; mark at
~400px; 6 gold callouts with leader lines + dots naming every decision
(beret, insignia, outline, horn, role prop, mane). Row: clear-space panel ·
min-size ladder (96/48/24 ✓, 14 ✗ with reason) · **lineage** (ref → explored
concept → ratified production, real catalog provenance). Approved-uses row:
primary on brand bg / on light mat / monochrome / ceremonial seal ("hero
use only" in accent). Do/don't tiles: correct · stretched (slash) ·
recolored (slash) · busy background (slash), each with ✓/✗ tick + rule text.
Actions: "Ask why she looks this way" (Studio), Download set, Commission
variant (Studio pre-loaded with mark + refs, invariants locked).

### 04 Studio — generation theater (mock: Majik's Online)
Two panes. **Conversation** (440px): steward header w/ working state;
user ↔ steward bubbles where the steward *argues* about the brand (CMYK
drift, hue-arc reasoning) — discussion is a first-class outcome, not just
generation; embedded **plan card** = the agent's real DAG (rows with live
status: stamped/flagged/composing/queued + cost); compliance memos as
centered dashed chips ("game-changer" replaced); composer + context chips
(refs, model, fine-tune). **Staging wall**: run meta (run #, $ metered,
streaming pulse); 2×2 variant slots — revealed+stamped (stamp top-right on
dark backing), revealed+flagged (offending swatch + % + Send to Refine /
Archive), composing (shimmer + progress + model/refs line), queued (dashed,
reason). **Try it on**: chosen direction composited live into phone /
OG-link preview (with title+description) / square tile. Footer rule:
"Nothing reaches the Library without a stamp."

### 05 New brand — the intake run (descriptive process chart)
Header carries the live run chip (`intake #31 · running 2m 41s`) and the
promise: "your only required click is Stage 4." Modes row: **Extend the
house** / **From a brand** / **From nothing** (active).

**The pipeline = the intake agent's REAL plan, streamed.** Six stage cards
in a **serpentine assembly-line** (row 1 flows →, the line drops at stage 3,
row 2 flows ← back: 1→2→3 ⤵ 4→5→6), joined by REAL drawn arrows whose state
tells the story — solid green where the run has traveled, a pulsing violet
arrow into the next transition (tagged "then you" into the human gate),
dashed ahead. Each card carrying:
- stage number + name + status (`done · 12s` / glowing `drafting` with
  working dots / `your move` / `queued`),
- **owner avatars** (You × Iris / Iris → research specialist + Brigade /
  new steward + asset_pack + compositor),
- **concrete action bullets** that check off live (e.g. Research: "17
  competitors crawled · cluster found 14/17 clinical blue · name check
  tidepoollabs.com free · pre-flight AA/CMYK/trademark clean"),
- **artifact chips** linking to records produced (brief.json ✓, dossier.md
  ✓, competitor-matrix ✓, brand.yaml ×3 ✓, steward identity, 6 assets,
  book v1),
- footer: wall time + metered cost per stage.
Stage 4 (Ratify) renders dashed with a `human gate` tag — the single human
click; everything downstream is delegated, gated, logged. Stage 5 spells
out steward spawn (avatar from house refs, Brigade registration, memory
init, standing orders) + the 6-type kit run; Stage 6 = book draft,
optional Public showcase, the watch begins, doors open.

Below: "Stage 3, live — Iris proposes" = the Iris rail (research findings,
a defended recommendation) + three **direction cards** as live token
previews (own display font, palette strip, type pairing, rationale, fit
bar, Ratify/Remix). Backend exists: `POST /brands/intake`.

### 06 Library — the vault
Chrome env. Header + "drop files anywhere" hint (no giant upload panel —
drag-anywhere overlay stages to the active brand through the gate).
Filter chips: brand (with gradient swatch dots), lifecycle (Production /
In review / Concepts with counts), type, backend. Groups by asset type with
editorial headers (`Logos · 8 · 7 approved` + hairline). Asset cards: mat
(light/dark by asset tone), brand dot, name, lifecycle chip (approved
green / review amber / concept = dashed chip + hatched mat). **Detail
rail** (sticky): preview on mat, full provenance KV (lifecycle, compliance,
made-by agent+model, references, cost+billing path, dims+alpha), **history
strip** (ref → explored → ratified thumbnails), actions (Download /
Anatomy / Variants). **Bulk bar** floats bottom-center on selection
(Re-tag / Send to review / Approve all).

### 07 Brand book (mock: Magic Unicorn)
Brand env. Toolrow: "living document · v4" + auto-maintained chip + Share
portal link + Export PDF. **Plates** (print-grade pages): COVER (giant
two-line wordmark w/ gradient word, mark, tagline/city/ratified-date
footline, № folio) · TOC (6 numbered cells) · spreads with left rule column
(numbered rules R-01… in mono) + right full-bleed stage: mark plate
(main + on-dark/on-light/mono strip), color plate (full-bleed paint stack
w/ PMS + token paths), voice plate (sounds-like-us vs caught-by-the-gate
columns), applications plate (OG + square rendered from tokens — "correct
by construction"). Print CSS exists in the current app; keep. Portal link =
scoped read-only share; white-label footer ("Prepared by <chrome brand>").

### 08 Standards / Grade
Chrome env. Board: per-brand rows — cover tile, grade letter (semantic
color), 5 dimension micro-bars (color/type/mark/coverage/voice; amber/red
under thresholds), trend sparkline, open issues, last audit; selected row
ink-edged. **Report card**: ring (letter + /100), biggest-lever callout,
"Fix the biggest lever" CTA (deep-links the steward action that moves the
grade most). Dimensions panel: rows w/ sub-captions naming the rule source
+ colored bars + values. **Issues**: stamp + title + found-date; the
**hue-arc strip** (gradient through the hue range, bracketed allowed
window, red pin at the offender w/ hex + filename) — generated from the
same gradient rule the gate runs (`compliance/evaluator.py` rule d);
swatch pair "found → nearest allowed"; actions (Send to Refine — re-tint /
Allow as exception / Archive). Coverage issue: drag quantified (−14),
one-run fix CTA w/ cost. **Methodology** panel: weights (color 25 · type
15 · mark 20 · coverage 25 · voice 15), recompute triggers, honest gaps
("mark usage measures min-size only until clear-space telemetry"),
exceptions expire.

### 09 Crew — agents as staff
Headline: "Staff, not features." Statline: on duty / actions this week /
approval rate / spend. Sections:
- **Orchestration** — Iris's card: model (gateway, metered), scope ("all
  brands · spawn stewards · no publish"), weekly budget cap + used, Brigade
  memory, standing orders (§), autonomy chip, live status; right column =
  recent decisions incl. "declined a request" and "escalated to you"
  (agents with judgment, on the record).
- **Brand stewards** — 6 cards: brand-mark avatar + presence, current
  activity, standing orders, stats strip (actions/wk · approved% · spend),
  autonomy chip, "Talk →".
- **Specialists** — 6 compact cards (model + calls/wk).
- **Doors** — Console (you, awaiting sign-off count) / **MCP** (highlighted;
  21 tools, calls today, clients, top tools) / API (service tokens, reads),
  plus a live **traffic feed** (actor · door · call · verdict · time).
- **House rules** — HR-01 propose-don't-publish · HR-02 universal gate ·
  HR-03 hard budgets · HR-04 everything signs its work.
Reads from: agent registry + Brigade manifests + gateway spend + MCP/API
access logs. Standing orders = editable text on the agent record.

Grade v1 computes from EXISTING data: color/voice from compliance results,
coverage from the kit rule, type/mark conservative until their checks ship.
No new backend required to launch the surface honestly.

## 6. Component inventory (build once, reuse)

BrandCover · Omnibar · OpsTicker · DutyBand · SignoffQueue · StageCard/
PipelineChart · AutonomyChip · StandingOrders · DoorCard · TrafficFeed ·
AuraRing · HudFrame · WorkspaceChip ·
PrismLine/PrismText · EnvProvider (sets `--b-*` + loads fonts) · Mat · Stamp
(pass/flag/fail, `fresh` animation) · GradeLetter/GradeRing · PaintField/
PaintWall/PaintStack · Specimen · ClearSpaceDiagram · MinSizeLadder ·
CalloutSheet · KitCell · AgentCard/PresenceChip/WorkingDots · PlanCard ·
VariantSlot (revealed/flagged/composing/queued) · TryOnStrip (Phone/OG/
Square) · AssetCard + DetailRail + HistoryStrip · BulkBar · BoardRow ·
DimensionRow · HueArc · IssueCard · Plate/Spread (book) · Tree ·
StepsRail · DirectionCard · MicroLabel/Annotation/SpecNote.

## 7. Implementation notes for the agent

- Keep: Vite/React/TS/Tailwind, React Query, react-router; reuse api client.
  Replace `theme.ts` accent-pair with the full `--c-*`/`--b-*` system
  (section 2). Tailwind maps colors to the vars.
- Routes: introduce `/b/:slug/*` layout that mounts EnvProvider; portfolio-
  level routes keep chrome env. ⌘K palette persists everywhere; add brand
  jump + actions (new brand, open book, review queue).
- The current GUI's real components worth carrying: CommandPalette, Mockups
  (phone/OG/card → TryOnStrip), CoverageRing (→ GradeRing), ErrorBoundary
  pattern, print CSS for the book.
- SSE: staging wall consumes per-item generation events (job stream);
  plan-card statuses update from the same stream. (Backend has async
  agents + jobs; wire-level SSE is additive.)
- **Fix regardless of redesign** (bugs found in the live app 2026-06-11):
  1. Guidelines "Lockups" renders 5/8 dead images — catalog sidecars point
     at quarantined files; filter or re-stamp.
  2. Brand cards/heroes show literal "unknown" (`3/6 types · unknown`) —
     missing-field fallback leaking to UI.
  3. Workflows page renders a header then nothing below the panel.
  4. Coverage rings show bare numbers with no label/legend.
- Accessibility: AA contrast minimum on all chrome combinations; brand
  environments inherit the brand's declared fg/bg (already AA in records);
  reduced-motion covered; all stamps/status carry text, never color-only.
- Performance: transform/opacity-only animation; grain is a static tiled
  SVG; ambient effects are 2 fixed elements max; fonts swap.

## 8. Files in this directory

```
01-portfolio.html   the shelf + architecture + new-brand band     (chrome)
02-brand-hq.html    Center Deep immersion HQ                      (brand)
03-mark.html        Meeting-Ops Lt Col anatomy sheet              (brand)
04-studio.html      Majik's generation theater                    (brand)
05-new-brand.html   3 modes + Tidepool Labs directions            (chrome)
06-library.html     the vault + provenance rail                   (chrome)
07-book.html        Magic Unicorn living brand book               (brand)
08-grade.html       standards board + report card + hue-arc       (chrome)
assets/system.css   the two-layer token system + primitives
assets/img/         real marks (copied from brand-kits + server volume)
```

Serve locally: `python3 -m http.server 8123` from this directory.
`SPEC ·` dashed notes inside the pages are inline contracts for the
implementing agent — they are part of this spec.
