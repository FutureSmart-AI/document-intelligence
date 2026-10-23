import { defaultTo, get, isArray, isEmpty, isNil, isString } from "lodash-es";

export const extractErrorMessage = (
  error: unknown,
  fallback: string,
): string => {
  if (!error) return fallback;
  if (isString(error)) return error;
  if (error instanceof Error) return defaultTo(error.message, fallback);
  const responseDetail = defaultTo(
    get(error, "response.data.detail"),
    defaultTo(get(error, "message"), get(error, "detail")),
  );
  if (
    isString(responseDetail) &&
    !isEmpty(defaultTo(responseDetail, "").trim())
  ) {
    return responseDetail;
  }
  return fallback;
};

export const isStringArray = (value: unknown): value is string[] =>
  isArray(value) && value.every((item) => isString(item));

export const isPlainObject = (value: unknown): value is Record<string, any> =>
  !isNil(value) && typeof value === "object" && !isArray(value);

export const hasRenderableValue = (value: any): boolean => {
  if (isNil(value)) return false;
  if (isString(value)) return !isEmpty(value.trim());
  if (isArray(value)) return !isEmpty(value);
  if (isPlainObject(value)) return !isEmpty(value);
  return true;
};
