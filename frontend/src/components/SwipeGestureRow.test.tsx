import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  LONG_PRESS_MS,
  SWIPE_CLAMP_PX,
  SWIPE_COMMIT_PX,
  SWIPE_HINT_DELAY_MS,
  SWIPE_HINT_PEEK_PX,
  SWIPE_HINT_STEP_MS,
} from '../hooks/useSwipeGesture'
import { SwipeGestureRow } from './SwipeGestureRow'

function renderRow(props: Partial<Parameters<typeof SwipeGestureRow>[0]> = {}) {
  const onTap = vi.fn()
  render(
    <SwipeGestureRow
      classPrefix="t-swipe"
      enabled
      renderReveal={({ right, left }) => <div data-testid="reveal" data-right={right} data-left={left} />}
      {...props}
    >
      <button type="button" onClick={onTap}>Row</button>
    </SwipeGestureRow>,
  )
  const content = document.querySelector('.t-swipe-content') as HTMLElement
  return { content, onTap }
}

function press(element: Element, x = 100): void {
  fireEvent.pointerDown(element, { button: 0, clientX: x, pointerId: 1 })
}
function move(element: Element, x: number): void {
  fireEvent.pointerMove(element, { clientX: x, pointerId: 1 })
}

describe('SwipeGestureRow', () => {
  afterEach(() => vi.useRealTimers())

  it('commits each live side past the threshold and snaps back short of it', () => {
    const onSwipeRight = vi.fn()
    const onSwipeLeft = vi.fn()
    const { content } = renderRow({ onSwipeRight, onSwipeLeft })
    press(content)
    move(content, 110)
    move(content, 100 + SWIPE_COMMIT_PX - 1)
    fireEvent.pointerUp(content)
    expect(onSwipeRight).not.toHaveBeenCalled()

    press(content)
    move(content, 110)
    move(content, 100 + SWIPE_COMMIT_PX)
    fireEvent.pointerUp(content)
    expect(onSwipeRight).toHaveBeenCalledOnce()

    press(content)
    move(content, 90)
    move(content, 100 - SWIPE_COMMIT_PX)
    fireEvent.pointerUp(content)
    expect(onSwipeLeft).toHaveBeenCalledOnce()
    expect(content.style.transform).toBe('translateX(0px)')
  })

  it('clamps the drag and pins a dead side at zero', () => {
    const { content } = renderRow({ onSwipeRight: vi.fn() })
    press(content)
    move(content, 110)
    move(content, 400)
    expect(content.style.transform).toBe(`translateX(${SWIPE_CLAMP_PX}px)`)
    expect(screen.getByTestId('reveal').dataset.right).toBe('1')
    move(content, 0)
    expect(content.style.transform).toBe('translateX(0px)')
  })

  it('ignores movement within the slop so a tap still lands', () => {
    const onSwipeRight = vi.fn()
    const { content, onTap } = renderRow({ onSwipeRight })
    const button = screen.getByRole('button', { name: 'Row' })
    press(button)
    move(button, 104)
    fireEvent.pointerUp(button)
    fireEvent.click(button, { detail: 1 })
    expect(onTap).toHaveBeenCalledOnce()
    expect(content.style.transform).toBe('translateX(0px)')
  })

  it('swallows the click after a drag but never a keyboard click', () => {
    const { onTap } = renderRow({ onSwipeRight: vi.fn() })
    const button = screen.getByRole('button', { name: 'Row' })
    press(button)
    move(button, 130)
    fireEvent.pointerUp(button)
    fireEvent.click(button, { detail: 0 })
    expect(onTap).toHaveBeenCalledOnce()
    fireEvent.click(button, { detail: 1 })
    expect(onTap).toHaveBeenCalledOnce()
  })

  it('fires a long-press while disabled, and a drag cancels it', () => {
    vi.useFakeTimers()
    const onLongPress = vi.fn()
    const { content } = renderRow({ enabled: false, onSwipeLeft: vi.fn(), onLongPress })
    expect(screen.queryByTestId('reveal')).toBeNull()
    press(content)
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS) })
    expect(onLongPress).toHaveBeenCalledOnce()

    const enabled = vi.fn()
    render(
      <SwipeGestureRow classPrefix="u-swipe" enabled onSwipeLeft={vi.fn()} onLongPress={enabled} renderReveal={() => null}>
        <span>Other</span>
      </SwipeGestureRow>,
    )
    const other = document.querySelector('.u-swipe-content') as HTMLElement
    press(other)
    // A push toward the dead right side is still a drag.
    move(other, 120)
    act(() => { vi.advanceTimersByTime(LONG_PRESS_MS) })
    expect(enabled).not.toHaveBeenCalled()
  })
})

function stubMotion(reduce: boolean): void {
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: reduce && query.includes('reduce'),
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }))
}

