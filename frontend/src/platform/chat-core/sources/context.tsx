"use client";

import { createContext, useContext, type ReactNode } from "react";

import { slateSourcesTheme, type SourcesTheme } from "./theme";

/** Renders a markdown string with the host app's own markdown pipeline. */
export type RenderMarkdown = (text: string) => ReactNode;

interface SourcesContextValue {
  theme: SourcesTheme;
  renderMarkdown: RenderMarkdown;
}

const defaultRenderMarkdown: RenderMarkdown = (text) => text;

const SourcesContext = createContext<SourcesContextValue>({
  theme: slateSourcesTheme,
  renderMarkdown: defaultRenderMarkdown,
});

export const SourcesProvider = ({
  theme,
  renderMarkdown,
  children,
}: {
  theme: SourcesTheme;
  renderMarkdown: RenderMarkdown;
  children: ReactNode;
}) => (
  <SourcesContext.Provider value={{ theme, renderMarkdown }}>
    {children}
  </SourcesContext.Provider>
);

export const useSourcesTheme = () => useContext(SourcesContext).theme;
export const useRenderMarkdown = () =>
  useContext(SourcesContext).renderMarkdown;
