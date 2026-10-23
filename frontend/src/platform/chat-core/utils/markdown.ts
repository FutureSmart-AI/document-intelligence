/**
 * Normalizes markdown tables to ensure proper formatting.
 * This is critical for streaming scenarios where table chunks arrive
 * without proper line breaks between rows.
 */
export const normalizeMarkdownTables = (text: string): string => {
  if (!text || typeof text !== "string") return text;
  if (!text.includes("|")) return text;

  // Regex to identify a markdown table separator row.
  // Matches sequences like |---|---| or | :--- | :---: |
  // Must contain only pipes, dashes, colons, and whitespace.
  // Must have at least one internal pipe to be a valid multi-column separator.
  const separatorRegex = /\|[ \t]*:?-+:?[ \t]*(?:\|[ \t]*:?-+:?[ \t]*)+\|/;

  // `done` is the text whose tables are already laid out; `rest` is what is
  // still to be scanned. An answer that writes two tables used to have only
  // its first one normalized — the loop stopped at the first — and the second
  // reached the renderer as one long line of pipes, which markdown draws as a
  // paragraph rather than a table.
  let done = "";
  let rest = text;
  let match;

  while ((match = separatorRegex.exec(rest)) !== null) {
    const separator = match[0];
    const separatorIndex = match.index;
    const preSeparator = rest.slice(0, separatorIndex);
    const postSeparator = rest.slice(separatorIndex + separator.length);

    // Count columns based on structural pipes in the separator
    // Handle escaped pipes first to avoid miscounting
    const cleanSeparator = separator.replace(/\\\|/g, "  ");
    const pipeCount = (cleanSeparator.match(/\|/g) || []).length;

    // Not a valid table: carry it across untouched and scan on past it, so a
    // real table further down the answer still gets laid out.
    if (pipeCount < 2) {
      done += preSeparator + separator;
      rest = postSeparator;
      continue;
    }

    // --- Process Header (Pre-Separator) ---
    // The header should be the last 'pipeCount' pipes before the separator.
    let headerStartIndex = -1;
    let headerPipes = 0;

    // Scan backwards
    for (let i = preSeparator.length - 1; i >= 0; i--) {
      // Check for unescaped pipe
      if (
        preSeparator[i] === "|" &&
        (i === 0 || preSeparator[i - 1] !== "\\")
      ) {
        headerPipes++;
        if (headerPipes === pipeCount) {
          headerStartIndex = i;
          break;
        }
      }
    }

    let p1 = preSeparator;
    if (headerStartIndex !== -1) {
      const beforeHeader = preSeparator.slice(0, headerStartIndex);
      const headerRow = preSeparator.slice(headerStartIndex);
      const cleanBefore = beforeHeader.trimEnd();
      // Ensure specific structure: Text -> \n\n -> Header -> \n
      p1 = `${cleanBefore}${cleanBefore ? "\n\n" : ""}${headerRow.trim()}\n`;
    } else {
      p1 = `${preSeparator.trimEnd()}\n`;
    }

    // --- Process Body (Post-Separator) ---
    const rows: string[] = [];
    let currentPipes = 0;
    let lastSliceIndex = 0;

    for (let i = 0; i < postSeparator.length; i++) {
      const char = postSeparator[i];

      // Check for unescaped pipe
      if (char === "|" && (i === 0 || postSeparator[i - 1] !== "\\")) {
        currentPipes++;

        // Found a full row?
        if (currentPipes === pipeCount) {
          const rawSegment = postSeparator.slice(lastSliceIndex, i + 1);

          // Check for double newline which indicates a paragraph break (end of table)
          if (/\n\s*\n/.test(rawSegment)) {
            // We found a gap, stop processing this table here.
            // Do NOT update lastSliceIndex, so the remainder includes this segment.
            break;
          }

          // Important: TRIM the segment to remove leading/trailing newlines
          // This fixes the issue where existing newlines + our added newlines caused gaps
          rows.push(rawSegment.trim());

          currentPipes = 0;
          lastSliceIndex = i + 1;
        }
      }
    }

    const body = rows.join("\n");
    const remainder = postSeparator.slice(lastSliceIndex);

    done += p1 + separator + "\n" + body;
    // If the remainder looks like just text/footer, append it with a gap. It
    // goes back on `rest`, not on `done`: a second table lives in there, and
    // it needs the same treatment.
    if (remainder.trim()) {
      done += "\n\n";
      rest = remainder.trim();
    } else {
      rest = "";
    }
  }

  return done + rest;
};
