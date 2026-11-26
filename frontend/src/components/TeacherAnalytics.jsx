/**
 * TeacherAnalytics.jsx — the teacher Analytics tab, for a class or one lab.
 *
 * Data: GET /api/teacher/analytics/classes/{classId}/details[?lab_id=]
 * Refill: POST /api/teacher/classes/{classId}/students/{studentId}/quota-refill,
 * or POST /api/teacher/classes/{classId}/quota-refill-all for every student.
 * A refill gives a full day and week again; usage is kept.
 *
 * Teacher test sessions are not counted. Days and hours are Paris time; the
 * charts cover the last 30 days. "Today" token figures are the student's
 * quota, which all their classes share.
 *
 * The admin Classes tab shows the same view with `refill={false}` (the admin
 * sets quotas in Users) and without the links to conversations.
 */
import { useEffect, useRef, useState } from 'react';
import toast from 'react-hot-toast';

import api from '../lib/api';
import Icon from './Icon';
import {
  busiestHour, canRefill, heatLevel, quotaShare, sortStudents, tipShift,
} from '../lib/analytics';
import { useLanguage, useT } from '../lib/i18n';

const WEEKDAY_KEYS = ['mon', 'tue', 'wed', 'thu', 'fri', 'sat', 'sun'];
// Intensity in the heat map: shades of the text colour, not the citation colour.
const HEAT = ['bg-ink-surface', 'bg-cream-muted/25', 'bg-cream-muted/45', 'bg-cream-muted/70', 'bg-cream-secondary'];
const LOCALES = { zh: 'zh-CN', fr: 'fr-FR', en: 'en-GB' };

// The helpers of lib/analytics.js return English words: these give the same text in the page language.
function useWords() {
  const t = useT();
  const lang = useLanguage();
  return {
    weekday: (i) => t(`p.an.day.${WEEKDAY_KEYS[i]}`),
    questions: (n) => t(n === 1 ? 'p.an.question1' : 'p.an.questionN', { n }),
    shortDay: (day) => {
      const [y, m, d] = day.split('-').map(Number);
      return new Date(y, m - 1, d).toLocaleDateString(LOCALES[lang] || 'en-GB', { day: 'numeric', month: 'short' });
    },
    lastActive: (iso) => {
      if (!iso) return t('p.an.never');
      const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
      const days = Math.round((start(new Date()) - start(new Date(iso))) / 86400000);
      if (days <= 0) return t('p.an.today');
      if (days === 1) return t('p.an.yesterday');
      return t('p.an.daysAgo', { n: days });
    },
  };
}

export default function TeacherAnalytics({
  data, classId, onReload, onOpenConversation, onOpenStudent, refill = true,
}) {
  const t = useT();
  if (!data) {
    return (
      <div className="flex items-center gap-2 text-[14px] text-cream-muted">
        <div className="h-4 w-4 rounded-full border-2 border-cream-muted border-t-transparent animate-[spin_0.8s_linear_infinite]" />
        {t('p.an.loading')}
      </div>
    );
  }
  const o = data.overview;
  const rated = o.thumbs_up + o.thumbs_down;
  return (
    <div className="space-y-6 max-w-5xl">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label={t('p.an.active7')} value={`${o.active_7d} / ${o.members}`}
          hint={t('p.an.activeEver', { a: o.active_ever, b: o.members })} />
        <Stat label={t('p.an.questions7')} value={o.questions_7d} hint={t('p.an.inAll', { n: o.questions })} />
        <Stat label={t('p.an.conversations')} value={o.sessions} />
        <Stat label={t('p.an.sourcesOpened')} value={`${o.answers_opened ?? 0} / ${o.answers_cited ?? 0}`}
          hint={t('p.an.sourcesOpenedHint', { n: o.citation_opens ?? 0 })} />
        <Stat label={t('p.an.thumbsDown')} value={o.thumbs_down}
          hint={rated ? t('p.an.ofRated', { pct: Math.round((100 * o.thumbs_down) / rated), n: rated }) : t('p.an.noRated')} />
        <Stat label={t('p.an.atLimit')} value={o.at_limit_today} warn={o.at_limit_today > 0}
          hint={!o.at_limit_today ? t('p.an.nobodyBlocked')
            : refill ? t('p.an.resetBelow') : t('p.an.resetInUsers')} />
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('p.an.perDay', { n: data.period_days })}>
          <DailyBars days={data.daily} />
        </Card>
        <Card title={t('p.an.when')} note={<BusiestNote heatmap={data.heatmap} />}>
          <Heatmap heatmap={data.heatmap} />
        </Card>
      </div>

      <Card title={t('p.an.students')} note={
        refill && data.students.some(canRefill) && <RefillAll classId={classId} onReload={onReload} />
      }>
        <StudentTable students={data.students} classId={classId} onReload={onReload}
          onOpenStudent={onOpenStudent} refill={refill} />
      </Card>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card title={t('p.an.thumbsDown')} note={t('p.an.thumbsNote')}>
          <EventList items={data.thumbs_down} empty={t('p.an.noThumbs')}
            onOpen={onOpenConversation} />
        </Card>
        <Card title={t('p.an.wrong')} note={t('p.an.wrongNote')}>
          <EventList items={data.problems} empty={t('p.an.nothingWrong')} onOpen={onOpenConversation} />
        </Card>
      </div>
    </div>
  );
}

