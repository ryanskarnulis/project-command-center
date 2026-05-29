export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    super(`API error ${status}`)
    this.status = status
    this.body = body
  }
}

const BASE_URL =
  import.meta.env.VITE_API_URL ?? `http://${window.location.hostname}:8000`

export async function apiClient(
  path: string,
  options?: RequestInit,
): Promise<Response> {
  const response = await fetch(`${BASE_URL}${path}`, options)
  if (!response.ok) {
    const body: unknown = await response.json().catch(() => null)
    throw new ApiError(response.status, body)
  }
  return response
}
