import { describe, expect, it } from 'vitest'
import { ApiError, ApiTimeoutError } from './client'
import { apiErrorMessage } from './errorMessage'

// This helper replaced four independently written copies (#230); these cases
// pin the union of what those call sites relied on.
describe('apiErrorMessage', () => {
  it('prefers the API detail over the generic status message', () => {
    const e = new ApiError(409, { detail: 'A run is in flight.' })
    expect(e.message).toBe('API error 409')
    expect(apiErrorMessage(e, 'fallback')).toBe('A run is in flight.')
  })

  it('falls back to the status message when there is no usable detail', () => {
    // No body, null body, absent detail, and a non-string detail all fall
    // through — only a string detail is renderable as-is.
    expect(apiErrorMessage(new ApiError(500, null), 'fallback')).toBe('API error 500')
    expect(apiErrorMessage(new ApiError(500, {}), 'fallback')).toBe('API error 500')
    expect(apiErrorMessage(new ApiError(422, { detail: [{ msg: 'bad' }] }), 'fallback')).toBe(
      'API error 422',
    )
  })

  it('keeps a non-API error’s own message', () => {
    expect(apiErrorMessage(new Error('Network down'), 'fallback')).toBe('Network down')
    // Timeouts carry their own actionable wording — don't flatten it.
    expect(apiErrorMessage(new ApiTimeoutError(30_000), 'fallback')).toMatch(
      /timed out after 30s/,
    )
  })

  it('uses the fallback only for a non-Error throw', () => {
    expect(apiErrorMessage('a string', 'fallback')).toBe('fallback')
    expect(apiErrorMessage(undefined, 'fallback')).toBe('fallback')
  })
})
