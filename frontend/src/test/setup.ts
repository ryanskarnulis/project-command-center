import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, beforeEach } from 'vitest'

// globals: true isn't set, so Testing Library's auto-cleanup never registers.
// Unmount between tests so rendered DOM doesn't leak across cases.

// Fail any test that leaks an unhandled promise rejection. This is the
// regression net for the fire-and-forget mutation handlers (`fireAndForget(...)`
// / the board `move()` swallow): a `void mutate()` that loses its `.catch()`
// would otherwise reject silently — jsdom never dispatches `window`'s
// `unhandledrejection`, so we listen on Node's `process` event, which does fire.
const unhandled: unknown[] = []
const onUnhandledRejection = (reason: unknown) => {
  unhandled.push(reason)
}
const testProcess = (
  globalThis as typeof globalThis & {
    process: {
      on: (event: 'unhandledRejection', listener: (reason: unknown) => void) => void
      off: (event: 'unhandledRejection', listener: (reason: unknown) => void) => void
    }
  }
).process

beforeEach(() => {
  unhandled.length = 0
  testProcess.on('unhandledRejection', onUnhandledRejection)
})

afterEach(async () => {
  cleanup()
  // Node dispatches `unhandledRejection` on a later microtask tick; flush the
  // queue so a rejection triggered during the test is recorded before we check.
  await new Promise((resolve) => setTimeout(resolve, 0))
  testProcess.off('unhandledRejection', onUnhandledRejection)
  if (unhandled.length > 0) {
    const reasons = unhandled.map((r) => String(r)).join('; ')
    unhandled.length = 0
    throw new Error(`Unhandled promise rejection during test: ${reasons}`)
  }
})
