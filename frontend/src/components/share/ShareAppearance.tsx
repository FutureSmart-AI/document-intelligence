"use client";

import Icon from "@/components/Icon";
import {
  Btn,
  FieldHelp,
  FieldLabel,
  Input,
  Textarea,
  ToggleRow,
} from "@/components/ui";
import { MAX_WIDGET_ASSET_BYTES } from "@/data/widgetSettings";
import { apiMessage } from "@/lib/apiError";
import { useToast } from "@/platform/components/ToastContext";

import { BRAND_SWATCHES, FALLBACK_GLYPHS } from "./constants";
import RemoteAsset from "./RemoteAsset";
import type { AppearanceDraft } from "./useAppearanceDraft";

/**
 * How a shared collection looks.
 *
 * The preview of exactly that is the row below this one, drawn from the same
 * draft — see `useAppearanceDraft`, which holds it for both.
 *
 * The colour choices are this product's own brand steps rather than a free
 * picker: the stored value is a hex the shared chat surface paints its header
 * and send button with, and an arbitrary colour there can land unreadable
 * against white text. Each swatch below is a step that does not.
 *
 * An icon or avatar is either one of the service's defaults (sent as a URL) or
 * an uploaded file — never both, because the service prefers the file and
 * sending the pair would leave which one wins to the reader's imagination.
 */

/**
 * One row of icon choices — the service's defaults, plus an upload.
 *
 * Each choice is drawn by `RemoteAsset`, which is what makes them *visible*:
 * the defaults are stored as `binary/octet-stream`, so a plain `<img>` painted
 * nothing and the row read as a line of empty boxes. A choice whose asset
 * cannot be recovered at all still shows a glyph from this product's own
 * sprite, so the row is never blank.
 */
function AssetPicker({
  label,
  urls,
  loading,
  selected,
  picked,
  onSelect,
  onUpload,
}: {
  label: string;
  urls: string[];
  loading: boolean;
  selected?: string;
  /** A file chosen but not yet saved. */
  picked?: File;
  onSelect: (url: string) => void;
  onUpload: (file: File | undefined) => void;
}) {
  return (
    <div>
      <FieldLabel>{label}</FieldLabel>
      <div className="flex flex-wrap gap-2">
        {urls.map((url, index) => (
          <button
            key={url}
            type="button"
            aria-label={`${label} ${index + 1}`}
            aria-pressed={selected === url}
            onClick={() => onSelect(url)}
            className={
              selected === url
                ? "grid size-9 cursor-pointer place-items-center rounded-di-md border-2 border-accent bg-surface text-accent"
                : "grid size-9 cursor-pointer place-items-center rounded-di-md border border-line bg-surface text-ink-2 hover:bg-sunken hover:text-ink"
            }
          >
            <RemoteAsset
              url={url}
              className="size-5 object-contain"
              fallback={
                <Icon
                  name={FALLBACK_GLYPHS[index % FALLBACK_GLYPHS.length]}
                  className="size-[18px]"
                />
              }
            />
          </button>
        ))}

        {loading && !urls.length && (
          <span className="text-[11.5px] text-ink-3">Loading choices…</span>
        )}

        <label className="grid size-9 cursor-pointer place-items-center rounded-di-md border border-dashed border-line-strong text-ink-3 hover:bg-sunken hover:text-ink">
          <Icon name="up" className="size-4" />
          <span className="sr-only">Upload a {label.toLowerCase()}</span>
          <input
            type="file"
            accept="image/*"
            className="hidden"
            onChange={(event) => {
              const file = event.target.files?.[0];
              // Cleared so choosing the same file twice fires again.
              event.target.value = "";
              onUpload(file);
            }}
          />
        </label>
      </div>
      {picked ? (
        <FieldHelp>Uploading {picked.name} on save.</FieldHelp>
      ) : (
        !loading &&
        !urls.length && (
          <FieldHelp>
            The service offers no default {label.toLowerCase()}s — upload one.
          </FieldHelp>
        )
      )}
    </div>
  );
}

