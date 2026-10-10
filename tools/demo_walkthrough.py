"""Repeatable browser verification/recording against the labelled fixture only.
Run: .venv/bin/python tools/demo_walkthrough.py
"""
import json
import re
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).absolute().parent.parent
OUT = ROOT / "docs" / "media" / "hackathon"
OUT.mkdir(parents=True, exist_ok=True)
DIFF = (ROOT / 'demo/change.diff').read_text()
EVIDENCE = json.loads((ROOT / 'demo/test-evidence.json').read_text())
AFTER = EVIDENCE['results'][1]
CORRECT = ['range has an exclusive upper bound, so subtracting one excludes the last index.',
           'For a singleton, range(1) visits index 0 and preserves the one element.',
           'The test preserves the last item; invalid input types and non-list iterables remain untested.']


def run():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, record_video_dir=str(OUT))
        page = context.new_page()
        errors = []
        page.on('pageerror', lambda e: errors.append(str(e)))
        assert context.request.get('http://127.0.0.1:8001/api/health').json().get('demo_fixture'), 'Refusing to reset a non-fixture server'
        context.request.delete('http://127.0.0.1:8001/api/profile')
        page.goto('http://127.0.0.1:8001')
        page.get_by_role('button', name='Find my first issue').click()
        questions = context.request.get('http://127.0.0.1:8001/api/questions').json()
        for i, q in enumerate(questions):
            value = {'mode':'learn','languages':'Python','skill':'beginner','interests':'web','goal':'hackathon','time':'event','git_level':'commit','contrib_type':'any','machine':'normal','legal':'both'}[q['id']]
            label = next(o[1] for o in q['options'] if o[0] == value)
            page.get_by_role('button', name=re.compile(re.escape(label))).click()
            page.get_by_role('button', name=re.compile('Show my matches|Next')).click()
        page.get_by_role('button', name=re.compile('Find me an issue')).click()
        page.get_by_role('button', name='Start working on this').click()
        page.get_by_role('tab', name='Repo tour').click()
        page.get_by_text('Controlled demo repository tour').wait_for()
        page.get_by_role('tab', name='Contribution review').click()
        page.get_by_role('heading', name='Issue verification').wait_for()
        page.get_by_label('Relevant file paths').fill('parser.py, test_parser.py')
        page.get_by_role('button', name='Fetch repository files').click()
        page.get_by_text('Source context saved for the coach.').wait_for()
        page.get_by_label('Unified diff').fill(DIFF)
        page.get_by_label(re.compile('Student-supplied evidence')).fill(f"Controlled fixture, executed by engineer\nCommand: {AFTER['command']}\nExit: {AFTER['exit_status']}\nOutput: {AFTER['output']}")
        page.get_by_label('Editable PR description').fill('Fixes #7. The loop excluded the final index; use len(items) as its exclusive bound. Controlled fixture: pytest exited 0 with two passing tests after the change. Invalid input types, third-party CI and maintainer acceptance are unverified.')
        page.get_by_label('AI-assistance disclosure').fill('Controlled fixture: OSS Mentor responses are scripted. I inspected the supplied loop change and attached recorded fixture test output; no live OpenAI inference is claimed.')
        page.get_by_role('button', name='Save review', exact=True).click()
        page.get_by_text('Review saved. Changes').wait_for()
        page.get_by_role('button', name='Generate questions').click()
        page.get_by_label(re.compile('1. Why does range')).fill('It sorts the list.')
        page.get_by_label(re.compile('2. Why does changing')).fill('It makes it faster.')
        page.get_by_label(re.compile('3. What does test')).fill('All inputs pass.')
        page.get_by_role('button', name='Check my understanding').click()
        page.get_by_text(re.compile('Try again: range')).wait_for()
        assert page.get_by_role('heading', name='Not ready for review yet').count() == 1
        page.screenshot(path=str(OUT/'checkpoint-feedback-desktop.png'))
        for i, answer in enumerate(CORRECT):
            page.locator('textarea').nth(i + 4).fill(answer)
        page.get_by_role('button', name='Check my understanding').click()
        page.get_by_role('heading', name='Ready for human review').wait_for()
        page.screenshot(path=str(OUT/'review-desktop.png'))
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path=str(OUT/'review-mobile.png'), full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Narrow viewport overflow'
        page.reload()
        page.get_by_role('button', name='Resume guided contribution').click()
        page.get_by_role('tab', name='Contribution review').click()
        page.get_by_role('heading', name='Ready for human review').wait_for()
        page.get_by_label('Unified diff').fill(DIFF + '\n')
        assert page.get_by_role('heading', name='Not ready for review yet').count() == 1
        page.get_by_role('button', name='Save review', exact=True).click()
        page.get_by_text('Checkpoint: incomplete').wait_for()
        assert not errors, errors
        result={'desktop':'1440x1000','narrow':'390x844','browser_errors':errors,'flows':['profile','deterministic match','simulated verification','tour','fetched code','diff attachment','attributed test evidence','failed checkpoint','successful retry','persisted reload','diff invalidation']}
        (OUT/'browser-verification.json').write_text(json.dumps(result,indent=2))
        print(json.dumps(result))
        video=page.video
        context.close()
        video.save_as(str(OUT/'controlled-fixture-walkthrough.webm'))
        video.delete()
        browser.close()


if __name__ == '__main__':
    run()
