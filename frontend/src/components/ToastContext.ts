import { createContext, useContext } from 'react'

export type ToastKind = 'success' | 'error'

export interface ToastApi {
  notify: (kind: ToastKind, message: string) => void
  /** Convenience wrapper: notify success, or an error toast if the promise
   * rejects. The error toast is `messages.error` when given, else the server's
   * `detail` for an `ApiError` that carries one, else the error's own message
   * (see `apiErrorMessage`). The rejection is rethrown unchanged. */
  withToast: <T>(
    promise: Promise<T>,
    messages: { success: string; error?: string },
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
