"use client";

import { useState, type FormEvent } from "react";

export default function StandaloneLogin() {
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [pending, setPending] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError("");
    try {
      const response = await fetch("/api/knowledge/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!response.ok)
        throw new Error("Unable to sign in. Check your workspace password.");
      window.location.reload();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Unable to sign in.");
      setPending(false);
    }
  }

  return (
    <main className="flex min-h-screen items-center justify-center bg-slate-50 p-6">
      <form
        onSubmit={submit}
        className="w-full max-w-sm space-y-5 rounded-xl border bg-white p-8 shadow-sm"
      >
        <h1 className="text-xl font-semibold">Document Intelligence</h1>
        <label className="block space-y-2">
          <span>Workspace password</span>
          <input
            aria-label="Workspace password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            className="w-full rounded-md border p-2"
          />
        </label>
        {error && (
          <p role="alert" className="text-sm text-red-700">
            {error}
          </p>
        )}
        <button
          disabled={pending}
          className="w-full rounded-md bg-slate-900 p-2 text-white"
        >
          {pending ? "Signing in…" : "Sign in"}
        </button>
      </form>
    </main>
  );
}
