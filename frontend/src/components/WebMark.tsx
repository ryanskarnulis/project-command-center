import type { SVGProps } from 'react'

interface WebMarkProps extends SVGProps<SVGSVGElement> {
  /** Rendered width/height in px, like lucide icons. */
  size?: number
}

/** The PCC brand mark — a spider web (the app is the web; the SpiderMark
 * agent is what lives on it). Six spokes with two sagging rings, same stroke
 * style as the spider. Decorative by default (aria-hidden); inherits text
 * color via currentColor. */
export function WebMark({ size = 24, ...props }: WebMarkProps) {
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
      {/* spokes */}
      <path d="M12 2.5v19" />
      <path d="M3.77 7.25 20.23 16.75" />
      <path d="M20.23 7.25 3.77 16.75" />
      {/* inner ring (sags toward the hub between spokes) */}
      <path d="M12 7 Q10 8.54 7.67 9.5 Q8 12 7.67 14.5 Q10 15.46 12 17 Q14 15.46 16.33 14.5 Q16 12 16.33 9.5 Q14 8.54 12 7 Z" />
      {/* outer ring */}
      <path d="M12 4 Q8.7 6.28 5.07 8 Q5.4 12 5.07 16 Q8.7 17.72 12 20 Q15.3 17.72 18.93 16 Q18.6 12 18.93 8 Q15.3 6.28 12 4 Z" />
      {/* hub, echoing the spider's filled body */}
      <circle cx="12" cy="12" r="1.2" fill="currentColor" stroke="none" />
    </svg>
  )
}
