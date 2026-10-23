export const isSafeHttpUrl = (url: string | undefined | null): boolean => {
  if (!url) return false;
  try {
    return new URL(url).protocol === "https:";
  } catch {
    return false;
  }
};