/** Two rows of one surface; returns both content cells, first row first. */
function renderList(hint: string, props: Partial<Parameters<typeof SwipeGestureRow>[0]> = {}) {
  const onSwipeRight = vi.fn()
  const onSwipeLeft = vi.fn()
  const view = render(
    <>
      {['a', 'b'].map((id) => (
        <SwipeGestureRow
          key={id}
          classPrefix={`h-${id}`}
          enabled
          hint={hint}
          onSwipeRight={onSwipeRight}
          onSwipeLeft={onSwipeLeft}
          renderReveal={() => null}
          {...props}
        >
          <span>{id}</span>
        </SwipeGestureRow>
      ))}
    </>,
  )
  const first = document.querySelector('.h-a-content') as HTMLElement
  const second = document.querySelector('.h-b-content') as HTMLElement
  return { first, second, onSwipeRight, onSwipeLeft, view }
}

const translate = (px: number): string => `translateX(${px}px)`

describe('SwipeGestureRow first-run hint', () => {
  // Each test uses its own surface key: the spent/claimed sets are per page load.
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
    stubMotion(false)
  })
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
    localStorage.clear()
  })

  it('peeks the first row toward each live side, then snaps back without firing', () => {
    const { first, second, onSwipeRight, onSwipeLeft } = renderList('peek')
    expect(first.style.transform).toBe(translate(0))
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect(first.style.transform).toBe(translate(SWIPE_HINT_PEEK_PX))
    expect(first).toHaveClass('snapping')
    expect(second.style.transform).toBe(translate(0))
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_STEP_MS) })
    expect(first.style.transform).toBe(translate(0))
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_STEP_MS) })
    expect(first.style.transform).toBe(translate(-SWIPE_HINT_PEEK_PX))
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_STEP_MS * 4) })
    expect(first.style.transform).toBe(translate(0))
    expect(second.style.transform).toBe(translate(0))
    expect(onSwipeRight).not.toHaveBeenCalled()
    expect(onSwipeLeft).not.toHaveBeenCalled()
    expect(SWIPE_HINT_PEEK_PX).toBeLessThan(SWIPE_COMMIT_PX)
  })

  it('peeks only toward a live side', () => {
    const { first } = renderList('one-side', { onSwipeRight: undefined })
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect(first.style.transform).toBe(translate(-SWIPE_HINT_PEEK_PX))
  })

  it('skips a disabled first row in favour of the first enabled one', () => {
    render(
      <>
        <SwipeGestureRow classPrefix="d-a" enabled={false} hint="skip-disabled" onSwipeRight={vi.fn()} renderReveal={() => null}>
          <span>done</span>
        </SwipeGestureRow>
        <SwipeGestureRow classPrefix="d-b" enabled hint="skip-disabled" onSwipeRight={vi.fn()} renderReveal={() => null}>
          <span>open</span>
        </SwipeGestureRow>
      </>,
    )
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect((document.querySelector('.d-a-content') as HTMLElement).style.transform).toBe(translate(0))
    expect((document.querySelector('.d-b-content') as HTMLElement).style.transform).toBe(translate(SWIPE_HINT_PEEK_PX))
  })

  it('persists, so a later mount does not peek again', () => {
    const { view } = renderList('persist')
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS + SWIPE_HINT_STEP_MS * 4) })
    expect(localStorage.getItem('swipe-hint.persist')).not.toBeNull()
    view.unmount()
    const again = renderList('persist')
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect(again.first.style.transform).toBe(translate(0))
  })

  it('does not peek under prefers-reduced-motion', () => {
    stubMotion(true)
    const { first } = renderList('reduced')
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS + SWIPE_HINT_STEP_MS) })
    expect(first.style.transform).toBe(translate(0))
  })

  it('is harmless when storage throws, and still plays once per load', () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('denied') })
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('denied') })
    const { first, view } = renderList('throws')
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect(first.style.transform).toBe(translate(SWIPE_HINT_PEEK_PX))
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_STEP_MS * 4) })
    view.unmount()
    const again = renderList('throws')
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect(again.first.style.transform).toBe(translate(0))
  })

  it('a press during the peek cancels it cleanly and the drag still works', () => {
    const { first, onSwipeRight } = renderList('cancel')
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS) })
    expect(first.style.transform).toBe(translate(SWIPE_HINT_PEEK_PX))
    press(first)
    expect(first.style.transform).toBe(translate(0))
    expect(first).not.toHaveClass('snapping')
    // The rest of the peek never plays over the user's own gesture.
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_STEP_MS * 4) })
    expect(first.style.transform).toBe(translate(0))
    // A release with no movement commits nothing.
    fireEvent.pointerUp(first)
    expect(onSwipeRight).not.toHaveBeenCalled()
    press(first)
    move(first, 110)
    move(first, 100 + SWIPE_COMMIT_PX)
    fireEvent.pointerUp(first)
    expect(onSwipeRight).toHaveBeenCalledOnce()
    expect(localStorage.getItem('swipe-hint.cancel')).not.toBeNull()
  })

  it('a press before the peek starts spends it', () => {
    const { first } = renderList('early')
    press(first)
    fireEvent.pointerUp(first)
    act(() => { vi.advanceTimersByTime(SWIPE_HINT_DELAY_MS + SWIPE_HINT_STEP_MS) })
    expect(first.style.transform).toBe(translate(0))
    expect(localStorage.getItem('swipe-hint.early')).not.toBeNull()
  })
})
