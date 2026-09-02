/**
 * The ready-made plan the Plan tab offers when generation cannot deliver.
 *
 * `blr-30x40-g1-3bhk` is the one registry template that carries a SOLVED plan
 * (plot, brief, storeys, rooms, facade), so it is the honest answer to "just show
 * me a plan". A plan template is a whole project, which is why the offer is a link
 * to the dashboard's new-project dialog rather than an apply-in-place button.
 */
export const READY_MADE_PLAN_TEMPLATE_ID = 'blr-30x40-g1-3bhk';
export const READY_MADE_PLAN_HREF = `/?new=1&template=${READY_MADE_PLAN_TEMPLATE_ID}`;
