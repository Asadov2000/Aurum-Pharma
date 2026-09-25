import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const calls = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/lib/api", () => ({ api: calls }));
vi.mock("@/features/auth/hooks", () => ({
  useAuth: () => ({
    user: {
      is_developer: false,
      active_tenant_id: "tenant-1",
      permissions: ["catalog.view", "catalog.update"],
      permission_scopes: { "catalog.view": null, "catalog.update": null },
    },
  }),
}));

import { CatalogTemplateField } from "@/features/catalog/CatalogTemplateField";

function setup(): void {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  function Form() {
    const [value, setValue] = useState("");
    return (
      <QueryClientProvider client={client}>
        <CatalogTemplateField
          kind="dosage"
          label="Дозировка"
          value={value}
          inputProps={{ value, onChange: (event) => setValue(event.target.value) }}
        />
      </QueryClientProvider>
    );
  }
  render(<Form />);
}

describe("CatalogTemplateField", () => {
  beforeEach(() => {
    calls.get.mockReset();
    calls.post.mockReset();
    calls.delete.mockReset();
    calls.get.mockResolvedValue({ data: [] });
  });

  it("saves a typed dosage as a reusable tenant template and can remove it", async () => {
    let templates: Array<{ id: string; kind: "dosage"; value: string }> = [];
    calls.get.mockImplementation(async () => ({ data: [...templates] }));
    calls.post.mockImplementation(async (_url, input) => {
      const created = { id: "template-1", kind: "dosage" as const, value: input.value };
      templates = [created];
      return { data: created };
    });
    calls.delete.mockImplementation(async () => {
      templates = [];
    });
    setup();
    fireEvent.change(screen.getByLabelText("Дозировка"), { target: { value: "500 мг" } });
    const save = await screen.findByRole("button", { name: "Сохранить как шаблон" });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    await waitFor(() =>
      expect(calls.post).toHaveBeenCalledWith("/catalog/field-templates", {
        kind: "dosage",
        value: "500 мг",
      }),
    );
    expect(await screen.findByRole("button", { name: "Удалить шаблон" })).toBeInTheDocument();
    expect(document.querySelector('#dosage-templates option[value="500 мг"]')).not.toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Удалить шаблон" }));
    fireEvent.click(
      within(screen.getByRole("dialog", { name: "Удалить шаблон?" })).getByRole("button", {
        name: "Удалить шаблон",
      }),
    );
    await waitFor(() =>
      expect(calls.delete).toHaveBeenCalledWith("/catalog/field-templates/template-1"),
    );
    await screen.findByRole("button", { name: "Сохранить как шаблон" });
    expect(screen.getByLabelText("Дозировка")).toHaveValue("500 мг");
  });
});