export default function ShareAppearance({
  appearance,
}: {
  /** Held by the screen, because the preview is a row of its own. */
  appearance: AppearanceDraft;
}) {
  const { toast } = useToast();
  const { draft, patch, icons, avatars, save } = appearance;

  /**
   * The presets, plus whatever colour is actually stored.
   *
   * A collection saved before this screen existed can carry any hex — the live
   * one carries `#3a5fe3`, which is none of the five below — and a picker with
   * nothing selected reads as "no colour set" when a colour very much is set.
   * So the stored value joins the row rather than being quietly dropped.
   */
  const swatches =
    draft.color && !BRAND_SWATCHES.some((swatch) => swatch.hex === draft.color)
      ? [
          ...BRAND_SWATCHES,
          { hex: draft.color, label: `Stored (${draft.color})` },
        ]
      : BRAND_SWATCHES;

  const pickFile = (kind: "icon" | "avatar") => (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_WIDGET_ASSET_BYTES) {
      toast.error("That image is over 2 MB, which the service rejects.");
      return;
    }
    patch(
      kind === "icon"
        ? { widget_icon_file: file, widget_icon: undefined }
        : { widget_avatar_file: file, widget_avatar: undefined },
    );
  };

  return (
    <div className="grid gap-3.5">
      <div>
        <FieldLabel required htmlFor="share-heading">
          Heading
        </FieldLabel>
        <Input
          id="share-heading"
          value={draft.heading}
          onChange={(event) => patch({ heading: event.target.value })}
        />
      </div>
      <div>
        <FieldLabel required htmlFor="share-sub">
          Sub heading
        </FieldLabel>
        <Input
          id="share-sub"
          value={draft.sub}
          onChange={(event) => patch({ sub: event.target.value })}
        />
      </div>
      <div>
        <FieldLabel required htmlFor="share-greet">
          Greeting
        </FieldLabel>
        <Textarea
          id="share-greet"
          rows={3}
          value={draft.greet}
          onChange={(event) => patch({ greet: event.target.value })}
        />
        <FieldHelp>The first thing a reader sees, before they ask.</FieldHelp>
      </div>

      <div>
        <FieldLabel>Brand colour</FieldLabel>
        <div className="flex flex-wrap gap-2">
          {swatches.map((swatch) => (
            <button
              key={swatch.hex}
              type="button"
              title={swatch.label}
              aria-label={`Brand colour ${swatch.label}`}
              aria-pressed={draft.color === swatch.hex}
              onClick={() => patch({ color: swatch.hex })}
              style={{ background: swatch.hex }}
              className={
                draft.color === swatch.hex
                  ? "size-7 cursor-pointer rounded-full ring-2 ring-accent ring-offset-2 ring-offset-surface"
                  : "size-7 cursor-pointer rounded-full ring-1 ring-line"
              }
            />
          ))}
        </div>
        <FieldHelp>
          Used for the header, the launcher and the send button.
        </FieldHelp>
      </div>

      <ToggleRow
        label="Show the source documents"
        desc="Off keeps just the answer — better for readers who do not want the file list."
        checked={draft.show_sources}
        onChange={(next) => patch({ show_sources: next })}
      />

      <AssetPicker
        label="Launcher icon"
        urls={icons.data ?? []}
        loading={icons.isLoading}
        selected={draft.widget_icon}
        picked={draft.widget_icon_file}
        onSelect={(url) =>
          patch({ widget_icon: url, widget_icon_file: undefined })
        }
        onUpload={pickFile("icon")}
      />

      <AssetPicker
        label="Chat avatar"
        urls={avatars.data ?? []}
        loading={avatars.isLoading}
        selected={draft.widget_avatar}
        picked={draft.widget_avatar_file}
        onSelect={(url) =>
          patch({ widget_avatar: url, widget_avatar_file: undefined })
        }
        onUpload={pickFile("avatar")}
      />

      <div className="flex flex-wrap items-center gap-2">
        <Btn
          variant="primary"
          className="ml-auto"
          disabled={save.isPending || !draft.heading.trim()}
          onClick={async () => {
            try {
              await save.mutateAsync(draft);
              toast.success("Appearance saved.");
            } catch (error) {
              toast.error(
                apiMessage(error, "The appearance could not be saved."),
              );
            }
          }}
        >
          Save appearance
        </Btn>
      </div>
    </div>
  );
}
