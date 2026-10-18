import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { LONG_PRESS_MS, SWIPE_CLAMP_PX, SWIPE_COMMIT_PX } from '../hooks/useSwipeGesture'
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
