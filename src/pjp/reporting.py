"""Two-page assignment write-up and local, confidential submission packaging."""
import hashlib
import json
from pathlib import Path
import shutil
import statistics
import zipfile
from xml.sax.saxutils import escape


def report_content(static,adaptive):
    sm,am = static['mean_rates'],adaptive['mean_rates']
    methods = [
        ('Problem and scope',
         'Build a usable November 2026 visit plan for three Sales Officers, then adapt it to missed appointments, '
         'sales changes and forecast disruptions. Focus-dealer coverage and timely intervention matter alongside '
         'adherence. The POC runs locally and never sends WhatsApp messages or uploads confidential records.'),
        ('Data and judgement',
         'The workbook supplies 677 dealers, 40,524 signed sales records and 3,714 activity records across 11 sheets. '
         'Sales end on 15 October 2026. Of 677 dealers, 282 lack coordinates; exact GST turnover is unavailable. '
         'Use explicit club membership, current dealer ownership and owner-group scheme balances. Broad GST bands '
         'are investigation signals, not measured wallet share. Returns retain their negative signs. The audit flags '
         'short calls and excessive historical visits; it does not treat them as productive coverage.'),
        ('1. Explainable ranking',
         'Score all dealers: 55% value-weighted decline, 30% supported recent drop, 10% coarse GST peer opportunity '
         'and 5% scheme opportunity. Decline blends MoM, monthly-equivalent QoQ and YoY losses at 40/30/30; compare '
         'complete matched periods. Recent-drop support avoids equating sparse trading with a sharp decline. '
         'Missing evidence earns no unsupported bonus. Scheme targets reused from the previous quarter are labeled '
         'assumptions; related outlets share earned points. Export features, reasons and sensitivity checks.'),
        ('2-3. Focus, calendar and routes',
         'November has 25 Monday-Saturday workdays. Six dealer visits daily and two visits per club require '
         '142/132/149 distinct focus dealers, above the approximate 100 in the brief. Protect all 27 clubs, with '
         'initial visits 14 days apart. Preserve commercial ordering while screening travel. Full-route search '
         'replaces two low-priority remote SO-C outlets explicitly; original selection and replacement reasons remain. '
         'A deterministic monthly swap search uses exact small-route optimization. Travel is Haversine x 1.35 at '
         '18 km/h metro or 30 km/h upcountry. Six 25-minute services plus travel fit 460 minutes, reserving 80 minutes '
         'for two DG calls including travel. No DG identities are invented; no return leg is required by the simulator.'),
        ('4-5. Nudges and evening decisions',
         'Generate 450 dated, evidence-backed WhatsApp drafts under 300 characters, with 12 diverse examples. '
         'Each evening persist observed sales, zero-fill absent dealer sales, and compare 3-5 workdays with the '
         'historical daily baseline. A conservative overdispersed count likelihood filters noisy drop/hike signals; '
         'speculative repeat visits require stronger evidence. Pull owed visits forward for drops, defer suitable '
         'non-club hike visits, restore misses and protect club obligations. Use the next-day forecast for capacity '
         'and travel, retain future anchors where possible, log changes and infeasible obligations. No seed or hidden '
         'event schedule enters the planner.')]
    results = [
        ('What the practice results establish',
         f"The same initial plan was evaluated on published seeds 1-3 with the unmodified supplied simulator. "
         f"Adaptive adherence averages {am['simulated_adherence']:.2%}, versus {sm['simulated_adherence']:.2%} static. "
         f"All 27 club cadences are met in each adaptive run. Drop response rises from {sm['drop_response_rate']:.2%} "
         f"to {am['drop_response_rate']:.2%}. Across 72 evening calls there are no guardrail breaches, re-plan failures "
         f"or timeouts; the longest recorded planner compute time is {adaptive['max_replan_compute_seconds']:.2f}s "
         '(excluding interpreter startup). Independent verification recalculates scores and checks every exported '
         'future route against ownership, frequency, cadence, forecast capacity and time budgets.'),
        ('The coverage trade-off',
         f"Adaptive focus coverage falls from {sm['focus_coverage']:.2%} to {am['focus_coverage']:.2%}. "
         'Forecast disruption days reserve five calls even when actual weather later proves clear. With a full '
         '150-visit baseline, there is little recovery capacity. Pulling scarce slots toward club obligations and '
         'drops changes which non-club dealers get covered. Higher adherence partly reflects a smaller denominator: '
         'report completed calls and focus coverage with it. Initial-plan adherence also falls when successful visits '
         'move off their original dates. This is a business trade-off, not an across-the-board improvement. The '
         'approximate 60% manual benchmark is contextual, not a simulated control; hidden-seed performance is untested.'),
        ('Limits and next improvements',
         'Quiet, low-volume dealers are difficult to distinguish from genuine drops in a few noisy days. False '
         'signals remain possible. Forecast errors and late-month missed visits can make full coverage impossible; '
         'the planner logs shortfalls rather than claiming guaranteed delivery. Missing GPS uses the simulator\'s '
         '25/45-minute allowance and unchanged known position, which cannot establish real route feasibility. '
         'The model does not reserve a separate lunch break or return-to-base leg. DG slots, dealer availability, '
         'road speeds, current scheme balances and location pins require field validation. With more time, use '
         'opening hours, real travel-time matrices, probability-calibrated forecasts, baseline uncertainty, and '
         'explicit business costs for drop response versus coverage. Preserve an operational recovery buffer '
         'instead of assuming all nominal slots are available. Refresh nudge evidence from live observations.'),
        ('Reproduce and inspect',
         'From the repository root, install Python 3.11+ dependencies and run: python -m pjp run. The command audits, '
         'ranks, selects, schedules, drafts nudges, evaluates static/adaptive practice runs, verifies results and '
         'packages this submission. README documents each stage and the --state/--plan/--out re-plan interface. '
         'Artifacts contain source hashes, all decisions, metrics and verification records. Confidential inputs and '
         'outputs stay Git-ignored; the private bundle contains required results, not the source workbook or simulator. '
         'AI assisted implementation; synthetic tests, independent source reconciliation and explicit assumptions '
         'support review. The author should be ready to explain each decision.')]
    return methods,results


