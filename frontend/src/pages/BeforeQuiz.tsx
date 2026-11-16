import { Link } from "react-router-dom";
import Layout from "../components/Layout";

export default function BeforeQuiz() {
  return (
    <Layout>
      <div className="text-center">
        <p className="inline-block rounded-full bg-warm px-4 py-2 text-xs font-semibold">
          Free quiz · Discover your interests
        </p>
        <h1 className="mt-7 text-5xl font-extrabold tracking-tight">
          What career fits
          <br />
          <span className="gradient-text">the real you?</span>
        </h1>
        <p className="mt-6 leading-relaxed text-muted">
          Answer 32 questions about your interests, abilities, work style, and values. There are no
          right answers. Choose what feels true for you today.
        </p>
        <Link to="/start" className="primary-button mt-8">
          Start the quiz →
        </Link>
        <p className="mt-3 text-xs text-muted">About 8 minutes · Take breaks whenever you need</p>
      </div>
      <div className="mt-10 grid gap-4 sm:grid-cols-2">
        {["Interests", "Abilities", "Work style", "Values"].map((label, index) => (
          <div key={label} className="rounded-2xl border border-line bg-surface p-6">
            <span className="text-sm text-accent">0{index + 1}</span>
            <h2 className="mt-2 text-lg font-bold">{label}</h2>
          </div>
        ))}
      </div>
      <p className="mt-8 text-center text-sm text-muted">
        Get your six-trait profile and eight career ideas. Your completed answers and report are
        saved on our servers; sign-in is optional.
      </p>
    </Layout>
  );
}
