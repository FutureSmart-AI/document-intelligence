import { useEffect, useState } from "react";

const EXPIRATION_MS = 2 * 60 * 60 * 1000; // 2 hours

export interface TrackedBatch {
  id: string;
  timestamp: number;
}

const getStorageKey = (knowledgeId: string) =>
  `knowledge_batches_${knowledgeId}`;

const getBatches = (knowledgeId: string): TrackedBatch[] => {
  try {
    const data = localStorage.getItem(getStorageKey(knowledgeId));
    if (!data) return [];
    const parsed: TrackedBatch[] = JSON.parse(data);
    const now = Date.now();
    return parsed.filter((b) => now - b.timestamp < EXPIRATION_MS);
  } catch {
    return [];
  }
};

const saveBatches = (knowledgeId: string, batches: TrackedBatch[]) => {
  localStorage.setItem(getStorageKey(knowledgeId), JSON.stringify(batches));
  window.dispatchEvent(
    new CustomEvent("batch_tracking_updated", { detail: { knowledgeId } }),
  );
};

export const addBatchToTracking = (knowledgeId: string, batchId: string) => {
  const current = getBatches(knowledgeId);
  const now = Date.now();

  const existingIndex = current.findIndex((b) => b.id === batchId);
  if (existingIndex >= 0) {
    current[existingIndex].timestamp = now;
  } else {
    current.push({ id: batchId, timestamp: now });
  }

  saveBatches(knowledgeId, current);
};

export const useBatchTracking = (knowledgeId: string | undefined) => {
  const [batches, setBatches] = useState<TrackedBatch[]>(() => {
    if (!knowledgeId) return [];
    return getBatches(knowledgeId);
  });

  useEffect(() => {
    if (!knowledgeId) {
      setBatches([]);
      return undefined;
    }

    const load = () => {
      setBatches(getBatches(knowledgeId));
    };

    // Load initially
    load();

    const handleEvent = (e: Event) => {
      const customEvent = e as CustomEvent;
      if (customEvent.detail?.knowledgeId === knowledgeId) {
        load();
      }
    };

    window.addEventListener("batch_tracking_updated", handleEvent);
    return () => {
      window.removeEventListener("batch_tracking_updated", handleEvent);
    };
  }, [knowledgeId]);

  return batches.map((b) => b.id);
};
