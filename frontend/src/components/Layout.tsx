import { useState } from "react";
import { Link } from "react-router-dom";

import AccountMenu from "./AccountMenu";

// Reference pages retain their own dimensions; utility pages use the default shell.
interface Props {
  children: React.ReactNode;
  width?: "narrow" | "wide";
  variant?: "landing" | "before_quiz" | "gatherinfo" | "quiz" | "report";
}

export default function Layout({ children, width = "narrow", variant }: Props) {
  const [dark, setDark] = useState(() => {
    const saved = localStorage.getItem("futurequest.theme") === "dark";
    document.documentElement.dataset.theme = saved ? "dark" : "light";
    return saved;
  });
  const toggleTheme = () => {
    const next = !dark;
    setDark(next);
    localStorage.setItem("futurequest.theme", next ? "dark" : "light");
    document.documentElement.dataset.theme = next ? "dark" : "light";
  };
  const measure = width === "wide" ? "max-w-5xl" : "max-w-2xl";

  if (variant) {
    return (
      <div className={`mockup mockup--${variant}`}>
        <header className="navbar">
          <div className="navbar-inner">
            <Link to="/" className="brand">
              <svg
                className="brand-icon"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2.2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <circle cx="12" cy="12" r="10" />
                <polygon points="16.24 7.76 14.12 14.12 7.76 16.24 9.88 9.88 16.24 7.76" />
              </svg>
              FutureQuest
            </Link>
            <button
              type="button"
              className="theme-toggle"
              onClick={toggleTheme}
              aria-label={dark ? "Use light theme" : "Use dark theme"}
            >
              <svg
                width="17"
                height="17"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                {dark ? (
                  <>
                    <circle cx="12" cy="12" r="4" />
                    <path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
                  </>
                ) : (
                  <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
                )}
              </svg>
            </button>
          </div>
        </header>
        <main className="container">{children}</main>
        {variant !== "gatherinfo" && variant !== "report" && (
          <footer className="footer">
            A career discovery bundle for curious students. Interests · Strengths · EQ · Compass.
          </footer>
        )}
        {variant !== "quiz" && (
          <div className="account-access">
            <AccountMenu />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-dvh flex-col">
      <header className="sticky top-0 z-30 border-b border-line/70 bg-canvas/90 backdrop-blur">
        <div className={`mx-auto flex ${measure} items-center justify-between gap-4 px-6 py-5`}>
          <Link to="/" className="font-display text-xl font-semibold">
            <span className="mr-2 text-accent" aria-hidden>
              ✧
            </span>
            FutureQuest
          </Link>
          <div className="flex items-center gap-4">
            <AccountMenu />
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={dark ? "Use light theme" : "Use dark theme"}
              className="rounded-full p-2"
            >
              {dark ? "☀" : "☾"}
            </button>
          </div>
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
