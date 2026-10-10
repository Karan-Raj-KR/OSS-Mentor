// app.jsx: the shell. Owns state and API calls; the six components do all the visual work.
// Flow: hero -> interview -> matches -> issues -> workspace, plus a Replies tab.

const renderMd = (md) => {
  // The model sometimes forgets the ```mermaid label, so also treat an unlabeled block that starts like a diagram as one.
  const html = marked.parse(md || '')
    .replace(/<pre><code class="language-mermaid">([\s\S]*?)<\/code><\/pre>/g, '<pre class="mermaid">$1</pre>')
    .replace(/<pre><code(?: class="language-[a-z]*")?>\s*((?:flowchart|graph|sequenceDiagram|classDiagram|stateDiagram)[\s\S]*?)<\/code><\/pre>/g, '<pre class="mermaid">$1</pre>');
  return DOMPurify.sanitize(html, { ADD_ATTR: ['class'] });
};

const FRIENDLY = {
  auth: 'Set OPENAI_API_KEY on the server, then retry. The configured key may be invalid.',
  budget: 'You have used all of your mentor time for this session. Your matches and notes are still here.',
  openai: 'The OpenAI mentor had a hiccup. Try again in a moment.',
  github: 'GitHub is busy or unreachable. Wait a few seconds and try again.',
  setup: 'This is a preview: sign-in is not switched on for this deployment yet, so live GitHub checks and the AI mentor are paused. Your matches above are real. To see the rest of the journey, open "How it works" or watch the recorded walkthrough.',
};

async function api(path, body, method, putBody) {
  const init = method ? { method, headers: { 'Content-Type': 'application/json' }, body: putBody ? JSON.stringify(putBody) : undefined }
    : body ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, init);
  const j = await r.json().catch(() => ({ error: 'Bad response from server' }));
  if (r.status === 401) { const e = new Error('login'); e.login = true; throw e; }
  if (r.status === 404 && j.detail === 'Not Found') { const e = new Error('The server is older than this page. Restart it with ./run.sh and reload.'); e.kind = 'stale'; throw e; }
  if (!r.ok) { const e = new Error(FRIENDLY[j.kind] || j.error || j.detail || 'Something went wrong'); e.kind = j.kind; throw e; }
  return j;
}

