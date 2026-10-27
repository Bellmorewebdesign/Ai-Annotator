import type { ButtonHTMLAttributes, ReactNode, SVGProps } from "react";

export function cx(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md" | "lg";

const variants: Record<Variant, string> = {
  primary:
    "bg-ink text-white shadow-card hover:bg-[#33312d] active:bg-black disabled:bg-[#b9b5ae] disabled:shadow-none",
  secondary:
    "bg-surface text-ink border border-line-strong shadow-card hover:bg-subtle active:bg-hover disabled:text-faint",
  ghost: "text-ink-2 hover:bg-hover hover:text-ink active:bg-line disabled:text-faint",
  danger: "text-danger hover:bg-danger-soft active:bg-[#fbe2de] disabled:text-faint",
};

const sizes: Record<Size, string> = {
  sm: "h-8 px-2.5 text-[13px] gap-1.5 rounded-md",
  md: "h-9 px-3.5 text-sm gap-2 rounded-lg",
  lg: "h-11 px-5 text-[15px] gap-2 rounded-lg",
};

export function buttonClass(variant: Variant = "secondary", size: Size = "md", className?: string): string {
  return cx(
    "inline-flex select-none items-center justify-center font-medium whitespace-nowrap transition-colors duration-150 disabled:cursor-not-allowed",
    variants[variant],
    sizes[size],
    className,
  );
}

export function Button({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return (
    <button {...props} className={buttonClass(variant, size, className)} />
  );
}

export function Card({ className, children }: { className?: string; children: ReactNode }) {
  return <div className={cx("rounded-xl border border-line bg-surface shadow-card", className)}>{children}</div>;
}

export function Notice({
  tone = "neutral",
  title,
  children,
  className,
}: {
  tone?: "neutral" | "warn" | "danger" | "success";
  title?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  const tones = {
    neutral: "bg-subtle border-line text-ink-2",
    warn: "bg-warn-soft border-[#f2dfae] text-warn",
    danger: "bg-danger-soft border-[#f4cdc6] text-danger",
    success: "bg-success-soft border-[#c9e7d6] text-success",
  };
  const Icon = tone === "success" ? IconCheckCircle : IconAlert;
  return (
    <div role={tone === "danger" ? "alert" : "status"} className={cx("flex gap-2.5 rounded-lg border px-3.5 py-3 text-[13px] leading-5", tones[tone], className)}>
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0">
        {title && <div className="font-medium">{title}</div>}
        {children && <div className={cx(title ? "mt-0.5 opacity-90" : undefined)}>{children}</div>}
      </div>
    </div>
  );
}

export function Spinner({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 16 16" className={cx("animate-spin", className ?? "size-4")} aria-hidden="true">
      <circle cx="8" cy="8" r="6.25" fill="none" stroke="currentColor" strokeOpacity="0.2" strokeWidth="1.5" />
      <path d="M14.25 8A6.25 6.25 0 0 0 8 1.75" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
    </svg>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-line-strong bg-surface px-1.5 py-0.5 font-mono text-[11px] text-muted">
      {children}
    </kbd>
  );
}

// --- Icons (1.5px stroke, 16px grid) -------------------------------------------------------

type IconProps = SVGProps<SVGSVGElement>;

function Svg({ children, className, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      className={className ?? "size-4"}
      {...props}
    >
      {children}
    </svg>
  );
}

export const IconUpload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 10.5V2.75M5 5.5l3-3 3 3M2.75 10v2.25c0 .55.45 1 1 1h8.5c.55 0 1-.45 1-1V10" />
  </Svg>
);
export const IconFile = (p: IconProps) => (
  <Svg {...p}>
    <path d="M9.25 1.75H4.5c-.55 0-1 .45-1 1v10.5c0 .55.45 1 1 1h7c.55 0 1-.45 1-1V5L9.25 1.75Z" />
    <path d="M9.25 1.75V5h3.25M5.75 8.25h4.5M5.75 10.75h3" />
  </Svg>
);
export const IconX = (p: IconProps) => (
  <Svg {...p}>
    <path d="M4 4l8 8M12 4l-8 8" />
  </Svg>
);
export const IconPaperclip = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13.25 7.5 8 12.75a3.18 3.18 0 0 1-4.5-4.5l5.6-5.6a2.12 2.12 0 0 1 3 3L6.5 11.25a1.06 1.06 0 0 1-1.5-1.5L10.25 4.5" />
  </Svg>
);
export const IconCheck = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.25 8.5 6.5 11.75l6.25-7" />
  </Svg>
);
export const IconCheckCircle = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M5.5 8.25 7.25 10l3.25-3.75" />
  </Svg>
);
export const IconAlert = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M8 4.75v3.75M8 11h.01" />
  </Svg>
);
export const IconDownload = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 2.75v7.75M5 7.5l3 3 3-3M2.75 10.5v1.75c0 .55.45 1 1 1h8.5c.55 0 1-.45 1-1V10.5" />
  </Svg>
);
export const IconPlus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M8 3v10M3 8h10" />
  </Svg>
);
export const IconTrash = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.75 4.25h10.5M6.25 4.25V2.75h3.5v1.5M4.25 4.25l.6 8.6c.04.52.47.9.98.9h4.34c.51 0 .94-.38.98-.9l.6-8.6" />
  </Svg>
);
export const IconChevronLeft = (p: IconProps) => (
  <Svg {...p}>
    <path d="M10 3.5 5.5 8l4.5 4.5" />
  </Svg>
);
export const IconChevronRight = (p: IconProps) => (
  <Svg {...p}>
    <path d="M6 3.5 10.5 8 6 12.5" />
  </Svg>
);
export const IconChevronDown = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3.5 6 8 10.5 12.5 6" />
  </Svg>
);
export const IconMinus = (p: IconProps) => (
  <Svg {...p}>
    <path d="M3 8h10" />
  </Svg>
);
export const IconArrowLeft = (p: IconProps) => (
  <Svg {...p}>
    <path d="M13 8H3M7 4 3 8l4 4" />
  </Svg>
);
export const IconClock = (p: IconProps) => (
  <Svg {...p}>
    <circle cx="8" cy="8" r="6.25" />
    <path d="M8 4.75V8l2 1.5" />
  </Svg>
);
export const IconScan = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.75 5.25v-1.5c0-.55.45-1 1-1h1.5M10.75 2.75h1.5c.55 0 1 .45 1 1v1.5M13.25 10.75v1.5c0 .55-.45 1-1 1h-1.5M5.25 13.25h-1.5c-.55 0-1-.45-1-1v-1.5M5 8h6" />
  </Svg>
);
export const IconWand = (p: IconProps) => (
  <Svg {...p}>
    <path d="M2.75 13.25 9.5 6.5M11 2.75v1.5M13.25 5h-1.5M12.5 7.75l-1-1M8.25 3.5l1 1" />
  </Svg>
);
