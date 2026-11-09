import { Link } from "react-router-dom";

import AccountMenu from "./AccountMenu";

/**
 * The page shell: wordmark, content, footer.
 *
 * `width` exists because the screens want different measures — a form reads best narrow, a
 * report needs room for cards — and a max-width hardcoded per page drifts apart over time.
 */
interface Props {
  children: React.ReactNode;
  width?: "narrow" | "wide";
}

export default function Layout({ children, width = "narrow" }: Props) {
  // One step wider than feels right for pure prose: the questionnaire's option rows and the
  // report's two-column grids both want the room, and a narrow measure made the whole thing
  // read as a form rather than a product.
  const measure = width === "wide" ? "max-w-5xl" : "max-w-2xl";

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="border-b border-line/70">
        <div
          className={`mx-auto flex ${measure} items-center justify-between gap-4 px-6 py-5`}
        >
          <Link to="/" className="font-display text-xl font-semibold">
            FutureQuest
          </Link>
          <AccountMenu />
        </div>
      </header>

      <main className={`mx-auto w-full flex-1 ${measure} px-6 py-10 sm:py-14`}>{children}</main>

      <footer className="border-t border-line/70">
        <div className={`mx-auto ${measure} px-6 py-6 text-sm text-muted`}>
          A guide, not a verdict. Nobody&rsquo;s career is decided by a questionnaire.
        </div>
      </footer>
    </div>
  );
}
