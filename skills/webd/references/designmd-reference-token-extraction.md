# DESIGN.md / DESIGNmd token extraction

Use when a web restyle depends on a published DESIGN.md kit or design-system reference, especially if the MCP/API path is unavailable or returns an auth/permission error.

## Pattern

1. Prefer structured DESIGN.md content first.
   - Use the DESIGNmd kit/tool when available because it usually preserves token roles and implementation notes.
   - Extract roles, not just values: primary CTA, secondary/status, background, surface, border, text, typography, spacing base, radius, shadow/elevation, decorative motifs.

2. If the tool/API is blocked, fall back to the public kit page.
   - Extract the public design page directly and mine the rendered text for tokens and rules.
   - Do not record the failure as “tool broken”; treat it as an auth/state issue and continue through a public source if the kit is public.
   - Keep the fallback evidence explicit in the final report: source path, what was extracted, and which values drove the implementation.

3. Convert the reference into a token contract before touching CSS.
   - Write/verify CSS custom properties first.
   - Map source color roles exactly; do not repurpose CTA-only/accent-only colors for generic decoration.
   - Keep typography pairings and density choices recognizable.

4. Apply through the system, not isolated patches.
   - Background/canvas, surfaces/cards, headings/body, buttons/controls, badges/status, charts/previews, spacing rhythm, and responsive rules should all move together.
   - Preserve product behavior and API wiring while restyling.

5. Visual QA is mandatory.
   - Take a screenshot of local or live UI.
   - Check that the reference’s signature traits are visible, not merely that CSS variables changed.
   - Fix long label wrapping, empty buttons, contrast issues, clipped cards, and mobile collapse before claiming completion.

## Example: Genesis-style dashboard extraction

Useful token roles from a Genesis-style reference:

- Primary/accent: indigo (`#4f46e5` class), used for primary actions, focus, and key highlights.
- Positive/status secondary: green (`#10b981` class), reserved for positive state/status rather than generic decoration.
- Canvas: warm off-white (`#f7f4ed` class), with white surfaces and subtle borders.
- Typography: display/heading face plus clean body face; preserve hierarchy and editorial precision.
- Shape: restrained radii (cards around 12px, controls around 6px) and thin borders over heavy shadows.
- Motif: low-contrast dot/grid decoration can carry the reference without adding noisy illustration.
