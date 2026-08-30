import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { type Branch } from "@/features/foundation/types";

const createBranch = vi.fn();
const updateBranch = vi.fn();

vi.mock("@/features/foundation/queries", () => ({
  useCreateBranch: () => ({ mutateAsync: createBranch }),
  useUpdateBranch: () => ({ mutateAsync: updateBranch }),
}));

import { BranchForm } from "@/features/foundation/BranchForm";

const branch: Branch = {
  id: "branch-1",
  tenant_id: "tenant-1",
  name: "Аптека Рудаки",
  address: "Рудаки 10",
  branch_type: "pharmacy",
  license_number: "TJ-001",
  license_expires_at: "2027-01-01",
  working_hours: null,
  receipt_header: {
    line1: "ООО Аптека Рудаки",
    line2: "Точка 1",
    phone: "+992000000000",
    inn_or_tin: "123456789",
    demo_notice: "Тестовый чек",
  },
  is_active: true,
  created_at: "2026-08-21T00:00:00Z",
  updated_at: "2026-08-21T00:00:00Z",
};

describe("BranchForm receipt details", () => {
  beforeEach(() => {
    createBranch.mockReset();
    updateBranch.mockReset();
    updateBranch.mockResolvedValue(undefined);
  });

  it("preserves the hidden demo notice while editing visible receipt details", async () => {
    render(
      <BranchForm branch={branch} onClose={vi.fn()} onCancel={vi.fn()} onDirtyChange={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText("Название точки"), {
      target: { value: "Точка 2" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() =>
      expect(updateBranch).toHaveBeenCalledWith({
        id: branch.id,
        payload: expect.objectContaining({
          receipt_header: expect.objectContaining({
            line1: "ООО Аптека Рудаки",
            line2: "Точка 2",
            demo_notice: "Тестовый чек",
          }),
        }),
      }),
    );
  });

  it("sends an explicit null when receipt details are cleared", async () => {
    render(
      <BranchForm branch={branch} onClose={vi.fn()} onCancel={vi.fn()} onDirtyChange={vi.fn()} />,
    );
    fireEvent.change(screen.getByLabelText("Название организации"), {
      target: { value: "" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить изменения" }));

    await waitFor(() =>
      expect(updateBranch).toHaveBeenCalledWith({
        id: branch.id,
        payload: expect.objectContaining({ receipt_header: null }),
      }),
    );
  });
});
