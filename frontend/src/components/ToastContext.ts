import { createContext, useContext } from 'react'

export type ToastKind = 'success' | 'error'

export interface ToastApi {
  notify: (kind: ToastKind, message: string) => void
  /** Convenience wrapper: notify success, or an error toast if the promise rejects. */
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
