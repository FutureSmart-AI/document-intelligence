import React, {
  memo,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useMemo,
  useState,
} from "react";

import { findIndex, get, map, size } from "lodash-es";

import Button from "./Button";

export type TabItem = {
  key: string;
  label: React.ReactNode;
  icon?: React.ReactNode;
  children?: React.ReactNode;
  content?: React.ReactNode;
  disabled?: boolean;
};

export type TabsProps = {
  items: TabItem[];
  activeKey?: string;
  defaultActiveKey?: string;
  onChange?: (key: string) => void;
  className?: string;
  headerClassName?: string;
  contentClassName?: string;
  idPrefix?: string;
  orientation?: "horizontal" | "vertical";
  justify?: "start" | "center" | "between";
  mountOnEnter?: boolean;
  unmountOnExit?: boolean;
  ariaLabel?: string;
};

type IndicatorStyle = React.CSSProperties;

const Tabs: React.FC<TabsProps> = ({
  items,
  activeKey: controlledActiveKey,
  defaultActiveKey,
  onChange,
  className = "",
  headerClassName = "",
  contentClassName = "",
  idPrefix = "tabs",
  orientation = "horizontal",
  justify = "start",
  mountOnEnter = false,
  unmountOnExit = false,
  ariaLabel,
}) => {
  const baseId = useId();
  const isHorizontal = orientation === "horizontal";

  const initialKey =
    controlledActiveKey ??
    defaultActiveKey ??
    (items.length > 0 ? items[0].key : "");

  const [internalKey, setInternalKey] = useState<string>(initialKey);
  const activeKey = controlledActiveKey ?? internalKey;

  useEffect(() => {
    if (controlledActiveKey !== undefined) {
      setInternalKey(controlledActiveKey);
    }
  }, [controlledActiveKey]);

  const computedItems = useMemo(
    () =>
      map(items, (item, index) => ({
        ...item,
        tabId: `${idPrefix}-${baseId}-tab-${index}`,
        panelId: `${idPrefix}-${baseId}-panel-${index}`,
      })),
    [items, idPrefix, baseId],
  );

  const setActive = useCallback(
    (key: string) => {
      if (controlledActiveKey === undefined) {
        setInternalKey(key);
      }
      onChange?.(key);
    },
    [controlledActiveKey, onChange],
  );

  const headerRef = React.useRef<HTMLDivElement | null>(null);
  const [indicatorStyle, setIndicatorStyle] = useState<IndicatorStyle>({
    opacity: 0,
  });

  const updateIndicator = useCallback(() => {
    if (!headerRef.current) return;
    const activeButton = headerRef.current.querySelector<HTMLButtonElement>(
      `[data-tab-key="${activeKey}"]`,
    );
    if (!activeButton) {
      setIndicatorStyle({ opacity: 0 });
      return;
    }
    const { offsetLeft, offsetWidth, offsetTop, offsetHeight } = activeButton;

    if (isHorizontal) {
      setIndicatorStyle({
        width: `${offsetWidth}px`,
        transform: `translateX(${offsetLeft}px)`,
        opacity: 1,
      });
    } else {
      setIndicatorStyle({
        height: `${offsetHeight}px`,
        transform: `translateY(${offsetTop}px)`,
        opacity: 1,
      });
    }
  }, [activeKey, isHorizontal]);

  useLayoutEffect(() => {
    updateIndicator();
  }, [updateIndicator, computedItems]);

  useEffect(() => {
    const handleResize = () => updateIndicator();
    window.addEventListener("resize", handleResize);
    return () => window.removeEventListener("resize", handleResize);
  }, [updateIndicator]);

  const [mountedKeys, setMountedKeys] = useState<Set<string>>(() => {
    if (!mountOnEnter && !unmountOnExit) {
      return new Set(map(items, (it) => it.key));
    }
    const initial = new Set<string>();
    if (activeKey) initial.add(activeKey);
    return initial;
  });

  useEffect(() => {
    if (!mountOnEnter) return;
    setMountedKeys((prev) => {
      if (prev.has(activeKey)) return prev;
      const next = new Set(prev);
      next.add(activeKey);
      return next;
    });
  }, [activeKey, mountOnEnter]);

  useEffect(() => {
    if (!mountOnEnter && !unmountOnExit) {
      setMountedKeys(new Set(map(items, (it) => it.key)));
    }
  }, [items, mountOnEnter, unmountOnExit]);

  const headerJustify =
    justify === "center"
      ? "justify-center"
      : justify === "between"
        ? "justify-between"
        : "justify-start";

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (size(computedItems) === 0) return;
    const idx = findIndex(computedItems, (it) => it.key === activeKey);
    if (idx < 0) return;

    const moveTo = (nextIdx: number) => {
      const item = get(computedItems, nextIdx);
      if (!item) return;
      if (item.disabled) {
        moveTo((nextIdx + 1) % size(computedItems));
        return;
      }
      setActive(item.key);
      const button = headerRef.current?.querySelector<HTMLButtonElement>(
        `[data-tab-key="${item.key}"]`,
      );
      button?.focus();
    };

    switch (e.key) {
      case "ArrowRight":
        if (isHorizontal) {
          e.preventDefault();
          moveTo((idx + 1) % size(computedItems));
        }
        break;
      case "ArrowLeft":
        if (isHorizontal) {
          e.preventDefault();
          moveTo((idx - 1 + size(computedItems)) % size(computedItems));
        }
        break;
      case "ArrowDown":
        if (!isHorizontal) {
          e.preventDefault();
          moveTo((idx + 1) % size(computedItems));
        }
        break;
      case "ArrowUp":
        if (!isHorizontal) {
          e.preventDefault();
          moveTo((idx - 1 + size(computedItems)) % size(computedItems));
        }
        break;
      case "Home":
        e.preventDefault();
        moveTo(0);
        break;
      case "End":
        e.preventDefault();
        moveTo(size(computedItems) - 1);
        break;
      default:
        break;
    }
  };

  return (
    <div className={className}>
      <div
        ref={headerRef}
        role="tablist"
        aria-label={ariaLabel}
        aria-orientation={orientation}
        onKeyDown={handleKeyDown}
        className={`relative ${
          isHorizontal
            ? "flex w-full flex-wrap items-center gap-2 border-b border-slate-200"
            : "flex w-full flex-col gap-2 border-slate-200 border-r"
        } ${headerJustify} px-2 pb-1 pt-2 sm:px-4 sm:pt-3 ${headerClassName}`}
      >
        {map(computedItems, (tab) => {
          const isActive = activeKey === tab.key;
          const stateClasses = isActive
            ? "text-indigo-600"
            : "text-slate-500 hover:text-slate-700";
          return (
            <Button
              key={tab.key}
              data-tab-key={tab.key}
              role="tab"
              variant="ghost"
              size="sm"
              id={tab.tabId}
              tabIndex={isActive ? 0 : -1}
              aria-selected={isActive}
              aria-controls={tab.panelId}
              disabled={tab.disabled}
              className={`group relative inline-flex items-center gap-2 rounded-full px-4 py-2 text-sm font-medium transition-colors duration-150 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-indigo-500 focus-visible:ring-offset-2 ${stateClasses} ${tab.disabled ? "opacity-50" : ""}`}
              onClick={() => !tab.disabled && setActive(tab.key)}
            >
              <span className="flex items-center gap-2">
                {tab.icon ? (
                  <span className="text-base text-current">{tab.icon}</span>
                ) : null}
                <span className="truncate">{tab.label}</span>
              </span>
            </Button>
          );
        })}
        <span
          aria-hidden="true"
          className={`pointer-events-none absolute rounded-full bg-indigo-500 transition-all duration-300 ease-out ${
            isHorizontal ? "bottom-0 left-0 h-0.5" : "top-2 left-0 w-0.5"
          }`}
          style={indicatorStyle}
        />
      </div>
      <div
        className={`${
          isHorizontal ? "mt-2" : "mt-2 border-l border-slate-200 pl-4"
        } ${contentClassName}`}
      >
        {map(computedItems, (tab) => {
          const isActive = activeKey === tab.key;
          const shouldRender = unmountOnExit
            ? isActive
            : mountedKeys.has(tab.key);
          return (
            <div
              key={tab.key}
              id={tab.panelId}
              role="tabpanel"
              aria-labelledby={tab.tabId}
              hidden={!isActive}
              className="rounded-xl bg-white px-2 py-3 focus-visible:outline-none sm:px-0"
              tabIndex={0}
            >
              {shouldRender ? (tab.children ?? tab.content ?? null) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default memo(Tabs);
