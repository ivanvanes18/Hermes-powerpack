# Reference-driven dashboard restyle

Use when the user provides a design reference/tokens file and asks to restyle an existing dashboard/site.

## Workflow

1. Treat the reference as a token contract, not just moodboard inspiration.
   - Extract color roles, typography roles, radius, shadows, density, surface hierarchy, component treatments, and do/don't rules.
   - Preserve accent discipline: if the reference says one saturated accent, do not add extra accent colors.

2. Inspect the current frontend before editing.
   - Identify entry HTML, CSS, JS, framework/build command, static route mapping, and deploy/runtime path.
   - Keep data/API behavior unchanged unless the user explicitly asks for product changes.

3. Apply the style at the system level.
   - Add CSS custom properties matching reference tokens.
   - Convert page background/surfaces/elevation/radius/typography/components first.
   - Then adjust layout structure only enough to make the style work: nav, hero, section headings, cards, badges, preview panels.

4. Preserve safety and behavior.
   - Do not rename API ids or JS selectors unless you update the JS too.
   - For dashboards with safety flags, keep flags visible and machine-readable; style them, do not hide them.

5. Verify like a user-facing UI change.
   - Run syntax/build/tests for touched stack.
   - Start or hit the app and capture a real screenshot.
   - Use vision review or manual inspection for: empty buttons/placeholders, broken long-token wraps, clipped cards, mobile/grid collapse, contrast, and whether the reference direction is recognizable.
   - Fix visual QA findings before committing/deploying.

6. Ship cleanly.
   - Commit only intended frontend files.
   - If the live runtime is not auto-deployed from git, copy/deploy the changed static assets and restart the service.
   - Smoke the public URL and capture a live screenshot as proof.

## Common pitfalls

- Copying token values but keeping the old visual language. A dark dashboard with new variables is not a reference-driven restyle.
- Adding new saturated semantic colors for ok/warn/bad that fight the reference accent. Use subdued state colors unless safety demands strong emphasis.
- Letting long keys like `wallet_private_key_allowed` or ISO timestamps wrap ugly in compact cards. Use wider grid min widths and `overflow-wrap:anywhere`; consider monospace/smaller text for timestamps.
- Leaving a styled button visually empty because text blends into a white pill or because old button styles got inherited.
- Calling it done after CSS checks only. For visual work, a screenshot is part of verification.
