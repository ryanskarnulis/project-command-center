import { createContext, useContext } from 'react'

export type ToastKind = 'success' | 'error'

/** A button on a toast, e.g. Undo. Clicking it dismisses the toast first. */
export interface ToastAction {
  label: string
  onAction: () => void
}

export interface ToastApi {
  notify: (kind: ToastKind, message: string, action?: ToastAction) => void
  /** Convenience wrapper: notify success, or an error toast if the promise
   * rejects. The error toast is `messages.error` when given, else the server's
   * `detail` for an `ApiError` that carries one, else the error's own message
   * (see `apiErrorMessage`). The rejection is rethrown unchanged. `action`
   * builds the success toast's button from the resolved value — an Undo that
   * needs the id the write returned. */
  withToast: <T>(
    promise: Promise<T>,
    messages: { success: string; error?: string; action?: (result: T) => ToastAction },
  ) => Promise<T>
}

// No-op default so components/hooks render fine without a provider (mirrors
// TrashCountContext) — toasts simply don't show, which never breaks a flow.
export const ToastContext = createContext<ToastApi>({
  notify: () => {},
  withToast: async (promise) => promise,
})

export function useToast(): ToastApi {
  return useContext(ToastContext)
}
