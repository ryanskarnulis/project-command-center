/**
 * Start a promise we intentionally don't await. Any rejection is already
 * surfaced to the user (a `withToast` error toast, or a hook's own error
 * state); swallow it here so the fire-and-forget call site — a `void`-invoked
 * event handler — can't raise an unhandled promise rejection.
 *
 * Wrap the *whole* chain, not just the mutation: chaining a follow-up
 * (`fireAndForget(remove(id).then(reload))`) keeps success-only semantics —
 * a rejected mutation skips the `.then` and the outer catch swallows it.
 */
export function fireAndForget(p: Promise<unknown>): void {
  void p.catch(() => {})
}
