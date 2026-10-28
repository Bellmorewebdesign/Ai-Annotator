import Link from "next/link";

export function BrandMark({ className }: { className?: string }) {
  // A line of text with one phrase marked: the product in one glyph.
  return (
    <svg viewBox="0 0 20 20" className={className ?? "size-5"} aria-hidden="true">
      <rect x="1" y="1" width="18" height="18" rx="5" fill="#1c1b19" />
      <rect x="4.5" y="6" width="11" height="2.2" rx="1.1" fill="#ffffff" fillOpacity="0.35" />
      <rect x="4" y="9.6" width="12" height="3.2" rx="1" fill="#ffe066" />
      <rect x="5.5" y="10.6" width="9" height="1.2" rx="0.6" fill="#1c1b19" fillOpacity="0.55" />
      <rect x="4.5" y="14.2" width="7" height="2.2" rx="1.1" fill="#ffffff" fillOpacity="0.35" />
    </svg>
  );
}

export function AppHeader() {
  return (
    <header className="sticky top-0 z-30 border-b border-line/80 bg-canvas/90 backdrop-blur-sm">
      <div className="mx-auto flex h-14 max-w-6xl items-center justify-between px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 rounded-md text-[15px] font-semibold tracking-tight text-ink">
          <BrandMark />
          Annotator
        </Link>
        <nav className="flex items-center gap-1 text-[13px]">
          <Link
            href="/"
            className="rounded-md px-2.5 py-1.5 text-ink-2 transition-colors hover:bg-hover hover:text-ink"
          >
            New document
          </Link>
        </nav>
      </div>
    </header>
  );
}