def build_submission(root=Path('artifacts')):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle,PageBreak
    from pypdf import PdfReader
    root = Path(root)
    static = json.loads((root/'simulation/static/baseline_summary.json').read_text())
    adaptive = json.loads((root/'simulation/adaptive/adaptive_summary.json').read_text())
    for path in [root/'plan/verification.json',root/'nudges/verification.json',root/'simulation/static/verification.json',root/'simulation/adaptive/verification.json']:
        if json.loads(path.read_text())['status']!='passed':
            raise ValueError(f'Verification is not complete: {path}')
    out = root/'submission'
    out.mkdir(parents=True,exist_ok=True)
    methods,results = report_content(static,adaptive)
    title = ParagraphStyle('Title',fontName='Helvetica-Bold',fontSize=20,leading=24,textColor=colors.HexColor('#17324d'),spaceAfter=6)
    heading = ParagraphStyle('Heading',fontName='Helvetica-Bold',fontSize=10.5,leading=13,spaceBefore=9,spaceAfter=4,textColor=colors.HexColor('#17324d'))
    body = ParagraphStyle('Body',fontName='Helvetica',fontSize=9.5,leading=12.5,spaceAfter=5)
    small = ParagraphStyle('Small',parent=body,fontSize=8,leading=10,textColor=colors.HexColor('#556575'))
    story = [Paragraph('November field-sales planner',title),
             Paragraph('1005 Intelligence Labs | Confidential take-home POC | Prem',small)]
    for label,text in methods:
        story += [Paragraph(escape(label),heading),Paragraph(escape(text),body)]
    story += [PageBreak(),Paragraph('Results and practical limits',title),
              Paragraph('Means across practice seeds 1, 2 and 3. Same initial plan for both policies.',small),Spacer(1,5)]
    labels = [('simulated_adherence','Adherence'),('focus_coverage','Focus coverage'),
              ('club_cadence_met','Club cadence'),('drop_response_rate','Drops visited within 7 days'),
              ('initial_plan_adherence','Original-date adherence')]
    data = [['Metric','Static','Adaptive']]+[[label,f"{static['mean_rates'][key]:.2%}",f"{adaptive['mean_rates'][key]:.2%}"] for key,label in labels]
    sc = statistics.mean(x['visits_completed'] for x in static['per_seed'])
    ac = statistics.mean(x['visits_completed'] for x in adaptive['per_seed'])
    data += [['Completed calls per run',f'{sc:.1f}',f'{ac:.1f}'],['Guardrail breaches / failures','0 / not invoked','0 / 0']]
    table = Table(data,colWidths=[265,105,105],hAlign='LEFT')
    table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#17324d')),('TEXTCOLOR',(0,0),(-1,0),colors.white),
        ('FONTNAME',(0,0),(-1,0),'Helvetica-Bold'),('FONTSIZE',(0,0),(-1,-1),9),('LEADING',(0,0),(-1,-1),11),
        ('BOTTOMPADDING',(0,0),(-1,-1),6),('TOPPADDING',(0,0),(-1,-1),6),
        ('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#eef3f6'),colors.white]),('ALIGN',(1,1),(-1,-1),'RIGHT')]))
    story += [table,Spacer(1,4)]
    for label,text in results:
        story += [Paragraph(escape(label),heading),Paragraph(escape(text),body)]
    def footer(canvas,document):
        canvas.setFont('Helvetica',8)
        canvas.setFillColor(colors.HexColor('#556575'))
        canvas.drawString(42,25,'Confidential | Supplied data and simulator; local practice evaluation')
        canvas.drawRightString(A4[0]-42,25,f'{document.page} / 2')
    pdf = out/'approach_and_results.pdf'
    SimpleDocTemplate(str(pdf),pagesize=A4,rightMargin=42,leftMargin=42,topMargin=35,bottomMargin=38,
                      title='November field-sales planner: approach and results',author='Prem').build(story,onFirstPage=footer,onLaterPages=footer)
    if len(PdfReader(pdf).pages)!=2:
        raise ValueError('Write-up must be exactly two pages; adjust layout before submitting')
    text = ['NOVEMBER FIELD-SALES PLANNER']+[f'{label}\n{text}\n' for label,text in methods]
    text += ['RESULTS AND PRACTICAL LIMITS']+[' | '.join(row) for row in data]+[f'{label}\n{text}\n' for label,text in results]
    reports = root/'reports'
    reports.mkdir(parents=True,exist_ok=True)
    (reports/'approach_and_results.txt').write_text('\n\n'.join(text)+'\n')
    return package_submission(root)


