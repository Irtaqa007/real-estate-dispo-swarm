import { describe, it, expect, vi, beforeEach } from "vitest";
import { apiFetch, cacheClear } from "./api";

beforeEach(() => {
  vi.restoreAllMocks();
  cacheClear(); // Clear API cache between tests
});

describe("apiFetch", () => {
  it("returns parsed JSON for a successful 200 response", async () => {
    const mockData = { id: "1", name: "Test" };
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(mockData), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await apiFetch<typeof mockData>("/api/test");
    expect(result).toEqual(mockData);
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/test", {
      headers: { "Content-Type": "application/json" },
    });
  });

  it("merges custom init with default headers", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ ok: true }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      })
    );

    await apiFetch("/api/test", {
      method: "POST",
      body: JSON.stringify({ key: "value" }),
    });

    expect(globalThis.fetch).toHaveBeenCalledWith("/api/test", {
      method: "POST",
      body: JSON.stringify({ key: "value" }),
      headers: { "Content-Type": "application/json" },
    });
  });

  it("throws with body.detail for a 400 error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ detail: "Bad request" }), {
        status: 400,
        headers: { "Content-Type": "application/json" },
      })
    );

    await expect(apiFetch("/api/test")).rejects.toThrow("Bad request");
  });

  it("throws with HTTP status fallback when body has no detail", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({ message: "nope" }), {
        status: 403,
        headers: { "Content-Type": "application/json" },
      })
    );

    await expect(apiFetch("/api/test")).rejects.toThrow("Request failed (403)");
  });

  it("throws with HTTP status fallback when error body is not JSON", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response("Internal Server Error", {
        status: 500,
        headers: { "Content-Type": "text/plain" },
      })
    );

    await expect(apiFetch("/api/test")).rejects.toThrow("Request failed (500)");
  });

  it("returns undefined for a 204 No Content response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(null, { status: 204 })
    );

    const result = await apiFetch<void>("/api/test");
    expect(result).toBeUndefined();
  });

  it("re-throws a network error when fetch fails", async () => {
    vi.spyOn(globalThis, "fetch").mockRejectedValueOnce(
      new TypeError("Failed to fetch")
    );

    await expect(apiFetch("/api/test")).rejects.toThrow("Failed to fetch");
  });

  it("passes custom headers from init (overriding default)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify({}), { status: 200 })
    );

    await apiFetch("/api/test", {
      headers: { Authorization: "Bearer token123" },
    });

    // Default Content-Type should be overridden since we passed headers
    expect(globalThis.fetch).toHaveBeenCalledWith("/api/test", {
      headers: { Authorization: "Bearer token123" },
    });
  });

  it("handles a 201 Created response correctly", async () => {
    const created = { id: "new-id" };
    vi.spyOn(globalThis, "fetch").mockResolvedValueOnce(
      new Response(JSON.stringify(created), {
        status: 201,
        headers: { "Content-Type": "application/json" },
      })
    );

    const result = await apiFetch("/api/test", { method: "POST" });
    expect(result).toEqual(created);
  });
});
