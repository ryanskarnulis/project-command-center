import type { SVGProps } from 'react'

interface SpiderMarkProps extends SVGProps<SVGSVGElement> {
  /** Rendered width/height in px, like lucide icons. */
  size?: number
}

/** The PCC spider mark — brand icon and agent avatar. Decorative by default
 * (aria-hidden); inherits text color via currentColor. */
export function SpiderMark({ size = 24, ...props }: SpiderMarkProps) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {/* legs */}
      <path d="M9.5 12.5 5 9 3.5 4.5" />
      <path d="M9 14 4 13l-2.5-3" />
      <path d="M9 16l-5 1-2 3.5" />
      <path d="M10 17.5 7 21" />
      <path d="M14.5 12.5 19 9l1.5-4.5" />
      <path d="M15 14l5-1 2.5-3" />
      <path d="M15 16l5 1 2 3.5" />
      <path d="M14 17.5 17 21" />
      {/* head + body */}
      <circle cx="12" cy="10.5" r="2" fill="currentColor" stroke="none" />
      <ellipse cx="12" cy="15.5" rx="3" ry="3.6" fill="currentColor" stroke="none" />
    </svg>
  )
}
