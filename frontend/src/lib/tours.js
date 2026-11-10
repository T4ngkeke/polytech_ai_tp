/**
 * tours.js — the guided tour of each role, step by step.
 *
 * A step shows its text next to the element marked data-tour="<target>". When
 * that element is not on screen (a phone, nothing selected yet), the text shows
 * in the middle instead. `nav: true` opens the phone drawer first (the class
 * list lives there). All texts are here, so that they can be translated later.
 */

export const TOURS = {
  student: [
    { title: 'Welcome', text: 'Edu-LLM is a tutor that answers from your course files. This short tour shows where things are. You can open it again with the ? button.' },
    { target: 'join-class', nav: true, title: 'Join a class', text: 'Join a class with the 6-character code your teacher gives you.' },
    { target: 'classes', nav: true, title: 'Pick a TP', text: 'Your classes and their TPs. Pick a TP: each one has its own course files.' },
    { target: 'new-session', nav: true, title: 'Conversations', text: 'Each conversation is saved. Start a new one for a new topic. Your teacher can read them.' },
    { target: 'modes', title: 'Three ways to answer', text: 'Direct answers at once, without the course files. Search finds the right pages in a few seconds. Research reads the files in depth, in about a minute.' },
    { target: 'input', title: 'Ask', text: 'Ask in any language. An answer cites the course: click [E1] or a page under the answer to open it.' },
    { target: 'quota', nav: true, title: 'Your limits', text: 'How much you have used today and this week. A question that has started always finishes.' },
    { target: 'help', nav: true, title: 'Help', text: 'Open this tour again here at any time.' },
  ],
  teacher: [
    { title: 'Welcome', text: 'This short tour shows how to set up a class for your students. You can open it again with the ? button.' },
    { target: 'new-class', nav: true, title: 'Create a class', text: 'Create a class. Students join it with its invite code, which you find in the class Settings.' },
    { target: 'classes', nav: true, title: 'Classes and TPs', text: 'A class has TPs (labs). Click a class or a TP to manage it; "+ Add Lab" adds a TP.' },
    { target: 'tabs', title: 'Set up a class or a TP', text: 'Settings: invite code, exam mode and the files open during an exam. Rules: what the tutor must do. Documents: upload the course PDFs. Analytics: what students ask. In a TP, Test drive lets you try the tutor as a student.' },
    { target: 'help', nav: true, title: 'Help', text: 'Open this tour again here at any time.' },
  ],
};

export const TOUR_BUTTONS = { back: 'Back', next: 'Next', done: 'Done', skip: 'Skip tour', of: 'of' };

const GAP = 12;   // between the lit element and the text box

/**
 * Where the text box goes: below the element, else above, else beside it,
 * always inside the screen. Without an element: in the middle.
 * rect {top, left, width, height}, box {width, height}, view {width, height}.
 */
export function boxPosition(rect, box, view) {
  const width = Math.min(box.width, view.width - 24);
  if (!rect) return { top: Math.max(12, (view.height - box.height) / 2), left: (view.width - width) / 2, width };
  const left = Math.min(Math.max(12, rect.left), view.width - width - 12);
  if (rect.top + rect.height + GAP + box.height <= view.height - 12) return { top: rect.top + rect.height + GAP, left, width };
  if (rect.top - GAP - box.height >= 12) return { top: rect.top - GAP - box.height, left, width };
  const right = rect.left + rect.width + GAP;
  const side = right + width <= view.width - 12 ? right : Math.max(12, rect.left - GAP - width);
  return { top: Math.min(Math.max(12, rect.top), view.height - box.height - 12), left: side, width };
}
