// citationReport.js — the lab citation report (GET /api/teacher/labs/{id}/citation-report)
// as Markdown, for the teacher's export. Every number comes from the report.

const day = (iso) => (iso ? iso.slice(0, 10) : '');

function periodText(period) {
  const start = day(period?.start);
  // The API end is exclusive (the day after the last day chosen).
  const end = period?.end ? day(new Date(new Date(period.end).getTime() - 86400000).toISOString()) : '';
  if (start && end) return `${start} to ${end}`;
  if (start) return `from ${start}`;
  if (end) return `until ${end}`;
  return 'all time';
}

const cell = (text) => String(text ?? '').replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim();

/** The report as a Markdown document. */
export function reportMarkdown(report, labName = '') {
  const t = report.totals;
  const lines = [
    `# Course use${labName ? ` — ${labName}` : ''}`,
    '',
    `Period: ${periodText(report.period)}. Teacher test drives are not counted.`,
    '',
    `- Questions: ${t.questions} from ${t.students} student(s)`,
    `- Answers citing the course: ${t.answers_with_sources}`,
    `- Answered with a question back to the student: ${t.questions_back ?? 0}`,
    `- Questions without a verified answer: ${t.unverified}`,
    `- Answers marked wrong by a student: ${t.marked_wrong}`,
    '',
    '## Files',
    '',
    '| File | Answers | Share | Students | Most cited pages |',
    '| --- | ---: | ---: | ---: | --- |',
  ];
  for (const f of report.files) {
    const pages = f.pages.slice(0, 5).map((p) => `p. ${p.pdf_page_no} (${p.answers})`).join(', ');
    lines.push(`| ${cell(f.filename)} | ${f.answers} | ${Math.round(f.share * 100)}% | ${f.students} | ${pages} |`);
  }
  for (const f of report.files) {
    lines.push('', `### ${f.filename}`, '');
    for (const p of f.pages) {
      lines.push(`- Page ${p.pdf_page_no}: ${p.answers} answer(s), ${p.students} student(s)`);
      for (const passage of p.passages.slice(0, 3)) {
        if (passage.excerpt) lines.push(`  - "${cell(passage.excerpt)}" (${passage.answers})`);
      }
      for (const e of p.examples) {
        lines.push(`  - ${e.student}, ${day(e.asked_at)}: ${cell(e.question) || '(question not stored)'}`);
      }
    }
  }
  if (report.uncited_files.length) {
    lines.push('', '## Files no answer cited', '');
    for (const f of report.uncited_files) lines.push(`- ${f.filename}`);
  }
  if (report.unverified.length) {
    lines.push('', '## Questions without a verified answer', '');
    for (const q of report.unverified) lines.push(`- ${q.student}, ${day(q.asked_at)}: ${cell(q.question)} (${q.outcome})`);
  }
  if (report.marked_wrong.length) {
    lines.push('', '## Answers marked wrong', '');
    for (const w of report.marked_wrong) {
      const pages = w.pages.map((p) => `${p.filename} p. ${p.pdf_page_no}`).join(', ');
      lines.push(`- ${w.student}, ${day(w.asked_at)}: ${cell(w.question) || '(question not stored)'} — cited ${pages}`);
    }
  }
  return `${lines.join('\n')}\n`;
}
