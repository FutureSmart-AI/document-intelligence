import type { ReactNode } from "react";

import { MediaEmbed } from "./embeds";
import { getParagraphMediaMatch, getParagraphMediaMatches } from "./media";

export interface MarkdownComponentOverrides {
  a?: (props: { href?: string; children?: ReactNode }) => ReactNode;
  img?: (props: {
    src?: string;
    alt?: string;
    [key: string]: any;
  }) => ReactNode;
  code?: (props: {
    language: string | null;
    children: ReactNode;
    className?: string;
    [key: string]: any;
  }) => ReactNode;
}

/**
 * Returns a ReactMarkdown-compatible `components` object with consistent
 * element styling. Consumers can provide framework-specific overrides for
 * `a`, `img`, and `code` elements.
 */
export function getMarkdownComponents(
  overrides?: MarkdownComponentOverrides,
): Record<string, any> {
  return {
    ol: ({ node, children, ...props }: any) => (
      <ol className="list-decimal list-outside ml-4" {...props}>
        {children}
      </ol>
    ),
    /* list-disc is load-bearing: Tailwind's preflight sets `list-style: none`
       on every ul, so a bulleted list rendered without it lost its bullets and
       read as a run of unindented lines. list-outside matches the ol above so
       a wrapped item lines up under its own text rather than under the mark. */
    ul: ({ node, children, ...props }: any) => (
      <ul className="list-disc list-outside ml-4" {...props}>
        {children}
      </ul>
    ),
    li: ({ node, children, ...props }: any) => (
      <li className="py-1" {...props}>
        {children}
      </li>
    ),
    /* A real <strong>, not a styled span: bold in an answer is emphasis, and a
       screen reader announces it only from the element. */
    strong: ({ node, children, ...props }: any) => (
      <strong className="font-semibold" {...props}>
        {children}
      </strong>
    ),
    p: ({ node, children, ...props }: any) => {
      // A paragraph that is *only* a bare media URL is replaced entirely by the
      // trusted embed (clean, no leftover link text).
      const soleMedia = getParagraphMediaMatch(node);
      if (soleMedia) return <MediaEmbed match={soleMedia} />;

      // Otherwise, if the paragraph still contains bare media URLs alongside
      // text (e.g. "Yes — https://youtu.be/ID"), keep the text and render the
      // embeds below it.
      const inlineMedia = getParagraphMediaMatches(node);
      const paragraph = (
        <p className="my-2" {...props}>
          {children}
        </p>
      );
      if (inlineMedia.length === 0) return paragraph;
      return (
        <>
          {paragraph}
          {inlineMedia.map((match, i) => (
            <MediaEmbed key={`${match.url}-${i}`} match={match} />
          ))}
        </>
      );
    },
    h1: ({ node, children, ...props }: any) => (
      <h1 className="text-3xl font-semibold mt-6 mb-2" {...props}>
        {children}
      </h1>
    ),
    h2: ({ node, children, ...props }: any) => (
      <h2 className="text-2xl font-semibold mt-6 mb-2" {...props}>
        {children}
      </h2>
    ),
    h3: ({ node, children, ...props }: any) => (
      <h3 className="text-xl font-semibold mt-6 mb-2" {...props}>
        {children}
      </h3>
    ),
    h4: ({ node, children, ...props }: any) => (
      <h4 className="text-lg font-semibold mt-6 mb-2" {...props}>
        {children}
      </h4>
    ),
    h5: ({ node, children, ...props }: any) => (
      <h5 className="text-base font-semibold mt-6 mb-2" {...props}>
        {children}
      </h5>
    ),
    h6: ({ node, children, ...props }: any) => (
      <h6 className="text-sm font-semibold mt-6 mb-2" {...props}>
        {children}
      </h6>
    ),
    blockquote: ({ node, children, ...props }: any) => (
      <blockquote
        className="border-l-4 border-slate-300 pl-4 italic my-4 text-slate-600"
        {...props}
      >
        {children}
      </blockquote>
    ),

    /* Raw-HTML media elements (rendered via rehype-raw). Each is forced to
       stay within the message bubble: max-w-full caps the width to the
       container regardless of any width="…" attribute, and h-auto preserves
       the aspect ratio so nothing overflows on narrow screens. */
    video: ({ node, children, ...props }: any) => (
      <video
        controls
        className="my-3 max-w-full h-auto rounded-lg block"
        style={{ maxWidth: "100%" }}
        {...props}
      >
        {children}
      </video>
    ),
    audio: ({ node, children, ...props }: any) => (
      <audio
        controls
        className="my-3 w-full max-w-full block"
        style={{ maxWidth: "100%" }}
        {...props}
      >
        {children}
      </audio>
    ),
    iframe: ({ src, title, allow }: any) => {
      // Only render http(s) embeds and force a restrictive sandbox so framed
      // content can't script the host page. Explicit attribute allowlist —
      // no blind {...props} spread that could reintroduce unsafe attributes.
      const isSafeSrc = typeof src === "string" && /^https?:\/\//i.test(src);
      if (!isSafeSrc) return null;
      return (
        <iframe
          className="my-3 max-w-full w-full rounded-lg border-0 aspect-video"
          style={{ maxWidth: "100%" }}
          src={src}
          title={title}
          allow={allow}
          loading="lazy"
          /* YouTube (and many embed providers) reject playback when no referrer
             is sent — that surfaced as Error 153. strict-origin-when-cross-origin
             sends the origin (not the full URL) so embeds work without leaking
             the full path. */
          referrerPolicy="strict-origin-when-cross-origin"
          sandbox="allow-scripts allow-same-origin allow-popups allow-presentation"
        />
      );
    },
    source: ({ src, srcSet, type, media, sizes }: any) => (
      <source
        src={src}
        srcSet={srcSet}
        type={type}
        media={media}
        sizes={sizes}
      />
    ),
    table: ({ node, children, ...props }: any) => (
      /* overflow-x-auto + -mx-1 lets the table scroll horizontally on narrow
         screens without the wrapper itself overflowing the message bubble */
      <div className="overflow-x-auto my-3 rounded-lg shadow-sm border border-slate-200 bg-white max-w-full">
        <table
          className="min-w-full border-collapse text-xs table-auto"
          {...props}
        >
          {children}
        </table>
      </div>
    ),
    thead: ({ node, children, ...props }: any) => (
      <thead
        className="bg-linear-to-r from-slate-50 to-slate-100/80 border-b-2 border-slate-200"
        {...props}
      >
        {children}
      </thead>
    ),
    tbody: ({ node, children, ...props }: any) => (
      <tbody className="bg-white divide-y divide-slate-100" {...props}>
        {children}
      </tbody>
    ),
    tr: ({ node, children, ...props }: any) => (
      <tr
        className="hover:bg-indigo-50/50 transition-colors duration-150 even:bg-slate-50/30"
        {...props}
      >
        {children}
      </tr>
    ),
    th: ({ node, children, ...props }: any) => (
      <th
        /* px-2.5 py-2: tighter padding so multi-column tables fit on narrow screens.
           whitespace-nowrap prevents header text from wrapping mid-word. */
        className="px-2.5 py-2 text-left font-semibold text-slate-900 text-xs uppercase tracking-wide whitespace-nowrap"
        {...props}
      >
        {children}
      </th>
    ),
    td: ({ node, children, ...props }: any) => (
      <td
        /* break-words + max-w-[200px] keeps long cells from blowing out the table;
           content scrolls horizontally via the parent wrapper. */
        className="px-2.5 py-2 text-slate-700 text-xs border-b border-slate-100 last:border-b-0 wrap-break-word max-w-[200px]"
        {...props}
      >
        {children}
      </td>
    ),

    // Override-able elements
    a: overrides?.a
      ? ({ node, children, href, ...props }: any) =>
          overrides.a!({ href, children })
      : ({ node, children, href, ...props }: any) => (
          <a
            className="text-blue-500 hover:underline break-all"
            target="_blank"
            rel="noreferrer"
            href={href}
            {...props}
          >
            {children}
          </a>
        ),

    img: overrides?.img
      ? ({ node, src, alt, ...props }: any) =>
          overrides.img!({ src, alt, ...props })
      : ({ node, src, alt, ...props }: any) => (
          <img
            className="max-w-full h-auto rounded-lg"
            style={{ maxWidth: "100%" }}
            src={src}
            alt={alt}
            loading="lazy"
            {...props}
          />
        ),

    code: overrides?.code
      ? ({ node, className, children, ...props }: any) => {
          const match = /language-(\w+)/.exec(className || "");
          const language = match ? match[1] : null;
          if (!language) {
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          }
          return overrides.code!({ language, children, className, ...props });
        }
      : ({ node, className, children, ...props }: any) => {
          const match = /language-(\w+)/.exec(className || "");
          if (!match) {
            return (
              <code className={className} {...props}>
                {children}
              </code>
            );
          }
          return (
            <pre className="bg-slate-900 text-slate-50 rounded-lg p-4 overflow-x-auto my-4">
              <code className={className} {...props}>
                {children}
              </code>
            </pre>
          );
        },
  };
}
