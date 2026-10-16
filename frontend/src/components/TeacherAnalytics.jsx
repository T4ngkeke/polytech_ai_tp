/**
 * TeacherAnalytics.jsx — the teacher Analytics tab, for a class or one lab.
 *
 * Data: GET /api/teacher/analytics/classes/{classId}/details[?lab_id=]
 * Refill: POST /api/teacher/classes/{classId}/students/{studentId}/quota-refill
 *
 * Teacher test sessions are not counted. Days and hours are Paris time; the
 * charts cover the last 30 days. "Today" token figures are the student's
 * quota, which all their classes share.
 */
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import Icon from './Icon';
import {
  WEEKDAYS, busiestHour, canRefill, heatLevel, lastActive, questionCount, quotaShare, shortDay,
  sortStudents, tipShift,
} from '../lib/analytics';

const MODE_LABEL = { direct: 'Direct', search: 'Search', research: 'Research' };
const HEAT = ['bg-ink-surface', 'bg-cyan/20', 'bg-cyan/40', 'bg-cyan/65', 'bg-cyan/90'];

export default function TeacherAnalytics({ data, classId, onReload, onOpenConversation, onOpenStudent }) {
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-cream-muted text-sm">
        <div className="w-4 h-4 rounded-full border-2 border-cyan border-t-transparent animate-[spin_0.8s_linear_infinite]" />
        Loading analytics…
      </div>
    );
  }
  const o = data.overview;
  const rated = o.thumbs_up + o.thumbs_down;
  return (
    <div className="space-y-6 max-w-5xl">
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label="Active, 7 days" value={`${o.active_7d} / ${o.members}`}
          hint={`${o.active_ever} of ${o.members} students have asked at least once`} />
        <Stat label="Questions, 7 days" value={o.questions_7d} hint={`${o.questions} in all`} />
        <Stat label="Conversations" value={o.sessions} />
        <Stat label="Thumbs down" value={o.thumbs_down}
          hint={rated ? `${Math.round((100 * o.thumbs_down) / rated)}% of ${rated} rated answers` : 'No answer rated yet'} />
        <Stat label="At limit today" value={o.at_limit_today} warn={o.at_limit_today > 0}
          hint={o.at_limit_today ? 'Refill their quota in the table below' : 'Nobody is blocked'} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={`Questions per day · last ${data.period_days} days`}>
          <DailyBars days={data.daily} />
        </Card>
        <Card title="When students ask" note={<BusiestNote heatmap={data.heatmap} />}>
          <Heatmap heatmap={data.heatmap} />
        </Card>
      </div>

      <Card title="Students" note={<Modes modes={data.modes} />}>
        <StudentTable students={data.students} classId={classId} onReload={onReload}
          onOpenStudent={onOpenStudent} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title="Thumbs down" note="The question before each answer a student rated down">
          <EventList items={data.thumbs_down} empty="No answer rated down."
            onOpen={onOpenConversation} />
        </Card>
        <Card title="Answers that went wrong" note="Failed, or stopped before a full answer">
          <EventList items={data.problems} empty="Nothing went wrong." onOpen={onOpenConversation} />
        </Card>
      </div>
    </div>
  );
}

