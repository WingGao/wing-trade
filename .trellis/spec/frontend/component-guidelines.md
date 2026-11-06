# Frontend Component Guidelines

## Component Structure

Use Vue single-file components with script setup, a typed script block, template,
and scoped or shared CSS. Keep page data loading in pages and pass data to
presentational components.

## Props and Events

Define props with TypeScript interfaces or defineProps generics. Use v-model for
dialog visibility and named events such as saved for mutations. Components should
not reach into another component's internal state.

## Styling

The project uses Element Plus primitives with shared classes in src/styles.css.
Prefer existing panel, toolbar, metric, and muted classes before adding one-off
styles. Preserve the red-up/green-down preference semantics.

## Accessibility

Use visible labels on form controls, meaningful button text, table headers, and
status tags. Destructive actions such as trade voiding require a confirmation
dialog. Stale quote state must have text, not color alone.

## Examples

QuoteBadge receives one Quote prop and renders price, change, source time, and
stale state. BuyTradeDialog and ProfitTargetDialog expose v-model and saved.