function Stat({ label, value, hint, warn }) {
  return (
    <div className="rounded-2xl border border-border-subtle bg-ink-raised p-4" title={hint}>
      <p className="text-[13px] text-cream-muted">{label}</p>
      <p className={`mt-1 text-2xl font-semibold ${warn ? 'text-gold' : 'text-cream'}`}>{value}</p>
      {hint && <p className="mt-0.5 text-[12.5px] text-cream-muted">{hint}</p>}
    </div>
  );
}

function Card({ title, note, children }) {
  return (
    <section className="min-w-0 rounded-2xl border border-border-subtle bg-ink-raised p-5">
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
        <h4 className="text-[15px] font-semibold text-cream">{title}</h4>
        {note && <span className="text-[12.5px] text-cream-muted">{note}</span>}
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
      className="pointer-events-none absolute z-10 whitespace-nowrap rounded-md border border-border-default bg-ink-surface px-2 py-1 text-[12px] text-cream shadow-lg">
      {tip.text}
    </div>
  );
}

export function DailyBars({ days }) {
  const t = useT();
  const w = useWords();
  const max = Math.max(1, ...days.map((d) => d.questions));
  const { ref, tip, on, hide } = useTip();
  return (
    <div ref={ref} className="relative" onMouseLeave={hide}>
      <div className="flex h-28 items-end gap-[2px]" role="img"
        aria-label={t('p.an.barsLabel', { n: max })}>
        {days.map((d) => (
          <div key={d.date} {...on(`${w.shortDay(d.date)}: ${w.questions(d.questions)}`)}
            className="group flex h-full flex-1 cursor-default items-end">
            <div className="w-full rounded-t bg-cream-muted group-hover:bg-cream-secondary"
              style={{ height: d.questions ? `${Math.max(4, (100 * d.questions) / max)}%` : '1px' }} />
          </div>
        ))}
      </div>
      <div className="mt-1 flex justify-between text-[12px] text-cream-muted">
        <span>{w.shortDay(days[0].date)}</span>
        <span>{t('p.an.maxPerDay', { n: max })}</span>
        <span>{w.shortDay(days[days.length - 1].date)}</span>
      </div>
      <Tip tip={tip} />
    </div>
  );
}

function BusiestNote({ heatmap }) {
  const t = useT();
  const w = useWords();
  const best = busiestHour(heatmap);
  return best
    ? t('p.an.busiest', { day: w.weekday(WEEKDAY_KEYS.indexOf(best.day.toLowerCase().slice(0, 3))), from: best.hour, to: best.hour + 1, q: w.questions(best.count) })
    : t('p.an.noQuestions');
}

