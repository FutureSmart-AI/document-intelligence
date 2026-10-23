import type { ReactNode } from "react";

// ---------- Base shimmer block ----------

type SkeletonProps = {
  className?: string;
  /** Stagger animation by this many ms */
  delay?: number;
};

/** A single shimmer rectangle. Compose these to build any skeleton layout. */
export const Skeleton = ({ className = "", delay = 0 }: SkeletonProps) => (
  <div
    className={`relative overflow-hidden rounded bg-slate-100 ${className}`}
    style={delay ? { animationDelay: `${delay}ms` } : undefined}
    aria-hidden="true"
  >
    <div
      className="absolute inset-0 -translate-x-full bg-linear-to-r from-transparent via-white/60 to-transparent"
      style={{
        animation: `shimmer 1.8s infinite ${delay}ms`,
      }}
    />
  </div>
);

// ---------- Semantic skeletons ----------

/** Text line skeleton — defaults to h-4. */
export const SkeletonLine = ({
  width = "w-full",
  height = "h-4",
  delay = 0,
}: {
  width?: string;
  height?: string;
  delay?: number;
}) => <Skeleton className={`${width} ${height}`} delay={delay} />;

/** Circle skeleton for avatars / icons. */
export const SkeletonCircle = ({
  size = "size-10",
  delay = 0,
}: {
  size?: string;
  delay?: number;
}) => <Skeleton className={`${size} rounded-full! shrink-0`} delay={delay} />;

/** Card-shaped skeleton with optional children for inner content. */
export const SkeletonCard = ({
  className = "",
  children,
  delay = 0,
}: {
  className?: string;
  children?: ReactNode;
  delay?: number;
}) => (
  <div
    className={`rounded-lg border border-gray-200 bg-white p-4 ${className}`}
    style={delay ? { animationDelay: `${delay}ms` } : undefined}
    aria-hidden="true"
  >
    {children ?? (
      <div className="space-y-3">
        <SkeletonLine width="w-1/3" height="h-5" />
        <SkeletonLine width="w-full" delay={100} />
        <SkeletonLine width="w-4/5" delay={200} />
      </div>
    )}
  </div>
);

// ---------- Page-level wrapper ----------

/** Full-width skeleton wrapper with header area + content slot. */
export const SkeletonPage = ({
  title,
  subtitle,
  children,
}: {
  title?: string;
  subtitle?: string;
  children: ReactNode;
}) => (
  <div
    className="relative w-full animate-in fade-in duration-300 px-6 pb-10"
    role="status"
    aria-live="polite"
    aria-busy="true"
  >
    {/* Header */}
    <div className="mb-6">
      {title ? (
        <h1 className="text-2xl font-semibold mt-10 text-gray-900">{title}</h1>
      ) : (
        <SkeletonLine width="w-48" height="h-7" />
      )}
      {subtitle ? (
        <p className="mt-1 text-sm text-gray-400">{subtitle}</p>
      ) : (
        <SkeletonLine width="w-80" height="h-4 mt-2" delay={80} />
      )}
    </div>

    {/* Page content */}
    {children}

    <span className="sr-only">Loading content</span>
  </div>
);
