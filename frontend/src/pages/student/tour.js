// tour.js — the student's guided tour of the new chat (components/Tour), in the
// interface language. Targets are data-tour marks of the new page.

export function studentTour(t) {
  const steps = [
    { title: 'tour.welcome.title', text: 'tour.welcome.text' },
    { target: 'classes', nav: true, title: 'tour.classes.title', text: 'tour.classes.text' },
    { target: 'sessions', nav: true, title: 'tour.sessions.title', text: 'tour.sessions.text' },
    { target: 'plus', title: 'tour.plus.title', text: 'tour.plus.text' },
    { target: 'ask', title: 'tour.ask.title', text: 'tour.ask.text' },
    { target: 'quota', nav: true, title: 'tour.quota.title', text: 'tour.quota.text' },
    { target: 'help', nav: true, title: 'tour.help.title', text: 'tour.help.text' },
  ];
  return steps.map((s) => ({ ...s, title: t(s.title), text: t(s.text) }));
}

export function tourButtons(t) {
  return { back: t('tour.back'), next: t('tour.next'), done: t('tour.done'), skip: t('tour.skip'), of: '/' };
}
