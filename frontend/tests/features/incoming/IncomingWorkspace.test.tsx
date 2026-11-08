import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { IncomingWorkspace } from "@/features/incoming/IncomingWorkspace";
import { type IncomingDocumentWithItems, type IncomingDraftRow } from "@/features/incoming/types";

const document: IncomingDocumentWithItems = {
  id: "document",
  tenant_id: "tenant",
  branch_id: "branch",
  supplier_id: "supplier",
  document_number: "TEST-1",
  document_date: "2026-11-08",
  status: "draft",
  total_amount: "12.00",
  currency: "TJS",
  notes: null,
  document_file_path: null,
  created_at: "2026-11-08T00:00:00Z",
  updated_at: "2026-11-08T00:00:00Z",
  accepted_at: null,
  items: [
    {
      id: "item",
      document_id: "document",
      catalog_id: "catalog",
      catalog_name: "Готовый товар",
      batch_number: "LOT",
      manufactured_at: null,
      expires_at: "2099-12-31",
      qty: "2",
      purchase_price: "6.00",
      sale_price: "9.00",
      currency: "TJS",
      created_batch_id: null,
    },
  ],
};
const draft: IncomingDraftRow = {
  id: "draft",
  document_id: "document",
  revision: 2,
  catalog_name: null,
  created_at: document.created_at,
  updated_at: document.updated_at,
  fields: {
    source_name: "Строка поставщика",
    catalog_id: "",
    batch_number: "",
    manufactured_at: "",
    expires_at: "",
    qty: "12,",
    purchase_price: "98,76",
    sale_price: "",
  },
};
function callbacks() {
  return {
    onEditDocument: vi.fn(),
    onImport: vi.fn(),
    onAdd: vi.fn(),
    onEditItem: vi.fn(),
    onDeleteItem: vi.fn(),
    onContinueDraft: vi.fn(),
    onDeleteDraft: vi.fn(),
    onRetryDrafts: vi.fn(),
    onAccept: vi.fn(),
    onReject: vi.fn(),
  };
}
const flags = {
  isDesktopLayout: false,
  draftsPending: false,
  draftsError: false,
  canEdit: true,
  canFinalize: true,
  acceptBlocked: false,
  draftsBlockAccept: false,
};

describe("IncomingWorkspace", () => {
  it.each([true, false])(
    "keeps a single row per item and the authoritative total (desktop=%s)",
    (isDesktopLayout) => {
      const handlers = callbacks();
      render(
        <IncomingWorkspace
          {...flags}
          isDesktopLayout={isDesktopLayout}
          {...handlers}
          document={document}
          drafts={[draft]}
          draftsBlockAccept
        />,
      );
      expect(screen.getByText("12,00 TJS")).toBeInTheDocument();
      expect(screen.getAllByText("Строка поставщика")).toHaveLength(1);
      expect(screen.getAllByText("Проверьте значение").length).toBeGreaterThan(0);
      fireEvent.click(screen.getByRole("button", { name: "Требуют внимания · 1" }));
      expect(screen.queryByText("Готовый товар")).not.toBeInTheDocument();
      fireEvent.click(screen.getAllByRole("button", { name: "Продолжить", exact: true })[0]!);
      expect(handlers.onContinueDraft).toHaveBeenCalledWith(draft);
      expect(screen.getByRole("button", { name: "Принять на склад" })).toBeDisabled();
      expect(handlers.onAccept).not.toHaveBeenCalled();
    },
  );

  it("does not reconstruct restricted purchase prices or infer a loss", () => {
    const redacted = {
      ...document,
      total_amount: null,
      items: document.items.map((item) => ({ ...item, purchase_price: null })),
    };
    render(<IncomingWorkspace {...flags} {...callbacks()} document={redacted} drafts={[]} />);
    expect(screen.getAllByText("Доступ ограничен").length).toBeGreaterThan(0);
    expect(screen.queryByText("12,00 TJS")).not.toBeInTheDocument();
    expect(screen.queryByText("6,00 TJS")).not.toBeInTheDocument();
    expect(screen.queryByText("Продажа ниже закупки")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Требуют внимания · 0" })).toBeInTheDocument();
  });

  it.each(["accepted", "rejected"] as const)(
    "keeps %s documents immutable despite edit permissions",
    (status) => {
      render(
        <IncomingWorkspace
          {...flags}
          {...callbacks()}
          document={{ ...document, status }}
          drafts={[draft]}
        />,
      );
      for (const name of [
        "Добавить позицию",
        "Изменить реквизиты",
        "Принять на склад",
        "Удалить",
        "Изменить",
        "Продолжить",
      ])
        expect(screen.queryByRole("button", { name, exact: true })).not.toBeInTheDocument();
      expect(screen.queryByText("Строка поставщика")).not.toBeInTheDocument();
    },
  );
});
