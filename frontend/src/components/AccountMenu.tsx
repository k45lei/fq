import { Link, useNavigate } from "react-router-dom";

import { api } from "../api/client";
import { useLogout, useMe } from "../api/queries";

/**
 * The account corner of the header.
 *
 * Signing in is optional throughout — the questionnaire, report and PDF all work without it,
 * on the ownership cookie alone. What it adds is reaching your reports from another device,
 * so that's what the prompt says rather than a bare "Sign in".
 */
export default function AccountMenu() {
  const navigate = useNavigate();
  const { data: me, isPending } = useMe();
  const logout = useLogout();

  const signOut = async () => {
    await logout.mutateAsync();
    // Home afterwards, not wherever they were. Signing out on /reports would leave them
    // looking at a page that now has nothing to show them.
    navigate("/");
  };

  // Nothing during the first load: a "Sign in" that flickers into a name reads as a glitch.
  if (isPending) return null;

  if (!me?.signed_in) {
    return (
      <a
        href={api.googleLoginUrl}
        className="text-sm text-muted underline underline-offset-4 transition hover:text-ink"
      >
        Sign in
      </a>
    );
  }

  return (
    <div className="flex items-center gap-4 text-sm">
      {/* Shown whenever signed in, even with nothing to list. Hiding it on an empty list
          made the page unreachable exactly when someone would want to check why it's
          empty. */}
      <Link to="/reports" className="underline underline-offset-4 hover:text-accent">
        Your reports
      </Link>
      <span className="hidden text-muted sm:inline">{me.name ?? me.email}</span>
      <button
        type="button"
        onClick={signOut}
        disabled={logout.isPending}
        className="text-muted underline underline-offset-4 transition hover:text-ink disabled:opacity-50"
      >
        Sign out
      </button>
    </div>
  );
}