// Reads newline-delimited JSON events from the scout: calls onStep for progress, returns the final result.
async function streamScout(body, onStep) {
  const r = await fetch('/api/scout/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
  if (r.status === 401) { const e = new Error('login'); e.login = true; throw e; }
  if (!r.ok || !r.body) { const j = await r.json().catch(() => ({})); throw new Error(FRIENDLY[j.kind] || j.error || 'Scouting failed. Try again.'); }
  const reader = r.body.getReader(), dec = new TextDecoder();
  let buf = '', result = null;
  for (;;) {
    const { value, done } = await reader.read();
    if (value) buf += dec.decode(value, { stream: true });
    let nl;
    while ((nl = buf.indexOf('\n')) >= 0) {
      const line = buf.slice(0, nl).trim(); buf = buf.slice(nl + 1);
      if (!line) continue;
      const ev = JSON.parse(line);
      if (ev.type === 'step') onStep(ev.text);
      else if (ev.type === 'error') throw new Error(FRIENDLY[ev.kind] || ev.error);
      else if (ev.type === 'result') result = ev;
    }
    if (done) break;
  }
  if (!result) throw new Error('Scouting ended unexpectedly. Try again.');
  return result;
}

export default function App() {
  const { HeroSection, InterviewStep, MatchPodium, DiscoverPanel, IssueBoard, WorkspaceView, RepliesInbox, ScoutProgress, BrowseCatalogue, FullAutoJob, ProfilePage } = window.OSS;
  const [view, setView] = React.useState('mentor');
  const [step, setStep] = React.useState('hero');
  const [me, setMe] = React.useState({ hosted: false, login: 'local' });
  const [orgs, setOrgs] = React.useState([]);
  const [qs, setQs] = React.useState([]);
  const [qi, setQi] = React.useState(0);
  const [answers, setAnswers] = React.useState({});
  const [ranking, setRanking] = React.useState([]);
  const [others, setOthers] = React.useState([]);
  const [catSize, setCatSize] = React.useState(0);
  const [found, setFound] = React.useState(null);
  const [discLoading, setDiscLoading] = React.useState(false);
  const [discError, setDiscError] = React.useState('');
  const [org, setOrg] = React.useState(null);
  const [health, setHealth] = React.useState(null);
  const [profile, setProfile] = React.useState(null);
  const [profErr, setProfErr] = React.useState('');
  const [profNote, setProfNote] = React.useState('');
  const [job, setJob] = React.useState(null);
  const [jobCmd, setJobCmd] = React.useState('');
  const [jobErr, setJobErr] = React.useState('');
  const [catStatus, setCatStatus] = React.useState(null);
  const [bFilters, setBFilters] = React.useState({ q: '', source: '', language: '', domain: '', page: 1 });
  const [bData, setBData] = React.useState(null);
  const [bLoading, setBLoading] = React.useState(false);
  const [scoutSteps, setScoutSteps] = React.useState([]);
  const [scouting, setScouting] = React.useState(false);
  const [issueNotice, setIssueNotice] = React.useState('');
  const [checking, setChecking] = React.useState(-1);
  const [scout, setScout] = React.useState({ picks: [], skipped: [] });
  const [issue, setIssue] = React.useState(null);
  const [tourHtml, setTourHtml] = React.useState('');
  const [tourMd, setTourMd] = React.useState('');
  const [structure, setStructure] = React.useState([]);
  const [msgs, setMsgs] = React.useState([]);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const [usage, setUsage] = React.useState(null);
  const [prUrl, setPrUrl] = React.useState('');
  const [drafts, setDrafts] = React.useState(null);
  const [notice, setNotice] = React.useState('');

  const needsLogin = me.hosted && !me.login;

  const previewRef = React.useRef(false);  // keyless preview: skip calls that are locked anyway (no needless 503s in the console)
  const refreshUsage = async () => { if (previewRef.current) return; try { setUsage(await api('/api/usage')); } catch {} };
  const run = async (fn) => {
    setBusy(true); setError('');
    try { await fn(); } catch (e) { if (e.login) setMe({ hosted: true, login: null }); else setError(e.message); }
    finally { setBusy(false); refreshUsage(); }
  };
  const stage3d = (st, extra) => { if (window.Scene && Scene.available) Scene.setStage(st, extra); };

  // boot
  React.useEffect(() => {
    (async () => {
      try { const h = await api('/api/health'); previewRef.current = !!h.preview; setHealth(h); } catch { setHealth({}); }  // an unconfigured public site still runs as a keyless preview
      try { setMe(await api('/api/me')); } catch {}
      try { const o = await api('/api/orgs'); setOrgs(o); if (window.Scene) Scene.init(o); } catch {}
      try { setCatStatus(await api('/api/catalogue/status')); } catch {}
      refreshUsage();
    })();
  }, []);

  // load the signed-in student's saved profile (also lets a returning student skip the quiz)
  const loadProfile = async () => { if (previewRef.current) return null; try { const p = await api('/api/profile'); setProfile(p); return p; } catch (e) { if (e.login) setMe({ hosted: true, login: null }); return null; } };
  React.useEffect(() => { if (!health) return; if (!me.hosted || me.login) loadProfile(); else setProfile(null); }, [me.hosted, me.login, health]);  // after health: preview skips it
  React.useEffect(() => { if (view === 'profile') { setProfNote(''); setProfErr(''); loadProfile(); if (!qs.length) api('/api/questions').then(setQs).catch(() => {}); } }, [view]);
  const saved = (profile && profile.answers) || {};
  const hasSaved = Object.keys(saved).length >= 5;
  const summary = [(saved.languages || []).slice(0, 2).join(', '), saved.skill].filter(Boolean).join(' · ');

  // Full-auto: poll the job while it is alive (the runner on the student's machine reports progress to the server)
  React.useEffect(() => {
    if (!job || ['done', 'failed', 'cancelled', 'expired'].includes(job.status)) return;
    const t = setInterval(async () => {
      try { const j = await api(`/api/jobs/${job.id}?since=${(job.events && job.events.length ? job.events[job.events.length - 1].id : 0)}`);
        setJob((cur) => (cur && cur.id === j.id ? { ...j, events: [...(cur.events || []), ...j.events] } : cur)); } catch {}
    }, 2000);
    return () => clearInterval(t);
  }, [job && job.id, job && job.status, job && job.events && job.events.length]);

  // an error belongs to the screen it happened on: clear it when the student moves on
  React.useEffect(() => { setError(''); }, [view, step]);

  // browse: refetch whenever the filters change
  React.useEffect(() => {
    if (view !== 'browse') return;
    let live = true;
    setBLoading(true);
    const qs = new URLSearchParams({ q: bFilters.q, source: bFilters.source, language: bFilters.language, domain: bFilters.domain, page: bFilters.page, size: 24 });
    api('/api/catalogue/browse?' + qs).then((d) => live && setBData(d)).catch((e) => live && setError(e.message)).finally(() => live && setBLoading(false));
    return () => { live = false; };
  }, [view, bFilters]);

  // galaxy reacts to interview answers
  const fit = (o, a) => {
    const l = (a.languages || []).map((x) => x.toLowerCase());
    const i = a.interests || [];
    const okL = !l.length || o.languages.some((x) => l.includes(x.toLowerCase()));
    const okI = !i.length || o.domains.some((x) => i.includes(x));
    return okL && okI;
  };
  const fitCount = orgs.length ? orgs.filter((o) => fit(o, answers)).length : null;
  React.useEffect(() => {
    if (step === 'interview' && window.Scene) Scene.react((o) => fit(o, answers));
  }, [answers, step, orgs]);

  // blur the galaxy behind content screens; keep it crisp on the hero and during the interview
  React.useEffect(() => {
    if (!window.Scene) return;
    const px = view === 'replies' || view === 'browse' || view === 'profile' ? 12 : step === 'matches' ? 6 : step === 'issues' ? 10 : step === 'work' || step === 'job' ? 12 : 0;
    Scene.setBlur(px);
  }, [view, step, orgs.length]);

  const start = () => run(async () => {
    if (!qs.length) setQs(await api('/api/questions'));
    if (hasSaved && !Object.keys(answers).length) setAnswers(saved);  // returning student: answers pre-filled, just confirm
    setQi(0); setStep('interview'); stage3d('explore');
  });
  const runMatch = async (a) => {
    const m = await api('/api/match', a);
    const r = m.ranking;
    setRanking(r); setOthers(m.others || []); setCatSize(m.catalogue_size || 0); setFound(null); setDiscError(''); setStep('matches');
    stage3d('ranked', { focus: r.map((x) => x.org), scores: Object.fromEntries(r.map((x) => [x.org, x.total])) });
  };
  const useSaved = () => run(async () => { setAnswers(saved); setView('mentor'); await runMatch(saved); });
  const next = () => {
    if (qi < qs.length - 1) return setQi(qi + 1);
    run(async () => {
      await runMatch(answers);
      if (!previewRef.current) try { setProfile(await api('/api/profile', null, 'PUT', { answers })); } catch {}  // remember the finished quiz for next time
    });
  };
  const back3d = () => stage3d('ranked', { focus: ranking.map((x) => x.org), scores: Object.fromEntries(ranking.map((x) => [x.org, x.total])) });
  const allOrgs = [...ranking, ...others];
  const scoutOrg = async (o, repo) => {
    setBusy(true); setError(''); setIssueNotice(''); setScoutSteps([]); setScouting(true); setOrg(o); stage3d('selected', { pick: o.org });
    try {
      const fallbacks = repo ? [] : allOrgs.map((x) => x.github).filter((g) => g.toLowerCase() !== o.github.toLowerCase()).slice(0, 2);
      const res = await streamScout({ org: o.github, repo: repo || null, profile: answers, fallback_orgs: fallbacks }, (t) => setScoutSteps((s) => [...s, t]));
      const used = res.org_used ? (allOrgs.find((x) => x.github.toLowerCase() === res.org_used.toLowerCase()) || { org: res.org_used, github: res.org_used }) : o;
      setOrg(used); setScout(res); setStep('issues'); stage3d('working', { pick: used.org });
      const notes = [];
      if (res.org_used && used.github.toLowerCase() !== o.github.toLowerCase()) notes.push(`${o.org} had nothing free right now, so we searched ${used.org} for you instead.`);
      if (res.ranked_by === 'fallback') notes.push('The mentor could not rank these this time, so they are ordered by how little discussion they have. Read each issue before you pick.');
      if (notes.length) setIssueNotice(notes.join(' '));
    } catch (e) { back3d(); if (e.login) setMe({ hosted: true, login: null }); else setError(e.message); }
    finally { setScouting(false); setBusy(false); refreshUsage(); }
  };
  const choose = (i) => scoutOrg(ranking[i]);
  const chooseRepo = (r) => scoutOrg({ org: r.repo, github: r.owner }, r.repo);
  const discover = async () => {
    setDiscLoading(true); setDiscError('');
    try { setFound((await api(`/api/discover?languages=${encodeURIComponent((answers.languages || []).join(','))}&interests=${encodeURIComponent((answers.interests || []).join(','))}`)).repos); }
    catch (e) { if (e.login) setMe({ hosted: true, login: null }); else setDiscError(e.message); }
    finally { setDiscLoading(false); }
  };
  const verifyPick = async (i) => {
    const p = scout.picks[i];
    const v = await api('/api/verify', { repo: p.repo, number: p.number });
    if (v.ok) {
      setScout((sc) => ({ ...sc, picks: sc.picks.map((x, j) => (j === i ? { ...x, verified_at: v.checked_at, verification: v } : x)) }));
      // a check GitHub did not answer is not a pass: say which part is unconfirmed
      if ((v.unchecked || []).length) setIssueNotice(`#${p.number} looks free, but GitHub did not let us check ${v.unchecked.join(' and ')}. Look at the issue page before you start.`);
      return true;
    }
    setScout((sc) => ({ ...sc, picks: sc.picks.filter((_, j) => j !== i) }));
    setIssueNotice(`#${p.number} in ${p.repo} just changed: ${v.reason}. We removed it so you do not waste time on it.`);
    return false;
  };
  const recheck = async (i) => { setChecking(i); setError(''); try { await verifyPick(i); } catch (e) { setError(e.message); } finally { setChecking(-1); } };
  const openTour = async (p) => {
    const j = await api('/api/journey/begin', { repo: p.repo, number: p.number });
    if (!j.verification.ok || j.verification.unchecked.length) throw new Error('Issue unavailable or not fully verified. Return to issues and re-check.');
    const t = await api('/api/tour', { repo: p.repo, issue_title: p.title, level: answers.skill || 'beginner' });
    setIssue({ ...p, title: j.issue.title, url: j.issue.url, verification: j.verification, policy: j.policy }); setTourMd(t.markdown); setStructure((t.overview && t.overview.top_level) || []); setTourHtml(renderMd(t.markdown)); setMsgs([]); setStep('work');
  };
  const pickIssue = (i) => run(async () => {
    if (!(await verifyPick(i))) return;
    const p = scout.picks[i];
    if (answers.mode === 'full') { setIssue(p); setJob(null); setJobCmd(''); setJobErr(''); setStep('job'); return; }  // Full-auto skips the tour
    await openTour(p);
  });
  const switchToLearn = () => run(async () => { setAnswers((a) => ({ ...a, mode: 'learn' })); await openTour(issue); });

  const saveProfile = (payload) => run(async () => {
    setProfErr(''); setProfNote('');
    try { const p = await api('/api/profile', null, 'PUT', payload); setProfile((cur) => ({ ...cur, ...p })); await loadProfile(); setProfNote('Profile saved.'); }
    catch (e) { if (e.login) throw e; setProfErr(e.message); }
  });
  const deleteMyData = () => run(async () => {
    setProfErr('');
    try {
      await api('/api/profile', null, 'DELETE');
      setProfile(null); setAnswers({}); setRanking([]); setJob(null); setIssue(null); setStep('hero'); stage3d('explore');
      if (me.hosted) setMe({ hosted: true, login: null }); else { setView('mentor'); await loadProfile(); }
    } catch (e) { if (e.login) throw e; setProfErr(e.message); }
  });
  const logout = async () => { try { await fetch('/auth/logout', { method: 'POST' }); } catch {} setMe({ hosted: true, login: null }); setProfile(null); reset(); };
  const useProfile = () => { setView('mentor'); useSaved(); };
  const createJob = ({ consent, signoff }) => run(async () => {
    setJobErr('');
    try {
      const j = await api('/api/jobs', { repo: issue.repo, number: issue.number, title: issue.title, consent, signoff });
      setJobCmd(j.command); setJob(await api(`/api/jobs/${j.id}`));
    } catch (e) { if (e.login) throw e; setJobErr(e.message); }
  });
  const approveJob = (a) => run(async () => { setJobErr(''); try { setJob({ ...(await api(`/api/jobs/${job.id}/approve`, a)) }); } catch (e) { if (e.login) throw e; setJobErr(e.message); } });
  const cancelJob = () => run(async () => { if (job) setJob(await api(`/api/jobs/${job.id}/cancel`, {})); });
  const send = (text) => {
    const history = [...msgs.map((m) => ({ role: m.role, content: m.md || m.text })), { role: 'user', content: text }];
    setMsgs((m) => [...m, { role: 'user', text }]);
    run(async () => {
      const r = await api('/api/coach', { repo: issue.repo, number: issue.number, mode: answers.mode === 'semi' ? 'semi' : 'learn', context: tourMd.slice(0, 3000) + '\nIssue: ' + issue.title, history });
      setMsgs((m) => [...m, { role: 'assistant', md: r.reply, html: renderMd(r.reply) }]);
    });
  };
  const reset = () => { setStep('hero'); setView('mentor'); setAnswers({}); setRanking([]); setMsgs([]); setIssue(null); stage3d('explore'); };

  const checkPr = () => run(async () => {
    const d = await api('/api/replies/draft', { pr_url: prUrl.trim() });
    setDrafts(d); setNotice(d.drafts.length ? '' : d.note || 'No new comments to answer.');
  });
  const sendReply = (d, text) => run(async () => {
    const r = await api('/api/replies/send', { pr_url: prUrl.trim(), items: [{ id: d.id, source: d.source, reply: text }], disclose: true });
    setDrafts({ ...drafts, drafts: drafts.drafts.filter((x) => x.id !== d.id) });
    if ((r.skipped || []).includes(d.id)) setNotice('You already answered that comment, so nothing new was posted.');
  });

  // Unconfigured public deployment: a keyless preview (interview, matching, catalogue). Visitors and judges' agents can explore;
  // anything that would spend the owner's keys or read GitHub explains itself instead of failing silently.
  const preview = !!(health && health.preview);
  const demo = !!(health && health.demo_fixture);
  const demoBanner = demo && (
    <div role="status" className="relative z-10 border-b border-amber-300/30 bg-zinc-950 px-4 py-3 text-sm text-amber-100 md:px-8">
      <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-x-4 gap-y-2">
        <span><b className="font-extrabold">Controlled demo.</b> Organisation matching is real. The issue, repository and mentor replies come from a small scripted fixture project (no OpenAI, no GitHub, nothing spent), so the whole journey works without sign-in.</span>
        <details className="basis-full text-amber-50" open><summary className="cursor-pointer font-extrabold">Judges and agents: try it in 6 steps</summary>
          <ol className="mt-2 list-decimal space-y-1 pl-5 text-[13px] leading-relaxed text-amber-100/90">
            <li><b>Find my first issue</b>, then answer the 10 questions (any answers; Learn mode is fine).</li>
            <li>In your matches, choose <b>Controlled demo project</b> (pinned first) and click <b>Find me an issue</b>.</li>
            <li>Read the issue card: state, assignees, competing PRs, policy source and timestamp. Click <b>Start working on this</b>.</li>
            <li><b>Repo tour</b> shows the repository explanation; <b>Coach</b> gives hint-first help.</li>
            <li><b>Contribution review</b>: fetch the files, paste a diff and test evidence, then answer the understanding checkpoint (retry on feedback).</li>
            <li>See what is ready and what is still unverified. Nothing is ever published. Full guide: <a href="/about" className="underline">/about</a>.</li>
          </ol>
        </details>
        <a href="/about" className="font-bold underline underline-offset-2 hover:text-white">How it works</a>
        {(health.owner_missing || []).length > 0 && (
          <details className="text-amber-200/80"><summary className="cursor-pointer font-bold">Site owner</summary>
            <p className="mt-1 text-xs">This deployment is not signed-in/live yet. Add these Vercel environment variables and redeploy for the real thing (docs/DEPLOY_VERCEL.md):</p>
            <ul className="mt-1 space-y-1 text-xs">{health.owner_missing.map((m) => <li key={m}>• {m}</li>)}<li>• OPENAI_API_KEY (the AI mentor)</li></ul>
          </details>)}
      </div>
    </div>
  );
  const previewBanner = preview && (
    <div role="status" className="relative z-10 border-b border-amber-300/30 bg-zinc-950 px-4 py-3 text-sm text-amber-100 md:px-8">
      <div className="mx-auto max-w-[1400px]"><b className="font-extrabold">Preview.</b> Sign-in is not switched on for this deployment yet. <a href="/about" className="font-bold underline">How it works</a></div>
    </div>
  );

  const q = qs[qi];
  const pill = usage ? `$${usage.session.cost_usd.toFixed(3)} / $${usage.session_budget_usd.toFixed(2)}` : null;
  const usedPct = usage ? Math.min(100, (usage.session.cost_usd / usage.session_budget_usd) * 100) : 0;

  return (
    <div>
      <header className="sticky top-0 z-40 border-b border-white/10 bg-zinc-950/70 backdrop-blur-xl">
        <div className="mx-auto flex max-w-[1400px] flex-wrap items-center gap-4 px-4 py-3 md:px-8">
          <button onClick={reset} className="display text-3xl leading-none text-white">
            OSS <em className="bg-gradient-to-r from-sky-200 via-cyan-200 to-orange-300 bg-clip-text pr-1 text-transparent">Mentor</em>
          </button>
          <nav className="order-3 flex w-full gap-1 sm:order-none sm:ml-2 sm:w-auto" aria-label="Main">
            {[['mentor', 'Mentor'], ['browse', 'Browse'], ['replies', 'PR replies']].map(([id, label]) => (
              <button key={id} onClick={() => setView(id)} aria-current={view === id ? 'page' : undefined}
                className={`whitespace-nowrap rounded-full px-3 py-1.5 text-sm font-extrabold transition sm:px-4 ${view === id ? 'bg-white text-zinc-950' : 'text-zinc-300 hover:bg-white/10'}`}>{label}</button>
            ))}
            {/* plain link (not a JS view): the same guide AI agents and judges read, works without signing in */}
            <a href="/about" className="whitespace-nowrap rounded-full px-3 py-1.5 text-sm font-bold text-zinc-400 sm:px-4 transition hover:bg-white/10 hover:text-white">How it works</a>
          </nav>
          <span className="flex-1" />
          {pill && (
            <span className="hidden items-center gap-2 rounded-full border border-white/10 bg-white/5 px-3 py-1.5 text-xs font-bold text-zinc-300 sm:flex" title="Estimated OpenAI cost this session">
              <span className="h-1.5 w-14 overflow-hidden rounded-full bg-white/10"><span className={`block h-full ${usedPct > 80 ? 'bg-rose-400' : 'bg-emerald-400'}`} style={{ width: usedPct + '%' }} /></span>
              {pill}
            </span>
          )}
          {!preview && (me.login || !me.hosted) && (
            <button onClick={() => setView('profile')} aria-label="Your profile" aria-current={view === 'profile' ? 'page' : undefined}
              className={`flex items-center gap-2 rounded-full border py-1 pl-1 pr-3 text-sm font-bold transition ${view === 'profile' ? 'border-sky-300 bg-sky-300/15 text-white' : 'border-white/15 text-zinc-200 hover:border-white/40'}`}>
              {me.avatar ? <img src={me.avatar} alt="" className="h-7 w-7 rounded-full" /> : <span className="flex h-7 w-7 items-center justify-center rounded-full bg-white/10 text-xs font-black">{(me.login || '?')[0].toUpperCase()}</span>}
              <span className="hidden max-w-[10rem] truncate sm:inline">@{me.login || 'local'}</span>
            </button>
          )}
        </div>
      </header>
      {demoBanner}{previewBanner}

      {scouting && <ScoutProgress title={org ? `Finding your issue in ${org.org}` : 'Scouting for you'} steps={scoutSteps} />}

      {error && (
        <div role="alert" className="mx-auto mt-4 flex max-w-3xl items-start justify-between gap-4 rounded-2xl border border-rose-400/40 bg-rose-500/10 px-5 py-4 text-base font-semibold text-rose-100">
          <span>{error}</span>
          <button onClick={() => setError('')} className="text-sm font-black text-rose-200 hover:text-white" aria-label="Dismiss">✕</button>
        </div>
      )}

      {health?.demo_fixture && <p role="status" className="mx-4 mt-4 relative z-10 rounded-xl border border-amber-300/40 bg-zinc-950 p-4 text-sm text-amber-100">CONTROLLED DEMO FIXTURE — scripted mentor and simulated GitHub checks. No live OpenAI, GitHub issue or external submission.</p>}
      <main>
        {view === 'profile' ? (
          needsLogin ? <HeroSection onResume={profile?.activity?.journeys?.length ? () => run(async () => { const p = profile.activity.journeys[profile.activity.journeys.length - 1]; setAnswers({ ...profile.answers, mode: profile.answers.mode === 'semi' ? 'semi' : 'learn' }); await openTour(p); }) : null} onLogin={() => (location.href = '/auth/login')} orgCount={(catStatus && catStatus.size) || orgs.length || 14} /> :
          <ProfilePage profile={profile} questions={qs} busy={busy} error={profErr} notice={profNote} onSave={saveProfile} onUse={useProfile}
            onExport={() => { window.location.href = '/api/profile/export'; }} onDelete={deleteMyData} onLogout={logout} />
        ) : view === 'browse' ? (
          <BrowseCatalogue data={bData} filters={bFilters} onFilters={(p) => setBFilters((f) => ({ ...f, ...p }))} loading={bLoading} status={catStatus} busy={busy}
            onChoose={(o) => { setView('mentor'); scoutOrg({ org: o.name, github: o.github }, o.repo); }} />
        ) : view === 'replies' ? (
          needsLogin ? <HeroSection onResume={profile?.activity?.journeys?.length ? () => run(async () => { const p = profile.activity.journeys[profile.activity.journeys.length - 1]; setAnswers({ ...profile.answers, mode: profile.answers.mode === 'semi' ? 'semi' : 'learn' }); await openTour(p); }) : null} onLogin={() => (location.href = '/auth/login')} orgCount={(catStatus && catStatus.size) || orgs.length || 14} /> :
          <RepliesInbox prUrl={prUrl} onPrUrl={setPrUrl} onCheck={checkPr} drafts={drafts ? drafts.drafts : null}
            aiFlags={drafts ? drafts.ai_flags || [] : []} canPost={!!(drafts && drafts.can_post)} notice={notice} busy={busy} onSend={sendReply} />
        ) : step === 'hero' || needsLogin ? (
          <HeroSection onResume={profile?.activity?.journeys?.length ? () => run(async () => { const p = profile.activity.journeys[profile.activity.journeys.length - 1]; setAnswers({ ...profile.answers, mode: profile.answers.mode === 'semi' ? 'semi' : 'learn' }); await openTour(p); }) : null} onStart={start} onUseSaved={hasSaved && !needsLogin ? useSaved : null} savedSummary={summary} onLogin={needsLogin ? () => (location.href = '/auth/login') : null} orgCount={(catStatus && catStatus.size) || orgs.length || 14} />
        ) : step === 'interview' && q ? (
          <InterviewStep key={q.id} q={q.q} options={q.options} multi={q.multi} index={qi} total={qs.length}
            value={answers[q.id] ?? (q.multi ? [] : '')} fitCount={qi >= 1 ? fitCount : null}
            onChange={(v) => setAnswers({ ...answers, [q.id]: v })} onBack={() => setQi(Math.max(0, qi - 1))} onNext={next} />
        ) : step === 'matches' ? (
          <>
            <MatchPodium matches={ranking} busy={busy} onChoose={choose} />
            <DiscoverPanel others={others} found={found} loading={discLoading} error={discError} catalogueSize={catSize} busy={busy}
              onChooseOrg={(o) => scoutOrg(o)} onChooseRepo={chooseRepo} onDiscover={discover} />
          </>
        ) : step === 'issues' ? (
          <IssueBoard issues={scout.picks} org={org ? org.org : ''} skipped={scout.skipped} stats={scout.stats} notice={issueNotice} checking={checking}
            alternatives={allOrgs.filter((x) => !org || x.github.toLowerCase() !== org.github.toLowerCase()).slice(0, 4)} busy={busy}
            onPick={pickIssue} onRecheck={recheck} onTry={(a) => scoutOrg(a)}
            onBack={() => { setStep('matches'); back3d(); }} />
        ) : step === 'job' && issue ? (
          <FullAutoJob defaultSignoff={!!(profile && profile.settings && profile.settings.signoff_default)} issue={issue} job={job} command={jobCmd} error={jobErr} busy={busy} onCreate={createJob} onApprove={approveJob} onCancel={cancelJob}
            onBack={() => { setStep('issues'); }} onLearn={switchToLearn} />
        ) : step === 'work' && issue ? (
          (
            <WorkspaceView issue={issue} tourHtml={tourHtml} structure={structure} messages={msgs} onSend={send} busy={busy}
              mode={answers.mode === 'semi' ? 'semi' : 'learn'} onBack={() => setStep('issues')}
              costNote={usage ? `Estimated this session: $${usage.session.cost_usd.toFixed(3)} of $${usage.session_budget_usd.toFixed(2)}` : ''} />
          )
        ) : null}
      </main>
    </div>
  );
}
