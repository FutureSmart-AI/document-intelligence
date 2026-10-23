/**
 * Theme contract for the shared Sources panel.
 *
 * The components own all *layout* (spacing, flex, rounding, sizing) and read
 * every *color / surface* decision from this object. That lets two apps with
 * incompatible palettes — the dashboard (hardcoded slate/indigo) and the
 * embeddable widget (shadcn HSL token classes) — reuse the same structure and
 * behaviour while staying visually native to each host.
 *
 * Two ready-made presets ship below so neither consumer hand-rolls the object.
 *
 * The dashboard preset's color values are the exact tokens from the approved
 * "Show Sources" design (the `FS` palette: bg #F5F6FA, brand #5B5BD6, etc.).
 */
export interface SourcesTheme {
  /** Outer panel container background + border. */
  panel: string;
  /** Tinted strip behind the panel header row. */
  panelHeaderBg: string;
  /** Inset background behind the scrollable list of cards. */
  bodyInset: string;
  /** Primary surface for cards (file cards, chunk cards). */
  surface: string;
  /** Hover affordance applied to interactive cards. */
  cardHover: string;
  /** Inset/muted surface for summary, metadata and content boxes. */
  surfaceMuted: string;
  /** Default divider/border color. */
  border: string;
  /** Subtle divider for in-card separators. */
  borderSubtle: string;
  /** Dashed empty-state surface. */
  emptyState: string;

  /** Strongest text (panel title). */
  textPrimary: string;
  /** Body text inside cards/content. */
  textBody: string;
  /** Secondary/label text. */
  textMuted: string;
  /** Faint text (placeholders, decorative icons). */
  textFaint: string;

  /** Accent text (file names, links, chevrons of interest). */
  accentText: string;
  /** Soft accent pill (scores, metadata array tags). */
  accentSoft: string;
  /** Accent icon container (header icon, card index badge). */
  accentIconBg: string;

  /** Header count pill (brand-soft fill + brand text). */
  countBadge: string;
  /** Card index badge — outlined circle. */
  indexBadge: string;
  /** Outlined pill (source type, position). */
  tagOutline: string;
  /** Brand-filled pill (score, metadata array tags). */
  tagBrand: string;

  /** Disclosure sub-header button (document metadata / related chunks). */
  subHeader: string;
  /** Icon chip inside the "Document metadata" sub-header. */
  subHeaderIconInfo: string;
  /** Icon chip inside the "Related chunks" sub-header. */
  subHeaderIconChunks: string;

  /** Monospace chunk-content box. */
  chunkPre: string;
  /** Square icon button (export, copy, open, expand). */
  iconBtn: string;
  /** Square icon button — active/toggled state. */
  iconBtnActive: string;

  /** Neutral chip (counts, type, chunk id, position). */
  chip: string;

  /** Hover background for compact buttons (load more, view toggle). */
  hoverBg: string;
  /** Hover background for outlined toolbar buttons. */
  hoverSurface: string;
  /** Outlined control border (toolbar buttons, input, select). */
  control: string;
  /** Focus ring/border for the search input + select. */
  controlFocus: string;

  /** Prose wrapper classes for rendered markdown (summary + chunk content). */
  prose: string;
}

/** Dashboard look: exact tokens from the approved Show Sources design. */
export const slateSourcesTheme: SourcesTheme = {
  panel: "border-[#E6E7EC] bg-white shadow-sm",
  panelHeaderBg: "bg-[#FAFAFB]",
  bodyInset: "bg-[#F5F6FA]",
  surface: "border-[#E6E7EC] bg-white",
  cardHover: "hover:border-[#D6D7DE]",
  surfaceMuted: "border-[#E6E7EC] bg-[#FAFAFB]",
  border: "border-[#E6E7EC]",
  borderSubtle: "border-[#E6E7EC]",
  emptyState: "border-[#D6D7DE] bg-white text-[#9095A6]",

  textPrimary: "text-[#1B1D29]",
  textBody: "text-[#3D4154]",
  textMuted: "text-[#6B6F82]",
  textFaint: "text-[#9095A6]",

  accentText: "text-[#4F46E5]",
  accentSoft: "bg-[#EEEEFB] text-[#4F46E5]",
  accentIconBg: "bg-[#EEEEFB] text-[#4F46E5]",

  countBadge: "bg-[#EEEEFB] text-[#4F46E5]",
  indexBadge: "border-[1.5px] border-[#5B5BD6] bg-white text-[#4F46E5]",
  tagOutline: "border border-[#E6E7EC] bg-white text-[#6B6F82]",
  tagBrand: "bg-[#EEEEFB] text-[#4F46E5]",

  subHeader:
    "border border-[#E6E7EC] bg-white text-[#1B1D29] hover:bg-[#FAFAFB]",
  subHeaderIconInfo: "bg-[#DBEAFE] text-[#3D4154]",
  subHeaderIconChunks: "bg-[#E0E7FF] text-[#3D4154]",

  chunkPre: "border-[#E6E7EC] bg-[#F7F8FB] text-[#3D4154]",
  iconBtn: "border border-[#E6E7EC] bg-white text-[#6B6F82] hover:bg-[#FAFAFB]",
  iconBtnActive: "border border-[#5B5BD6] bg-[#EEEEFB] text-[#4F46E5]",

  chip: "bg-[#F1F2F7] text-[#3D4154]",

  hoverBg: "hover:bg-[#F1F2F7]",
  hoverSurface: "hover:bg-[#FAFAFB]",
  control: "border-[#E6E7EC]",
  controlFocus: "focus:border-[#5B5BD6]",

  prose: "prose prose-sm",
};

/** Widget look: shadcn semantic token classes, themed per-agent via CSS vars. */
export const tokenSourcesTheme: SourcesTheme = {
  panel: "border-border bg-card shadow-sm",
  panelHeaderBg: "bg-muted/30",
  bodyInset: "bg-muted/20",
  surface: "border-border bg-card",
  cardHover: "hover:border-primary/40",
  surfaceMuted: "border-border bg-muted/50",
  border: "border-border",
  borderSubtle: "border-border/60",
  emptyState: "border-border bg-card text-muted-foreground",

  textPrimary: "text-foreground",
  textBody: "text-foreground",
  textMuted: "text-muted-foreground",
  textFaint: "text-muted-foreground/70",

  accentText: "text-primary",
  accentSoft: "bg-primary/10 text-primary",
  accentIconBg: "bg-primary/15 text-primary",

  countBadge: "bg-primary/10 text-primary",
  indexBadge: "border-[1.5px] border-primary bg-background text-primary",
  tagOutline: "border border-border bg-background text-muted-foreground",
  tagBrand: "bg-primary/10 text-primary",

  subHeader: "border border-border bg-card text-foreground hover:bg-muted/50",
  subHeaderIconInfo: "bg-primary/15 text-primary",
  subHeaderIconChunks: "bg-primary/20 text-primary",

  chunkPre: "border-border bg-muted/40 text-foreground",
  iconBtn:
    "border border-border bg-card text-muted-foreground hover:bg-muted/50",
  iconBtnActive: "border border-primary bg-primary/10 text-primary",

  chip: "bg-muted text-muted-foreground",

  hoverBg: "hover:bg-muted",
  hoverSurface: "hover:bg-muted/50",
  control: "border-border",
  controlFocus: "focus:border-primary",

  prose: "prose prose-sm dark:prose-invert",
};
