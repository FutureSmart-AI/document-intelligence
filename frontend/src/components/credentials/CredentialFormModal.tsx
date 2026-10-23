"use client";

import { useEffect, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  FieldHelp,
  FieldLabel,
  Input,
  Modal,
  Select,
} from "@/components/ui";
import {
  MIN_API_KEY_LENGTH,
  providerGroups,
  useCredentialProviders,
  useCredentials,
  useSaveCredential,
} from "@/data/credentials";
import { useToast } from "@/platform/components/ToastContext";

/**
 * Add or replace one provider key.
 *
 * The form alone — the keys themselves live on the credentials board, which is
 * what opens this. A provider that already has a key is offered as a disabled
 * option rather than dropped from the list: an account has one key per
 * provider, and silently hiding the ones already added leaves the reader
 * wondering where their provider went.
 *
 * The picker is built from the set the service pins in its own request schema
 * and merely enriched by the catalogue route, so a catalogue request that is
 * still in flight — or one that fails — cannot leave the dropdown empty with no
 * way to add a key at all.
 */
export default function CredentialFormModal({
  open,
  onClose,
  /** When set, the provider is fixed and saving replaces its stored key. */
  editProvider,
}: {
  open: boolean;
  onClose: () => void;
  editProvider?: string;
}) {
  const { toast } = useToast();
  const { data: credentials = [] } = useCredentials();
  const {
    data: providerOptions,
    isLoading: providersLoading,
    isError: providersFailed,
  } = useCredentialProviders();
  const save = useSaveCredential();

  const [provider, setProvider] = useState(editProvider ?? "");
  const [apiKey, setApiKey] = useState("");

  // Opening the dialog on a different row must not keep the last one's
  // provider, and must never keep a typed key around.
  useEffect(() => {
    if (open) {
      setProvider(editProvider ?? "");
      setApiKey("");
    }
  }, [open, editProvider]);

  const groups = providerGroups(providerOptions);
  const noProviders = !providersLoading && groups.length === 0;
  const keyTooShort = apiKey.trim().length < MIN_API_KEY_LENGTH;
  const canSubmit = Boolean(provider) && !keyTooShort && !save.isPending;
  const hasKey = (name: string) =>
    credentials.some(
      (credential) => credential.provider.toLowerCase() === name.toLowerCase(),
    );

  const submit = async () => {
    if (!provider || keyTooShort) return;
    try {
      await save.mutateAsync({ provider, apiKey: apiKey.trim() });
      // The response carries the stored mask rather than a message, so the
      // success line is worded here.
      toast.success(
        editProvider
          ? `${provider} credential updated`
          : `${provider} credential added — every collection in this workspace now uses your key`,
      );
      onClose();
    } catch {
      // Surfaced by the shared axios interceptor. The dialog stays open: the
      // service stores nothing when it rejects a key, and closing here would
      // report a save that did not happen.
    }
  };

  return (
    <Modal
      open={open}
      onClose={onClose}
      title={
        editProvider
          ? `Edit credential — ${editProvider}`
          : "Add New Credential"
      }
      width="560px"
      footer={
        <>
          <Btn onClick={onClose}>Cancel</Btn>
          <Btn variant="primary" onClick={submit} disabled={!canSubmit}>
            {save.isPending
              ? "Saving…"
              : editProvider
                ? "Save changes"
                : "Add Credential"}
          </Btn>
        </>
      }
    >
      <p className="m-0 mb-3.5 text-[13.5px] text-ink-2">
        Enter your provider credentials securely below.
      </p>

      <div className="mb-3.5">
        <FieldLabel htmlFor="di-cred-provider">
          Provider service <span className="text-danger-text">*</span>
        </FieldLabel>
        <Select
          id="di-cred-provider"
          value={provider}
          disabled={Boolean(editProvider) || providersLoading || noProviders}
          onChange={(event) => setProvider(event.target.value)}
        >
          {/* The list is the service's catalogue and nothing else, so while it
              is in flight — or if it could not be read — the picker says which
              of the two it is rather than showing an empty dropdown. */}
          <option value="">
            {providersLoading
              ? "Loading providers…"
              : noProviders
                ? "No providers available"
                : "Select a provider"}
          </option>
          {groups.map((group) => (
            <optgroup key={group.label} label={group.label}>
              {group.providers.map((name) => {
                const taken = name !== editProvider && hasKey(name);
                return (
                  <option key={name} value={name} disabled={taken}>
                    {name}
                    {taken ? " — already added, edit it on the board" : ""}
                  </option>
                );
              })}
            </optgroup>
          ))}
        </Select>
        <FieldHelp>
          {providersFailed
            ? "The provider catalogue could not be read, so there is nothing to choose from. Try again in a moment."
            : "Answer providers write the answers. Document and website readers turn your files and links into text."}
        </FieldHelp>
      </div>

      <div className="mb-3.5">
        <FieldLabel htmlFor="di-cred-key">
          API key <span className="text-danger-text">*</span>
        </FieldLabel>
        <Input
          id="di-cred-key"
          type="password"
          autoComplete="off"
          placeholder="sk-…"
          className="font-mono"
          value={apiKey}
          onChange={(event) => setApiKey(event.target.value)}
        />
        <FieldHelp>
          {apiKey.trim() && keyTooShort
            ? `Provider keys are at least ${MIN_API_KEY_LENGTH} characters — this one is too short to store.`
            : "Shown afterwards as the first four and last two characters only."}
        </FieldHelp>
      </div>

      <div className="flex items-start gap-2.5 rounded-di-md bg-warn-bg px-3 py-2.5 text-[12.5px] font-semibold text-warn-text">
        <Icon name="warn" className="mt-px size-[15px] shrink-0" />
        <div>
          The key is encrypted before it is stored and never sent back — a
          stored key is only ever read as a mask. Saving again replaces the one
          that provider has.
        </div>
      </div>
    </Modal>
  );
}
