export interface PersonalShift {
  id: string;
  branch_id: string;
  branch_name: string;
  register_id: string;
  register_name: string;
  opened_at: string;
  closed_at: string | null;
  status: "open" | "closed" | "suspended";
  sales_total: string;
  returns_total: string;
  currency: string;
}

export interface PersonalShiftList {
  items: PersonalShift[];
  total: number;
  page: number;
  page_size: number;
  timezone: string;
}
