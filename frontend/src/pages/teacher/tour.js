// tour.js — the teacher's guided tour (components/Tour), in the interface
// language. Targets are data-tour marks of the teacher page.

export function teacherTour(t) {
  const steps = [
    { title: 't.tour.welcome.title', text: 't.tour.welcome.text' },
    { target: 'new-class', nav: true, title: 't.tour.newClass.title', text: 't.tour.newClass.text' },
    { target: 'classes', nav: true, title: 't.tour.classes.title', text: 't.tour.classes.text' },
    { target: 'tabs', title: 't.tour.tabs.title', text: 't.tour.tabs.text' },
    { target: 'help', nav: true, title: 't.tour.help.title', text: 't.tour.help.text' },
  ];
  return steps.map((s) => ({ ...s, title: t(s.title), text: t(s.text) }));
}
