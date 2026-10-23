import { PrismAsyncLight as SyntaxHighlighter } from "react-syntax-highlighter";
import { materialDark } from "react-syntax-highlighter/dist/esm/styles/prism";

import { CopyToClipboard } from "./copy-to-clipboard";

interface CodeBlockProps {
  language?: string;
  children: string;
  /** Wrap long lines instead of scrolling horizontally (e.g. one-line SQL). */
  wrap?: boolean;
}

export const CodeBlock = ({ language, children, wrap }: CodeBlockProps) => {
  return (
    <>
      <div className="flex! items-center w-full gap-2 justify-between bg-[#0c0d0f] p-3 rounded-t-lg mt-4">
        <span className="text-xs text-white underline">{language}</span>
        <CopyToClipboard
          name={"Copy Code"}
          tooltip=""
          className="text-white! w-fit hover:text-white!"
        >
          {children}
        </CopyToClipboard>
      </div>
      <SyntaxHighlighter
        customStyle={{
          marginTop: "0px",
          borderRadius: "0px 0px 8px 8px",
          ...(wrap ? { whiteSpace: "pre-wrap", wordBreak: "break-word" } : {}),
        }}
        wrapLongLines={wrap}
        codeTagProps={
          wrap
            ? { style: { whiteSpace: "pre-wrap", wordBreak: "break-word" } }
            : undefined
        }
        className="scroll-custom"
        language={language ?? undefined}
        style={materialDark}
      >
        {children}
      </SyntaxHighlighter>
    </>
  );
};

export default CodeBlock;
