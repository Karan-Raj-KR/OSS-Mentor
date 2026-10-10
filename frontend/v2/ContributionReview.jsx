export default function ContributionReview({ issue }) {
  const [now, setNow] = React.useState(Date.now());
  React.useEffect(() => { const t = setInterval(() => setNow(Date.now()), 15000); return () => clearInterval(t); }, []);
  const [review, setReview] = React.useState(null);
  const [draft, setDraft] = React.useState({ diff: '', evidence: '', pr_description: '', disclosure: '' });
  const [answers, setAnswers] = React.useState([]);
  const [paths, setPaths] = React.useState('');
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const [note, setNote] = React.useState('');
  const [dirty, setDirty] = React.useState(false);
  const target = { repo: issue.repo, number: issue.number };
  const field = 'mt-2 w-full rounded-xl border border-white/25 bg-black/60 p-3 text-sm text-white placeholder-zinc-400 focus:outline-none focus-visible:ring-2 focus-visible:ring-sky-300';
  const button = 'rounded-full border border-white/30 px-5 py-2.5 text-sm font-bold text-white hover:border-sky-300 focus-visible:ring-2 focus-visible:ring-sky-300 disabled:opacity-40';
  const request = async (path, data) => {
    const r = await fetch(path, data ? { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...target, ...data }) } : {});
    const j = await r.json();
    if (!r.ok) throw new Error(j.error || j.detail || 'Request failed. Retry.');
    return j;
  };
  const apply = (j) => {
    setReview(j); setDraft({ diff: j.diff || '', evidence: j.evidence || '', pr_description: j.pr_description || '', disclosure: j.disclosure || '' });
    setAnswers(j.checkpoint?.answers || []); setDirty(false);
  };
  const run = async (fn) => {
    setBusy(true); setError(''); setNote('');
    try { await fn(); } catch (e) { setError(e.message); } finally { setBusy(false); }
  };
  React.useEffect(() => { let current = true; request(`/api/journey?repo=${encodeURIComponent(issue.repo)}&number=${issue.number}`).then(j => { if (current) apply(j); }).catch(e => { if (current) setError(e.message); }); return () => { current = false; }; }, [issue.repo, issue.number]);
  const edit = (key, value) => { setDraft(d => ({ ...d, [key]: value })); setDirty(true); };
  const save = async () => { const j = await request('/api/journey/save', draft); apply(j); setNote('Review saved. Changes to the diff or evidence reset the checkpoint.'); return j; };
  const cp = review?.checkpoint;
  const v = review?.verification || {};
  const policy = review?.policy || {};
  const fresh = now - Date.parse(v.checked_at) <= 300000;
  const ready = review?.ready_for_review && !dirty && fresh;
  return (
    <div className="pointer-events-auto max-h-[80vh] space-y-6 overflow-auto p-5 md:p-8">
      <h3 className="text-3xl text-white">Contribution review</h3>
      <p className="text-sm text-zinc-300">Review a change you can explain. This learning checkpoint helps you practise; it is not proof of competence.</p>
      <a className="break-all text-sky-300 underline" href={issue.url} target="_blank" rel="noopener noreferrer">{issue.repo} #{issue.number}</a>
      {error && <p role="alert" className="text-rose-300">{error} <button className={button} disabled={busy} onClick={() => run(async () => apply(await request(`/api/journey?repo=${encodeURIComponent(issue.repo)}&number=${issue.number}`)))}>Reload saved review</button></p>}
      {note && <p role="status" className="text-emerald-300">{note}</p>}
      {!review ? <p role="status" className="text-zinc-300">Loading saved review…</p> : <>
        <section className="space-y-2 border-b border-white/15 pb-5">
          <h4 className="text-lg font-bold text-white">Issue verification</h4>
          <p className="text-sm text-zinc-300">{v.state || 'Unknown state'} · Assignees: {(v.assignees || []).join(', ') || 'none found'} · Linked open PRs: {(v.linked_prs || []).join(', ') || 'none found'} · Recent claim: {v.claimed === null ? 'unknown' : v.claimed ? 'found' : 'none found'}</p>
          <p className="text-sm text-zinc-300">Checked: {v.checked_at || 'not checked'}. No conflict found does not guarantee availability.</p>
          {(v.unchecked || []).length > 0 && <p className="text-amber-200">Could not inspect: {v.unchecked.join(', ')}</p>}
          {(v.claiming_comments || []).map((c, i) => <p key={i} className="text-sm text-amber-200"><a className="underline" href={c.url} target="_blank" rel="noopener noreferrer">Recent claim ({c.created_at})</a>: {c.text}</p>)}
          <p className="text-sm text-zinc-300">AI policy: {({ restriction_found: 'Explicit restriction found — read the source', review_required: 'AI guidance found — read the source', no_restriction_found: 'No restriction found in inspected files', unknown: 'Unknown — no policy files found' })[policy.status] || 'Unknown'}</p>
          {(policy.sources || []).map(s => <a key={s.file} className="mr-3 inline-block break-all text-sm text-sky-300 underline" href={s.url} target="_blank" rel="noopener noreferrer">{s.file}</a>)}
          {(policy.ai_flags || []).map((f, i) => <p key={i} className="text-sm text-amber-200">{f.line}</p>)}
          <button className={button} disabled={busy || dirty} onClick={() => run(async () => apply(await request('/api/journey/begin', {})))}>Re-check issue and policy</button>
        </section>
        <section>
          <h4 className="text-lg font-bold text-white">Ground the coaching in code</h4>
          <label className="block text-sm text-zinc-300">Relevant file paths (comma-separated, up to 5)
            <input className={field} value={paths} onChange={e => setPaths(e.target.value)} placeholder="src/example.py, tests/test_example.py" />
          </label>
          <button className={`${button} mt-3`} disabled={busy || dirty || !paths.trim()} onClick={() => run(async () => { apply(await request('/api/journey/sources', { paths: paths.split(',').map(p => p.trim()).filter(Boolean) })); setNote('Source context saved for the coach.'); })}>Fetch repository files</button>
          {Object.entries(review.sources || {}).map(([path, src]) => <details key={path} className="mt-3 text-sm text-zinc-200"><summary>{path} — {src.text === null ? 'unavailable' : src.truncated ? 'fetched (truncated)' : 'fetched'}</summary><a className="text-sky-300 underline" href={src.url} target="_blank" rel="noopener noreferrer">Source</a><pre className="mt-2 overflow-auto whitespace-pre-wrap rounded-lg bg-black p-3 font-mono">{src.text ?? 'No file content available. The mentor must not invent it.'}</pre></details>)}
        </section>
        <section className="space-y-4">
          <h4 className="text-lg font-bold text-white">Actual diff and test evidence</h4>
          <label className="block text-sm text-zinc-300">Unified diff (student-supplied; run git diff HEAD)
            <textarea className={`${field} font-mono`} rows="8" maxLength="24000" value={draft.diff} onChange={e => edit('diff', e.target.value)} />
          </label>
          <label className="block text-sm text-zinc-300">Attach a .diff or .patch file
            <input type="file" accept=".diff,.patch,text/plain" className="mt-2 block w-full text-zinc-200" disabled={busy} onChange={e => { const f = e.target.files?.[0]; if (!f) return; if (f.size > 96000) { setError('Diff file is too large.'); return; } f.text().then(t => edit('diff', t)).catch(() => setError('Could not read file. Paste the diff instead.')); }} />
          </label>
          <p className="break-all text-sm text-zinc-300">Changed files: {(review.files || []).join(', ') || 'save a diff to inspect its files'}</p>
          <label className="block text-sm text-zinc-300">Student-supplied evidence — not executed by OSS Mentor. Include command, exit status and relevant output.
            <textarea className={`${field} font-mono`} rows="5" maxLength="12000" value={draft.evidence} onChange={e => edit('evidence', e.target.value)} placeholder="Command: python -m pytest\nExit: 0\nOutput: … (only what actually ran)" />
          </label>
          <label className="block text-sm text-zinc-300">Editable PR description
            <textarea className={field} rows="5" maxLength="6000" value={draft.pr_description} onChange={e => edit('pr_description', e.target.value)} />
          </label>
          <label className="block text-sm text-zinc-300">AI-assistance disclosure (edit to reflect what you used)
            <textarea className={field} rows="3" maxLength="2000" value={draft.disclosure} onChange={e => edit('disclosure', e.target.value)} />
          </label>
          <button className={button} disabled={busy} onClick={() => run(save)}>Save review</button>
          {dirty && <p className="text-sm text-amber-200">Unsaved changes. Save before fetching context, re-checking or answering questions. Readiness is withheld.</p>}
        </section>
        <section className="space-y-3 border-t border-white/15 pt-5">
          <h4 className="text-lg font-bold text-white">Understanding checkpoint</h4>
          <button className={button} disabled={busy || dirty || !review.diff} onClick={() => run(async () => apply(await request('/api/journey/checkpoint', {})))}>Generate questions from saved diff</button>
          {(cp?.questions || []).map((q, i) => <div key={q}><label className="block text-sm text-zinc-200">{i + 1}. {q}<textarea className={field} rows="3" maxLength="2000" value={answers[i] || ''} onChange={e => setAnswers(a => { const n = [...a]; n[i] = e.target.value; return n; })} /></label>{cp.results?.[i] && <p className={`mt-2 text-sm ${cp.results[i].passed ? 'text-emerald-300' : 'text-amber-200'}`}>{cp.results[i].passed ? 'Understood: ' : 'Try again: '}{cp.results[i].feedback}</p>}</div>)}
          {cp && <button className={button} disabled={busy || dirty || cp.questions.some((_, i) => !answers[i]?.trim())} onClick={() => run(async () => { const j = await request('/api/journey/checkpoint', { answers }); apply(j); })}>Check my understanding / retry</button>}
          <p className="text-sm text-zinc-300">Checkpoint: {cp?.complete && !dirty ? 'completed for saved diff' : 'incomplete'}</p>
        </section>
        <section className="space-y-3 border-t border-white/15 pt-5" aria-live="polite">
          <h4 className={`text-xl font-bold ${ready ? 'text-emerald-300' : 'text-amber-200'}`}>{ready ? 'Ready for human review — tests remain unverified' : 'Not ready for review yet'}</h4>
          {!fresh && <p className="text-sm text-amber-200">Issue verification has expired. Re-check before review.</p>}
          <ul className="space-y-2 text-sm text-zinc-300">{[...review.readiness_reasons, ...review.unverified].map(t => <li key={t}>{t}</li>)}</ul>
          <p className="text-sm text-zinc-300">{review.submission}</p>
          <button className={button} disabled={busy || !ready} onClick={() => { const blob = new Blob([`${draft.pr_description}\n\nAI assistance:\n${draft.disclosure}\n\n${review.evidence_label}:\n${draft.evidence}\n\nUnverified:\n${review.unverified.join('\n')}`], { type: 'text/plain' }); const url = URL.createObjectURL(blob); const a = document.createElement('a'); a.href = url; a.download = 'oss-mentor-review.txt'; a.click(); URL.revokeObjectURL(url); }}>Download review draft</button>
        </section>
      </>}
      {busy && <p role="status" className="text-sky-300">Working… please wait.</p>}
    </div>
  );
}
