/** Translate container-local S3 links only for the supported standalone UI proxy. */
export function browserStorageUrl(url: string): string {
  return process.env.NEXT_PUBLIC_AUTH_MODE === "standalone"
    ? url.replace(/^http:\/\/localstack:4566\//, "/storage/")
    : url;
}
