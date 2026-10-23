import { memo, type ReactNode } from "react";
import dynamic from "next/dynamic";
import Image from "next/image";
import Link from "next/link";

import ReactMarkdown from "react-markdown";
import rehypeRaw from "rehype-raw";
import rehypeSanitize from "rehype-sanitize";
import remarkGfm from "remark-gfm";

import {
  getMarkdownComponents,
  markdownSanitizeSchema,
} from "@/platform/chat-core/markdown";
import { normalizeMarkdownTables } from "@/platform/chat-core/utils";

const CodeBlock = dynamic(() => import("./CodeBlock"), { ssr: false });

interface IMarkdown {
  children: string;
}

// A markdown link styled consistently for both real links and bare URLs the
// model wrapped in backticks (see the `code` handler below). Inside the user
// bubble these are recolored via a `[&_a]:` override in ChatMessage.
const MarkdownLink = ({
  href,
  children,
}: {
  href: string;
  children: ReactNode;
}) => (
  <Link
    className="text-blue-500 hover:underline break-all"
    target="_blank"
    rel="noreferrer"
    href={href}
  >
    {children}
  </Link>
);

// Inline code whose entire content is a URL. Chat models frequently wrap links
// in backticks, which markdown would otherwise render as non-clickable code.
const inlineCodeUrlPattern = /^(https?:\/\/[^\s]+|www\.[^\s]+)$/i;
const inlineCodeHref = (text: string): string | null => {
  if (!inlineCodeUrlPattern.test(text)) return null;
  return text.toLowerCase().startsWith("www.") ? `http://${text}` : text;
};

const baseComponents = getMarkdownComponents({
  a: ({ href, children }) => (
    <MarkdownLink href={href ?? ""}>{children}</MarkdownLink>
  ),
  img: ({ src, alt }) => (
    <Image
      className="max-w-md lg:max-w-xl"
      src={src ?? ""}
      alt={alt ?? ""}
      width={100}
      height={100}
      loading="lazy"
      priority={false}
      quality={75}
      sizes="100vw"
      style={{
        width: "100%",
        height: "auto",
      }}
    />
  ),
  code: ({ language, children }) => {
    const codeString = String(children);
    return <CodeBlock language={language}>{codeString}</CodeBlock>;
  },
});

// chat-core routes only fenced blocks (with a `language-*` class) to the `code`
// override above and renders inline code as plain <code>. Wrap it so a URL-only
// inline span becomes a real link; everything else keeps chat-core's rendering.
const BaseCode = baseComponents.code;
const components = {
  ...baseComponents,
  code: (props: {
    className?: string;
    children?: ReactNode;
    [key: string]: unknown;
  }) => {
    const isFenced = /language-(\w+)/.test(props.className || "");
    if (!isFenced) {
      const text = String(props.children ?? "").trim();
      const href = inlineCodeHref(text);
      if (href) return <MarkdownLink href={href}>{text}</MarkdownLink>;
    }
    return <BaseCode {...props} />;
  },
};

const NonMemoizedMarkdown = ({ children }: IMarkdown) => {
  const normalizedContent = normalizeMarkdownTables(children);

  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeRaw, [rehypeSanitize, markdownSanitizeSchema]]}
      components={components}
    >
      {normalizedContent}
    </ReactMarkdown>
  );
};

export const Markdown = memo(
  NonMemoizedMarkdown,
  (prevProps, nextProps) => prevProps.children === nextProps.children,
);
