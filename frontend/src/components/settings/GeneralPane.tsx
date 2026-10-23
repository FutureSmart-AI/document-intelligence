"use client";

import { useEffect, useMemo, useState } from "react";

import Icon from "@/components/Icon";
import {
  Btn,
  Card,
  CheckRow,
  FieldHelp,
  FieldLabel,
  Hair,
  Input,
  Pill,
  SectionLabel,
  Select,
  Stepper,
} from "@/components/ui";
import {
  useAnswerModels,
  useCollectionSettings,
  useSaveCollectionSettings,
  type CollectionSettings,
} from "@/data/chatConfig";
import { getVoiceLanguage, setVoiceLanguage } from "@/data/localPrefs";
import { useToast } from "@/platform/components/ToastContext";
import { RichSelect, type RichSelectOption } from "@/platform/components/ui";

const languages = [
  { value: "", label: "Auto-detect" },
  { value: "en", label: "English" },
  { value: "hi", label: "Hindi" },
  { value: "es", label: "Spanish" },
];

/**
 * How this collection answers.
 *
 * Every control here is one field of the knowledge service's own chat config
 * (`GET/PUT /api/knowledge/v1/{knowledge_id}/chat/config`), so what the screen
 * shows is what the next question will be answered with — the answer model, how
 * creative it may be, what the answer is allowed to carry, voice, and the
 * questions offered before anyone has asked one.
 *
 * The one exception is kept per collection in `data/localPrefs`: the voice
 * language, which that config has no field for.
 *
 * Document processing is deliberately **not** here. It is a per-document upload
 * field, not a collection setting, so a control on this screen implied a
 * collection-wide choice the service has nowhere to keep — it is asked where the
 * documents are added instead, on the add-documents and create dialogs.
 */
