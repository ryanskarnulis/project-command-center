/**
 * The agent's avatar: the house mascot, Glitch (frontend/public/glitch.png,
 * master in gateway/theme/assets/). Lucide-compatible size prop so it sits
 * in the same icon rows SpiderMark used to.
 */
export function GlitchMark({ size = 24, className }: { size?: number; className?: string }) {
  return (
    <img
      className={className}
      src="/glitch.png"
      alt=""
      width={size}
      height={size}
      style={{ borderRadius: '50%', display: 'block' }}
      aria-hidden="true"
    />
  )
}
