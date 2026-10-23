import React, { memo } from "react";

type Renderable<T> = React.ReactNode | ((value: T) => React.ReactNode);

export interface ShowProps<T = unknown> {
  when: T | boolean | null | undefined;
  fallback?: Renderable<T>;
  children: Renderable<T>;
}

/**
 * Show component: renders children when the `when` prop is truthy, otherwise renders `fallback`.
 * - Supports render-prop pattern for both `children` and `fallback`:
 *   <Show when={value}>{(v) => <div>{v}</div>}</Show>
 */
export function Show<T = unknown>({
  when,
  fallback = null,
  children,
}: ShowProps<T>) {
  const isTruthy = Boolean(when);

  if (isTruthy) {
    if (typeof children === "function") {
      return <>{(children as (value: T) => React.ReactNode)(when as T)}</>;
    }
    return <>{children}</>;
  }

  if (typeof fallback === "function") {
    return <>{(fallback as (value: T) => React.ReactNode)(when as T)}</>;
  }
  return <>{fallback}</>;
}

export default memo(Show);
