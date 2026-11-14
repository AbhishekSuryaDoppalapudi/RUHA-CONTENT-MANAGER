import { useEffect, useRef } from 'react'

/**
 * Brand banner that stays pinned under the top bar. At the top of the page it is fully visible;
 * as you scroll it fades to a translucent (but still visible) backdrop and the content scrolls over it.
 * Put the page content in a `relative z-10` wrapper after this component.
 */
export default function ScrollBanner() {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const MIN = 0.2, FADE_PX = 260
    let raf = 0
    const update = () => {
      raf = 0
      const o = Math.max(MIN, 1 - window.scrollY / FADE_PX)
      if (ref.current) ref.current.style.opacity = String(o)
    }
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(update) }
    update()
    window.addEventListener('scroll', onScroll, { passive: true })
    return () => { window.removeEventListener('scroll', onScroll); if (raf) cancelAnimationFrame(raf) }
  }, [])
  return (
    <div ref={ref} className="sticky top-[61px] z-0 mb-6 pointer-events-none transition-opacity duration-150">
      <div className="rounded-2xl overflow-hidden border border-line/70 shadow-glow">
        <img src="/brand/el-ruach-banner.png" alt="El Ruach - The God's Breath" className="w-full max-h-48 object-cover object-center" />
      </div>
    </div>
  )
}
