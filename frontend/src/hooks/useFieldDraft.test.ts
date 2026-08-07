import { act, renderHook } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { useFieldDraft } from './useFieldDraft'

describe('useFieldDraft', () => {
  it('shows the server value until the user types', () => {
    const { result } = renderHook(() => useFieldDraft(1, 'Firewall'))

    expect(result.current.value).toBe('Firewall')
    expect(result.current.dirty).toBe(false)
  })

  it('holds the user’s text and reports dirty while it diverges', () => {
    const { result } = renderHook(() => useFieldDraft(1, 'Firewall'))

    act(() => result.current.set('Edge Firewall'))

    expect(result.current.value).toBe('Edge Firewall')
    expect(result.current.dirty).toBe(true)
  })

  it('adopts a server change while the field is untouched', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useFieldDraft(1, value),
      { initialProps: { value: 'Firewall' } },
    )

    // The agent renames the project while nobody is editing this field.
    rerender({ value: 'Perimeter' })

    expect(result.current.value).toBe('Perimeter')
    expect(result.current.dirty).toBe(false)
  })

  it('keeps an in-progress edit when the server moves the field', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useFieldDraft(1, value),
      { initialProps: { value: 'Firewall' } },
    )

    act(() => result.current.set('Edge Fire'))
    rerender({ value: 'Perimeter' })

    expect(result.current.value).toBe('Edge Fire')
    expect(result.current.dirty).toBe(true)
    // A further server change still must not wipe the half-typed word.
    rerender({ value: 'Perimeter II' })
    expect(result.current.value).toBe('Edge Fire')
  })

  it('goes clean once the server catches up to the typed value', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useFieldDraft(1, value),
      { initialProps: { value: 'Firewall' } },
    )

    act(() => result.current.set('Edge Firewall'))
    // The field's own PATCH lands, echoing what the user typed.
    rerender({ value: 'Edge Firewall' })

    expect(result.current.value).toBe('Edge Firewall')
    expect(result.current.dirty).toBe(false)

    // Having reconciled, the field accepts later server changes again.
    rerender({ value: 'Perimeter' })
    expect(result.current.value).toBe('Perimeter')
  })

  it('drops the draft when the record key changes', () => {
    const { result, rerender } = renderHook(
      ({ key, value }: { key: number; value: string }) => useFieldDraft(key, value),
      { initialProps: { key: 1, value: 'Firewall' } },
    )

    act(() => result.current.set('Edge Fire'))
    // A different record: the previous one's typing must not leak into it.
    rerender({ key: 2, value: 'Perimeter' })

    expect(result.current.value).toBe('Perimeter')
    expect(result.current.dirty).toBe(false)
  })

  it('treats typing back to the server value as clean', () => {
    const { result, rerender } = renderHook(
      ({ value }: { value: string }) => useFieldDraft(1, value),
      { initialProps: { value: 'Firewall' } },
    )

    act(() => result.current.set('Edge Firewall'))
    act(() => result.current.set('Firewall'))

    expect(result.current.dirty).toBe(false)
    // No divergence to protect, so a server change flows in.
    rerender({ value: 'Perimeter' })
    expect(result.current.value).toBe('Perimeter')
  })
})
