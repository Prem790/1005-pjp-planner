"""Reproducible full assignment workflow; never commits or sends artifacts."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time


def run_pipeline(workbook,simulator):
    from .audit import main as audit_main
    from .ranking import run_ranking
    from .selection import run_selection
    from .planning import run_planning
    from .nudges import run_nudges
    from .evaluation import run_static_evaluation
    from .replanning import prepare_context
    from .adaptive_evaluation import run_adaptive_evaluation
    from .reporting import build_submission
    workbook,simulator = Path(workbook),Path(simulator)
    started = time.monotonic()
    for path in [workbook,simulator]:
        if not path.exists():
            raise FileNotFoundError(f'Place assignment inputs locally as documented in README: {path}')
    original = {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in [workbook,simulator]}
    logs = Path('artifacts/pipeline')
    logs.mkdir(parents=True,exist_ok=True)
    def check(name,command):
        result = subprocess.run([sys.executable,'-W','error',*command],capture_output=True,text=True)
        (logs/f'{name}.log').write_text(result.stdout+'\n'+result.stderr)
        if result.returncode:
            raise RuntimeError(f'{name} failed; see {logs/name}.log\n{result.stderr[-2000:]}')
        print(f'Verified: {name}',flush=True)
    print('1/9 Audit source workbook',flush=True)
    audit_main(['--workbook',str(workbook),'--out','artifacts/audit'])
    print('2/9 Rank and verify dealer evidence',flush=True)
    run_ranking(workbook,Path('config/ranking.json'),Path('artifacts/ranking'))
    check('rankings',['scripts/verify_rankings.py','--workbook',str(workbook)])
    print('3/9 Select and verify provisional focus',flush=True)
    run_selection(workbook,Path('artifacts/ranking/dealer_rankings.csv'),Path('config/selection.json'),Path('artifacts/focus'))
    check('focus',['scripts/verify_focus.py','--workbook',str(workbook)])
    print('4/9 Build and verify November routes',flush=True)
    run_planning(workbook,Path('artifacts/focus/focus_dealers.csv'),Path('config/selection.json'),Path('config/scheduling.json'),Path('artifacts/plan'))
    check('plan',['scripts/verify_plan.py','--workbook',str(workbook)])
    print('5/9 Generate and verify WhatsApp drafts',flush=True)
    run_nudges(Path('artifacts/plan/plan_november.csv'),Path('artifacts/plan/scheduled_focus_dealers.csv'),Path('config/ranking.json'),Path('config/nudges.json'),Path('artifacts/nudges'))
    check('nudges',['scripts/verify_nudges.py','--workbook',str(workbook)])
    print('6/9 Evaluate static practice baseline',flush=True)
    run_static_evaluation(workbook,Path('artifacts/plan/plan_november.csv'),Path('artifacts/plan/scheduled_focus_dealers.csv'),simulator,Path('artifacts/simulation/static'))
    print('7/9 Prepare context and evaluate adaptive policy',flush=True)
    prepare_context(workbook,Path('artifacts/ranking/dealer_rankings.csv'),Path('artifacts/plan/scheduled_focus_dealers.csv'),Path('config/selection.json'),Path('config/replanning.json'),Path('artifacts/replanning/context.json'))
    run_adaptive_evaluation(workbook,Path('artifacts/plan/plan_november.csv'),Path('artifacts/replanning/context.json'),Path('artifacts/simulation/adaptive'),simulator=simulator)
    check('adaptive',['scripts/verify_adaptive.py'])
    print('8/9 Run synthetic tests',flush=True)
    check('tests',['-m','unittest','discover','-s','tests','-v'])
    print('9/9 Build private submission and two-page write-up',flush=True)
    destination = build_submission()
    for name,value in original.items():
        if hashlib.sha256(Path(name).read_bytes()).hexdigest()!=value:
            raise ValueError(f'Input changed during pipeline: {name}')
    result = dict(status='complete',source_inputs_unchanged=True,input_sha256=original,
        duration_seconds=time.monotonic()-started,submission_directory=str(destination),
        committed=False,uploaded=False)
    (logs/'run_summary.json').write_text(json.dumps(result,indent=2)+'\n')
    print(f'Full workflow completed in {result["duration_seconds"]:.1f}s. No commits or uploads performed.')
    return result
