// HeroSection.jsx
// Customize: headline, subcopy, CTA labels, gradient colors (gold), the three stat chips.
// Self-contained: React only. Transparent background on purpose so the 3D galaxy shows through.
export default function HeroSection({
  onResume = null,
  onStart = () => {},
  onLogin = null,            // pass a function to show "Continue with GitHub" instead of Start
  orgCount = 14,
  onUseSaved = null,         // returning student: skip the quiz
  savedSummary = '',
}) {
  const stats = [
    ['10', 'questions'],
    [String(orgCount), 'organisations scored'],
    ['3', 'ways to work'],
  ];
  return (
    <section className="relative flex min-h-[86vh] overflow-hidden items-center px-6 py-20 md:px-20 pointer-events-none">
      {/* scrim: keeps the copy readable where the galaxy drifts behind it (tablets and small laptops) */}
      <div className="absolute inset-y-0 left-0 -z-10 w-full bg-gradient-to-r from-black/90 via-black/60 to-transparent xl:w-2/3" />
      <div className="absolute -left-40 top-10 -z-10 h-96 w-96 rounded-full bg-sky-800 opacity-25 blur-3xl" />
      <div className="absolute left-1/3 bottom-0 -z-10 h-80 w-80 rounded-full bg-orange-700 opacity-15 blur-3xl" />
      <div className="max-w-3xl">
        <span className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-white/15 bg-white/5 px-4 py-1.5 text-sm font-semibold text-sky-200 backdrop-blur">
          <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-400" />
          Open source, minus the gatekeeping
        </span>
        <h1 className="mt-6 text-5xl leading-[1.05] text-white sm:text-6xl md:text-7xl">
          Your first open-source contribution—with understanding, not guesswork.
        </h1>
        <p className="mt-8 max-w-xl text-xl font-medium leading-relaxed text-stone-300 md:text-2xl">
          Find a suitable issue, understand the code, and prepare a contribution you can explain.
        </p>
        <div className="pointer-events-auto mt-10 flex flex-wrap items-center gap-4">
          {onLogin ? (
            <button
              onClick={onLogin}
              className="rounded-full bg-white px-10 py-4 text-lg font-extrabold text-zinc-950 shadow-[0_0_60px_-10px_rgba(125,211,252,0.55)] transition duration-200 hover:scale-105 hover:brightness-110 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400"
            >
              Continue with GitHub →
            </button>
          ) : (
            <button
              onClick={onStart}
              className="rounded-full bg-white px-10 py-4 text-lg font-extrabold text-zinc-950 shadow-[0_0_60px_-10px_rgba(125,211,252,0.55)] transition duration-200 hover:scale-105 hover:brightness-110 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-400"
            >
              Find my first issue →
            </button>
          )}
          {onResume && <button onClick={onResume} className="rounded-full border-2 border-white/25 px-6 py-3 font-bold text-white focus-visible:ring-4 focus-visible:ring-sky-300">Resume guided contribution</button>}
          {onUseSaved && (
            <button onClick={onUseSaved} className="rounded-full border-2 border-white/25 bg-white/5 px-8 py-4 text-lg font-extrabold text-white backdrop-blur transition duration-200 hover:scale-105 hover:border-sky-300 focus:outline-none focus-visible:ring-4 focus-visible:ring-sky-300">
              Use my saved profile{savedSummary ? <span className="ml-2 text-sm font-semibold text-zinc-400">{savedSummary}</span> : null}
            </button>
          )}
          <span className="text-sm font-semibold text-zinc-400">Takes about 2 minutes. Drag the galaxy while you wait.</span>
        </div>
        <dl className="mt-14 flex flex-wrap gap-x-12 gap-y-6">
          {stats.map(([n, label]) => (
            <div key={label}>
              <dt className="display text-6xl text-white">{n}</dt>
              <dd className="text-sm font-semibold uppercase tracking-widest text-zinc-400">{label}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
