import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react'
import { apiErrorMessage } from '../api/errorMessage'
import {
  ToastContext,
  type ToastAction,
  type ToastApi,
  type ToastKind,
} from './ToastContext'

interface Toast {
  id: number
  kind: ToastKind
  message: string
  action?: ToastAction
}

const DISMISS_MS = 4000
// A toast with a button (Undo) stands as long as the phone's undo bar does, so
// the window to take a change back is the same on both surfaces.
const ACTION_DISMISS_MS = 5000

function ToastActionButton({
  action,
  onDismiss,
}: {
  action: ToastAction
  onDismiss: () => void
}) {
  return (
    <button
      type="button"
      className="toast-action"
      onClick={() => {
        onDismiss()
        action.onAction()
      }}
    >
      {action.label}
    </button>
  )
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const nextId = useRef(0)
  const timers = useRef<Map<number, ReturnType<typeof setTimeout>>>(new Map())

  const dismiss = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
    const timer = timers.current.get(id)
    if (timer) {
      clearTimeout(timer)
      timers.current.delete(id)
    }
  }, [])

  const notify = useCallback(
    (kind: ToastKind, message: string, action?: ToastAction) => {
      const id = nextId.current++
      setToasts((prev) => [...prev, { id, kind, message, action }])
      timers.current.set(
        id,
        setTimeout(() => dismiss(id), action ? ACTION_DISMISS_MS : DISMISS_MS),
      )
    },
    [dismiss],
  )

  const withToast = useCallback(
    async <T,>(
      promise: Promise<T>,
      messages: { success: string; error?: string; action?: (result: T) => ToastAction },
    ): Promise<T> => {
      try {
        const result = await promise
        notify('success', messages.success, messages.action?.(result))
        return result
      } catch (e: unknown) {
        // An explicit override wins. Otherwise show the server's `detail` —
        // the reason a mutation was refused — rather than `ApiError`'s bare
        // "API error <status>"; any other Error keeps its own message.
        notify('error', messages.error ?? apiErrorMessage(e, 'Something went wrong'))
        throw e
      }
    },
    [notify],
  )

  // Clear any pending timers on unmount so we don't set state on a gone tree.
  useEffect(() => {
    const map = timers.current
    return () => {
      map.forEach(clearTimeout)
      map.clear()
    }
  }, [])

  // Memoized so consumers depending on `withToast`/`notify` in useCallback deps
  // don't churn when the provider re-renders to show/hide a toast.
  const api = useMemo<ToastApi>(() => ({ notify, withToast }), [notify, withToast])

  return (
    <ToastContext.Provider value={api}>
      {children}
      <div className="toast-stack" role="region" aria-label="Notifications">
        {toasts.map((toast) => (
          <div
            key={toast.id}
            className={`toast toast--${toast.kind}`}
            role={toast.kind === 'error' ? 'alert' : 'status'}
          >
            <span>{toast.message}</span>
            {toast.action && (
              <ToastActionButton
                action={toast.action}
                // One shot: the toast goes before the action runs, so a second
                // click can't undo twice.
                onDismiss={() => dismiss(toast.id)}
              />
            )}
            <button
              type="button"
              className="toast-dismiss"
              aria-label="Dismiss notification"
              onClick={() => dismiss(toast.id)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  )
}
