## scaffold_vite_react_app
**Applies to:** creating and previewing a new Vite + React app from scratch
**Steps:**
1. Run `npm create vite@latest <dir> -- --template react` (use an absolute path for <dir>).
2. `cd <dir> && npm install`.
3. Start the dev server: `npm run dev`, and confirm it serves (default http://localhost:5173).
4. If the page is blank, read the browser console and fix import paths before retrying.
**Notes:** This is an example skill that ships with ANet to show the format. ANet
writes real skills automatically after complex, self-corrected tasks, and injects
relevant ones into future tasks. You can hand-author skills too — keep `## <name>`
as the first line and `**Applies to:**` as a one-line trigger. Safe to delete.
**Created:** 2026-01-01
**Used:** 0
**Last improved:** 2026-01-01
