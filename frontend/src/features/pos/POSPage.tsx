import { useEffect, useState } from "react";

import { Card, CardContent, Label, Select } from "@/components/ui";
import { useRegistersQuery } from "@/features/foundation/queries";

import { SaleArea } from "./SaleArea";
import { ShiftBar } from "./ShiftBar";
import { useCurrentShiftQuery } from "./queries";

const STORAGE_KEY = "pos:lastRegisterId";

export function POSPage(): JSX.Element {
  const registers = useRegistersQuery(null, false);
  const [registerId, setRegisterId] = useState<string>(() => {
    return window.localStorage.getItem(STORAGE_KEY) ?? "";
  });

  // Auto-select the first active register the first time the user lands.
  useEffect(() => {
    if (!registerId && registers.data && registers.data.length > 0) {
      const first = registers.data[0];
      if (first) setRegisterId(first.id);
    }
  }, [registers.data, registerId]);

  useEffect(() => {
    if (registerId) window.localStorage.setItem(STORAGE_KEY, registerId);
  }, [registerId]);

  const shiftQuery = useCurrentShiftQuery(registerId || null);
  const hasShift = Boolean(shiftQuery.data);

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-semibold text-slate-900">Касса</h1>

      <Card>
        <CardContent className="flex items-end gap-3 py-4">
          <div>
            <Label htmlFor="register">Касса</Label>
            <Select
              id="register"
              value={registerId}
              onChange={(e) => setRegisterId(e.target.value)}
              className="w-64"
            >
              <option value="">— выберите —</option>
              {registers.data?.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.name}
                </option>
              ))}
            </Select>
          </div>
          {registers.data?.length === 0 && (
            <p className="text-sm text-slate-500">
              Нет активных касс. Создайте кассу в разделе «Кассы».
            </p>
          )}
        </CardContent>
      </Card>

      {registerId && (
        <>
          <ShiftBar registerId={registerId} />
          {hasShift && <SaleArea registerId={registerId} />}
        </>
      )}
    </div>
  );
}
