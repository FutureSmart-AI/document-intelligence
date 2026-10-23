# Self-hosted web fonts

The four families the apps draw in, as **variable** woff2 files cut to the
`latin` subset — byte-identical to what `fonts.gstatic.com` serves for
`subsets: ["latin"]`, which is the subset every call site asked for.

They are committed rather than fetched by `next/font/google` because that loader
resolves the font file URLs at **build time**, and Google's edge intermittently
answers with its dynamic-cut form:

    https://fonts.gstatic.com/l/font?kit=…&skey=…&v=v20

Next 16's Turbopack font replacer packs that URL into a `?{json}` query, whose
raw `&` splits it into three query entries, and the build dies with
`next/font/google queries have exactly one entry`. It took the prod deploy down
on 2026-09-23 (run 35908220394) with 48 such errors. Self-hosting removes the
network from every build, so a deploy can no longer fail on Google's mood.

Each file is the full variable weight axis, so the discrete weights the call
sites used to request are still available — `next/font/local` declares the range
in `weight` and the browser instances it.

| File                                      | Family              | Axis shipped                                |
| ----------------------------------------- | ------------------- | ------------------------------------------- |
| `BricolageGrotesque-Variable-latin.woff2` | Bricolage Grotesque | `opsz` 12–96, `wdth` 75–100, `wght` 200–800 |
| `Manrope-Variable-latin.woff2`            | Manrope             | `wght` 200–800                              |
| `PlusJakartaSans-Variable-latin.woff2`    | Plus Jakarta Sans   | `wght` 200–800                              |
| `JetBrainsMono-Variable-latin.woff2`      | JetBrains Mono      | `wght` 100–800                              |

All four are licensed under the SIL Open Font License 1.1, which permits
redistribution. Upstream: <https://fonts.google.com/>.

To refresh a file, request its CSS with a modern Chrome user agent and take the
URL from the `/* latin */` block:

    curl -A '<modern Chrome UA>' \
      'https://fonts.googleapis.com/css2?family=Manrope:wght@200..800&display=swap'