def package_submission(root=Path('artifacts')):
    """Keep one reviewer-facing format per deliverable; retain detail outside ZIP."""
    from pypdf import PdfReader
    root = Path(root)
    out = root/'submission'
    pdf = out/'approach_and_results.pdf'
    if len(PdfReader(pdf).pages)!=2:
        raise ValueError('Submission write-up must be exactly two pages')
    for path in [root/'plan/verification.json',root/'nudges/verification.json',
                 root/'simulation/static/verification.json',root/'simulation/adaptive/verification.json']:
        if json.loads(path.read_text())['status']!='passed':
            raise ValueError(f'Verification is not complete: {path}')
    # Preserve the editable report locally when migrating an existing package.
    editable = out/'approach_and_results.txt'
    if editable.exists():
        reports = root/'reports'
        reports.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(editable,reports/'approach_and_results.txt')
    obsolete = ['approach_and_results.txt','plan_november.csv','focus_repairs.csv',
                'whatsapp_nudges.csv','simulator_comparison.csv',
                'adaptive_verification.json','manifest.json']
    obsolete += [f'practice_seed_{seed}/static_summary.json' for seed in [1,2,3]]
    for name in obsolete:
        (out/name).unlink(missing_ok=True)
    copies = {root/'plan'/f'plan_{so}.csv':out/f'plan_{so}.csv' for so in ['SO-A','SO-B','SO-C']}
    copies.update({root/'nudges/example_nudges.txt':out/'whatsapp_nudges_with_evidence.txt',
                   root/'simulation/adaptive/comparison_report.txt':out/'simulator_comparison.txt'})
    for seed in [1,2,3]:
        copies[root/f'simulation/adaptive/seed_{seed}/summary.json'] = out/f'practice_seed_{seed}'/'summary.json'
    for source,destination in copies.items():
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(source,destination)
    readme = out/'READ_ME.txt'
    readme.write_text('CONFIDENTIAL ASSIGNMENT SUBMISSION\n\n'
        '1. plan_SO-A.csv, plan_SO-B.csv, plan_SO-C.csv: November appointment plans.\n'
        '2. practice_seed_1/summary.json through practice_seed_3/summary.json: adaptive results.\n'
        '3. whatsapp_nudges_with_evidence.txt: twelve drafts with the data behind them.\n'
        '4. approach_and_results.pdf: two-page approach, assumptions and results.\n'
        '5. simulator_comparison.txt: static/adaptive averages and trade-offs.\n\n'
        'Code and setup instructions: accompanying Git repository README.\n'
        'From the repository root after setup: python -m pjp run\n'
        'Simulator callback: python -m pjp replan\n'
        'The simulator appends --state STATE.json --plan CURRENT_PLAN.csv --out NEXT_PLAN.csv.\n'
        'Run the full workflow first to generate artifacts/replanning/context.json.\n'
        'Default memory persists beside the simulator-provided plan, isolating runs.\n\n'
        'Detailed CSVs, static summaries, verification records, repair logs and editable report\n'
        'remain in local artifacts outside this concise bundle and can be regenerated.\n'
        'Source workbook, documents and supplied simulator are excluded. No messages are sent.\n')
    members = sorted([str(p.relative_to(out)) for p in copies.values()]+[pdf.name,readme.name])
    files = {name:hashlib.sha256((out/name).read_bytes()).hexdigest() for name in members}
    source_hash = json.loads((root/'replanning/context.json').read_text())['source_sha256']
    (root/'submission_manifest.json').write_text(json.dumps(dict(source_sha256=source_hash,
        confidential=True,pdf_pages=2,files=files),indent=2)+'\n')
    archive = root/'submission_bundle.zip'
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as z:
        for name in members:
            z.write(out/name,name)
    print(f'Private submission prepared: {len(members)} files in {archive}')
    return out
