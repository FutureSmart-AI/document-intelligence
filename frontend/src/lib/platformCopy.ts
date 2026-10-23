/**
 * Copy that differs between FutureSmart's hosted service and a self-hosted
 * installation. In the standalone build, services without an owner key run on
 * the keys the operator configured, not on FutureSmart's.
 */
const isStandalone = () => process.env.NEXT_PUBLIC_AUTH_MODE === "standalone";

export function platformCredentialsLabel(): string {
  return isStandalone()
    ? "this installation's configured keys"
    : "FutureSmart platform credentials";
}

export function productTagline(): string {
  return isStandalone()
    ? "Turn a folder of documents into structured, searchable data."
    : "Turn a folder of documents into structured, searchable data. Works out of the box — our team can fine-tune it further for your specific needs.";
}