export default function GeneralPane({
  collectionId,
}: {
  collectionId: string;
}) {
  const { toast } = useToast();
  const { data: settings, isLoading } = useCollectionSettings(collectionId);
  const { data: providers = [] } = useAnswerModels();
  const save = useSaveCollectionSettings(collectionId);

  const [form, setForm] = useState<CollectionSettings>({});
  const [language, setLanguage] = useState("");
  const [newQuestion, setNewQuestion] = useState<string | null>(null);

  // Seed the form once the saved settings arrive, and again whenever a save
  // brings fresh ones back.
  useEffect(() => {
    if (settings) setForm(settings);
  }, [settings]);

  // A collection with no chat config of its own answers on the service's own
  // default model, so that is what the screen has to show — read from the
  // catalogue rather than named here, since the backend changes it without a UI
  // release. Only ever fills a blank; a chosen model is never overwritten.
  useEffect(() => {
    if (providers.length === 0) return;
    setForm((current) => {
      if (current.llm_model) return current;
      const provider =
        providers.find(
          (candidate) => candidate.provider === current.llm_provider,
        ) ?? providers[0];
      const model = provider.defaultModel ?? provider.models[0];
      if (!model) return current;
      return { ...current, llm_provider: provider.provider, llm_model: model };
    });
  }, [providers, settings]);

  useEffect(() => {
    setLanguage(getVoiceLanguage(collectionId));
  }, [collectionId]);

  // The model list follows the chosen provider, the way the agent dashboard's
  // Model Configuration pair does.
  const providerOptions: RichSelectOption<string>[] = useMemo(
    () =>
      providers.map((provider) => ({
        value: provider.provider,
        label: provider.provider,
      })),
    [providers],
  );

  const chosenProvider = providers.find(
    (provider) => provider.provider === form.llm_provider,
  );

  const modelOptions: RichSelectOption<string>[] = useMemo(
    () =>
      (chosenProvider?.models ?? []).map((model) => ({
        value: model,
        label: model,
      })),
    [chosenProvider],
  );

  const questions = form.suggested_questions ?? [];

  const patch = (next: Partial<CollectionSettings>) =>
    setForm((current) => ({ ...current, ...next }));

  const submit = async () => {
    try {
      const response = await save.mutateAsync({
        show_sources: form.show_sources,
        follow_up: form.follow_up,
        include_charts: form.include_charts,
        show_raw_response: form.show_raw_response,
        voice_input_enabled: form.voice_input_enabled,
        voice_output_enabled: form.voice_output_enabled,
        suggested_questions: questions,
        llm_provider: form.llm_provider,
        llm_model: form.llm_model,
        temperature: form.temperature,
        max_tokens: form.max_tokens,
      });
      setVoiceLanguage(collectionId, language);
      toast.success(response?.message || "Settings saved");
    } catch {
      // Surfaced by the shared axios interceptor.
    }
  };

  if (isLoading) {
    return (
      <p className="text-[13px] text-ink-2">
        Reading this collection&rsquo;s settings…
      </p>
    );
  }

  return (
    <div className="max-w-[760px]">
      <SectionLabel>How Document Intelligence answers</SectionLabel>
      <Hair />

      {providers.length === 0 && (
        <FieldHelp className="mb-3">
          The list of answer models could not be read. The collection keeps the
          model it already has.
        </FieldHelp>
      )}

      <div className="mb-2 grid gap-3.5 sm:grid-cols-2">
        <div>
          <FieldLabel>Provider</FieldLabel>
          <RichSelect
            options={providerOptions}
            value={form.llm_provider || undefined}
            placeholder="Select provider"
            disabled={providerOptions.length === 0}
            onChange={(provider) => {
              // Switching provider strands the old model, so the new provider's
              // recommended one (or its first) takes over.
              const next = providers.find(
                (candidate) => candidate.provider === provider,
              );
              patch({
                llm_provider: provider,
                llm_model: next?.defaultModel ?? next?.models[0] ?? "",
              });
            }}
          />
        </div>
        <div>
          <FieldLabel>Answer model</FieldLabel>
          <RichSelect
            options={modelOptions}
            value={form.llm_model || undefined}
            placeholder="Select model"
            disabled={!form.llm_provider || modelOptions.length === 0}
            onChange={(model) => patch({ llm_model: model })}
            renderOption={(option, isSelected) => (
              <div
                className={`flex items-center justify-between gap-3 px-3 py-2 text-sm ${
                  isSelected ? "bg-indigo-50 text-indigo-700" : "text-slate-700"
                }`}
              >
                <span className="truncate">{option.label}</span>
                {chosenProvider?.defaultModel === option.value && (
                  <Pill tone="success">Recommended</Pill>
                )}
              </div>
            )}
          />
        </div>
        <div>
          <FieldLabel>Temperature</FieldLabel>
          <Stepper
            label="Temperature"
            value={form.temperature ?? 0.3}
            step={0.1}
            min={0}
            max={2}
            onChange={(value) => patch({ temperature: value })}
          />
        </div>
        <div>
          <FieldLabel>Max tokens</FieldLabel>
          <Stepper
            label="Max tokens"
            value={form.max_tokens ?? 1024}
            step={128}
            min={128}
            max={8192}
            onChange={(value) => patch({ max_tokens: value })}
          />
        </div>
      </div>

      <FieldHelp className="mb-4">
        You can change this anytime — it only affects future answers, not your
        indexed documents.
      </FieldHelp>

      <SectionLabel className="mt-1.5">What answers can include</SectionLabel>
      <Hair />
      <div className="grid gap-x-5 sm:grid-cols-2">
        <CheckRow
          label="Cite source documents"
          desc="Show which files an answer came from"
          checked={Boolean(form.show_sources)}
          onChange={(value) => patch({ show_sources: value })}
        />
        <CheckRow
          label="Suggest follow-up questions"
          desc="Offer related questions after each answer"
          checked={Boolean(form.follow_up)}
          onChange={(value) => patch({ follow_up: value })}
        />
        <CheckRow
          label="Include charts when useful"
          desc="Render a chart for numeric comparisons"
          checked={Boolean(form.include_charts)}
          onChange={(value) => patch({ include_charts: value })}
        />
        <CheckRow
          label="Show raw response"
          desc="Debug — adds the full API response under each answer"
          checked={Boolean(form.show_raw_response)}
          onChange={(value) => patch({ show_raw_response: value })}
        />
      </div>

      <SectionLabel className="mt-2.5">Voice</SectionLabel>
      <Hair />
      <Card className="mb-3.5 px-4 py-3.5">
        <CheckRow
          className="py-0 pb-2.5"
          label="Enable Voice Input"
          checked={Boolean(form.voice_input_enabled)}
          onChange={(value) => patch({ voice_input_enabled: value })}
        />
        {form.voice_input_enabled && (
          <div className="max-w-[320px] pl-6.5">
            <FieldLabel htmlFor="di-voice-language">
              Default Language
            </FieldLabel>
            <Select
              id="di-voice-language"
              value={language}
              onChange={(event) => setLanguage(event.target.value)}
            >
              {languages.map((language) => (
                <option key={language.label} value={language.value}>
                  {language.label}
                </option>
              ))}
            </Select>
            <FieldHelp>
              Sent to the transcription service to improve accuracy. Leave on
              Auto-detect for mixed-language use.
            </FieldHelp>
          </div>
        )}
        <CheckRow
          className="mt-2.5 border-t border-line-soft pt-2.5"
          label="Enable Voice Output"
          checked={Boolean(form.voice_output_enabled)}
          onChange={(value) => patch({ voice_output_enabled: value })}
        />
      </Card>

      <SectionLabel className="mt-2.5">Suggested Questions</SectionLabel>
      <Hair />
      <FieldHelp className="mt-0 mb-2.5">
        Add suggested questions that users can click to start conversations.
      </FieldHelp>
      <div className="mb-2.5 flex flex-wrap gap-1.5">
        {questions.length === 0 && (
          <span className="text-[13.5px] text-ink-2">
            No suggested questions yet.
          </span>
        )}
        {questions.map((question, index) => (
          <Pill key={`${question}-${index}`} tone="neutral">
            {question}
            <button
              type="button"
              aria-label={`Remove "${question}"`}
              onClick={() =>
                patch({
                  suggested_questions: questions.filter(
                    (_, position) => position !== index,
                  ),
                })
              }
              className="ml-0.5 cursor-pointer text-ink-3 hover:text-danger-text"
            >
              <Icon name="x" className="size-3" />
            </button>
          </Pill>
        ))}
      </div>

      {newQuestion === null ? (
        <Btn
          size="sm"
          className="w-full justify-center border-dashed"
          onClick={() => setNewQuestion("")}
          startIcon={<Icon name="plus" className="size-4" />}
        >
          Add Question
        </Btn>
      ) : (
        <div className="mb-2.5 flex gap-2">
          <Input
            autoFocus
            value={newQuestion}
            placeholder="Type a question…"
            onChange={(event) => setNewQuestion(event.target.value)}
            onKeyDown={(event) => {
              if (event.key !== "Enter") return;
              event.preventDefault();
              const value = newQuestion.trim();
              if (value) {
                patch({ suggested_questions: [...questions, value] });
              }
              setNewQuestion(null);
            }}
          />
          <Btn
            variant="primary"
            size="sm"
            onClick={() => {
              const value = newQuestion.trim();
              if (value) patch({ suggested_questions: [...questions, value] });
              setNewQuestion(null);
            }}
          >
            Add
          </Btn>
          <Btn size="sm" onClick={() => setNewQuestion(null)}>
            Cancel
          </Btn>
        </div>
      )}

      <div className="mt-5">
        <Btn variant="primary" onClick={submit} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save changes"}
        </Btn>
      </div>
    </div>
  );
}
