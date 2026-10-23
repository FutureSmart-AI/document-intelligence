"use client";

import React, {
  createContext,
  isValidElement,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";
import { createPortal } from "react-dom";

import { filter, map } from "lodash-es";
import {
  AlertTriangle,
  CheckCircle2,
  Info,
  Loader2,
  X,
  XCircle,
} from "lucide-react";

import { Button } from "./ui";
import {
  DEFAULT_POSITION,
  POSITION_STYLES,
  ToastPosition,
} from "./ui/constants";

export { DEFAULT_POSITION };

export type { ToastPosition };

const ToastContext = createContext({
  toast: {} as ToastApi,
});

type ToastType = "success" | "error" | "info" | "warning" | "loading";

type ToastOptions = {
  key?: string | number;
  duration?: number; // ms
  position?: ToastPosition;
};

type ToastArgs = ToastOptions & {
  type?: ToastType;
  content: string | React.ReactNode;
};

type ToastApiFunction = (
  message: string | React.ReactNode,
  options?: ToastOptions,
) => void;

export type ToastApi = {
  error: ToastApiFunction;
  success: ToastApiFunction;
  info: ToastApiFunction;
  warning: ToastApiFunction;
  loading: (
    message: string | React.ReactNode,
    options?: ToastOptions,
  ) => string;
  open: (args: ToastArgs) => string | number;
  destroy: (key: string | number) => void;
};

type ToastItem = {
  id: string | number;
  type: ToastType;
  content: React.ReactNode;
  createdAt: number;
  duration: number | null; // null means persistent
  mounted?: boolean; // enter animation
  exiting?: boolean; // exit animation
  position: ToastPosition;
};

interface ToastBaseProps {
  timersRef: React.RefObject<Map<string | number, number>>;
  expiriesRef: React.RefObject<Map<string | number, number>>;
  dismiss: (id: string | number) => void;
  scheduleDismiss: (id: string | number, delayMs: number) => void;
  iconFor: (type: ToastType) => React.ReactNode;
}

interface ToastViewProps extends ToastBaseProps {
  toasts: ToastItem[];
}

interface ToastItemComponentProps extends ToastBaseProps {
  toast: ToastItem;
}

const ToastItemComponent = ({ toast, ...rest }: ToastItemComponentProps) => {
  const { timersRef, expiriesRef, dismiss, scheduleDismiss, iconFor } = rest;

  const tone =
    toast.type === "success"
      ? "border-emerald-200 bg-emerald-50"
      : toast.type === "error"
        ? "border-rose-200 bg-rose-50"
        : toast.type === "warning"
          ? "border-amber-200 bg-amber-50"
          : "border-slate-200 bg-white";
  const motion =
    toast.mounted && !toast.exiting
      ? "opacity-100 translate-y-0 scale-100"
      : "opacity-0 translate-y-2 scale-95";

  return (
    <div
      key={toast.id}
      role="status"
      className={`pointer-events-auto inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm shadow-lg ring-1 ring-black/5 transform transition-all duration-200 ease-out ${tone} ${motion}`}
      onMouseEnter={() => {
        if (toast.duration !== null) {
          const h = timersRef.current.get(toast.id);
          if (h) {
            window.clearTimeout(h);
            timersRef.current.delete(toast.id);
          }
        }
      }}
      onMouseLeave={() => {
        if (toast.duration !== null && !toast.exiting) {
          const expiryAt =
            expiriesRef.current.get(toast.id) ??
            toast.createdAt + (toast.duration ?? 0);
          const remaining = Math.max(0, expiryAt - Date.now());
          if (remaining > 0) {
            scheduleDismiss(toast.id, remaining);
          } else {
            dismiss(toast.id);
          }
        }
      }}
    >
      <div className="mt-0.5">{iconFor(toast.type)}</div>
      <div className="min-w-0 flex-1 text-slate-800">
        <span>
          {/* An element renders as itself: the API takes a `ReactNode`, and a
              toast carrying a link (Document Intelligence's quota refusal
              points at the Limits tab) used to be printed as its own JSON.
              Anything else is still stringified rather than thrown at React. */}
          {typeof toast.content === "string" || isValidElement(toast.content)
            ? toast.content
            : JSON.stringify(toast.content)}
        </span>
      </div>
      <Button
        type="button"
        variant="ghost"
        size="icon"
        shape="rounded"
        className="ml-2 hover:bg-emerald-100!"
        onClick={() => dismiss(toast.id)}
        aria-label="Dismiss"
        startIcon={<X className="size-4 text-emerald-600" aria-hidden="true" />}
      />
    </div>
  );
};

const ToastView = ({ toasts, ...rest }: ToastViewProps) => {
  const grouped = useMemo(() => {
    return toasts.reduce<Record<ToastPosition, ToastItem[]>>(
      (acc, toast) => {
        const key = toast.position ?? DEFAULT_POSITION;
        if (!acc[key]) {
          acc[key] = [];
        }
        acc[key].push(toast);
        return acc;
      },
      {} as Record<ToastPosition, ToastItem[]>,
    );
  }, [toasts]);

  return (
    <>
      {map(Object.entries(grouped), ([position, positionToasts]) => {
        const { container, direction } =
          POSITION_STYLES[position as ToastPosition];
        return (
          <div
            key={position}
            className={`pointer-events-none fixed z-10000 flex w-full max-w-sm gap-2 ${container} ${direction}`}
            aria-live="polite"
          >
            {map(positionToasts, (toast) => (
              <ToastItemComponent key={toast.id} toast={toast} {...rest} />
            ))}
          </div>
        );
      })}
    </>
  );
};

export const ToastProvider = ({ children }: { children: React.ReactNode }) => {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const [mounted, setMounted] = useState(false);
  const idRef = useRef(0);
  const timersRef = useRef<Map<string | number, number>>(new Map());
  const expiriesRef = useRef<Map<string | number, number>>(new Map());

  useEffect(() => {
    setMounted(true);
  }, []);

  const removeNow = useCallback((id: string | number) => {
    setToasts((prev) => filter(prev, (t) => t.id !== id));
  }, []);

  const dismiss = useCallback(
    (id: string | number) => {
      // cancel any running auto-dismiss timer
      const h = timersRef.current.get(id);
      if (h) {
        window.clearTimeout(h);
        timersRef.current.delete(id);
      }
      // trigger exit animation then remove
      setToasts((prev) =>
        map(prev, (t) => (t.id === id ? { ...t, exiting: true } : t)),
      );
      window.setTimeout(() => removeNow(id), 220);
    },
    [removeNow],
  );

  const scheduleDismiss = useCallback(
    (id: string | number, delayMs: number) => {
      // clear previous timer if any
      const prev = timersRef.current.get(id);
      if (prev) {
        window.clearTimeout(prev);
        timersRef.current.delete(id);
      }
      const handle = window.setTimeout(() => dismiss(id), delayMs);
      timersRef.current.set(id, handle);
    },
    [dismiss],
  );

  const push = useCallback(
    (type: ToastType, content: React.ReactNode, options: ToastOptions = {}) => {
      const { key, duration, position } = options;
      const id = key ?? `t-${Date.now()}-${++idRef.current}`;
      const isLoading = type === "loading";
      const toastItem: ToastItem = {
        id,
        type,
        content:
          typeof content === "object" && content !== null
            ? content
            : String(content),
        createdAt: Date.now(),
        duration: isLoading
          ? null
          : typeof duration === "number"
            ? duration
            : 3000,
        mounted: false,
        exiting: false,
        position: position ?? DEFAULT_POSITION,
      };
      setToasts((prev) => [...prev, toastItem]);
      // enter animation
      window.setTimeout(() => {
        setToasts((prev) =>
          map(prev, (t) => (t.id === id ? { ...t, mounted: true } : t)),
        );
      }, 10);
      // auto dismiss
      if (toastItem.duration) {
        // remember expiry and schedule a timer
        const expiryAt = Date.now() + toastItem.duration;
        expiriesRef.current.set(id, expiryAt);
        scheduleDismiss(id, toastItem.duration);
      }
      return id;
    },
    [scheduleDismiss],
  );

  const toast: ToastApi = useMemo(
    () => ({
      error: (message: string | React.ReactNode, options?: ToastOptions) => {
        push("error", message, options);
      },
      success: (message: string | React.ReactNode, options?: ToastOptions) => {
        push("success", message, options);
      },
      info: (message: string | React.ReactNode, options?: ToastOptions) => {
        push("info", message, options);
      },
      warning: (message: string | React.ReactNode, options?: ToastOptions) => {
        push("warning", message, options);
      },
      loading: (message: string | React.ReactNode, options?: ToastOptions) => {
        return String(push("loading", message, options));
      },
      open: (args: ToastArgs) => {
        const { type = "info", content, duration, key, position } = args || {};
        return push(type, content, { key, duration, position });
      },
      destroy: (key: string | number) => dismiss(key),
    }),
    [push, dismiss],
  );

  const iconFor = (type: ToastType) => {
    switch (type) {
      case "success":
        return (
          <CheckCircle2
            className="h-4 w-4 text-emerald-600"
            aria-hidden="true"
          />
        );
      case "error":
        return <XCircle className="h-4 w-4 text-rose-600" aria-hidden="true" />;
      case "warning":
        return (
          <AlertTriangle
            className="h-4 w-4 text-amber-600"
            aria-hidden="true"
          />
        );
      case "loading":
        return (
          <Loader2
            className="h-4 w-4 text-slate-600 animate-spin"
            aria-hidden="true"
          />
        );
      default:
        return <Info className="h-4 w-4 text-indigo-600" aria-hidden="true" />;
    }
  };

  const contextValue = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={contextValue}>
      {children}
      {/* Toast viewport */}
      {mounted &&
        typeof document !== "undefined" &&
        createPortal(
          <ToastView
            toasts={toasts}
            timersRef={timersRef}
            expiriesRef={expiriesRef}
            dismiss={dismiss}
            scheduleDismiss={scheduleDismiss}
            iconFor={iconFor}
          />,
          document.body,
        )}
    </ToastContext.Provider>
  );
};

export const useToast = () => {
  const context = useContext(ToastContext);
  if (!context.toast.open) {
    throw new Error("useToast must be used within a ToastProvider");
  }
  return context;
};
