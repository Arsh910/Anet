## Analyze Codebase for UI Improvements
**Applies to:** When you need to review a frontend codebase (React, Shopify App, etc.) to identify UI/UX enhancements and provide concrete recommendations.  
**Steps:**
1. **Gather the file structure** – Run a recursive directory listing (e.g., `tree` or `find`) and capture all files, focusing on `.jsx`, `.tsx`, `.js`, `.ts`, `.css`, `.scss`, and component folders.  
2. **Identify UI‑related files** – Filter the list for files that render UI (components, pages, routes, style sheets) and note any UI libraries or design systems in use (e.g., Polaris, AntD, Tailwind).  
3. **Perform a quick scan for consistency** – Open a sample of components and check:  
   - Naming conventions (PascalCase for components, camelCase for props).  
   - PropTypes/TypeScript prop definitions.  
   - Inline styles vs. external CSS/utility‑first classes.  
   - Use of design tokens or theme variables.  
4. **Check accessibility basics** – Look for missing `alt` attributes, proper heading hierarchy, ARIA labels, focus management, and color‑contrast usage in CSS.  
5. **Assess responsiveness** – Search for media queries, flex/grid usage, and hard‑coded pixel values that may break on different screen sizes.  
6. **Review state/UI coupling** – Identify components that mix data fetching logic with presentation; suggest separating concerns (e.g., move data hooks to custom hooks or containers).  
7. **Look for duplicated UI patterns** – Spot repeated button, card, modal, or form implementations; recommend extracting them into reusable components or a UI library.  
8. **Validate routing and layout consistency** – Ensure layout wrappers (headers, footers, sidebars) are applied uniformly across routes and that nested routes use `<Outlet>` correctly.  
9. **Document findings** – Create a markdown report grouped by:  
   - Consistency & Naming  
   - Styling & Theming  
   - Accessibility (a11y)  
   - Responsiveness  
   - Component Reusability  
   - Performance (e.g., unnecessary re‑renders, large bundles)  
   For each item, include file path, issue description, and a suggested fix or refactor.  
10. **Prioritize recommendations** – Rank issues by impact (high, medium, low) and effort (quick win, refactor, redesign) to help the team plan next steps.  
**Notes:** Keep the scan lightweight for large repos—focus on