function Stat({ label, value, hint, warn }) {
  return (
    <div className="rounded-xl border border-border-subtle bg-ink-raised p-3" title={hint}>
      <p className="text-[10px] uppercase tracking-widest text-cream-muted">{label}</p>
      <p className={`mt-1 text-2xl font-display ${warn ? 'text-gold' : 'text-cream'}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[11px] text-cream-muted">{hint}</p>}
    </div>
  );
}

function Card({ title, note, children }) {
  return (
    <section className="min-w-0 rounded-xl border border-border-subtle bg-ink-raised p-4">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h4 className="text-sm font-semibold text-cream">{title}</h4>
        {note && <span className="text-[11px] text-cream-muted">{note}</span>}
      </div>
      {children}
    </section>
  );
}

/**
 * Tooltip shown at once over the bar or cell under the pointer (a tap on a
 * phone). The browser's own `title` tooltip is late and absent on touch.
 */
function useTip() {
  const ref = useRef(null);
  const [tip, setTip] = useState(null);
  const show = (text) => (event) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    const r = event.currentTarget.getBoundingClientRect();
    const x = r.left + r.width / 2 - box.left;
    setTip({ text, x, y: r.top - box.top, shift: tipShift(x, box.width) });
  };
  const hide = () => setTip(null);
  // A phone sends no mouseleave: a tap elsewhere closes the tooltip.
  useEffect(() => {
    if (!tip) return undefined;
    const away = (event) => { if (!ref.current?.contains(event.target)) setTip(null); };
    document.addEventListener('pointerdown', away);
    return () => document.removeEventListener('pointerdown', away);
  }, [tip]);
  const on = (text) => ({ onMouseEnter: show(text), onClick: show(text), 'aria-label': text });
  return { ref, tip, on, hide };
}

function Tip({ tip }) {
  if (!tip) return null;
  return (
    <div role="tooltip" style={{ left: tip.x, top: tip.y - 6, transform: `translate(${tip.shift}, -100%)` }}
      className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border border-border-default bg-ink-surface px-2 py-1 text-[11px] text-cream shadow-lg">
      {tip.text}
    </div>
  );
}

function DailyBars({ days }) {
  const max = Math.max(1, ...days.map((d) => d.questions));
  const { ref, tip, on, hide } = useTip();
  return (
    <div ref={ref} className="relative" onMouseLeave={hide}>
      <div className="flex h-28 items-end gap-[2px]" role="img"
        aria-label={`Questions per day, at most ${max} in one day`}>
        {days.map((d) => (
          <div key={d.date} {...on(`${shortDay(d.date)}: ${questionCount(d.questions)}`)}
            className="group flex h-full flex-1 cursor-default items-end">
            <div className="w-full rounded-t bg-cyan/70 group-hover:bg-cyan"
              style={{ height: d.questions ? `${Math.max(4, (100 * d.questions) / max)}%` : '1px' }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-cream-muted">
        <span>{shortDay(days[0].date)}</span>
        <span>max {max} / day</span>
        <span>{shortDay(days[days.length - 1].date)}</span>
      </div>
      <Tip tip={tip} />
    </div>
  );
}

function BusiestNote({ heatmap }) {
  const best = busiestHour(heatmap);
  return best
    ? `Busiest: ${best.day} ${best.hour}:00–${best.hour + 1}:00 (${questionCount(best.count)})`
    : 'No questions yet';
}

function Heatmap({ heatmap }) {
  const max = Math.max(0, ...heatmap.flat());
  const { ref, tip, on, hide } = useTip();
  return (
    <div ref={ref} className="relative" onMouseLeave={hide}>
      <div className="overflow-x-auto" onScroll={hide}>
        <div className="grid min-w-[420px] grid-cols-[2rem_repeat(24,minmax(0,1fr))] gap-[2px] text-[10px] text-cream-muted">
          {heatmap.map((hours, day) => (
            <div key={WEEKDAYS[day]} className="contents">
              <span className="self-center">{WEEKDAYS[day]}</span>
              {hours.map((count, hour) => (
                <div key={hour} {...on(`${WEEKDAYS[day]} ${hour}:00–${hour + 1}:00: ${questionCount(count)}`)}
                  className={`h-4 rounded-sm ${HEAT[heatLevel(count, max)]} hover:ring-1 hover:ring-cream-secondary`} />
              ))}
            </div>
          ))}
          <span />
          {Array.from({ length: 24 }, (_, hour) => (
            <span key={hour} className="text-center">{hour % 6 === 0 ? hour : ''}</span>
          ))}
        </div>
      </div>
      <div className="mt-2 flex items-center justify-end gap-1 text-[10px] text-cream-muted">
        <span>0</span>
        {HEAT.map((shade) => <span key={shade} className={`h-3 w-3 rounded-sm ${shade}`} />)}
        <span>{questionCount(max)} in one hour</span>
      </div>
      <Tip tip={tip} />
    </div>
  );
}

function Modes({ modes }) {
  const entries = Object.entries(modes);
  if (!entries.length) return null;
  return `Answers by mode: ${entries.map(([m, n]) => `${MODE_LABEL[m] || m} ${n}`).join(' · ')}`;
}

const COLUMNS = [
  ['username', 'Student'], ['questions', 'Questions'], ['last_active', 'Last active'],
  ['today', 'Today'],
  ['thumbs_down', <Icon key="icon" name="thumbsDown" className="inline h-3.5 w-3.5 align-[-2px]" title="Thumbs down" />],
];

function StudentTable({ students, classId, onReload, onOpenStudent }) {
  const [sort, setSort] = useState({ key: 'questions', dir: 'desc' });
  const [busy, setBusy] = useState(null);
  const rows = sortStudents(students, sort.key, sort.dir);
  const toggle = (key) => setSort((s) => (s.key === key
    ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'username' ? 'asc' : 'desc' }));

  const refill = async (s) => {
    setBusy(s.user_id);
    try {
      const out = await api.post(`/api/teacher/classes/${classId}/students/${s.user_id}/quota-refill`);
      toast.success(`${s.username} can use ${(out.limit - out.used).toLocaleString()} tokens today`);
      await onReload?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(null);
    }
  };

  if (!students.length) return <p className="text-xs italic text-cream-muted">No student has joined yet.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[560px] text-xs">
        <thead>
          <tr className="border-b border-border-subtle text-left text-cream-muted">
            {COLUMNS.map(([key, label]) => (
              <th key={key} className="px-2 py-2 font-medium" aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                <button type="button" onClick={() => toggle(key)} className="cursor-pointer hover:text-cream">
                  {label}{sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
            ))}
            <th className="px-2 py-2 text-right font-medium">Quota</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.user_id} className="border-b border-border-subtle last:border-0">
              <td className="max-w-[180px] truncate px-2 py-2">
                {onOpenStudent && s.questions > 0 ? (
                  <button type="button" onClick={() => onOpenStudent(s.user_id)}
                    className="cursor-pointer text-cream hover:text-cyan hover:underline" title="Open in Audit">
                    {s.username}
                  </button>
                ) : <span className={s.questions ? 'text-cream' : 'text-cream-muted'}>{s.username}</span>}
                {!s.member && <span className="ml-1 text-cream-muted">(left)</span>}
              </td>
              <td className="px-2 py-2 font-mono text-cream-secondary">
                {s.questions || <span className="text-cream-muted">none yet</span>}
                {s.sessions > 0 && <span className="text-cream-muted"> · {s.sessions} conv.</span>}
              </td>
              <td className="px-2 py-2 text-cream-secondary">{lastActive(s.last_active)}</td>
              <td className="px-2 py-2">
                {s.limit_today != null ? (
                  <div className="flex items-center gap-2" title={`${s.used_today.toLocaleString()} of ${s.limit_today.toLocaleString()} tokens today`}>
                    <div className="h-1.5 w-20 overflow-hidden rounded bg-ink-surface">
                      <div className={`h-full ${s.at_limit ? 'bg-danger' : 'bg-cyan'}`}
                        style={{ width: `${100 * quotaShare(s.used_today, s.limit_today)}%` }} />
                    </div>
                    <span className={`font-mono ${s.at_limit ? 'text-danger' : 'text-cream-muted'}`}>
                      {Math.round(100 * quotaShare(s.used_today, s.limit_today))}%
                    </span>
                  </div>
                ) : '—'}
              </td>
              <td className="px-2 py-2 font-mono text-cream-secondary">{s.thumbs_down || ''}</td>
              <td className="px-2 py-1 text-right">
                {canRefill(s) && (
                  <button type="button" disabled={busy === s.user_id} onClick={() => refill(s)}
                    title="Give a full daily quota again for today; usage is kept"
                    className={`rounded-lg border px-2.5 py-1 text-xs transition-colors disabled:opacity-50 ${
                      s.at_limit ? 'border-cyan/40 bg-cyan-muted text-cream hover:bg-cyan/20'
                        : 'border-border-default text-cream-secondary hover:bg-ink-hover'}`}>
                    Refill
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function EventList({ items, empty, onOpen }) {
  if (!items.length) return <p className="text-xs italic text-cream-muted">{empty}</p>;
  return (
    <ul className="space-y-1">
      {items.map((e) => (
        <li key={`${e.session_id}-${e.at}`}>
          <button type="button" disabled={!onOpen} onClick={() => onOpen?.(e)}
            className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-ink-hover disabled:cursor-default disabled:hover:bg-transparent">
            <span className="block truncate text-xs text-cream">{e.question || '(no question text)'}</span>
            <span className="block text-[11px] text-cream-muted">
              {e.reason && <span className="mr-1 rounded bg-gold-muted px-1 text-gold">{e.reason}</span>}
              {e.username}{e.lab_name ? ` · ${e.lab_name}` : ''} · {lastActive(e.at)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
