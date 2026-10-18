# Premium UI/UX Design & Lucid Motion Skill for React Native AI Apps

## Metadata
- Name: ui-ux-design
- Description: Rules for building premium, micro-animated interfaces using React Native Reusables, Gluestack UI, Reanimated, and Wix UI Lib with Lucid Motion physics.
- Triggers: "ui", "ux", "design", "screen", "component", "chat interface", "animation", "motion", "lucid"

## Core UI/UX Directives
1. **Design System & Aesthetics**: Follow a clean, modern, minimalist aesthetic. Prioritize component primitives and styling tokens from **React Native Reusables** for a clean shadcn/ui look.
2. **Layout Primitives**: Utilize **Gluestack UI** and **Wix UI Lib** for core production-tested building blocks, layout structures, and pre-built chat modules.
3. **Styling Engine**: Strictly use Tailwind CSS classes via **NativeWind** for structural styling. Avoid raw, inline `StyleSheet.create` objects.
4. **Lucid Motion Physics**: All movement must feel organic, fluid, and continuous rather than mechanical. 
   - **Spring Configurations**: Never use linear transitions or rigid timing values. Force all spring physics to use custom configurations for snappy yet fluid damping (e.g., `withSpring(value, { damping: 15, stiffness: 120, mass: 0.5 })`).
   - **Continuous Layout**: Any item added or removed from the DOM (like streaming chat bubbles or sliding action pills) must utilize exit/entry transitions (e.g., `entering={FadeInDown.springify().mass(0.5)}` or `layout={LinearTransition.springify()}`).
   - **Interactive Gestures**: Pair actions like swiping down to dismiss a settings panel or scrolling through historical queries with active touch velocity-tracking interpolation.

## Execution Workflow
- Before generating code, list a quick 3-bullet architecture plan explicitly stating which components from React Native Reusables, Gluestack UI, Reanimated, or Wix UI Lib will be utilized.
- Explicitly detail the animation mechanics (e.g., Shared Value properties, trigger state hooks, and interpolation limits).
- Ensure all modules natively support dark and light theme tokens (e.g., `bg-white dark:bg-zinc-950`).
