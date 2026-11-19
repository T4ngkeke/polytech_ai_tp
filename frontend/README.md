# Edu-LLM web app

The React app (Vite, Tailwind, Zustand) that students, teachers and admins use. nginx serves the built files and passes `/api/` to the backend (`nginx.conf` for the dev stack, `nginx.release.conf` for the teachers' stack). The project is described in the [README](../README.md).

## Files

| Folder | What it holds |
| --- | --- |
| `src/pages/` | One file per page: `Login`, `Register`, `ChoosePassword`, `Account`, `Chat` (students), `Teacher`, `Admin`, `SourcePdf` (a cited PDF in its own tab). |
| `src/pages/chat/` | The parts of the chat page: session list, message box, history line, quota meter, join-a-class window. |
| `src/pages/teacher/` | The tabs of a class or a lab: Settings, Rules, Students, Audit, Test drive. Analytics and Documents are shared components. |
| `src/pages/admin/` | The admin tabs: Users, Classes, Documents, LLM Config, Maintenance. Monitoring is `components/MonitoringPanel.jsx`. |
| `src/components/` | Pieces used by several pages: a chat message with its sources, the document manager, the PDF preview, analytics, reports, the sidebar, icons (`Icon.jsx`, lucide), the guided tours. |
| `src/lib/` | Code without React: the API client, the chat stream events, citations, answer modes, quota, rules, the history line, and more. Each file with logic has a `*.test.js` next to it. |
| `src/store/` | The Zustand store of the signed-in user (`authStore.js`). |

`Chat.jsx`, `Teacher.jsx` and `Admin.jsx` hold the page frame and its state; each tab or panel lives in its own file in the folder of the same name.

## Run

```bash
npm install
npm run dev      # http://localhost:5173, /api/ goes to the backend on port 8000
npm run build    # the files nginx serves, in dist/
```

## Checks

```bash
node --test src/lib/*.test.js   # unit tests of src/lib (Node 20)
npm run lint                    # eslint
npm run build
```

When Node is not installed on the host, run them in a container, for example `docker run --rm -u $(id -u):$(id -g) -v $PWD:/f -w /f node:20-alpine node --test src/lib/*.test.js`.

`scripts/check_layout_ui.py` (from the project root, with Playwright and Chromium) opens the student chat, a teacher lab and the admin tabs in both themes at phone and desktop width, against a running stack (`UI_TEST_URL`, default `http://127.0.0.1:8080`). All API answers are faked, so it changes no data. It also checks that `/grafana/` is closed without the monitoring cookie. The checks are listed at the top of the script.
