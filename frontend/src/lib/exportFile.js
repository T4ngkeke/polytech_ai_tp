/**
 * Exports of the teacher's reports: a text file to download, or one part of
 * the page printed (the print dialog also saves it as PDF). The browser does
 * the work; nothing is generated on the server.
 */

/** Download `text` as a file named `name`. */
export function downloadText(name, text, type = 'text/markdown;charset=utf-8') {
  const url = URL.createObjectURL(new Blob([text], { type }));
  const link = document.createElement('a');
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/**
 * Print a copy of `element` alone, in the light theme. The copy sits in
 * #print-root, the only thing index.css shows in print; controls marked
 * `no-print` are left out. `title` is the PDF's default file name.
 */
export function printElement(element, title) {
  const root = document.createElement('div');
  root.id = 'print-root';
  root.appendChild(element.cloneNode(true));
  document.body.appendChild(root);
  const html = document.documentElement;
  const theme = html.dataset.theme;
  const pageTitle = document.title;
  html.dataset.theme = 'light';
  if (title) document.title = title;
  let restored = false;
  const restore = () => {
    if (restored) return;
    restored = true;
    root.remove();
    if (theme === undefined) delete html.dataset.theme;
    else html.dataset.theme = theme;
    document.title = pageTitle;
    window.removeEventListener('afterprint', restore);
  };
  // print() returns when the dialog closes in desktop browsers; afterprint
  // follows. Some mobile browsers return at once, so only afterprint restores.
  window.addEventListener('afterprint', restore);
  window.print();
}
