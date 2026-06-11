/**
 * Architecture view — the brand portfolio as a tidy org chart. Reads the same
 * ShowcaseBrand[] the other views use and builds a tree client-side from each
 * brand's `parent`: roots (no parent, or a parent outside the set) are the top
 * nodes, with their sub-brands connected beneath by plain CSS connector lines
 * (no graph library). Arbitrary depth is supported, so a division can itself
 * carry product or client sub-brands. Each node is a compact, brand-accented
 * card linking into the brand; nodes stagger in.
 */
import { Link } from "react-router-dom";
import { motion } from "framer-motion";
import { GitFork } from "lucide-react";
import { CoverageRing } from "../../../components/CoverageRing";
import { AgentAvatar } from "../../../components/AgentAvatar";
import { EmptyState } from "../../../components/EmptyState";
import { staggerContainer, staggerItem } from "../../../lib/motion";
import { resolveAccent, hexToRgbChannels } from "../../../theme";
import type { CSSProperties } from "react";
import type { ShowcaseBrand } from "../data";

/** A brand with its resolved children, the shape the tree renderer walks. */
interface TreeNode {
  sb: ShowcaseBrand;
  children: TreeNode[];
}

/**
 * Build the parent -> children forest. A root is a brand with no parent or a
 * parent not present in the set (so an orphaned sub-brand still surfaces as its
 * own root rather than vanishing). Order within each level follows the incoming
 * order. A brand is attached to at most one parent; a parent cycle cannot form
 * because each node is placed exactly once, the first time it is reached.
 */
function buildForest(brands: ShowcaseBrand[]): TreeNode[] {
  const bySlug = new Map<string, ShowcaseBrand>();
  for (const b of brands) bySlug.set(b.brand.slug, b);

  const nodes = new Map<string, TreeNode>();
  for (const b of brands) nodes.set(b.brand.slug, { sb: b, children: [] });

  const roots: TreeNode[] = [];
  for (const b of brands) {
    const node = nodes.get(b.brand.slug)!;
    const parentSlug = b.brand.parent;
    const parentNode =
      parentSlug && bySlug.has(parentSlug) ? nodes.get(parentSlug) : undefined;
    if (parentNode && parentNode !== node) parentNode.children.push(node);
    else roots.push(node);
  }
  return roots;
}

/**
 * A compact brand card themed in the brand's own accent. Scopes `--accent` /
 * `--accent-secondary` to the card so the CoverageRing, ring, and connector
 * read in that brand's color without touching the global theme. The mark uses
 * AgentAvatar, which falls back to a gradient orb when the brand has no mark.
 */
function BrandNodeCard({ sb }: { sb: ShowcaseBrand }) {
  const accent = resolveAccent(sb.brand.slug);
  const primary = hexToRgbChannels(accent.primary);
  const secondary = hexToRgbChannels(accent.secondary);
  const style: CSSProperties = {};
  if (primary) (style as Record<string, string>)["--accent"] = primary;
  if (secondary) (style as Record<string, string>)["--accent-secondary"] = secondary;

  return (
    <Link
      to={`/brands/${sb.brand.slug}`}
      style={style}
      className="group/node accent-ring relative flex w-56 items-center gap-3 rounded-2xl bg-chrome-raised/60 p-3 shadow-panel transition-transform duration-300 ease-spring hover:-translate-y-0.5 hover:shadow-float"
    >
      <AgentAvatar name={sb.brand.display_name} src={sb.markUrl} size={40} />
      <div className="min-w-0 flex-1">
        <p className="truncate font-display text-sm font-semibold tracking-tight text-text group-hover/node:text-accent">
          {sb.brand.display_name}
        </p>
        <p className="truncate text-xs tabular-nums text-text-faint">
          {sb.stats.total} asset{sb.stats.total === 1 ? "" : "s"}
        </p>
      </div>
      <CoverageRing pct={sb.stats.coveragePct} size={32} stroke={3.5} />
    </Link>
  );
}

/**
 * One subtree, drawn top-down. The card sits on top; when it has children, a
 * short vertical spine drops to a row of children, each joined by its own
 * vertical connector (the horizontal rail between siblings is drawn by the
 * children row's top border). Lines are CSS borders only.
 */
function TreeBranch({ node }: { node: TreeNode }) {
  const kids = node.children;
  return (
    <motion.li variants={staggerItem} className="flex flex-col items-center">
      <BrandNodeCard sb={node.sb} />

      {kids.length > 0 && (
        <>
          {/* The spine from the parent down to the children rail. */}
          <span aria-hidden className="h-5 w-px bg-white/20" />
          <ul className="flex items-start justify-center">
            {kids.map((child, i) => {
              const isFirst = i === 0;
              const isLast = i === kids.length - 1;
              const only = kids.length === 1;
              return (
                <li
                  key={child.sb.brand.slug}
                  className="relative flex flex-col items-center px-3"
                >
                  {/* The rail: a top border spanning siblings. The first and
                      last children round their outer half away so the rail
                      reads as one line with stubs, not a full-width bar. A lone
                      child draws no rail (just the spine continues). */}
                  {!only && (
                    <span
                      aria-hidden
                      className={[
                        "absolute left-0 right-0 top-0 h-px bg-white/20",
                        isFirst ? "left-1/2" : "",
                        isLast ? "right-1/2" : "",
                      ]
                        .filter(Boolean)
                        .join(" ")}
                    />
                  )}
                  {/* The drop from the rail into this child. */}
                  <span aria-hidden className="h-5 w-px bg-white/20" />
                  <TreeBranch node={child} />
                </li>
              );
            })}
          </ul>
        </>
      )}
    </motion.li>
  );
}

/**
 * One root and its whole subtree, boxed so each organization reads as a distinct
 * unit. Roots with no children render as a single card (a brand that stands on
 * its own). The box scrolls horizontally on its own if a wide tree overflows, so
 * a broad portfolio stays readable on narrow screens.
 */
function RootTree({ node }: { node: TreeNode }) {
  return (
    <div className="glass rounded-2xl p-5 shadow-panel">
      <div className="overflow-x-auto">
        <ul className="flex w-max min-w-full justify-center px-2">
          <TreeBranch node={node} />
        </ul>
      </div>
    </div>
  );
}

export function ArchitectureView({ brands }: { brands: ShowcaseBrand[] }) {
  const forest = buildForest(brands);

  if (forest.length === 0) {
    return (
      <div className="glass rounded-2xl shadow-panel">
        <EmptyState
          icon={<GitFork size={22} />}
          title="No brands to chart"
          description="Add or onboard a brand to see the portfolio architecture."
        />
      </div>
    );
  }

  return (
    <motion.div
      variants={staggerContainer}
      initial="hidden"
      animate="show"
      className="space-y-5"
    >
      {forest.map((root) => (
        <motion.div key={root.sb.brand.slug} variants={staggerItem}>
          <RootTree node={root} />
        </motion.div>
      ))}
    </motion.div>
  );
}