function Heatmap({ heatmap }) {
  const t = useT();
  const w = useWords();
  const max = Math.max(0, ...heatmap.flat());
  const { ref, tip, on, hide } = useTip();
  return (
    <div ref={ref} className="relative" onMouseLeave={hide}>
      <div className="overflow-x-auto" onScroll={hide}>
        <div className="grid min-w-[420px] grid-cols-[2rem_repeat(24,minmax(0,1fr))] gap-[2px] text-[10px] text-cream-muted">
          {heatmap.map((hours, day) => (
            <div key={WEEKDAY_KEYS[day]} className="contents">
              <span className="self-center">{w.weekday(day)}</span>
              {hours.map((count, hour) => (
                <div key={hour} {...on(`${w.weekday(day)} ${hour}:00–${hour + 1}:00: ${w.questions(count)}`)}
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
        <span>{t('p.an.inOneHour', { q: w.questions(max) })}</span>
      </div>
      <Tip tip={tip} />
    </div>
  );
}

function StudentTable({ students, classId, onReload, onOpenStudent, refill }) {
  const t = useT();
  const w = useWords();
  const COLUMNS = [
    ['username', t('p.an.col.student')], ['questions', t('p.an.col.questions')], ['last_active', t('p.an.col.last')],
    ['today', t('p.an.today')], ['week', t('p.an.col.week')],
    ['thumbs_down', <Icon key="icon" name="thumbsDown" className="inline h-3.5 w-3.5 align-[-2px]" title={t('p.an.thumbsDown')} />],
  ];
  const [sort, setSort] = useState({ key: 'questions', dir: 'desc' });
  const [busy, setBusy] = useState(null);
  const rows = sortStudents(students, sort.key, sort.dir);
  const toggle = (key) => setSort((s) => (s.key === key
    ? { key, dir: s.dir === 'asc' ? 'desc' : 'asc' }
    : { key, dir: key === 'username' ? 'asc' : 'desc' }));

  const refillQuota = async (s) => {
    setBusy(s.user_id);
    try {
      const out = await api.post(`/api/teacher/classes/${classId}/students/${s.user_id}/quota-refill`);
      toast.success(t('p.an.refilled', { name: s.username, n: (out.limit - out.used).toLocaleString() }));
      await onReload?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(null);
    }
  };

  if (!students.length) return <p className="text-[13px] text-cream-muted">{t('p.an.noStudents')}</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[640px] text-[13px]">
        <thead>
          <tr className="border-b border-border-subtle text-left text-cream-muted">
            {COLUMNS.map(([key, label]) => (
              <th key={key} className="px-2 py-2 font-medium" aria-sort={sort.key === key ? (sort.dir === 'asc' ? 'ascending' : 'descending') : undefined}>
                <button type="button" onClick={() => toggle(key)} className="cursor-pointer hover:text-cream">
                  {label}{sort.key === key ? (sort.dir === 'asc' ? ' ↑' : ' ↓') : ''}
                </button>
              </th>
            ))}
            {refill && <th className="px-2 py-2 text-right font-medium">{t('p.an.col.quota')}</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((s) => (
            <tr key={s.user_id} className="border-b border-border-subtle last:border-0">
              <td className="max-w-[180px] truncate px-2 py-2">
                {onOpenStudent && s.questions > 0 ? (
                  <button type="button" onClick={() => onOpenStudent(s.user_id)}
                    className="cursor-pointer text-cream hover:underline" title={t('p.an.openAudit')}>
                    {s.username}
                  </button>
                ) : <span className={s.questions ? 'text-cream' : 'text-cream-muted'}>{s.username}</span>}
                {!s.member && <span className="ml-1 text-cream-muted">{t('p.an.left')}</span>}
                {s.reset_requested && <span className="ml-1 rounded bg-warn-soft px-1 text-gold" title={t('p.an.askedToday')}>{t('p.an.asksReset')}</span>}
                {s.resets > 0 && <span className="ml-1 text-cream-muted" title={t('p.an.resetsTitle')}>· {t('p.an.resetCount', { n: s.resets })}</span>}
              </td>
              <td className="px-2 py-2 font-mono text-cream-secondary">
                {s.questions || <span className="text-cream-muted">{t('p.an.noneYet')}</span>}
                {s.sessions > 0 && <span className="text-cream-muted"> · {t('p.an.convShort', { n: s.sessions })}</span>}
              </td>
              <td className="px-2 py-2 text-cream-secondary">{w.lastActive(s.last_active)}</td>
              <td className="px-2 py-2">
                <Share used={s.used_today} limit={s.limit_today} blocked={s.blocked === 'day'} span="today" />
              </td>
              <td className="px-2 py-2">
                <Share used={s.used_week} limit={s.limit_week} blocked={s.blocked === 'week'} span="week" />
              </td>
              <td className="px-2 py-2 font-mono text-cream-secondary">{s.thumbs_down || ''}</td>
              {refill && <td className="px-2 py-1 text-right">
                {canRefill(s) && (
                  <button type="button" disabled={busy === s.user_id} onClick={() => refillQuota(s)}
                    title={t('p.an.refillTitle')}
                    className={`cursor-pointer rounded-full border px-3 py-1 text-[13px] transition-colors disabled:opacity-50 ${
                      s.at_limit ? 'border-transparent bg-ink text-on-ink hover:opacity-90'
                        : 'border-border-default text-cream-secondary hover:bg-ink-hover'}`}>
                    {t('p.an.reset')}
                  </button>
                )}
              </td>}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Share({ used, limit, blocked, span }) {
  const t = useT();
  if (limit == null) return '—';
  const share = quotaShare(used, limit);
  return (
    <div className="flex items-center gap-2" title={t(span === 'today' ? 'p.an.tokensToday' : 'p.an.tokensWeek', { a: used.toLocaleString(), b: limit.toLocaleString() })}>
      <div className="h-1.5 w-16 overflow-hidden rounded bg-ink-surface">
        <div className={`h-full ${blocked ? 'bg-danger' : 'bg-cream-secondary'}`} style={{ width: `${100 * share}%` }} />
      </div>
      <span className={`font-mono ${blocked ? 'text-danger' : 'text-cream-muted'}`}>{Math.round(100 * share)}%</span>
    </div>
  );
}

function RefillAll({ classId, onReload }) {
  const t = useT();
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState(false);
  const run = async () => {
    setAsking(false);
    setBusy(true);
    try {
      const out = await api.post(`/api/teacher/classes/${classId}/quota-refill-all`);
      toast.success(t('p.an.refilledAll', { a: out.refilled, b: out.students }));
      await onReload?.();
    } catch (err) {
      toast.error(err.message);
    } finally {
      setBusy(false);
    }
  };
  if (asking) {
    return (
      <span role="alertdialog" aria-label={t('p.an.resetAll')} className="ml-3 inline-flex flex-wrap items-center gap-2">
        <span className="text-cream">{t('p.an.resetAllAsk')}</span>
        <button type="button" onClick={run}
          className="cursor-pointer rounded-full bg-ink px-3 py-0.5 text-on-ink hover:opacity-90">{t('p.confirm')}</button>
        <button type="button" onClick={() => setAsking(false)}
          className="cursor-pointer rounded-full px-3 py-0.5 text-cream-secondary hover:bg-ink-hover">{t('cancel')}</button>
      </span>
    );
  }
  return (
    <button type="button" onClick={() => setAsking(true)} disabled={busy}
      className="ml-3 cursor-pointer rounded-full border border-border-default px-3 py-0.5 text-cream-secondary hover:bg-ink-hover hover:text-cream disabled:opacity-50">
      {t('p.an.resetAll')}
    </button>
  );
}

function EventList({ items, empty, onOpen }) {
  const t = useT();
  const w = useWords();
  if (!items.length) return <p className="text-[13px] text-cream-muted">{empty}</p>;
  return (
    <ul className="space-y-1">
      {items.map((e) => (
        <li key={`${e.session_id}-${e.at}`}>
          <button type="button" disabled={!onOpen} onClick={() => onOpen?.(e)}
            className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-ink-hover disabled:cursor-default disabled:hover:bg-transparent">
            <span className="block truncate text-[13px] text-cream">{e.question || t('p.an.noQuestionText')}</span>
            <span className="block text-[12.5px] text-cream-muted">
              {e.reason && <span className="mr-1 rounded bg-warn-soft px-1 text-gold">{e.reason}</span>}
              {e.username}{e.lab_name ? ` · ${e.lab_name}` : ''} · {w.lastActive(e.at)}
            </span>
          </button>
        </li>
      ))}
    </ul>
  );
}
