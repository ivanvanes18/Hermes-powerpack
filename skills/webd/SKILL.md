---
name: webd
description: Complete web design system combining aesthetics, conversion optimization, and component libraries. Use for landing pages, SaaS sites, or any frontend project requiring design thinking + conversion focus. Includes design framework (7 aesthetic directions), conversion patterns (from Landing Pages Explained), and 17+ UI libraries. Three workflows - Component-First (quick), Design-First (distinctive), Conversion-First (marketing).
---

# webd 🎨

Curated web design resources for building professional landing pages with shadcn/ui-based libraries.

## When to Use

- User asks for `WEBD`, web-quality skill, anti-slop web workflow, or the web layer of the Codex+Hermes workshop
- User asks for UI component libraries or design resources
- Building landing pages that need to look professional
- Looking for alternatives to "vibe coded" designs
- Need specific components (hero sections, pricing, testimonials)
- Want animation libraries or icon sets
- Creating distinctive, non-generic frontend interfaces
- Need design direction before starting to code

Codex+Hermes lesson positioning:
- In lesson/workshop materials, `WEBD` is not a decorative design add-on. It is the bridge after `goal`/`SuperGoal` and before Supabase/backend work.
- Frame it as the system that turns "сделай красиво" into explicit web-product quality: structure, components, typography, CTA, states, mobile, reference discipline, and anti-slop checks.
- For theory decks/lessons, do **not** teach Supabase commands, SQL, env, dashboard setup, or deploy mechanics under the WEBD block. Explain Supabase only as the backend/product layer: user memory, access, profile/cabinet, and stored state.
- The web-product layer should also ask the product questions around legal/trust and visibility: what personal data is collected, where offers/policies/checkboxes belong, and which SEO/AEO/GEO pages or answers make the product discoverable.

## Core Resources

- **[Design Framework](references/design-framework.md)** - Establish aesthetic direction BEFORE coding
- **[Conversion Patterns](references/conversion-patterns.md)** - Landing page optimization from Landing Pages Explained
- **[UI Libraries](references/ui-libraries.md)** - 17+ shadcn/ui-based component libraries
- **[DESIGN.md / DESIGNmd token extraction](references/designmd-reference-token-extraction.md)** - fallback + token-role workflow for applying published design kits

## Quick Categories

### For Marketing Pages
- **MVP Blocks** - Animated sections with Framer Motion
- **Magic UI** - 50+ animated components (used by Vercel/Stripe)
- **Luxe UI** - Premium testimonials, features, CTAs

### For Animations
- **Aceternity UI** - Crazy 3D effects
- **Motion Primitives** - Animation building blocks
- **Smooth UI** - Buttery hover effects

### For Clean/Minimal
- **Origin UI** - Consistent design language
- **Cult UI** - Minimal but powerful
- **Tailark** - Clean startup aesthetic

### For Unique Elements
- **Skipper UI** - Components you won't find elsewhere
- **Phosphor Icons** - 9000+ alternative to Lucide
- **PatternCraft** - Background patterns and textures

## Recommendations by Use Case

| Goal | Top Pick | Runner Up |
|------|----------|-----------|
| Launch fast | Bundui | MVP Blocks |
| Look premium | Luxe UI | Aceternity UI |
| SaaS dashboard | Cult UI | Origin UI |
| Stand out | Skipper UI | Kokonut UI |
| Heavy animations | Aceternity UI | Magic UI |

## Micro-patterns

### Reference-driven dashboard restyles

Use when the user provides a DESIGN.md/style reference and asks to redesign an existing dashboard/site.

- Treat the reference as a token contract: color roles, surfaces, type, radius, shadows, density, and accent discipline.
- Keep product/API behavior stable; restyle the frontend system first, then adjust layout only where needed.
- For analytics dashboards, prioritize white cards, clear section rhythm, compact metrics, restrained shadows, and readable long technical labels.
- If a DESIGNmd/MCP lookup is unavailable but the kit is public, extract the public design page as fallback and keep token roles explicit; do not stop at “tool failed.”
- Visual QA is mandatory: run syntax/build/tests, capture a real screenshot, inspect for empty buttons, clipped cards, ugly long-token/timestamp wraps, and mismatch with the reference.
- See `references/reference-driven-dashboard-restyle.md` for the full workflow and pitfalls.
- See `references/designmd-reference-token-extraction.md` for DESIGN.md/DESIGNmd extraction and fallback patterns.

### Section scale badges

Use when a landing/homepage section needs to make catalog size or product scale immediately visible without adding visual noise.

- Prefer a compact pill next to the section title, not a new line of explanatory copy.
- Source the number from the real collection/query (`items.length`, API count, catalog response), not a hardcoded marketing number unless the user explicitly asks for static copy.
- Use subdued accent styling: small font, rounded border, translucent accent background, no heavy CTA color.
- Keep the primary title readable first: `⚡ Скилы  24 скила` should feel like metadata, not a second heading.
- Localize plural forms when the UI language needs it (`1 скил`, `2 скила`, `5 скилов`).
- Verify visually with a real screenshot after deploy/build when the user asked for “красиво” or when spacing could wrap on mobile.

## Workflow

### Option 1: Component-First (Quick)
1. Identify the landing page goal (conversion, showcase, app)
2. Pick 1-2 primary libraries matching the aesthetic
3. Supplement with specific needs (icons from Phosphor, backgrounds from PatternCraft)
4. Keep it consistent - don't mix too many design languages

### Option 2: Design-First (Distinctive)
1. **Read [design-framework.md](references/design-framework.md)** for full process
2. Define context (purpose, audience, brand)
3. Choose aesthetic direction (brutalist, luxury, playful, etc.)
4. Establish typography, colors, motion strategy
5. Match UI libraries to aesthetic (see framework guide)
6. Code with intentional design choices (avoid generic patterns)

**Use Option 2 when:**
- The design needs to stand out from generic AI sites
- Building a brand identity through UI
- Client wants something distinctive
- You have time to think about design properly

### Option 3: Conversion-First (Marketing)
1. **Read [conversion-patterns.md](references/conversion-patterns.md)** for full patterns
2. Start with clarity: one clear value proposition
3. Show the product (demo, screenshot, video)
4. Build trust (social proof + transparency)
5. Remove friction (simple forms, clear pricing)
6. Optimize for speed (Core Web Vitals)
7. Test everything (headlines, CTAs, visuals)

**Use Option 3 when:**
- Conversion rate is the primary metric
- Building SaaS, course, or product landing pages
- Need to optimize existing page performance
- Marketing/growth-focused project
