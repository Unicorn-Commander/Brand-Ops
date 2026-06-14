import { useEffect } from "react";
import { NavLink, Outlet, useParams } from "react-router-dom";
import { CommandPalette } from "./CommandPalette";
import { ErrorBoundary } from "./ErrorBoundary";
import { EnvProvider, useBrandEnv } from "../v2/EnvProvider";
import { UserMenu } from "../v2/UserMenu";
import { WorkspaceSwitcher } from "../v2/WorkspaceSwitcher";
import { applyAmbient } from "../pages/v2/SettingsPage";
import { useActiveBrand } from "../brand-context";
import { MarkImg } from "./MarkImg";

const CHROME_NAV = [
  { to: "/", label: "Portfolio", end: true },
  { to: "/library", label: "Library" },
  { to: "/standards", label: "Standards" },
  { to: "/crew", label: "Crew" },
] as const;

const BRAND_NAV = [
  { key: "overview", label: "Overview" },
  { key: "mark", label: "Mark" },
  { key: "book", label: "Book" },
  { key: "assets", label: "Assets" },
  { key: "studio", label: "Studio" },
  { key: "grade", label: "Grade" },
] as const;

function commandHint() {
  document.dispatchEvent(new KeyboardEvent("keydown", { key: "k", metaKey: true }));
}

function Topbar({
  brandSlug,
  brandName,
  markUrl,
}: {
  brandSlug?: string;
  brandName?: string;
  markUrl?: string | null;
}) {
  const isBrand = Boolean(brandSlug);
  return (
    <header className="v2-topbar">
      <div className="v2-topbar-inner">
        <NavLink className="v2-wordmark" to="/">
          <img src="/design/assets/img/bo-mark.jpg" alt="" />
          <b>{isBrand ? "‹ Portfolio" : "Brand-Ops"}</b>
        </NavLink>

        {isBrand && (
          <span className="v2-brand-id">
            <MarkImg
              brand={{
                display_name: brandName ?? brandSlug ?? "Brand",
                mark_url: markUrl ?? null,
              }}
            />
            <b>{brandName ?? brandSlug}</b>
          </span>
        )}

        <nav className="v2-nav">
          {isBrand && brandSlug
            ? BRAND_NAV.map((item) => (
                <NavLink
                  key={item.key}
                  to={`/b/${brandSlug}/${item.key}`}
                  className={({ isActive }) => (isActive ? "on" : undefined)}
                >
                  {item.label}
                </NavLink>
              ))
            : CHROME_NAV.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={"end" in item ? item.end : false}
                  className={({ isActive }) => (isActive ? "on" : undefined)}
                >
                  {item.label}
                </NavLink>
              ))}
        </nav>

        <div className="v2-topbar-right">
          {isBrand ? (
            <span className="chip">
              <span className="dot dot-live" /> Steward online
            </span>
          ) : (
            <WorkspaceSwitcher />
          )}
          <button className="kbd" type="button" onClick={commandHint}>
            ⌘K
          </button>
          {!isBrand && (
            <NavLink className="btn btn-ink" to="/brands/new">
              New brand
            </NavLink>
          )}
          <UserMenu />
        </div>
      </div>
    </header>
  );
}

export function Layout() {
  useEffect(() => {
    applyAmbient();
  }, []);
  return (
    <EnvProvider>
      <Topbar />
      <main className="v2-wrap">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
      <CommandPalette />
    </EnvProvider>
  );
}

export function BrandLayout() {
  const { slug } = useParams();
  const { setActiveSlug } = useActiveBrand();
  const brandQ = useBrandEnv(slug);

  useEffect(() => {
    if (slug) setActiveSlug(slug);
  }, [setActiveSlug, slug]);

  return (
    <EnvProvider slug={slug}>
      <Topbar
        brandSlug={slug}
        brandName={brandQ.data?.display_name}
        markUrl={brandQ.data?.mark_url}
      />
      <main className="v2-wrap">
        <ErrorBoundary>
          <Outlet />
        </ErrorBoundary>
      </main>
      <CommandPalette />
    </EnvProvider>
  );
}
